#!/bin/sh
# Nightly backup of the whole production stack. Run from cron on the host:
#
#   15 3 * * * /opt/axor/deploy/prod/backup.sh >> /var/log/axor-backup.log 2>&1
#
# What it saves:
#   - one pg_dump (custom format) per database: axor, axor_identity, axor_lab.
#     Postgres is the system of record for all three apps.
#   - the proxy's traces volume and the Lab's data directory (tar.gz).
# Then it prunes local copies older than BACKUP_KEEP_DAYS, and copies the run
# off-site with rclone when BACKUP_RCLONE_REMOTE is set. A backup that exists
# only on the server it protects does not survive losing that server.
set -eu
# Dumps hold password hashes and API-key hashes: owner-only.
umask 077

cd "$(dirname "$0")"
# shellcheck disable=SC1091
set -a; . ./.env; set +a

BACKUP_DIR=${BACKUP_DIR:-/var/backups/axor}
KEEP=${BACKUP_KEEP_DAYS:-14}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$BACKUP_DIR/$STAMP"
mkdir -p "$OUT"

for db in axor axor_identity axor_lab; do
    docker compose exec -T postgres pg_dump -U axor -Fc "$db" > "$OUT/$db.dump"
done

# Volumes are read through the running containers, so the compose project name
# does not matter.
docker compose exec -T proxy tar czf - -C /data traces > "$OUT/proxy-traces.tgz" || \
    echo "warn: no proxy traces yet"
docker compose exec -T lab tar czf - -C /data . > "$OUT/lab-data.tgz"

echo "backup: $OUT ($(du -sh "$OUT" | cut -f1))"

find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP" -exec rm -rf {} +

if [ -n "${BACKUP_RCLONE_REMOTE:-}" ]; then
    rclone copy "$OUT" "$BACKUP_RCLONE_REMOTE/$STAMP"
    echo "backup: copied to $BACKUP_RCLONE_REMOTE/$STAMP"
fi
