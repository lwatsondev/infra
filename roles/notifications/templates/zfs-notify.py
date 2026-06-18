#!/usr/bin/env -S uv run --script

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
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import apprise

APPRISE_CONFIG = "/etc/apprise/apprise.yml"
LOCK_DIR = Path("/var/lock")

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
log = logging.getLogger("zfs-notify")

CRITICAL_SUBCLASSES = frozenset(
    {
        "io_failure",
        "data",
        "vdev_remove",
        "vdev_fault",
        "vdev_degraded",
    }
)

WARNING_SUBCLASSES = frozenset(
    {
        "checksum",
        "scrub_abort",
    }
)

RECOVERY_SUBCLASSES = frozenset(
    {
        "scrub_finish",
        "resilver_finish",
        "vdev_clear",
    }
)


@dataclass(frozen=True)
class Event:
    tag: str
    notify_type: str
    header: str


EVENTS: dict[str, Event] = {
    "error": Event(
        tag="zfs zfs-error",
        notify_type=apprise.NotifyType.FAILURE,
        header="🔴 ZFS CRITICAL",
    ),
    "warning": Event(
        tag="zfs zfs-warning",
        notify_type=apprise.NotifyType.WARNING,
        header="🟡 ZFS WARNING",
    ),
    "ok": Event(
        tag="zfs zfs-ok",
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

    if subclass in CRITICAL_SUBCLASSES:
        level = "error"
    elif subclass in WARNING_SUBCLASSES:
        level = "warning"
    elif subclass in RECOVERY_SUBCLASSES:
        level = "ok" if pool_is_healthy(pool) else "error"
    else:
        sys.exit(0)

    if not rate_limit(pool, subclass):
        log.info(f"Rate limited: {subclass} on {pool}")
        sys.exit(0)

    event = EVENTS[level]
    hostname = html.escape(socket.gethostname())
    pool_esc = html.escape(pool)
    subclass_esc = html.escape(subclass)
    time_esc = html.escape(time_string)

    device_line = ""
    if vdev_path:
        display_device = html.escape(resolve_display_device(vdev_path))
        device_line = f"<b>Device:</b> {display_device}\n"

    status_output = pool_status(pool)

    body = (
        f"<b>{event.header}</b>\n \n"
        f"<b>Host:</b> {hostname}\n"
        f"<b>Pool:</b> {pool_esc}\n"
        f"{device_line}"
        f"<b>Event:</b> {subclass_esc}\n"
        f"<b>Time:</b> {time_esc}"
    )

    if status_output:
        body += f"\n \n<pre>{html.escape(status_output)}</pre>"

    ap = apprise.Apprise()
    cfg = apprise.AppriseConfig()
    cfg.add(APPRISE_CONFIG)
    ap.add(cfg)

    ok = ap.notify(
        body=body,
        notify_type=event.notify_type,
        body_format=apprise.NotifyFormat.HTML,
        tag=event.tag,
    )

    if not ok:
        raise RuntimeError(f"Notification failed for {subclass} on {pool}")

    sys.exit(0)


if __name__ == "__main__":
    main()
