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

from apprise import Apprise, AppriseConfig, NotifyFormat, NotifyType

APPRISE_CONFIG = "/etc/apprise/apprise.yml"

_syslog_handler = SysLogHandler(address="/dev/log")
_syslog_handler.setFormatter(Formatter("%(name)s: %(message)s"))
basicConfig(handlers=[_syslog_handler], level=INFO)
log = getLogger("smartd-notify")


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
        notify_type=NotifyType.FAILURE,
        header="🔴 S.M.A.R.T. CRITICAL",
    ),
    Level.WARNING: Event(
        tag="smartd-warning",
        notify_type=NotifyType.WARNING,
        header="🟡 S.M.A.R.T. WARNING",
    ),
    Level.TEST: Event(
        tag="smartd-test",
        notify_type=NotifyType.INFO,
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
    hostname = gethostname()
    display_device = resolve_display_device(device)

    try:
        attrs = subprocess.check_output(
            ["smartctl", "-A", device],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except subprocess.CalledProcessError, FileNotFoundError:
        attrs = ""

    html_body = (
        f"<b>{event.header}</b>\n \n"
        f"<b>Host:</b> {escape(hostname)}\n"
        f"<b>Device:</b> {escape(display_device)}\n"
        f"<b>Failure:</b> {escape(failtype)}\n"
        f"<b>Summary:</b> {escape(message)}"
    )

    if attrs:
        html_body += f"\n \n<pre>{escape(attrs.strip())}</pre>"

    md_body = (
        f"**{event.header}**\n"
        f"**Host:** {hostname}\n"
        f"**Device:** {display_device}\n"
        f"**Failure:** {failtype}\n"
        f"**Summary:** {message}"
    )

    if attrs:
        md_body += f"\n```\n{attrs.strip()}\n```"

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
            f"Notification failed or no URLs configured for {failtype} on {display_device}"
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Unhandled exception")
        sys.exit(1)
