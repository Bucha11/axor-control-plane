#!/bin/sh
# Build the offline bundle: one archive that installs the whole stack on a
# machine with no route out. Run this where there IS a network, from a clone of
# this repository, then carry the archive in.
#
#   ./bundle.sh                     the tag in deploy/self-hosted/.env.example
#   ./bundle.sh 0.4.0               that release
#   ./bundle.sh 0.4.0 --with-lab    with Axor Lab (a bigger archive)
#   OUT=/mnt/usb ./bundle.sh        write it somewhere else
#   ./bundle.sh 0.4.0 --no-pull     use the images already on this machine
#                                   (a build host that just built them)
#
# What it writes: axor-offline-<tag>[-lab].tar.gz — the images as one
# `docker save` stream, the compose files, the database bootstrap, install.sh,
# a manifest of every image digest, and SHA256SUMS over all of it.
#
# The archive carries no secrets: install.sh generates them on the machine
# where it runs. The licence is yours to copy in (see README.md).
set -eu
cd "$(dirname "$0")"
REPO=$(cd ../.. && pwd)
OUT=${OUT:-$(pwd)}

TAG=""
WITH_LAB=""
NO_PULL=""
for arg in "$@"; do
    case $arg in
        --with-lab) WITH_LAB=1 ;;
        --no-pull) NO_PULL=1 ;;
        -*) echo "unknown option: $arg" >&2; exit 2 ;;
        *) TAG=$arg ;;
    esac
done
if [ -z "$TAG" ]; then
    TAG=$(grep -E '^AXOR_TAG=' ../self-hosted/.env.example | cut -d= -f2-)
fi
[ -n "$TAG" ] || { echo "error: no tag given and none in .env.example" >&2; exit 2; }

command -v docker >/dev/null || { echo "error: docker is not installed" >&2; exit 1; }

# The two base images come from Docker Hub. A company that mirrors it points
# these at its own registry — the compose file reads the same names, so set
# them in .env on the host as well.
PLATFORM_IMAGE="${AXOR_IMAGE:-ghcr.io/bucha11/axor-platform}:$TAG"
FRONTEND_IMAGE="${AXOR_FRONTEND_IMAGE:-ghcr.io/bucha11/axor-frontend}:$TAG"
CADDY_IMAGE=${CADDY_IMAGE:-caddy:2-alpine}
POSTGRES_IMAGE=${POSTGRES_IMAGE:-postgres:16-alpine}
IMAGES="$PLATFORM_IMAGE $FRONTEND_IMAGE $CADDY_IMAGE $POSTGRES_IMAGE"
NAME="axor-offline-$TAG"
if [ -n "$WITH_LAB" ]; then
    LAB_TAG=${LAB_TAG:-$(grep -E '^LAB_TAG=' ../self-hosted/.env.example | cut -d= -f2-)}
    LAB_TAG=${LAB_TAG:-edge}
    IMAGES="$IMAGES ${LAB_IMAGE:-ghcr.io/bucha11/axor-lab}:$LAB_TAG ${NGINX_IMAGE:-nginx:1.27-alpine}"
    NAME="$NAME-lab"
fi

WORK=$(mktemp -d)
trap 'rm -rf "${WORK:?}"' EXIT INT TERM
ROOT=$WORK/$NAME
mkdir -p "$ROOT/deploy/self-hosted/sites" "$ROOT/deploy/initdb" "$ROOT/deploy/prod/initdb"

if [ -n "$NO_PULL" ]; then
    echo "using the images already on this machine …"
else
    echo "pulling ${TAG} (amd64) …"
fi
for image in $IMAGES; do
    [ -n "$NO_PULL" ] || docker pull --platform linux/amd64 -q "$image" >/dev/null
    docker image inspect "$image" >/dev/null 2>&1 || {
        echo "error: $image is not on this machine" >&2; exit 1; }
    echo "  $image"
done

echo "saving the images (this is most of the archive) …"
docker save $IMAGES | gzip -1 > "$ROOT/images.tar.gz"

