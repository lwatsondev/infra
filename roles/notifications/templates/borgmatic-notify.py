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
import socket
import sys
from dataclasses import dataclass

import apprise

APPRISE_CONFIG = "/etc/apprise/apprise.yml"

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
log = logging.getLogger("borgmatic-notify")


@dataclass(frozen=True)
class Event:
    tag: str
    notify_type: str
    header: str


EVENTS: dict[str, Event] = {
    "start": Event(
        tag="borgmatic-start",
        notify_type=apprise.NotifyType.INFO,
        header="🔵 <b>borgmatic</b>: backup started",
    ),
    "finish": Event(
        tag="borgmatic-ok",
        notify_type=apprise.NotifyType.SUCCESS,
        header="🟢 <b>borgmatic</b>: backup finished",
    ),
    "error": Event(
        tag="borgmatic-error",
        notify_type=apprise.NotifyType.FAILURE,
        header="🔴 <b>borgmatic</b>: backup failed",
    ),
}


def build_body(
    event_name: str, *, header: str, hostname: str, error_args: list[str]
) -> str:
    match event_name:
        case "error":
            error_text = html.escape(" ".join(error_args))
            return f"{header} on <b>{hostname}</b>\n \n<pre>{error_text}</pre>"
        case _:
            return f"{header} on <b>{hostname}</b>"


def main() -> None:
    event_name = sys.argv[1] if len(sys.argv) > 1 else ""
    event = EVENTS.get(event_name)
    hostname = html.escape(socket.gethostname())

    if event is None:
        log.error("Usage: start|finish|error [error text]")
        sys.exit(1)

    ap = apprise.Apprise()
    cfg = apprise.AppriseConfig()
    cfg.add(APPRISE_CONFIG)
    ap.add(cfg)

    ok = ap.notify(
        body=build_body(
            event_name, header=event.header, hostname=hostname, error_args=sys.argv[2:]
        ),
        notify_type=event.notify_type,
        body_format=apprise.NotifyFormat.HTML,
        tag=event.tag,
    )

    if not ok:
        raise RuntimeError(f"Notification failed: {event_name}")

    sys.exit(0)


if __name__ == "__main__":
    main()
