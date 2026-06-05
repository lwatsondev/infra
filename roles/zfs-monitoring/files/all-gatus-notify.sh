#!/bin/sh

#
# Ping a Gatus external endpoint on various ZFS events.
#

# shellcheck disable=SC1091
[ -f "${ZED_ZEDLET_DIR}/zed.rc" ] && . "${ZED_ZEDLET_DIR}/zed.rc"

# shellcheck disable=SC1091
. "${ZED_ZEDLET_DIR}/zed-functions.sh"

[ -n "${ZED_GATUS_BASE_URL}" ] || exit 2
[ -n "${ZED_GATUS_ENDPOINT_KEY}" ] || exit 2
[ -n "${ZED_GATUS_AUTH_TOKEN}" ] || exit 2

[ -n "${ZEVENT_POOL}" ] || exit 9
[ -n "${ZEVENT_SUBCLASS}" ] || exit 9

zed_check_cmd "${ZPOOL}" "curl" || exit 9

case "${ZEVENT_SUBCLASS}" in
    checksum | io | delay | deadman | data | io_failure)
        push_success="false"
        rate_limit_tag="${ZEVENT_POOL};${ZEVENT_SUBCLASS};gatus-notify"
        zed_rate_limit "${rate_limit_tag}" || exit 3
        ;;
    scrub_finish | resilver_finish | vdev_clear)
        if "${ZPOOL}" status -x "${ZEVENT_POOL}" | grep -q "'${ZEVENT_POOL}' is healthy"; then
            push_success="true"
        else
            push_success="false"
        fi
        ;;
    *)
        exit 3
        ;;
esac

gatus_url="${ZED_GATUS_BASE_URL%/}/api/v1/endpoints/${ZED_GATUS_ENDPOINT_KEY}/external"
curl --silent --output /dev/null --show-error --fail \
    --max-time 5 --retry 3 --retry-delay 3 \
    --request POST \
    --header "Authorization: Bearer ${ZED_GATUS_AUTH_TOKEN}" \
    --url-query "success=${push_success}" \
    "${gatus_url}"

rc=$?
if [ "${rc}" -eq 0 ]; then
    zed_log_msg "Gatus push sent (success=${push_success}) for ${ZEVENT_SUBCLASS} on ${ZEVENT_POOL}"
else
    zed_log_err "Gatus push failed (curl exit=${rc})"
fi
exit "${rc}"
