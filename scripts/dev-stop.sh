#!/usr/bin/env bash
set -euo pipefail

. "$(dirname "${BASH_SOURCE[0]}")/lib/dev-common.sh"
load_env

compose stop nginx kafka redis mysql
printf '中间件与网关已停止，数据卷保留。前后端请通过 VS Code 的“终止任务”停止。\n'
