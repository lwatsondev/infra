#!/usr/bin/env -S {{ __notifications_uv_bin_dir }}/uv run --script

# /// script
# requires-python = ">=3.14"
# dependencies = [
#   "apprise",
# ]
# ///

#
# {{ ansible_managed }}
#

import os
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum, auto
from html import escape
from logging import INFO, Formatter, basicConfig, getLogger
from logging.handlers import SysLogHandler
from pathlib import Path
from socket import gethostname
from time import time

from apprise import Apprise, AppriseConfig, NotifyFormat, NotifyType

APPRISE_CONFIG = "/etc/apprise/apprise.yml"
LOCK_DIR = Path("/var/lock")

_syslog_handler = SysLogHandler(address="/dev/log")
_syslog_handler.setFormatter(Formatter("%(name)s: %(message)s"))
basicConfig(handlers=[_syslog_handler], level=INFO)
log = getLogger("zfs-notify")


class Level(Enum):
    ERROR = auto()
    WARNING = auto()
    INFO = auto()
    OK = auto()
    CHECK = auto()
    RECOVERY_CHECK = auto()
    STATECHANGE = auto()


# Matches the state list in ZFS's own statechange-notify.sh zedlet.
BAD_VDEV_STATES = {"DEGRADED", "FAULTED", "UNAVAIL", "REMOVED"}

SUBCLASS_LEVEL: dict[str, Level] = {
    subclass: level
    for level, subclasses in [
        (
            Level.ERROR,
            ["io", "io_failure", "data", "vdev_fault", "vdev_degraded"],
        ),
        (Level.WARNING, ["checksum", "scrub_abort"]),
        # Scheduled heartbeat, silent unless unhealthy.
        (Level.CHECK, ["scrub_finish"]),
        # Deliberate recovery actions, worth a chat notice on success.
        (Level.RECOVERY_CHECK, ["resilver_finish", "vdev_clear", "vdev_remove"]),
        (Level.OK, ["scrub_start"]),
        # Covers unplugged/failed devices, gated on ZEVENT_VDEV_STATE_STR.
        (Level.STATECHANGE, ["statechange"]),
    ]
    for subclass in subclasses
}


@dataclass(frozen=True)
class Event:
    tag: str
    notify_type: str
    header: str


EVENTS: dict[Level, Event] = {
    Level.ERROR: Event(
        tag="zfs-error",
        notify_type=NotifyType.FAILURE,
        header="🔴 ZFS CRITICAL",
    ),
    Level.WARNING: Event(
        tag="zfs-warning",
        notify_type=NotifyType.WARNING,
        header="🟡 ZFS WARNING",
    ),
    Level.OK: Event(
        tag="zfs-heartbeat",
        notify_type=NotifyType.INFO,
        header="🔵 ZFS HEARTBEAT",
    ),
    Level.INFO: Event(
        tag="zfs-ok",
        notify_type=NotifyType.SUCCESS,
        header="🟢 ZFS HEALTHY",
    ),
}


def rate_limit(pool: str, key: str) -> bool:
    interval = int(os.environ.get("ZED_NOTIFY_INTERVAL_SECS", "3600"))
    lock_file = LOCK_DIR / f"zed-{pool}-{key}"

    try:
        age = time() - lock_file.stat().st_mtime
        if age < interval:
            return False
    except FileNotFoundError:
        pass

    try:
        lock_file.touch()
    except OSError as exc:
        log.warning(f"Could not update rate limit file: {exc}")

    return True


def unhealthy_marker(pool: str) -> Path:
    return LOCK_DIR / f"zed-{pool}-unhealthy"


def mark_unhealthy(pool: str) -> None:
    try:
        unhealthy_marker(pool).touch()
    except OSError as exc:
        log.warning(f"Could not set unhealthy marker: {exc}")


def clear_unhealthy(pool: str) -> bool:
    try:
        unhealthy_marker(pool).unlink()
    except FileNotFoundError:
        return False

    return True


