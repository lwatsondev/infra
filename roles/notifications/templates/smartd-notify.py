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
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import apprise

APPRISE_CONFIG = "/etc/apprise/apprise.yml"

_syslog_handler = logging.handlers.SysLogHandler(address="/dev/log")
_syslog_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
logging.basicConfig(handlers=[_syslog_handler], level=logging.INFO)
log = logging.getLogger("smartd-notify")


class Level(Enum):
    ERROR = auto()
    WARNING = auto()
    TEST = auto()


FAILTYPE_LEVEL: dict[str, Level] = {
    failtype: level
    for level, failtypes in [
        (
            Level.ERROR,
            [
                "Health",
                "Usage",
                "SelfTest",
                "CurrentPendingSector",
                "OfflineUncorrectableSector",
                "FailedHealthCheck",
                "FailedOpenDevice",
            ],
        ),
        (
            Level.WARNING,
            [
                "ErrorCount",
                "Temperature",
                "FailedReadSmartData",
                "FailedReadSmartErrorLog",
                "FailedReadSmartSelfTestLog",
            ],
        ),
        (Level.TEST, ["EmailTest"]),
    ]
    for failtype in failtypes
}


@dataclass(frozen=True)
class Event:
    tag: str
    notify_type: str
    header: str


EVENTS: dict[Level, Event] = {
    Level.ERROR: Event(
        tag="smartd-error",
        notify_type=apprise.NotifyType.FAILURE,
        header="🔴 S.M.A.R.T. CRITICAL",
    ),
    Level.WARNING: Event(
        tag="smartd-warning",
        notify_type=apprise.NotifyType.WARNING,
        header="🟡 S.M.A.R.T. WARNING",
    ),
    Level.TEST: Event(
        tag="smartd-test",
        notify_type=apprise.NotifyType.INFO,
        header="🔵 S.M.A.R.T. TEST",
    ),
}


def classify_failtype(failtype: str) -> Level | None:
    return FAILTYPE_LEVEL.get(failtype)


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
    device = os.environ.get("SMARTD_DEVICE", "")
    failtype = os.environ.get("SMARTD_FAILTYPE", "")
    message = os.environ.get("SMARTD_MESSAGE", "")

    if not device or not failtype:
        log.error("SMARTD_DEVICE and SMARTD_FAILTYPE must be set")
        sys.exit(1)

    level = classify_failtype(failtype)
    if level is None:
        return

    event = EVENTS[level]
    hostname = html.escape(socket.gethostname())
    display_device = html.escape(resolve_display_device(device))

    try:
        attrs = subprocess.check_output(
            ["smartctl", "-A", device],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except subprocess.CalledProcessError, FileNotFoundError:
        attrs = ""

    body = (
        f"<b>{event.header}</b>\n \n"  # The telegram endpoint replaces consecutive newlines with a single newline, so we add a space to preserve the blank line.
        f"<b>Host:</b> {hostname}\n"
        f"<b>Device:</b> {display_device}\n"
        f"<b>Failure:</b> {html.escape(failtype)}\n"
        f"<b>Summary:</b> {html.escape(message)}"
    )

    if attrs:
        body += f"\n \n<pre>{html.escape(attrs)}</pre>"

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
        raise RuntimeError(f"Notification failed for {failtype} on {display_device}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Unhandled exception")
        sys.exit(1)
