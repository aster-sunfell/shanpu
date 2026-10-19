#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib/bench-common.sh"

load_env

PRESSURE_STOCK=${PRESSURE_STOCK:-20000}
API_BASE_IN_COMPOSE=${API_BASE_IN_COMPOSE:-$(backend_api_base)}
TMP_DIR=$(mktemp -d /tmp/shanpu-k6.XXXXXX)
REPORT_DIR="$ROOT_DIR/load/reports/$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$REPORT_DIR"
RESTORE_DONE=0
RESET_STARTED=0
SAMPLER_PID=

cleanup() {
  if [ -n "$SAMPLER_PID" ]; then
    stop_stats_sampler "$SAMPLER_PID"
  fi
  if [ "$RESET_STARTED" -eq 1 ] && [ "$RESTORE_DONE" -eq 0 ]; then
    bash "$SCRIPT_DIR/k6-bench-restore.sh" >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

field() {
  local line=$1
  local key=$2
  local token
  for token in $line; do
    if [[ "$token" == "$key="* ]]; then
      printf '%s' "${token#*=}"
      return 0
    fi
  done
  printf ''
}

pct() {
  awk -v v="${1:-0}" 'BEGIN { printf "%.2f%%", v * 100 }'
}

delta_pct() {
  awk -v before="${1:-0}" -v after="${2:-0}" 'BEGIN {
    if (before <= 0) { printf "0.00%%" } else { printf "%.2f%%", (before - after) * 100 / before }
  }'
}

abs_delta_pct() {
  awk -v before="${1:-0}" -v after="${2:-0}" 'BEGIN {
    if (before <= 0) {
      printf "0.00%%"
    } else {
      diff = (after - before) * 100 / before
      if (diff < 0) diff = -diff
      printf "%.2f%%", diff
    }
  }'
}

ratio_per_second() {
  awk -v before="${1:-0}" -v after="${2:-0}" 'BEGIN { printf "%.2f", (after - before) / 30 }'
}

mysql_count_orders() {
  local source=$1
  local activity_id=$2
  mysql_scalar -e "SELECT COUNT(*) FROM t_order WHERE source = $source AND activity_id = $activity_id;"
}

mysql_count_point_logs_for_orders() {
  local source=$1
  local activity_id=$2
  mysql_scalar -e "SELECT COUNT(*) FROM t_point_log p JOIN t_order o ON o.order_id = p.biz_ref WHERE p.biz_type = 2 AND o.source = $source AND o.activity_id = $activity_id;"
}

start_stats_sampler() {
  local name=$1
  local file="$REPORT_DIR/resources-$name.tsv"
  (
    while true; do
      date +%s >> "$file"
      docker stats --no-stream --format '{{.Name}}\t{{.MemUsage}}' \
        shanpu-mysql shanpu-redis shanpu-kafka >> "$file" 2>/dev/null || true
      if [ -f "$DEV_RUNTIME_DIR/backend.pid" ]; then
        local backend_pid
        backend_pid=$(cat "$DEV_RUNTIME_DIR/backend.pid")
        if [[ "$backend_pid" =~ ^[0-9]+$ ]] \
            && [ -r "/proc/$backend_pid/cmdline" ] \
            && tr '\0' ' ' < "/proc/$backend_pid/cmdline" | grep -q 'target/shanpu-backend-1.0.0.jar'; then
          ps -p "$backend_pid" -o pid=,rss=,%cpu= | awk '{print "backend_host\tpid=" $1 "\trss_kib=" $2 "\tcpu_percent=" $3}' >> "$file"
        fi
      fi
      sleep 2
    done
  ) </dev/null >/dev/null 2>&1 &
  SAMPLER_PID=$!
}

stop_stats_sampler() {
  local pid=$1
  if kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
    wait "$pid" >/dev/null 2>&1 || true
  fi
  SAMPLER_PID=
}

run_k6() {
  local output_file=$1
  shift
  compose --profile loadtest run --rm "$@" > "$output_file"
}

wait_c_consistency() {
  local expected_success=$1
  local deadline=$((SECONDS + 90))
  local orders=0
  local logs=0
  while [ "$SECONDS" -lt "$deadline" ]; do
    orders=$(mysql_count_orders 4 3001)
    logs=$(mysql_count_point_logs_for_orders 4 3001)
    if [ "$orders" -ge "$expected_success" ] && [ "$orders" -eq "$logs" ]; then
      echo "[k6-run] C consistency orders=$orders pointLogs=$logs"
      return 0
    fi
    sleep 3
  done
  echo "[k6-run] C consistency timeout orders=$orders pointLogs=$logs expectedSuccess=$expected_success" >&2
  return 1
}

