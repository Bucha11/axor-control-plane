#!/bin/sh
# First start of a self-hosted Control Plane. Writes .env with every secret
# generated, pulls the images and starts the stack. Safe to re-run: an existing
# .env is never overwritten.
#
#   ./bootstrap.sh axor.example.com you@example.com
#
# Signup mode defaults to first-only — the first account claims the install.
# Override with SIGNUP=closed ./bootstrap.sh …
set -eu
cd "$(dirname "$0")"

HOST=${1:-}
EMAIL=${2:-}
if [ -z "$HOST" ] || [ -z "$EMAIL" ]; then
    echo "usage: $0 <hostname> <acme-email>" >&2
    echo "   e.g. $0 axor.example.com ops@example.com" >&2
    exit 2
fi

if [ "$(uname -m)" != "x86_64" ]; then
    echo "error: $(uname -m) host — the published images are amd64 only" >&2
    exit 1
fi
command -v docker >/dev/null || {
    echo "error: docker is not installed (curl -fsSL https://get.docker.com | sh)" >&2
    exit 1
}

if [ -e .env ]; then
    echo ".env exists — keeping it (delete it to regenerate secrets)"
else
    umask 077
    secret() { openssl rand -hex 24; }
    IMAGE="${AXOR_IMAGE:-ghcr.io/bucha11/axor-platform}:${AXOR_TAG:-0.4.0}"
    echo "generating the identity signing key…"
    PEM=$(docker run --rm --entrypoint python "$IMAGE" \
        -c "from axor_identity.keys import generate_pem; print(generate_pem())")
    sed \
        -e "s|^CP_HOST=.*|CP_HOST=$HOST|" \
        -e "s|^ACME_EMAIL=.*|ACME_EMAIL=$EMAIL|" \
        -e "s|^AXOR_IDENTITY_SIGNUP=.*|AXOR_IDENTITY_SIGNUP=${SIGNUP:-first-only}|" \
        -e "s|^AXOR_PG_PASSWORD=.*|AXOR_PG_PASSWORD=$(secret)|" \
        -e "s|^AXOR_API_TOKEN=.*|AXOR_API_TOKEN=$(secret)|" \
        -e "s|^AXOR_PROXY_TOKEN=.*|AXOR_PROXY_TOKEN=$(secret)|" \
        -e "/^AXOR_IDENTITY_SIGNING_KEY=/d" \
        .env.example > .env
    printf 'AXOR_IDENTITY_SIGNING_KEY="%s"\n' "$PEM" >> .env
    chmod 600 .env
    echo "wrote .env (chmod 600)"
fi

docker compose pull
docker compose up -d
docker compose ps

set -a; . ./.env; set +a
cat <<TXT

Started. The certificate is issued on the first request, so give it a minute:

  https://$CP_HOST

Signup is '$AXOR_IDENTITY_SIGNUP'. Register the first account now — it becomes
the owner of this installation, and in first-only mode nobody else can.

Operator master token (also in $(pwd)/.env — keep that file private):
  AXOR_API_TOKEN=$AXOR_API_TOKEN

It is NOT how people log in; it is yours, for Settings -> auth, to mint the
proxy's ingest key.
TXT
