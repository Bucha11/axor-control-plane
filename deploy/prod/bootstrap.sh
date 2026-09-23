#!/bin/sh
# First start on a fresh server: write .env with generated secrets, then pull
# and start the stack. Safe to re-run: an existing .env is never overwritten.
#
#   cd /opt/axor/deploy/prod
#   ./bootstrap.sh you@example.com            # ACME e-mail (Let's Encrypt)
#
# Hosts default to plane.useaxor.net / lab.useaxor.net; override with
# CP_HOST=… LAB_HOST=… ./bootstrap.sh you@example.com
set -eu
cd "$(dirname "$0")"

EMAIL=${1:-}
if [ -z "$EMAIL" ]; then
    echo "usage: $0 <acme-email>" >&2
    exit 2
fi

if [ "$(uname -m)" != "x86_64" ]; then
    echo "error: $(uname -m) host — the GHCR images are amd64 only" >&2
    exit 1
fi
command -v docker >/dev/null || { echo "error: docker is not installed (curl -fsSL https://get.docker.com | sh)" >&2; exit 1; }

if [ -e .env ]; then
    echo ".env exists — keeping it (delete it to regenerate secrets)"
else
    umask 077
    secret() { openssl rand -hex 24; }
    echo "generating the identity signing key…"
    PEM=$(docker run --rm --entrypoint python "${AXOR_IMAGE:-ghcr.io/bucha11/axor-platform}:${AXOR_TAG:-latest}" \
        -c "from axor_identity.keys import generate_pem; print(generate_pem())")
    sed \
        -e "s|^CP_HOST=.*|CP_HOST=${CP_HOST:-plane.useaxor.net}|" \
        -e "s|^LAB_HOST=.*|LAB_HOST=${LAB_HOST:-lab.useaxor.net}|" \
        -e "s|^ACME_EMAIL=.*|ACME_EMAIL=$EMAIL|" \
        -e "s|^AXOR_PG_PASSWORD=.*|AXOR_PG_PASSWORD=$(secret)|" \
        -e "s|^AXOR_API_TOKEN=.*|AXOR_API_TOKEN=$(secret)|" \
        -e "s|^AXOR_PROXY_TOKEN=.*|AXOR_PROXY_TOKEN=$(secret)|" \
        -e "s|^AXOR_LAB_CONTROL_TOKEN=.*|AXOR_LAB_CONTROL_TOKEN=$(secret)|" \
        -e "s|^AXOR_LAB_ADMIN_TOKEN=.*|AXOR_LAB_ADMIN_TOKEN=$(secret)|" \
        -e "s|^AXOR_IDENTITY_ADMIN_TOKEN=.*|AXOR_IDENTITY_ADMIN_TOKEN=$(secret)|" \
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
cat <<EOF

Started. Certificates are issued on the first request to each host, so give
it a minute, then open:
  https://$CP_HOST   — paste AXOR_API_TOKEN in Settings → auth
  https://$LAB_HOST

Tokens (also in $(pwd)/.env — keep that file private):
  AXOR_API_TOKEN=$AXOR_API_TOKEN
  AXOR_LAB_CONTROL_TOKEN=$AXOR_LAB_CONTROL_TOKEN
EOF