run_abc_scenario() {
  local key=$1
  local label=$2
  local script=$3
  local activity_id=$4
  local source=$5
  local bottleneck=$6
  local outfile="$TMP_DIR/k6-$key.out"

  bash "$SCRIPT_DIR/k6-bench-reset.sh" "$activity_id"
  start_stats_sampler "$key"
  run_k6 "$outfile" \
    -e "ACTIVITY_ID=$activity_id" \
    -e "API_BASE=$API_BASE_IN_COMPOSE" \
    k6 run "/load/$script"
  stop_stats_sampler "$SAMPLER_PID"

  local summary
  summary=$(grep '^K6_SUMMARY ' "$outfile" | tail -n 1)
  if [ -z "$summary" ]; then
    cat "$outfile" >&2
    echo "[k6-run] missing K6_SUMMARY for $key" >&2
    exit 1
  fi

  local success
  success=$(field "$summary" success)
  if [ "$key" = "c" ]; then
    wait_c_consistency "$success"
  fi

  local order_count
  order_count=$(mysql_count_orders "$source" "$activity_id")
  local oversell="否"
  if [ "$order_count" -gt "$PRESSURE_STOCK" ]; then
    oversell="是"
  fi

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$label" \
    "POST /api/seckills/$activity_id/grab" \
    "100 VU / 30s" \
    "$(field "$summary" success_rps)" \
    "$(field "$summary" p50)" \
    "$(field "$summary" p95)" \
    "$(field "$summary" p99)" \
    "$(pct "$(field "$summary" http_error_rate)")" \
    "$(pct "$(field "$summary" business_error_rate)")" \
    "$order_count" \
    "$oversell" \
    "$bottleneck" >> "$TMP_DIR/abc.tsv"
}

cache_hit_rate() {
  admin_get '/admin/cache/stats' \
    | sed -n 's/.*"hitRate":\([0-9.]*\).*/\1/p' \
    | head -n 1
}

run_d_round() {
  local target=$1
  local mode=$2
  local outfile="$TMP_DIR/k6-d-$target-$mode.out"
  local enabled=false
  if [ "$mode" = "on" ]; then
    enabled=true
  fi

  admin_post "/admin/cache/activity-local/enabled?enabled=$enabled" >/dev/null
  admin_post '/admin/cache/activity-local/reset' >/dev/null
  redis_cli DEL \
    "shanpu:seckill:activity:3001" \
    "shanpu:seckill:activity:lock:3001" \
    "shanpu:product:detail:1" \
    "shanpu:product:detail:lock:1" >/dev/null

  local get_before
  get_before=$(redis_get_calls)
  run_k6 "$outfile" \
    -e "CACHE_TARGET=$target" \
    -e "CACHE_MODE=$mode" \
    -e "ACTIVITY_ID=3001" \
    -e "PRODUCT_ID=1" \
    -e "API_BASE=$API_BASE_IN_COMPOSE" \
    k6 run /load/scenario-d-cache-comparison.js
  local get_after
  get_after=$(redis_get_calls)

  local summary
  summary=$(grep '^K6_D_SUMMARY ' "$outfile" | tail -n 1)
  if [ -z "$summary" ]; then
    cat "$outfile" >&2
    echo "[k6-run] missing K6_D_SUMMARY for target=$target mode=$mode" >&2
    exit 1
  fi

  local hit_rate="—"
  if [ "$target" = "activity" ] && [ "$mode" = "on" ]; then
    hit_rate=$(cache_hit_rate)
  fi

  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$target" \
    "$mode" \
    "$(field "$summary" rps)" \
    "$(field "$summary" p50)" \
    "$(field "$summary" p95)" \
    "$(field "$summary" p99)" \
    "$(ratio_per_second "$get_before" "$get_after")" \
    "$hit_rate" \
    "$(field "$summary" http_error_rate)" >> "$TMP_DIR/d.tsv"
}

print_abc_table() {
  echo
  echo "### A/B/C 压测对比"
  printf '| %-17s | %-31s | %-12s | %-8s | %-12s | %-12s | %-12s | %-11s | %-11s | %-10s | %-4s | %-28s |\n' \
    "场景" "接口" "压力模型" "成功RPS" "成功p50(ms)" "成功p95(ms)" "成功p99(ms)" "HTTP错误率" "业务错误率" "成功订单数" "超卖" "瓶颈观察"
  printf '| %-17s | %-31s | %-12s | %-8s | %-12s | %-12s | %-12s | %-11s | %-11s | %-10s | %-4s | %-28s |\n' \
    "---" "---" "---" "---" "---" "---" "---" "---" "---" "---" "---" "---"
  while IFS=$'\t' read -r label endpoint pressure rps p50 p95 p99 http_err biz_err orders oversell bottleneck; do
    printf '| %-17s | %-31s | %-12s | %8s | %12s | %12s | %12s | %11s | %11s | %10s | %-4s | %-28s |\n' \
      "$label" "$endpoint" "$pressure" "$rps" "$p50" "$p95" "$p99" "$http_err" "$biz_err" "$orders" "$oversell" "$bottleneck"
  done < "$TMP_DIR/abc.tsv"
}

