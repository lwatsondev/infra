#!/usr/bin/sh

#
# {{ ansible_managed }}
#

#
# Send a Telegram message on various ZFS events.
#

# shellcheck disable=SC1091
[ -f "${ZED_ZEDLET_DIR}/zed.rc" ] && . "${ZED_ZEDLET_DIR}/zed.rc"

# shellcheck disable=SC1091
. "${ZED_ZEDLET_DIR}/zed-functions.sh"

[ -n "${ZED_TELEGRAM_BOT_TOKEN}" ] || exit 2
[ -n "${ZED_TELEGRAM_CHAT_ID}" ] || exit 2

[ -n "${ZEVENT_POOL}" ] || exit 9
[ -n "${ZEVENT_SUBCLASS}" ] || exit 9

zed_check_cmd "${ZPOOL}" "curl" "jq" || exit 9

case "${ZEVENT_SUBCLASS}" in
    io_failure | data | vdev_remove | vdev_fault | vdev_degraded)
        emoji="🔴"
        severity="CRITICAL"
        ;;
    checksum | scrub_abort)
        emoji="⚠️"
        severity="WARNING"
        ;;
    scrub_finish | resilver_finish | vdev_clear)
        "${ZPOOL}" status -x "${ZEVENT_POOL}" | grep -q "'${ZEVENT_POOL}' is healthy" && exit 0
        emoji="🔴"
        severity="CRITICAL"
        ;;
    *) exit 3 ;;
esac

zed_rate_limit "telegram-${ZEVENT_POOL}-${ZEVENT_SUBCLASS}" || exit 3

if [ -n "${ZEVENT_VDEV_PATH}" ]; then
    display_device=$(find /dev/disk/by-id/ -maxdepth 1 ! -name '*-part*' -type l | sort | while IFS= read -r _link; do [ "$(readlink -f "$_link")" = "$(readlink -f "${ZEVENT_VDEV_PATH}")" ] && { basename "$_link"; break; }; done)
    display_device="${display_device:-$(basename "${ZEVENT_VDEV_PATH}")}"
    device_line="<b>Device:</b> ${display_device}
"
else
    device_line=""
fi

zpool_status="$("${ZPOOL}" status "${ZEVENT_POOL}" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')"
message="${emoji} <b>ZFS ${severity}</b>

<b>Host:</b> $(hostname -s)
<b>Pool:</b> ${ZEVENT_POOL}
${device_line}<b>Event:</b> ${ZEVENT_SUBCLASS}
<b>Time:</b> ${ZEVENT_TIME_STRING}

<pre>${zpool_status}</pre>"

response=$(curl \
    --silent \
    --max-time 15 \
    --retry 3 \
    --retry-delay 5 \
    --request POST \
    --data-urlencode "chat_id=${ZED_TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${message}" \
    --data-urlencode "parse_mode=HTML" \
    "https://api.telegram.org/bot${ZED_TELEGRAM_BOT_TOKEN}/sendMessage")

if printf '%s\n' "${response}" | jq -e '.ok' > /dev/null 2>&1; then
    zed_log_msg "Telegram alert sent for ${ZEVENT_SUBCLASS} on ${ZEVENT_POOL}"
else
    zed_log_err "Telegram API error: $(printf '%s\n' "${response}" | jq -r '.description // "unknown error"')"
    exit 1
fi
