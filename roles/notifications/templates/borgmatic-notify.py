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

import sys
from dataclasses import dataclass
from html import escape
from logging import INFO, Formatter, basicConfig, getLogger
from logging.handlers import SysLogHandler
from socket import gethostname

from apprise import Apprise, AppriseConfig, NotifyFormat, NotifyType

APPRISE_CONFIG = "/etc/apprise/apprise.yml"

_syslog_handler = SysLogHandler(address="/dev/log")
_syslog_handler.setFormatter(Formatter("%(name)s: %(message)s"))
basicConfig(handlers=[_syslog_handler], level=INFO)
log = getLogger("borgmatic-notify")


@dataclass(frozen=True)
class Event:
    tag: str
    notify_type: str
    header: str


EVENTS: dict[str, Event] = {
    "start": Event(
        tag="borgmatic-start",
        notify_type=NotifyType.INFO,
        header="🔵 <b>borgmatic</b>: backup started",
    ),
    "finish": Event(
        tag="borgmatic-ok",
        notify_type=NotifyType.SUCCESS,
        header="🟢 <b>borgmatic</b>: backup finished",
    ),
    "error": Event(
        tag="borgmatic-error",
        notify_type=NotifyType.FAILURE,
        header="🔴 <b>borgmatic</b>: backup failed",
    ),
}


def build_html_body(
    event_name: str, *, header: str, hostname: str, error_args: list[str]
) -> str:
    match event_name:
        case "error":
            error_text = escape(" ".join(error_args))
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
    hostname = gethostname()

    if event is None:
        if event_name:
            log.error(f"Unknown event {event_name!r}, expected: start, finish, error")
        else:
            log.error("No event passed, expected: start, finish, error")
        sys.exit(1)

    ap = Apprise()
    cfg = AppriseConfig()
    cfg.add(APPRISE_CONFIG)
    ap.add(cfg)

    ok_html = ap.notify(
        body=build_html_body(
            event_name,
            header=event.header,
            hostname=escape(hostname),
            error_args=sys.argv[2:],
        ),
        notify_type=event.notify_type,
        body_format=NotifyFormat.HTML,
        tag=f"{event.tag}-html",
    )

    ok_md = ap.notify(
        body=build_md_body(
            event_name, header=event.header, hostname=hostname, error_args=sys.argv[2:]
        ),
        notify_type=event.notify_type,
        body_format=NotifyFormat.TEXT,
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
