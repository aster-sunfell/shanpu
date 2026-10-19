#!/usr/bin/env bash

# shellcheck source=dev-common.sh
. "$(dirname "${BASH_SOURCE[0]}")/dev-common.sh"
RUNTIME_DIR="$ROOT_DIR/load/.runtime"
TOKEN_FILE="$RUNTIME_DIR/bench-tokens.txt"
BENCH_USER_FILE="$RUNTIME_DIR/bench-users.tsv"

mkdir -p "$RUNTIME_DIR"

mysql_exec() {
  compose exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" "$@"
}

mysql_scalar() {
  mysql_exec -N -B "$@" | tail -n 1 | tr -d '\r'
}

redis_cli() {
  compose exec -T redis redis-cli -a "$REDIS_PASSWORD" "$@" | tr -d '\r'
}

first_bench_token() {
  if [ -f "$TOKEN_FILE" ]; then
    head -n 1 "$TOKEN_FILE"
  fi
}

admin_get() {
  local path=$1
  local token
  token=$(first_bench_token)
  curl -fsS -H "Authorization: Bearer $token" "$(backend_api_base)$path"
}

admin_post() {
  local path=$1
  local token
  token=$(first_bench_token)
  curl -fsS -X POST -H "Authorization: Bearer $token" "$(backend_api_base)$path"
}

redis_get_calls() {
  redis_cli INFO commandstats | awk -F'[=,]' '/^cmdstat_get:/ {print $2 + 0}'
}

require_numeric_id_list() {
  local id
  for id in "$@"; do
    case "$id" in
      ''|*[!0-9]*)
        echo "invalid activity id: $id" >&2
        exit 2
        ;;
    esac
  done
}
