#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/lib/common.sh"

load_env

command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }
if ! command -v sha256sum >/dev/null && ! command -v shasum >/dev/null; then
  echo "sha256sum or shasum is required" >&2
  exit 1
fi

USER_RANGE_MAX=${USER_RANGE_MAX:-20000}
SESSION_TTL_SECONDS=${SESSION_TTL_SECONDS:-604800}
BENCH_POINTS=${BENCH_POINTS:-10000000}
PASSWORD_HASH=${PASSWORD_HASH:-'$2a$10$fBeV768zLk6uXsgo3qJ9.epYnyamSrcbthxG32DeId6jxoMiIG8IO'}

echo "[seed-users] ensure bench users u0001..u$USER_RANGE_MAX"
mysql_exec <<SQL
DROP TEMPORARY TABLE IF EXISTS bench_seq;
CREATE TEMPORARY TABLE bench_seq AS
SELECT @row := @row + 1 AS n
FROM information_schema.COLUMNS c1
CROSS JOIN information_schema.COLUMNS c2
CROSS JOIN (SELECT @row := 0) r
LIMIT $USER_RANGE_MAX;

INSERT INTO t_user (username, password, nickname, points)
SELECT
  CASE WHEN n < 10000 THEN CONCAT('u', LPAD(n, 4, '0')) ELSE CONCAT('u', n) END,
  '$PASSWORD_HASH',
  CONCAT('压测用户', n),
  $BENCH_POINTS
FROM bench_seq
ON DUPLICATE KEY UPDATE
  password = VALUES(password),
  nickname = VALUES(nickname);

INSERT INTO t_point_log (user_id, delta, balance_after, biz_type, biz_ref)
SELECT u.id, $BENCH_POINTS, $BENCH_POINTS, 1, ''
FROM t_user u
LEFT JOIN t_point_log p
  ON p.user_id = u.id
 AND p.biz_type = 1
 AND p.biz_ref = ''
WHERE u.username REGEXP '^u[0-9]+$'
  AND CAST(SUBSTRING(u.username, 2) AS UNSIGNED) BETWEEN 1 AND $USER_RANGE_MAX
  AND p.id IS NULL;
SQL

mysql_exec -N -B <<SQL > "$BENCH_USER_FILE"
SELECT id, username
FROM t_user
WHERE username REGEXP '^u[0-9]+$'
  AND CAST(SUBSTRING(username, 2) AS UNSIGNED) BETWEEN 1 AND $USER_RANGE_MAX
ORDER BY CAST(SUBSTRING(username, 2) AS UNSIGNED);
SQL

actual_users=$(wc -l < "$BENCH_USER_FILE" | tr -d ' ')
if [ "$actual_users" -ne "$USER_RANGE_MAX" ]; then
  echo "[seed-users] expected $USER_RANGE_MAX users, got $actual_users" >&2
  exit 1
fi

b64url() {
  openssl base64 -A | tr '+/' '-_' | tr -d '='
}

redis_bulk() {
  local value=$1
  printf '$%s\r\n%s\r\n' "${#value}" "$value"
}

redis_setex() {
  local key=$1
  local ttl=$2
  local value=$3
  printf '*4\r\n'
  redis_bulk SETEX
  redis_bulk "$key"
  redis_bulk "$ttl"
  redis_bulk "$value"
}

calc_token_hash() {
  if command -v sha256sum >/dev/null; then
    printf '%s' "$1" | sha256sum | awk '{print substr($1,1,32)}'
  else
    printf '%s' "$1" | shasum -a 256 | awk '{print substr($1,1,32)}'
  fi
}

header=$(printf '{"alg":"HS256"}' | b64url)
now=$(date +%s)
exp=$((now + SESSION_TTL_SECONDS))
pipe_file=$(mktemp /tmp/shanpu-redis-sessions.XXXXXX)
trap 'rm -f "$pipe_file"' EXIT
: > "$TOKEN_FILE"
: > "$pipe_file"

while IFS=$'\t' read -r uid username; do
  payload=$(printf '{"uid":%s,"uname":"%s","iat":%s,"exp":%s}' "$uid" "$username" "$now" "$exp" | b64url)
  signature=$(printf '%s' "$header.$payload" | openssl dgst -sha256 -hmac "$JWT_SECRET" -binary | b64url)
  token="$header.$payload.$signature"
  token_hash=$(calc_token_hash "$token")
  printf '%s\n' "$token" >> "$TOKEN_FILE"
  redis_setex "shanpu:user:session:$token_hash" "$SESSION_TTL_SECONDS" "$uid" >> "$pipe_file"
done < "$BENCH_USER_FILE"

compose exec -T redis redis-cli -a "$REDIS_PASSWORD" --pipe < "$pipe_file" >/dev/null

echo "[seed-users] tokens=$TOKEN_FILE users=$actual_users sessions=$actual_users"
