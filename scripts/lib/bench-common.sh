#!/usr/bin/env bash

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
RUNTIME_DIR="$ROOT_DIR/load/.runtime"
TOKEN_FILE="$RUNTIME_DIR/bench-tokens.txt"
BENCH_USER_FILE="$RUNTIME_DIR/bench-users.tsv"

ensure_env() {
  if [ ! -f "$ROOT_DIR/.env" ]; then
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  fi
}

load_env() {
  ensure_env
  set -a
  # shellcheck disable=SC1091
  . "$ROOT_DIR/.env"
  set +a
  MYSQL_DATABASE=${MYSQL_DATABASE:-shanpu}
  MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PASSWORD:-123456}
  REDIS_PASSWORD=${REDIS_PASSWORD:-123456}
  BACKEND_PORT=${BACKEND_PORT:-8080}
  JWT_SECRET=${JWT_SECRET:-replace-me-with-a-32-byte-random-secret-for-development}
  mkdir -p "$RUNTIME_DIR"
}

compose() {
  (cd "$ROOT_DIR" && docker compose "$@")
}

mysql_exec() {
  compose exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" "$@"
}

mysql_scalar() {
  mysql_exec -N -B "$@" | tail -n 1 | tr -d '\r'
}

redis_cli() {
  compose exec -T redis redis-cli -a "$REDIS_PASSWORD" "$@" | tr -d '\r'
}

backend_api_base() {
  printf 'http://localhost:%s/api' "$BACKEND_PORT"
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
