#!/bin/sh

#
# Ping an Uptime Kuma push monitor on various ZFS events.
#

# shellcheck disable=SC1091
[ -f "${ZED_ZEDLET_DIR}/zed.rc" ] && . "${ZED_ZEDLET_DIR}/zed.rc"

# shellcheck disable=SC1091
. "${ZED_ZEDLET_DIR}/zed-functions.sh"

[ -n "${ZED_UPTIME_KUMA_PUSH_URL}" ] || exit 2

[ -n "${ZEVENT_POOL}" ] || exit 9
[ -n "${ZEVENT_SUBCLASS}" ] || exit 9

zed_check_cmd "${ZPOOL}" "curl" || exit 9

case "${ZEVENT_SUBCLASS}" in
    checksum | io | delay | deadman | data | io_failure)
        push_status="down"
        rate_limit_tag="${ZEVENT_POOL};${ZEVENT_SUBCLASS};uptime-kuma-notify"
        zed_rate_limit "${rate_limit_tag}" || exit 3
        ;;
    scrub_finish | resilver_finish | vdev_clear)
        if "${ZPOOL}" status -x "${ZEVENT_POOL}" | grep -q "'${ZEVENT_POOL}' is healthy"; then
            push_status="up"
        else
            push_status="down"
        fi
        ;;
    *)
        exit 3
        ;;
esac

curl --silent --output /dev/null --show-error --fail \
    --max-time 5 --retry 3 --retry-delay 3 \
    --url-query "status=${push_status}" \
    "${ZED_UPTIME_KUMA_PUSH_URL}"

rc=$?
[ "${rc}" -ne 0 ] && zed_log_err "Uptime Kuma push failed (curl exit=${rc})"
exit "${rc}"
