#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib/bench-common.sh"

load_env

compose up -d mysql redis kafka backend frontend

wait_healthy() {
  local container=$1
  local deadline=$((SECONDS + 300))
  while [ "$SECONDS" -lt "$deadline" ]; do
    local status
    status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container" 2>/dev/null || true)
    if [ "$status" = "healthy" ] || [ "$status" = "running" ]; then
      echo "[dev-up] $container $status"
      return 0
    fi
    sleep 3
  done
  echo "[dev-up] timeout waiting for $container" >&2
  compose ps
  return 1
}

wait_healthy shanpu-mysql
wait_healthy shanpu-redis
wait_healthy shanpu-kafka
wait_healthy shanpu-backend
wait_healthy shanpu-frontend

if compose ps --services --filter status=running | grep -qx 'k6'; then
  echo "[dev-up] k6 should only run through the loadtest profile" >&2
  exit 1
fi

compose ps
