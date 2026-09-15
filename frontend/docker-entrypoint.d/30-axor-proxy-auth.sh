#!/bin/sh
# Attach the proxy's control-surface credential at the nginx hop.
#
# The SPA reaches the observe-only proxy through this origin (/axor -> :8401).
# That surface arms runs, injects faults into live tool traffic and reads back
# traces, so it can require its own bearer — which is an infrastructure secret
# and has no business in a browser. nginx holds it instead.
#
# Runs from the stock nginx image's /docker-entrypoint.d hook. Writes an
# include file that is either empty (no token configured -> the proxy is open,
# same as before) or one proxy_set_header line. The file always exists, because
# `include` of a missing file is a startup failure.
set -eu

CONF=/etc/nginx/axor-proxy-auth.conf

if [ -z "${AXOR_PROXY_TOKEN:-}" ]; then
    : > "$CONF"
    echo "$0: AXOR_PROXY_TOKEN unset - /axor forwarded unauthenticated"
    exit 0
fi

# Allowed: what a token realistically is — the backend's own key format
# (ak_xxxx.<urlsafe>, so the dot matters), base64, a UUID. Refused: anything
# that changes the MEANING of the nginx string we are about to write. `$` is
# variable interpolation, `"` and `\` end or escape the string, and whitespace
# or `;` would start a new directive. That is config injection, not a typo to
# paper over, so it aborts the container instead of quietly falling back to
# forwarding unauthenticated — which is the failure the token exists to prevent.
case "$AXOR_PROXY_TOKEN" in
    *[!A-Za-z0-9._~+/=:-]*)
        echo "$0: AXOR_PROXY_TOKEN contains a character outside" \
             "[A-Za-z0-9._~+/=:-] — refusing to build an nginx directive" \
             "from it. Refusing loudly rather than forwarding /axor" \
             "unauthenticated." >&2
        exit 1
        ;;
esac

printf 'proxy_set_header Authorization "Bearer %s";\n' "$AXOR_PROXY_TOKEN" > "$CONF"
echo "$0: /axor forwarded with the configured proxy token"
