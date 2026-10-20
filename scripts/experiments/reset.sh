#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib/common.sh"

load_env
require_backend
require_drained_orders

if [ "$#" -eq 0 ]; then
  ACTIVITY_IDS=(1001 2001 3001)
else
  ACTIVITY_IDS=("$@")
fi
require_numeric_id_list "${ACTIVITY_IDS[@]}"

PRESSURE_STOCK=${PRESSURE_STOCK:-20000}
USER_RANGE_MAX=${USER_RANGE_MAX:-20000}
BENCH_POINTS=${BENCH_POINTS:-10000000}
ids_csv=$(IFS=,; echo "${ACTIVITY_IDS[*]}")
require_experiment_data_ownership "$ids_csv"

echo "[bench-reset] activities=$ids_csv stock=$PRESSURE_STOCK users=1..$USER_RANGE_MAX"

mysql_exec <<SQL
CREATE TEMPORARY TABLE bench_users AS
SELECT id
FROM t_user
WHERE username REGEXP '^u[0-9]+$'
  AND CAST(SUBSTRING(username, 2) AS UNSIGNED) BETWEEN 1 AND $USER_RANGE_MAX;

CREATE TEMPORARY TABLE bench_orders AS
SELECT order_id
FROM t_order
WHERE activity_id IN ($ids_csv)
  AND user_id IN (SELECT id FROM bench_users);

DELETE p
FROM t_point_log p
JOIN bench_orders b ON b.order_id = p.biz_ref
WHERE p.biz_type IN (2, 3);

DELETE o
FROM t_order o
JOIN bench_orders b ON b.order_id = o.order_id;

UPDATE t_user
SET points = $BENCH_POINTS + COALESCE((
      SELECT SUM(p.delta) FROM t_point_log p
      WHERE p.user_id = t_user.id AND p.biz_type IN (2, 3)
    ), 0),
    updated_at = CURRENT_TIMESTAMP
WHERE id IN (SELECT id FROM bench_users);

UPDATE t_seckill_activity
SET total_stock = $PRESSURE_STOCK,
    status = 1,
    start_at = NOW() - INTERVAL 1 HOUR,
    end_at = NOW() + INTERVAL 6 HOUR,
    updated_at = CURRENT_TIMESTAMP
WHERE id IN ($ids_csv);
SQL

for id in "${ACTIVITY_IDS[@]}"; do
  redis_cli DEL \
    "shanpu:seckill:stock:$id" \
    "shanpu:seckill:bought:$id" \
    "shanpu:seckill:activity:$id" \
    "shanpu:seckill:activity:lock:$id" >/dev/null
  case "$id" in
    2*|3*)
      redis_cli SET "shanpu:seckill:stock:$id" "$PRESSURE_STOCK" >/dev/null
      ;;
  esac
done

if [ -s "$TOKEN_FILE" ]; then
  admin_post '/admin/cache/activity-local/reset' >/dev/null || true
fi

echo "[bench-reset] done"
