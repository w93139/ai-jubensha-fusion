#!/bin/sh
set -eu

BACKUP_DIR=${BACKUP_DIR:-/var/backups/ai-jubensha}
RETENTION_DAYS=${RETENTION_DAYS:-14}
mkdir -p "$BACKUP_DIR"
STAMP=$(date +%Y%m%d-%H%M%S)
docker compose exec -T db pg_dump -U "${DB_USER:-jubensha}" -Fc "${DB_NAME:-jubensha}" > "$BACKUP_DIR/jubensha-$STAMP.dump"
find "$BACKUP_DIR" -type f -name 'jubensha-*.dump' -mtime "+$RETENTION_DAYS" -delete

