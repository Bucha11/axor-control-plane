#!/bin/sh
# Install the Control Plane on a machine with no route out, from this bundle.
# Nothing here reaches a network: the images come from images.tar.gz, the
# secrets are generated locally, and TLS is either your own certificate or
# Caddy's local CA. Safe to re-run: an existing .env is never overwritten.
#
#   ./install.sh axor.internal
#   ./install.sh axor.internal --cert /path/tls.crt --key /path/tls.key
#   ./install.sh axor.internal --with-lab [lab.internal]
#
# Without --cert, Caddy issues the certificate from its own local CA and the
# script prints where to find the root to trust on your machines.
#
# Signup mode defaults to first-only — the first account claims the install.
# Override with SIGNUP=closed ./install.sh …
set -eu
cd "$(dirname "$0")"
BUNDLE=$(pwd)
STACK=$BUNDLE/deploy/self-hosted

HOST=${1:-}
[ -n "$HOST" ] || {
    echo "usage: $0 <hostname> [--cert FILE --key FILE] [--with-lab [lab-hostname]]" >&2
    echo "   e.g. $0 axor.internal --cert /etc/ssl/axor.crt --key /etc/ssl/axor.key" >&2
    exit 2
}
shift
CERT=""; KEY=""; WITH_LAB=""; LAB_HOST_ARG=""
while [ $# -gt 0 ]; do
    case $1 in
        --cert) CERT=${2:?--cert needs a file}; shift 2 ;;
        --key) KEY=${2:?--key needs a file}; shift 2 ;;
        --with-lab)
            WITH_LAB=1
            case ${2:-} in -*|"") LAB_HOST_ARG=lab.$HOST; shift ;; *) LAB_HOST_ARG=$2; shift 2 ;; esac ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done
if { [ -n "$CERT" ] && [ -z "$KEY" ]; } || { [ -z "$CERT" ] && [ -n "$KEY" ]; }; then
    echo "error: --cert and --key go together" >&2; exit 2
fi

if [ "$(uname -m)" != "x86_64" ]; then
    echo "error: $(uname -m) host — the bundled images are amd64 only" >&2
    exit 1
fi
command -v docker >/dev/null || {
    echo "error: docker is not installed. An air-gapped host needs it from your" >&2
    echo "       own mirror or the distribution's packages — this bundle does not" >&2
    echo "       carry the engine." >&2
    exit 1
}
docker compose version >/dev/null 2>&1 || {
    echo "error: this docker has no 'compose' (v2) plugin" >&2; exit 1; }
docker info >/dev/null 2>&1 || {
    echo "error: cannot talk to the docker daemon (is it running? are you in the docker group?)" >&2
    exit 1
}

if command -v sha256sum >/dev/null && [ -f SHA256SUMS ]; then
    echo "checking the archive …"
    sha256sum -c --quiet SHA256SUMS || {
        echo "error: this bundle does not match its checksums — copy it again" >&2; exit 1; }
fi

echo "loading the images …"
gzip -dc images.tar.gz | docker load

PLATFORM_IMAGE="$(grep -E '^AXOR_IMAGE=' "$STACK/.env.example" 2>/dev/null | cut -d= -f2- || true)"
[ -n "$PLATFORM_IMAGE" ] || PLATFORM_IMAGE=ghcr.io/bucha11/axor-platform
TAG=$(grep -E '^AXOR_TAG=' "$STACK/.env.example" | cut -d= -f2-)
IMAGE="$PLATFORM_IMAGE:$TAG"

cd "$STACK"
if [ -e .env ]; then
    echo ".env exists — keeping it (delete it to regenerate secrets)"
