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
from dataclasses import dataclass
from pathlib import Path

import apprise

APPRISE_CONFIG = "/etc/apprise/apprise.yml"

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
log = logging.getLogger("smartd-notify")

CRITICAL_FAIL_TYPES = frozenset(
    {
        "Health",
        "Usage",
        "SelfTest",
        "CurrentPendingSector",
        "OfflineUncorrectableSector",
        "FailedHealthCheck",
        "FailedOpenDevice",
    }
)
WARNING_FAIL_TYPES = frozenset(
    {
        "ErrorCount",
        "Temperature",
        "FailedReadSmartData",
        "FailedReadSmartErrorLog",
        "FailedReadSmartSelfTestLog",
    }
)


@dataclass(frozen=True)
class Event:
    tag: str
    notify_type: str
    header: str


EVENTS: dict[str, Event] = {
    "error": Event(
        tag="smartd smartd-error",
        notify_type=apprise.NotifyType.FAILURE,
        header="🔴 S.M.A.R.T. CRITICAL",
    ),
    "warning": Event(
        tag="smartd smartd-warning",
        notify_type=apprise.NotifyType.WARNING,
        header="🟡 S.M.A.R.T. WARNING",
    ),
    "test": Event(
        tag="smartd smartd-test",
        notify_type=apprise.NotifyType.INFO,
        header="🔵 S.M.A.R.T. TEST",
    ),
}


def classify_failtype(failtype: str) -> str | None:
    if failtype in CRITICAL_FAIL_TYPES:
        return "error"
    if failtype in WARNING_FAIL_TYPES:
        return "warning"
    if failtype == "EmailTest":
        return "test"

    return None


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
        sys.exit(0)

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

    sys.exit(0)


if __name__ == "__main__":
    main()
