#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=../lib/db-common.sh
. "$SCRIPT_DIR/../lib/db-common.sh"
load_env

# Flyway must have created the tables first; this command never changes schema.
mysql_exec < "$SCRIPT_DIR/fixtures/demo.sql"
printf '演示数据已补齐，已有积分、库存及活动保持原值。\n'