print_d_table() {
  local activity_off_p99 activity_on_p99 product_off_p99 product_on_p99 activity_off_get activity_on_get
  activity_off_p99=$(awk -F'\t' '$1=="activity" && $2=="off" {print $6}' "$TMP_DIR/d.tsv")
  activity_on_p99=$(awk -F'\t' '$1=="activity" && $2=="on" {print $6}' "$TMP_DIR/d.tsv")
  product_off_p99=$(awk -F'\t' '$1=="product" && $2=="off" {print $6}' "$TMP_DIR/d.tsv")
  product_on_p99=$(awk -F'\t' '$1=="product" && $2=="on" {print $6}' "$TMP_DIR/d.tsv")
  activity_off_get=$(awk -F'\t' '$1=="activity" && $2=="off" {print $7}' "$TMP_DIR/d.tsv")
  activity_on_get=$(awk -F'\t' '$1=="activity" && $2=="on" {print $7}' "$TMP_DIR/d.tsv")

  echo
  echo "### D 缓存层级对比"
  printf '| %-24s | %-18s | %-7s | %-8s | %-8s | %-8s | %-8s | %-14s | %-10s |\n' \
    "接口" "缓存层级" "L1状态" "平均RPS" "p50(ms)" "p95(ms)" "p99(ms)" "Redis GET/s" "L1 hitRate"
  printf '| %-24s | %-18s | %-7s | %-8s | %-8s | %-8s | %-8s | %-14s | %-10s |\n' \
    "---" "---" "---" "---" "---" "---" "---" "---" "---"
  while IFS=$'\t' read -r target mode rps p50 p95 p99 redis_get hit_rate http_err; do
    local endpoint layer state display_hit
    if [ "$target" = "activity" ]; then
      endpoint="GET /api/seckills/3001"
      layer=$([ "$mode" = "on" ] && echo "Caffeine -> Redis" || echo "Redis")
    else
      endpoint="GET /api/products/1"
      layer="Redis"
    fi
    state=$([ "$mode" = "on" ] && echo "开启" || echo "关闭")
    display_hit=$hit_rate
    if [ "$display_hit" != "—" ]; then
      display_hit=$(awk -v v="$display_hit" 'BEGIN { printf "%.4f", v }')
    fi
    printf '| %-24s | %-18s | %-7s | %8s | %8s | %8s | %8s | %14s | %10s |\n' \
      "$endpoint" "$layer" "$state" "$rps" "$p50" "$p95" "$p99" "$redis_get" "$display_hit"
  done < "$TMP_DIR/d.tsv"

  echo
  echo "活动接口 p99 收益: $(delta_pct "$activity_off_p99" "$activity_on_p99")"
  echo "活动 Redis GET 降幅: $(delta_pct "$activity_off_get" "$activity_on_get")"
  echo "商品接口 p99 偏差: $(abs_delta_pct "$product_off_p99" "$product_on_p99")"
}

echo "[k6-run] start core services"
bash "$SCRIPT_DIR/dev-up.sh" >/dev/null
require_backend

echo "[k6-run] seed bench users and JWT sessions"
RESET_STARTED=1
bash "$SCRIPT_DIR/seed-users.sh"

: > "$TMP_DIR/abc.tsv"
: > "$TMP_DIR/d.tsv"

run_abc_scenario "a" "A 普惠(tier=1)" "scenario-a-inclusive.js" "1001" "2" "DB 单行行锁串行化"
run_abc_scenario "b" "B 限时(tier=2)" "scenario-b-redis-sync.js" "2001" "3" "Redis 减负后同步落库"
run_abc_scenario "c" "C 超级(tier=3)" "scenario-c-redis-kafka.js" "3001" "4" "Kafka producer ack"

echo "[k6-run] run cache comparison rounds"
bash "$SCRIPT_DIR/k6-bench-reset.sh" 3001 >/dev/null
run_d_round activity off
run_d_round activity on
run_d_round product off
run_d_round product on
admin_post '/admin/cache/activity-local/enabled?enabled=true' >/dev/null || true

print_abc_table
print_d_table

echo
docker inspect -f '{{.Name}} OOMKilled={{.State.OOMKilled}}' shanpu-mysql shanpu-redis shanpu-kafka
echo "资源采样报告：$REPORT_DIR"

bash "$SCRIPT_DIR/k6-bench-restore.sh" >/dev/null
RESTORE_DONE=1
