#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
. "$SCRIPT_DIR/../lib/dev-common.sh"
load_env
export MYSQL_DATABASE=${EXPERIMENT_DATABASE:-$MYSQL_DATABASE}
exec python3 "$SCRIPT_DIR/lib/cleanup.py" "$@"
