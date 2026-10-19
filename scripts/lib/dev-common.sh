#!/usr/bin/env bash

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
DEV_RUNTIME_DIR="$ROOT_DIR/.local/run"

load_env() {
  if [ ! -f "$ROOT_DIR/.env" ]; then
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  fi
  set -a
  # shellcheck disable=SC1091
  . "$ROOT_DIR/.env"
  MYSQL_DATABASE=${MYSQL_DATABASE:-shanpu}
  MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PASSWORD:-123456}
  MYSQL_PORT=${MYSQL_PORT:-3306}
  REDIS_PASSWORD=${REDIS_PASSWORD:-123456}
  REDIS_PORT=${REDIS_PORT:-6379}
  KAFKA_PORT=${KAFKA_PORT:-9092}
  BACKEND_PORT=${BACKEND_PORT:-8081}
  FRONTEND_PORT=${FRONTEND_PORT:-3000}
  GATEWAY_PORT=${GATEWAY_PORT:-8089}
  JWT_SECRET=${JWT_SECRET:-replace-me-with-a-32-byte-random-secret-for-development}
  set +a
}

compose() {
  docker compose --project-name shanpu --env-file "$ROOT_DIR/.env" \
    --file "$ROOT_DIR/docker-compose.yml" "$@"
}

backend_api_base() {
  printf 'http://127.0.0.1:%s/api' "$BACKEND_PORT"
}

require_backend() {
  local health
  if ! health=$(curl -fsS --connect-timeout 2 --max-time 10 "$(backend_api_base)/health") \
      || ! printf '%s' "$health" | python3 -c \
        'import json,sys; s=json.load(sys.stdin); sys.exit(0 if all(s.get(k)=="UP" for k in ("status","mysql","redis","kafka")) else 1)'; then
    printf '后端尚未就绪，请先运行“启动后端”：%s/health\n' "$(backend_api_base)" >&2
    return 1
  fi
}
