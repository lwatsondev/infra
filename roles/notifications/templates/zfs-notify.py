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

import html
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import apprise

APPRISE_CONFIG = "/etc/apprise/apprise.yml"
LOCK_DIR = Path("/var/lock")

_syslog_handler = logging.handlers.SysLogHandler(address="/dev/log")
_syslog_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
logging.basicConfig(handlers=[_syslog_handler], level=logging.INFO)
log = logging.getLogger("zfs-notify")


class Level(Enum):
    ERROR = auto()
    WARNING = auto()
    OK = auto()
    CHECK = auto()


SUBCLASS_LEVEL: dict[str, Level] = {
    subclass: level
    for level, subclasses in [
        (
            Level.ERROR,
            ["io_failure", "data", "vdev_remove", "vdev_fault", "vdev_degraded"],
        ),
        (Level.WARNING, ["checksum", "scrub_abort"]),
        (Level.CHECK, ["scrub_finish", "resilver_finish", "vdev_clear"]),
        (Level.OK, ["scrub_start"]),
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
        notify_type=apprise.NotifyType.FAILURE,
        header="🔴 ZFS CRITICAL",
    ),
    Level.WARNING: Event(
        tag="zfs-warning",
        notify_type=apprise.NotifyType.WARNING,
        header="🟡 ZFS WARNING",
    ),
    Level.OK: Event(
        tag="zfs-ok",
        notify_type=apprise.NotifyType.SUCCESS,
        header="🟢 ZFS HEALTHY",
    ),
}


def rate_limit(pool: str, subclass: str) -> bool:
    interval = int(os.environ.get("ZED_NOTIFY_INTERVAL_SECS", "3600"))
    lock_file = LOCK_DIR / f"zed-{pool}-{subclass}"

    try:
        age = time.time() - lock_file.stat().st_mtime
        if age < interval:
            return False
    except FileNotFoundError:
        pass

    try:
        lock_file.touch()
    except OSError as exc:
        log.warning(f"Could not update rate limit file: {exc}")
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


def main() -> None:
    pool = os.environ.get("ZEVENT_POOL", "")
    subclass = os.environ.get("ZEVENT_SUBCLASS", "")
    vdev_path = os.environ.get("ZEVENT_VDEV_PATH", "")
    time_string = os.environ.get("ZEVENT_TIME_STRING", "")

    if not pool or not subclass:
        log.error("ZEVENT_POOL and ZEVENT_SUBCLASS must be set")
        sys.exit(1)

    level = SUBCLASS_LEVEL.get(subclass)
    if level is None:
        return

    if level is Level.CHECK:
        level = Level.OK if pool_is_healthy(pool) else Level.ERROR

    if not rate_limit(pool, subclass):
        log.info(f"Rate limited: {subclass} on {pool}")
        return

    event = EVENTS[level]
    hostname = socket.gethostname()
    display_device = resolve_display_device(vdev_path) if vdev_path else ""
    status_output = pool_status(pool)

    html_device_line = (
        f"<b>Device:</b> {html.escape(display_device)}\n" if display_device else ""
    )
    html_body = (
        f"<b>{event.header}</b>\n \n"
        f"<b>Host:</b> {html.escape(hostname)}\n"
        f"<b>Pool:</b> {html.escape(pool)}\n"
        f"{html_device_line}"
        f"<b>Event:</b> {html.escape(subclass)}\n"
        f"<b>Time:</b> {html.escape(time_string)}"
    )

    if status_output:
        html_body += f"\n \n<pre>{html.escape(status_output.strip())}</pre>"

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

    ap = apprise.Apprise()
    cfg = apprise.AppriseConfig()
    cfg.add(APPRISE_CONFIG)
    ap.add(cfg)

    ok_html = ap.notify(
        body=html_body,
        notify_type=event.notify_type,
        body_format=apprise.NotifyFormat.HTML,
        tag=f"{event.tag}-html",
    )

    ok_md = ap.notify(
        body=md_body,
        notify_type=event.notify_type,
        body_format=apprise.NotifyFormat.TEXT,
        tag=f"{event.tag}-md",
    )

    if not ok_html and not ok_md:
        log.warning(
            f"Notification failed or no URLs configured for {subclass} on {pool}"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Unhandled exception")
        sys.exit(1)
