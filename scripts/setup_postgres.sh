#!/usr/bin/env bash
# Локальный PostgreSQL 16 без прав администратора (user-space) для Windows (Git Bash) и Linux/WSL.
# Бинарники: io.zonky.test.postgres:embedded-postgres-binaries-{windows,linux}-amd64 (Maven Central).
# Использование: bash scripts/setup_postgres.sh [порт]   (по умолчанию 5432; в WSL порт 5432 часто
# занят Windows-инстансом — укажите 5433 и экспортируйте TAXCORPUS_DB).
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"  # корень проекта, без привязки к машине
PORT="${1:-5432}"
PGROOT="$BASE/.pg"

case "$(uname -s)" in
  Linux*)  OS=linux;   EXE="";     TAR="tar" ;;
  *)       OS=windows; EXE=".exe"; TAR="MSYS_NO_PATHCONV=1 C:/Windows/System32/tar.exe" ;;
esac
JARDIR="$PGROOT/$OS/downloads"
INST="$PGROOT/$OS/instance"
JAR="$JARDIR/pg16.jar"
URL="https://repo1.maven.org/maven2/io/zonky/test/postgres/embedded-postgres-binaries-$OS-amd64/16.4.0/embedded-postgres-binaries-$OS-amd64-16.4.0.jar"

echo "=== [1/5] download ($OS) ==="
if [ ! -f "$JAR" ]; then
  mkdir -p "$JARDIR"
  curl -sSL --retry 3 -o "$JAR" "$URL"
fi

echo "=== [2/5] extract ==="
rm -rf "$JARDIR/jar" "$INST"
mkdir -p "$JARDIR/jar" "$INST"
python -m zipfile -e "$JAR" "$JARDIR/jar/" 2>/dev/null || python3 -m zipfile -e "$JAR" "$JARDIR/jar/"
TXZ=$(ls "$JARDIR/jar/"*.txz | head -1)
eval "$TAR -xJf \"$TXZ\" -C \"$INST\""

echo "=== [3/5] initdb ==="
"$INST/bin/initdb$EXE" -D "$INST/data" -U postgres --auth=trust --encoding=UTF8 --no-locale >/dev/null

echo "=== [4/5] start on port $PORT ==="
"$INST/bin/pg_ctl$EXE" -D "$INST/data" -l "$INST/postgres.log" -o "-p $PORT -c listen_addresses=127.0.0.1" start

echo "=== [5/5] create database ==="
sleep 2
DB_URL="postgresql://postgres@127.0.0.1:$PORT/taxcorpus"
PY=$( [ -x "$BASE/.venv-linux/bin/python" ] && echo "$BASE/.venv-linux/bin/python" || echo python )
"$PY" - "$PORT" <<'PYEOF'
import sys, psycopg
port = sys.argv[1]
with psycopg.connect(f"postgresql://postgres@127.0.0.1:{port}/postgres", autocommit=True) as c:
    if not c.execute("select 1 from pg_database where datname='taxcorpus'").fetchone():
        c.execute("CREATE DATABASE taxcorpus")
print("database taxcorpus ready")
PYEOF
echo
echo "готово. Экспортируйте: export TAXCORPUS_DB=$DB_URL"
echo "остановить: $INST/bin/pg_ctl$EXE -D $INST/data stop"
