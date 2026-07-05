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
import socket
import sys
from dataclasses import dataclass

import apprise

APPRISE_CONFIG = "/etc/apprise/apprise.yml"

_syslog_handler = logging.handlers.SysLogHandler(address="/dev/log")
_syslog_handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
logging.basicConfig(handlers=[_syslog_handler], level=logging.INFO)
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


def build_html_body(
    event_name: str, *, header: str, hostname: str, error_args: list[str]
) -> str:
    match event_name:
        case "error":
            error_text = html.escape(" ".join(error_args))
            return f"{header} on <b>{hostname}</b>\n \n<pre>{error_text}</pre>"
        case _:
            return f"{header} on <b>{hostname}</b>"


def build_md_body(
    event_name: str, *, header: str, hostname: str, error_args: list[str]
) -> str:
    md_header = header.replace("<b>", "**").replace("</b>", "**")
    match event_name:
        case "error":
            error_text = " ".join(error_args)
            return f"{md_header} on **{hostname}**\n```\n{error_text}\n```"
        case _:
            return f"{md_header} on **{hostname}**"


def main() -> None:
    event_name = sys.argv[1] if len(sys.argv) > 1 else ""
    event = EVENTS.get(event_name)
    hostname = socket.gethostname()

    if event is None:
        if event_name:
            log.error(f"Unknown event {event_name!r}, expected: start, finish, error")
        else:
            log.error("No event passed, expected: start, finish, error")
        sys.exit(1)

    ap = apprise.Apprise()
    cfg = apprise.AppriseConfig()
    cfg.add(APPRISE_CONFIG)
    ap.add(cfg)

    ok_html = ap.notify(
        body=build_html_body(
            event_name,
            header=event.header,
            hostname=html.escape(hostname),
            error_args=sys.argv[2:],
        ),
        notify_type=event.notify_type,
        body_format=apprise.NotifyFormat.HTML,
        tag=f"{event.tag}-html",
    )

    ok_md = ap.notify(
        body=build_md_body(
            event_name, header=event.header, hostname=hostname, error_args=sys.argv[2:]
        ),
        notify_type=event.notify_type,
        body_format=apprise.NotifyFormat.TEXT,
        tag=f"{event.tag}-md",
    )

    if not ok_html and not ok_md:
        log.error(f"Notification failed: {event_name}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Unhandled exception")
        sys.exit(1)
