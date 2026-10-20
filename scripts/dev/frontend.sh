#!/usr/bin/env bash
set -euo pipefail

. "$(dirname "${BASH_SOURCE[0]}")/../lib/dev-common.sh"
load_env

export PUBLIC_API_BASE_URL=/api
export NEXT_PUBLIC_API_BASE_URL=/api
export INTERNAL_API_BASE_URL="$(backend_api_base)"
cd "$ROOT_DIR/frontend"
exec pnpm dev --hostname 127.0.0.1 --port "$FRONTEND_PORT"
