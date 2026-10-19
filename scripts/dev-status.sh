#!/usr/bin/env bash
set -euo pipefail

. "$(dirname "${BASH_SOURCE[0]}")/lib/dev-common.sh"
load_env

compose ps --all mysql redis kafka nginx
printf '\n后端健康检查：%s/health\n' "$(backend_api_base)"
require_backend
curl -fsS --connect-timeout 2 --max-time 5 "http://127.0.0.1:$GATEWAY_PORT/gateway-health" > /dev/null
curl -fsS --connect-timeout 2 --max-time 90 "http://127.0.0.1:$GATEWAY_PORT/login" > /dev/null
printf 'MySQL / Redis / Kafka 均为 UP，网关与前端可访问。\n浏览器入口：http://127.0.0.1:%s\n' "$GATEWAY_PORT"
