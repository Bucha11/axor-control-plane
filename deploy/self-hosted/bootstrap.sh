#!/bin/sh
# First start of a self-hosted Control Plane. Writes .env with every secret
# generated, pulls the images and starts the stack. Safe to re-run: an existing
# .env is never overwritten.
#
#   ./bootstrap.sh axor.example.com you@example.com
#   ./bootstrap.sh axor.example.com you@example.com --with-lab [lab.example.com]
#
# --with-lab adds Axor Lab on its own hostname (default lab.<hostname>), signed
# into with the same accounts. Re-running with it on an existing install adds
# Lab without touching anything already generated.
#
# Signup mode defaults to first-only — the first account claims the install.
# Override with SIGNUP=closed ./bootstrap.sh …
set -eu
cd "$(dirname "$0")"

HOST=${1:-}
EMAIL=${2:-}
WITH_LAB=""
LAB_HOST_ARG=""
if [ "${3:-}" = "--with-lab" ]; then
    WITH_LAB=1
    LAB_HOST_ARG=${4:-lab.$HOST}
elif [ -n "${3:-}" ]; then
    echo "unknown option: $3 (did you mean --with-lab?)" >&2
    exit 2
fi
if [ -z "$HOST" ] || [ -z "$EMAIL" ]; then
    echo "usage: $0 <hostname> <acme-email> [--with-lab [lab-hostname]]" >&2
    echo "   e.g. $0 axor.example.com ops@example.com --with-lab" >&2
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

if [ -n "$WITH_LAB" ]; then
    # Only fill what is empty, so a re-run never rotates a token in use.
    setvar() {  # setvar NAME VALUE — set NAME in .env if it is empty or absent
        if grep -qE "^$1=.+" .env; then return 0; fi
        if grep -qE "^$1=" .env; then
            sed -i "s|^$1=.*|$1=$2|" .env
        else
            printf '%s=%s\n' "$1" "$2" >> .env
        fi
    }
    setvar COMPOSE_PROFILES lab
    setvar LAB_HOST "$LAB_HOST_ARG"
    setvar AXOR_LAB_CONTROL_TOKEN "$(openssl rand -hex 24)"
    setvar LAB_TAG edge
    mkdir -p sites
    cp lab.caddy.template sites/lab.caddy
    echo "Lab enabled on $(grep -E '^LAB_HOST=' .env | cut -d= -f2-)"
fi

docker compose pull

if grep -qE '^COMPOSE_PROFILES=.*lab' .env; then
    # postgres creates Lab's database only on a fresh volume; an install that
    # turns Lab on later needs it created here, once.
    docker compose up -d postgres
    i=0
    until docker compose exec -T postgres pg_isready -U axor -d axor >/dev/null 2>&1; do
        i=$((i + 1)); [ "$i" -gt 60 ] && { echo "postgres did not become ready" >&2; exit 1; }
        sleep 1
    done
    docker compose exec -T postgres psql -U axor -d axor -tAc \
        "SELECT 1 FROM pg_database WHERE datname='axor_lab'" | grep -q 1 \
        || docker compose exec -T postgres psql -U axor -d axor -c "CREATE DATABASE axor_lab"
fi

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
if [ -n "${LAB_HOST:-}" ] && [ "${COMPOSE_PROFILES:-}" = "lab" ]; then
    cat <<TXT

Axor Lab:  https://$LAB_HOST   (needs its own DNS A record to this server)
Sign in with the same account as the Control Plane.
TXT
fi
