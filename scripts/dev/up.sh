#!/usr/bin/env bash
set -euo pipefail

. "$(dirname "${BASH_SOURCE[0]}")/../lib/dev-common.sh"
load_env

compose up -d --wait --wait-timeout 180 mysql redis kafka
compose ps mysql redis kafka
