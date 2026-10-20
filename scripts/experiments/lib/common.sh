#!/usr/bin/env bash

# shellcheck source=../../lib/db-common.sh
. "$(dirname "${BASH_SOURCE[0]}")/../../lib/db-common.sh"
RUNTIME_DIR="$ROOT_DIR/.local/experiments"
TOKEN_FILE="$RUNTIME_DIR/bench-tokens.txt"
BENCH_USER_FILE="$RUNTIME_DIR/bench-users.tsv"

mkdir -p "$RUNTIME_DIR"

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

require_experiment_data_ownership() {
  local ids_csv=$1 others
  others=$(mysql_scalar -e "SELECT COUNT(*) FROM t_order o JOIN t_user u ON u.id = o.user_id
    WHERE o.activity_id IN ($ids_csv) AND NOT (
      u.username REGEXP '^u[0-9]+$'
      AND CAST(SUBSTRING(u.username, 2) AS UNSIGNED) BETWEEN 1 AND $USER_RANGE_MAX);")
  if [ "$others" != 0 ]; then
    printf '目标活动包含非实验用户订单，请使用独立实验数据库；拒绝覆盖库存。\n' >&2
    return 1
  fi
}

# Cleanup is allowed only in an otherwise idle local experiment environment.
# Kafka lag alone is insufficient: unresolved DLQ entries must also be absent.
require_drained_orders() {
  local state unresolved
  state=$(compose exec -T kafka /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server kafka:9092 --describe --group shanpu-seckill-consumer)
  if ! printf '%s\n' "$state" | awk '
    $2 == "shanpu.seckill.order" {
      partitions++; if ($6 !~ /^[0-9]+$/ || $6 != 0) pending = 1
    }
    END { exit !(partitions == 3 && !pending) }'; then
    printf 'Kafka 消费尚未排空或无法确认状态，保留现场，拒绝重置数据。\n' >&2
    return 1
  fi
  unresolved=$(mysql_scalar -e 'SELECT COUNT(*) FROM t_order_dlq_log WHERE status <> 1;')
  if [ "$unresolved" != 0 ]; then
    printf '仍有未解决的死信记录，保留现场，拒绝重置数据。\n' >&2
    return 1
  fi
}
