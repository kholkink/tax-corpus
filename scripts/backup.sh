#!/usr/bin/env bash
# Бэкап (P7): дамп БД + дела + реестры разъяснений/позиций; хранить 14 последних.
# Использование: bash scripts/backup.sh [каталог]   (TAXCORPUS_DB — строка подключения)
set -euo pipefail
cd "$(dirname "$0")/.."
DEST="${1:-${BACKUP_DIR:-backups}}"; mkdir -p "$DEST"
STAMP=$(date +%Y%m%d-%H%M%S)
DB="${TAXCORPUS_DB:-postgresql://postgres@127.0.0.1:5433/taxcorpus}"
pg_dump --no-owner --format=custom "$DB" > "$DEST/db-$STAMP.dump"
tar -czf "$DEST/files-$STAMP.tgz" workspaces data/interpretations data/parameters 2>/dev/null || true
sha256sum "$DEST/db-$STAMP.dump" "$DEST/files-$STAMP.tgz" > "$DEST/backup-$STAMP.sha256"
ls -1t "$DEST"/db-*.dump | tail -n +15 | xargs -r rm -f
ls -1t "$DEST"/files-*.tgz | tail -n +15 | xargs -r rm -f
ls -1t "$DEST"/backup-*.sha256 | tail -n +15 | xargs -r rm -f
echo "бэкап: $DEST/db-$STAMP.dump, $DEST/files-$STAMP.tgz"
