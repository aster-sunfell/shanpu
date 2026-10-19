#!/usr/bin/env bash
set -euo pipefail

. "$(dirname "${BASH_SOURCE[0]}")/lib/dev-common.sh"
load_env

compose up -d --wait --wait-timeout 60 nginx
printf '浏览器入口：http://127.0.0.1:%s\n' "$GATEWAY_PORT"
