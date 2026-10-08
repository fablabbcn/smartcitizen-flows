#!/bin/sh
# Dumps the flows database (metadata, revisions, jobs and runs) and prunes old dumps.
# Usage: scripts/backup.sh   (from anywhere; reads POSTGRES_USER and POSTGRES_DB from .env)
#   BACKUP_DIR (default ~/backups) and RETENTION_DAYS (default 14) can be set in the environment
set -eu

cd "$(dirname "$0")/.."
BACKUP_DIR=${BACKUP_DIR:-$HOME/backups}
RETENTION_DAYS=${RETENTION_DAYS:-14}

set -a
. ./.env
set +a

mkdir -p "$BACKUP_DIR"
file="$BACKUP_DIR/flows-$(date +%F).sql.gz"
# Write to a temporary file: a failed dump never replaces a good one
# pg_dump compresses itself (-Z): with a pipe to gzip, a failed dump would go unnoticed
docker compose exec -T postgres pg_dump -Z 6 -U "$POSTGRES_USER" "$POSTGRES_DB" > "$file.tmp"
mv "$file.tmp" "$file"
echo "$(date '+%F %T') wrote $file ($(du -h "$file" | cut -f1))"

find "$BACKUP_DIR" -name 'flows-*.sql.gz' -mtime +"$RETENTION_DAYS" -print -delete
