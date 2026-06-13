#!/usr/bin/sh

#
# {{ ansible_managed }}
#

set -eu

error=$(printf '%s' "$*" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g')
printf '🔴 borgmatic error on %s\n\n<pre>%s</pre>' "$(hostname)" "$error" \
    | curl --silent --show-error --fail \
        --max-time 5 --retry 3 --retry-delay 3 \
        --request POST \
        "https://api.telegram.org/bot{{ _borgmatic_telegram_bot_token }}/sendMessage" \
        --data-urlencode "chat_id={{ _borgmatic_telegram_chat_id }}" \
        --data-urlencode "parse_mode=HTML" \
        --data-urlencode "text@-"
