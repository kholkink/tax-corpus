#!/usr/bin/env bash
# Развёртывание локального PostgreSQL 16 без прав администратора (user-space).
# Источник бинарников: io.zonky.test.postgres:embedded-postgres-binaries-windows-amd64 (Maven Central).
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"  # корень проекта, без привязки к машине
PGROOT="$BASE/.pg"
JARDIR="$PGROOT/downloads"
INST="$PGROOT/instance"
JAR="$JARDIR/pg16.jar"
URL="https://repo1.maven.org/maven2/io/zonky/test/postgres/embedded-postgres-binaries-windows-amd64/16.4.0/embedded-postgres-binaries-windows-amd64-16.4.0.jar"

echo "=== [1/6] download ==="
if [ ! -f "$JAR" ]; then
  mkdir -p "$JARDIR"
  curl -sSL --retry 3 -o "$JAR" "$URL"
fi
ls -la "$JAR"

echo "=== [2/6] unzip jar ==="
rm -rf "$JARDIR/jar"
mkdir -p "$JARDIR/jar"
python -m zipfile -e "$JAR" "$JARDIR/jar/"
ls -la "$JARDIR/jar/"

echo "=== [3/6] extract txz ==="
TXZ=$(ls "$JARDIR/jar/"*.txz | head -1)
echo "txz: $TXZ"
rm -rf "$INST"
mkdir -p "$INST"
MSYS_NO_PATHCONV=1 C:/Windows/System32/tar.exe -xJf "$TXZ" -C "$INST"
ls "$INST/bin" | head -20

echo "=== [4/6] initdb ==="
rm -rf "$INST/data"
"$INST/bin/initdb.exe" -D "$INST/data" -U postgres --auth=trust --encoding=UTF8 --no-locale

echo "=== [5/6] start ==="
"$INST/bin/pg_ctl.exe" -D "$INST/data" -l "$INST/postgres.log" -o "-p 5432 -c listen_addresses=127.0.0.1" start

for i in $(seq 1 15); do
  if "$INST/bin/pg_isready.exe" -U postgres -p 5432; then break; fi
  sleep 1
done

echo "=== [6/6] create db ==="
"$INST/bin/createdb.exe" -U postgres -p 5432 taxcorpus 2>&1 || echo "(db уже существует?)"
"$INST/bin/psql.exe" -U postgres -p 5432 -d taxcorpus -c "select version();"

echo "POSTGRES READY: postgresql://postgres@localhost:5432/taxcorpus"
