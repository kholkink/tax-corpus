#!/usr/bin/env bash
# Восстановление (P7): bash scripts/restore.sh backups/db-<stamp>.dump [backups/files-<stamp>.tgz]
set -euo pipefail
cd "$(dirname "$0")/.."
DUMP="$1"; FILES="${2:-}"
DB="${TAXCORPUS_DB:-postgresql://postgres@127.0.0.1:5433/taxcorpus}"
sha256sum -c --ignore-missing "$(dirname "$DUMP")/backup-$(basename "$DUMP" .dump | sed 's/^db-//').sha256"
pg_restore --clean --if-exists --no-owner --dbname="$DB" "$DUMP"
[ -n "$FILES" ] && tar -xzf "$FILES"
echo "восстановлено из $DUMP ${FILES:+и $FILES}"