else
    umask 077
    secret() { if command -v openssl >/dev/null; then openssl rand -hex 24;
               else docker run --rm --entrypoint python "$IMAGE" -c \
                    "import secrets;print(secrets.token_hex(24))"; fi; }
    echo "generating the identity signing key …"
    PEM=$(docker run --rm --entrypoint python "$IMAGE" \
        -c "from axor_identity.keys import generate_pem; print(generate_pem())")
    sed \
        -e "s|^CP_HOST=.*|CP_HOST=$HOST|" \
        -e "s|^AXOR_IDENTITY_SIGNUP=.*|AXOR_IDENTITY_SIGNUP=${SIGNUP:-first-only}|" \
        -e "s|^AXOR_PG_PASSWORD=.*|AXOR_PG_PASSWORD=$(secret)|" \
        -e "s|^AXOR_API_TOKEN=.*|AXOR_API_TOKEN=$(secret)|" \
        -e "s|^AXOR_PROXY_TOKEN=.*|AXOR_PROXY_TOKEN=$(secret)|" \
        -e "s|^AXOR_LAB_CONTROL_TOKEN=.*|AXOR_LAB_CONTROL_TOKEN=$(secret)|" \
        -e "/^AXOR_IDENTITY_SIGNING_KEY=/d" \
        .env.example > .env
    printf 'AXOR_IDENTITY_SIGNING_KEY="%s"\n' "$PEM" >> .env
    # both compose files, so plain `docker compose …` here works afterwards
    printf '\n# air-gapped: the override pins every image to what was loaded\nCOMPOSE_FILE=docker-compose.yml:docker-compose.offline.yml\n' >> .env
    chmod 600 .env
    echo "wrote .env (chmod 600)"
fi

# ── TLS: your certificate, or Caddy's local CA ──────────────────────────────
mkdir -p certs
if [ -n "$CERT" ]; then
    cp "$CERT" certs/tls.crt
    cp "$KEY" certs/tls.key
    chmod 600 certs/tls.key
    echo "tls /certs/tls.crt /certs/tls.key" > tls.caddy
    echo "using the certificate you gave (certs/tls.crt)"
else
    echo "tls internal" > tls.caddy
    echo "no certificate given — Caddy will issue one from its own local CA"
fi

if [ -n "$WITH_LAB" ]; then
    setvar() {  # setvar NAME VALUE — set NAME in .env if it is empty or absent
        if grep -qE "^$1=.+" .env; then return 0; fi
        if grep -qE "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env
        else printf '%s=%s\n' "$1" "$2" >> .env; fi
    }
    setvar COMPOSE_PROFILES lab
    setvar LAB_HOST "$LAB_HOST_ARG"
    mkdir -p sites
    cp lab.caddy.template sites/lab.caddy
    echo "Lab enabled on $(grep -E '^LAB_HOST=' .env | cut -d= -f2-)"
fi

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

Started, with no network needed:

  https://$CP_HOST

Point that name at this host (DNS, or /etc/hosts on the machines that use it).

Signup is '$AXOR_IDENTITY_SIGNUP'. Register the first account now — it becomes
the owner of this installation, and in first-only mode nobody else can.

Operator master token (also in $STACK/.env — keep that file private):
  AXOR_API_TOKEN=$AXOR_API_TOKEN

It is NOT how people log in; it is yours, for Settings -> auth, to mint the
proxy's ingest key.
TXT

if [ -z "$CERT" ]; then
    cat <<'TXT'

Caddy issued the certificate from its own local CA. Browsers will warn until
that root is trusted. Take it out of the container and install it on the
machines that will use this host:

  docker compose exec caddy \
    cat /data/caddy/pki/authorities/local/root.crt > axor-root.crt

To use your company's certificate instead, re-run with:
  ./install.sh <hostname> --cert /path/tls.crt --key /path/tls.key
TXT
fi

cat <<'TXT'

Your licence (Business, Team, Security, Enterprise) is verified offline — it
never calls out. Paste it in Settings -> Billing, or put it in .env as
AXOR_LICENSE with the vendor key in AXOR_VENDOR_PUBKEY, then:

  docker compose up -d backend identity
TXT