# the stack, laid out as the compose file expects to find it
cp ../self-hosted/docker-compose.yml "$ROOT/deploy/self-hosted/"
cp ../self-hosted/lab.caddy.template "$ROOT/deploy/self-hosted/"
cp docker-compose.offline.yml "$ROOT/deploy/self-hosted/"
cp Caddyfile "$ROOT/deploy/self-hosted/Caddyfile"       # the offline one: no ACME
cp ../initdb/10-identity-db.sql "$ROOT/deploy/initdb/"
cp ../prod/initdb/20-lab-db.sql "$ROOT/deploy/prod/initdb/"
cp ../prod/lab-nginx.conf ../prod/backup.sh "$ROOT/deploy/prod/"
: > "$ROOT/deploy/self-hosted/sites/.keep"
cp install.sh "$ROOT/install.sh"
cp README.md "$ROOT/README.md"
chmod +x "$ROOT/install.sh"

# .env.example, pinned to what is actually in the archive
sed -e "s|^AXOR_TAG=.*|AXOR_TAG=$TAG|" \
    -e "s|^ACME_EMAIL=.*|ACME_EMAIL=none@localhost|" \
    -e "s|^# Where Let's Encrypt sends expiry warnings.*|# Unused in an air-gapped install (no ACME); the compose file reads it.|" \
    ../self-hosted/.env.example > "$ROOT/deploy/self-hosted/.env.example"
[ -n "$WITH_LAB" ] && sed -i.bak -e "s|^LAB_TAG=.*|LAB_TAG=$LAB_TAG|" \
    "$ROOT/deploy/self-hosted/.env.example" && rm -f "$ROOT/deploy/self-hosted/.env.example.bak"

# Pin every image to exactly what the archive carries: the host must not
# resolve a name to something it does not have (and cannot fetch).
{
    echo
    echo "# ── the images in this bundle; the host has no registry to ask ──"
    echo "AXOR_IMAGE=${PLATFORM_IMAGE%:*}"
    echo "AXOR_FRONTEND_IMAGE=${FRONTEND_IMAGE%:*}"
    echo "CADDY_IMAGE=$CADDY_IMAGE"
    echo "POSTGRES_IMAGE=$POSTGRES_IMAGE"
    if [ -n "$WITH_LAB" ]; then
        echo "LAB_IMAGE=${LAB_IMAGE:-ghcr.io/bucha11/axor-lab}"
        echo "NGINX_IMAGE=${NGINX_IMAGE:-nginx:1.27-alpine}"
    fi
} >> "$ROOT/deploy/self-hosted/.env.example"

{
    echo "Axor Control Plane — offline bundle"
    echo "tag:      $TAG"
    echo "built:    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "platform: linux/amd64"
    echo
    echo "images (digest as pulled):"
    for image in $IMAGES; do
        # the digest FOR THIS repository: an image with several tags carries
        # several, and the first one is not always this one
        digest=$(docker image inspect "$image" --format '{{range .RepoDigests}}{{println .}}{{end}}' \
            2>/dev/null | grep "^${image%:*}@" | head -1)
        if [ -n "$digest" ]; then
            echo "  $image  $digest"
        else
            echo "  $image  (built here: $(docker image inspect "$image" --format '{{.Id}}'))"
        fi
    done
    echo
    echo "Check the digests against the release notes before installing."
} > "$ROOT/MANIFEST.txt"

(cd "$ROOT" && find . -type f ! -name SHA256SUMS -print0 | sort -z |
    xargs -0 sha256sum > SHA256SUMS)

mkdir -p "$OUT"
tar czf "$OUT/$NAME.tar.gz" -C "$WORK" "$NAME"
SIZE=$(du -h "$OUT/$NAME.tar.gz" | cut -f1)
cat <<TXT

Wrote $OUT/$NAME.tar.gz ($SIZE)

Carry it to the air-gapped host, then:

  tar xzf $NAME.tar.gz
  cd $NAME
  sha256sum -c SHA256SUMS      # the archive is intact
  ./install.sh axor.internal   # the hostname it will answer on

README.md inside the archive has the rest: your own certificate, the licence,
upgrades and backups.
TXT
