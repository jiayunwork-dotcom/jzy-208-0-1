#!/usr/bin/env bash
# 下载并启动一个临时的 PostgreSQL 16（无需 root、无需 docker），
# 用于在开发机上跑集成测试。用法：
#   ./scripts/start_test_db.sh          # 启动并打印 DSN
#   BOILER_TEST_PG_BIN=.pgtest/bin python3 -m pytest
set -euo pipefail
cd "$(dirname "$0")/.."

ARCH="$(uname -m)"
case "$ARCH" in
  x86_64)  ZONKY=embedded-postgres-binaries-linux-amd64 ;;
  aarch64) ZONKY=embedded-postgres-binaries-linux-arm64v8 ;;
  *) echo "不支持的架构: $ARCH" >&2; exit 1 ;;
esac

PG_VERSION="16.15.0"
DEST=".pgtest"
JAR_URL="https://repo1.maven.org/maven2/io/zonky/test/postgres/${ZONKY}/${PG_VERSION}/${ZONKY}-${PG_VERSION}.jar"

if [ ! -x "$DEST/bin/postgres" ]; then
  echo "下载 PostgreSQL ${PG_VERSION} (${ZONKY}) ..."
  mkdir -p "$DEST"
  TMP_JAR="$(mktemp)"
  curl -fsSL "$JAR_URL" -o "$TMP_JAR"
  python3 - "$TMP_JAR" "$DEST" <<'EOF'
import sys, zipfile, tarfile, io
jar, dest = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(jar) as z:
    txz_name = next(n for n in z.namelist() if n.endswith(".txz"))
    data = z.read(txz_name)
with tarfile.open(fileobj=io.BytesIO(data), mode="r:xz") as t:
    t.extractall(dest)
EOF
  rm -f "$TMP_JAR"
fi

export LD_LIBRARY_PATH="$PWD/$DEST/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
DATA="$PWD/.pgtest-data"
PORT="${PGPORT:-55432}"
if [ ! -d "$DATA" ]; then
  "$DEST/bin/initdb" -D "$DATA" -U postgres --auth=trust -E UTF8 >/dev/null
fi
"$DEST/bin/pg_ctl" -D "$DATA" -o "-p $PORT -k /tmp" -l "$DATA/pg.log" start
echo "DSN: postgresql://postgres@localhost:$PORT/postgres"
echo "运行集成测试: BOILER_TEST_DSN=postgresql://postgres@localhost:$PORT/postgres python3 -m pytest"