def pool_is_healthy(pool: str) -> bool:
    try:
        result = subprocess.run(
            ["zpool", "status", "-x", pool],
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.CalledProcessError, FileNotFoundError:
        return False
    else:
        return f"'{pool}' is healthy" in result.stdout


def pool_status(pool: str) -> str:
    try:
        return subprocess.check_output(
            ["zpool", "status", pool],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except subprocess.CalledProcessError, FileNotFoundError:
        return ""


def resolve_display_device(device_path: str) -> str:
    try:
        real = Path(device_path).resolve()
        by_id = Path("/dev/disk/by-id")
        for link in sorted(by_id.iterdir()):
            if "-part" in link.name:
                continue

            if link.resolve() == real:
                return link.name
    except OSError:
        pass

    return Path(device_path).name


def resolve_health_level(pool: str, routine_level: Level) -> Level:
    if not pool_is_healthy(pool):
        mark_unhealthy(pool)
        return Level.ERROR

    # A scrub confirming recovery from a known problem is worth a chat notice.
    return Level.INFO if clear_unhealthy(pool) else routine_level


def resolve_level(pool: str, level: Level) -> Level | None:
    if level is Level.CHECK:
        return resolve_health_level(pool, routine_level=Level.OK)

    if level is Level.RECOVERY_CHECK:
        return resolve_health_level(pool, routine_level=Level.INFO)

    if level is Level.STATECHANGE:
        vdev_state = os.environ.get("ZEVENT_VDEV_STATE_STR", "")

        if vdev_state not in BAD_VDEV_STATES:
            log.info(f"statechange on {pool}: ignoring benign state {vdev_state!r}")
            return None

        mark_unhealthy(pool)
        return Level.ERROR

    if level is Level.ERROR:
        mark_unhealthy(pool)

    return level


def main() -> None:
    pool = os.environ.get("ZEVENT_POOL", "")
    subclass = os.environ.get("ZEVENT_SUBCLASS", "")
    vdev_path = os.environ.get("ZEVENT_VDEV_PATH", "")
    time_string = os.environ.get("ZEVENT_TIME_STRING", "")

    if not pool or not subclass:
        log.error("ZEVENT_POOL and ZEVENT_SUBCLASS must be set")
        sys.exit(1)

    raw_level = SUBCLASS_LEVEL.get(subclass)
    if raw_level is None:
        log.info(f"Ignoring unhandled subclass {subclass} on {pool}")
        return

    level = resolve_level(pool, raw_level)
    if level is None:
        return

    log.info(f"{subclass} on {pool}: resolved to {level.name}")

    # Dedupe unrelated subclasses landing on the same outcome for one pool.
    if level is Level.ERROR:
        rate_limit_key = "error"
    elif level is Level.INFO:
        rate_limit_key = "recovery"
    else:
        rate_limit_key = subclass

    if not rate_limit(pool, rate_limit_key):
        log.info(f"Rate limited: {subclass} on {pool} (key={rate_limit_key})")
        return

    event = EVENTS[level]
    log.info(f"{subclass} on {pool}: notifying tag={event.tag}")

    hostname = gethostname()
    display_device = resolve_display_device(vdev_path) if vdev_path else ""
    status_output = pool_status(pool)

    html_device_line = (
        f"<b>Device:</b> {escape(display_device)}\n" if display_device else ""
    )
    html_body = (
        f"<b>{event.header}</b>\n \n"
        f"<b>Host:</b> {escape(hostname)}\n"
        f"<b>Pool:</b> {escape(pool)}\n"
        f"{html_device_line}"
        f"<b>Event:</b> {escape(subclass)}\n"
        f"<b>Time:</b> {escape(time_string)}"
    )

    if status_output:
        html_body += f"\n \n<pre>{escape(status_output.strip())}</pre>"

    md_device_line = f"**Device:** {display_device}\n" if display_device else ""
    md_body = (
        f"**{event.header}**\n"
        f"**Host:** {hostname}\n"
        f"**Pool:** {pool}\n"
        f"{md_device_line}"
        f"**Event:** {subclass}\n"
        f"**Time:** {time_string}"
    )

    if status_output:
        md_body += f"\n```\n{status_output.strip()}\n```"

    ap = Apprise()
    cfg = AppriseConfig()
    cfg.add(APPRISE_CONFIG)
    ap.add(cfg)

    ok_html = ap.notify(
        body=html_body,
        notify_type=event.notify_type,
        body_format=NotifyFormat.HTML,
        tag=f"{event.tag}-html",
    )

    ok_md = ap.notify(
        body=md_body,
        notify_type=event.notify_type,
        body_format=NotifyFormat.TEXT,
        tag=f"{event.tag}-md",
    )

    if not ok_html and not ok_md:
        log.warning(
            f"Notification failed or no URLs configured for {subclass} on {pool}"
        )
    else:
        log.info(f"{subclass} on {pool}: sent (html={ok_html}, md={ok_md})")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Unhandled exception")
        sys.exit(1)
