#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ] || [ "$1" != '--schema-verified' ]; then
  printf '用法：bash scripts/db/baseline.sh --schema-verified\n仅用于已逐项核对结构与 V1 一致的旧库，操作前停止后端并备份。见 docs/DATABASE.md。\n' >&2
  exit 2
fi

# shellcheck source=../lib/dev-common.sh
. "$(dirname "${BASH_SOURCE[0]}")/../lib/dev-common.sh"
load_env

export FLYWAY_URL="jdbc:mysql://${MYSQL_HOST:-127.0.0.1}:$MYSQL_PORT/$MYSQL_DATABASE?useUnicode=true&characterEncoding=utf8&serverTimezone=Asia/Shanghai&useSSL=false&allowPublicKeyRetrieval=true"
export FLYWAY_USER="${MYSQL_USERNAME:-root}"
export FLYWAY_PASSWORD="${MYSQL_PASSWORD:-$MYSQL_ROOT_PASSWORD}"
exec mvn -B -ntp -f "$ROOT_DIR/backend/pom.xml" flyway:baseline
