# 快速启动

```bash
cp .env.example .env       # 按需修改密码与 JWT_SECRET
docker compose up -d
```

约 90 秒后 `docker compose ps` 全部 `(healthy)`，浏览器访问 [http://localhost:3000](http://localhost:3000)，使用测试账号 `summer` / `123456` 登录，或注册账号后完成浏览商品、用积分下单、查看订单、查看个人中心、登出。

停止容器：

```bash
docker compose down
```

清空 MySQL / Redis 数据卷并重新执行 `01_schema.sql` / `02_seed.sql`（重建表结构、测试用户与商品种子）：

```bash
docker compose down -v
docker compose up -d
```

## M4 手工验收

以下命令在仓库根目录执行，依赖 `curl` 与 `jq`：

```bash
set -a
. ./.env
set +a
API="http://localhost:${BACKEND_PORT:-8080}/api"
```

### 1. 启动与健康检查

```bash
docker compose up -d --build
docker compose ps
curl -s "$API/health" | jq
```

验收点：

- `docker compose ps` 中 mysql、redis、kafka、backend、frontend 均为 healthy。
- `/api/health` 返回 `mysql=UP`、`redis=UP`、`kafka=UP`。

### 2. 三档抢购链路

```bash
TOKEN=$(curl -s -X POST "$API/users/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"summer","password":"123456"}' | jq -r '.data.token')

curl -s -X POST "$API/seckills/1001/grab" \
  -H "Authorization: Bearer $TOKEN" | jq

curl -s -X POST "$API/seckills/2001/grab" \
  -H "Authorization: Bearer $TOKEN" | jq

SUPER_RESP=$(curl -s -X POST "$API/seckills/3001/grab" \
  -H "Authorization: Bearer $TOKEN")
echo "$SUPER_RESP" | jq
SUPER_ORDER_ID=$(echo "$SUPER_RESP" | jq -r '.data.orderId')
sleep 5
curl -s "$API/orders/$SUPER_ORDER_ID" \
  -H "Authorization: Bearer $TOKEN" | jq
```

验收点：

- `1001` 普惠档返回 `status=PAID`。
- `2001` 限时档返回 `status=PAID`。
- `3001` 超级档先返回 `status=PENDING`，Kafka 消费后订单详情返回 `status=PAID`。

### 3. 售罄、重复购买与数据一致性

重复购买：

```bash
curl -s -X POST "$API/seckills/1001/grab" \
  -H "Authorization: Bearer $TOKEN" | jq
```

验收点：响应 `code=40900`。

售罄：

```bash
docker compose exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" \
  -e "UPDATE t_seckill_activity SET total_stock=0 WHERE id=2002;"
docker compose exec -T redis redis-cli -a "$REDIS_PASSWORD" DEL \
  shanpu:seckill:activity:2002 shanpu:seckill:stock:2002 shanpu:seckill:bought:2002
docker compose exec -T redis redis-cli -a "$REDIS_PASSWORD" SET shanpu:seckill:stock:2002 0

curl -s -X POST "$API/seckills/2002/grab" \
  -H "Authorization: Bearer $TOKEN" | jq
```

验收点：响应 `code=41000`。

数据一致性：

```bash
docker compose exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" \
  -e "SELECT activity_id, COUNT(*) paid_orders FROM t_order WHERE activity_id IN (1001,2001,3001) AND status=2 GROUP BY activity_id;
      SELECT COUNT(*) consume_logs FROM t_point_log WHERE biz_type=2;
      SELECT id,total_stock FROM t_seckill_activity WHERE id IN (1001,2001,3001);"
```

验收点：成功订单数、扣分流水数与活动库存扣减一致。

### 4. DLQ 故障注入与回投

```bash
DLQ_USER="dlq_$(date +%s)"
curl -s -X POST "$API/users/register" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$DLQ_USER\",\"password\":\"123456\",\"nickname\":\"$DLQ_USER\"}" | jq
DLQ_TOKEN=$(curl -s -X POST "$API/users/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$DLQ_USER\",\"password\":\"123456\"}" | jq -r '.data.token')

docker compose exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" \
  -e "UPDATE t_user SET points=0 WHERE username='$DLQ_USER';"

DLQ_RESP=$(curl -s -X POST "$API/seckills/3001/grab" \
  -H "Authorization: Bearer $DLQ_TOKEN")
echo "$DLQ_RESP" | jq
DLQ_ORDER_ID=$(echo "$DLQ_RESP" | jq -r '.data.orderId')

sleep 8
docker compose exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" \
  -e "SELECT order_id,fail_count,status,last_failed_at FROM t_order_dlq_log WHERE order_id='$DLQ_ORDER_ID';"

docker compose exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" \
  -e "UPDATE t_user SET points=10000 WHERE username='$DLQ_USER';"

sleep 70
docker compose exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" \
  -e "SELECT order_id,status,republished_at FROM t_order_dlq_log WHERE order_id='$DLQ_ORDER_ID';
      SELECT order_id,status FROM t_order WHERE order_id='$DLQ_ORDER_ID';"
```

验收点：消费失败后 `t_order_dlq_log.status=0`，积分补足后 `KafkaDlqRescueJob` 回投，DLQ 记录变为 `status=1`，订单落库且 `t_order.status=2`。

### 5. 档位热切换

切换到超级档：

```bash
docker compose exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" \
  -e "UPDATE t_seckill_activity SET tier=3 WHERE id=1002;"
STOCK_1002=$(docker compose exec -T mysql mysql -N -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" \
  -e "SELECT total_stock FROM t_seckill_activity WHERE id=1002;")
docker compose exec -T redis redis-cli -a "$REDIS_PASSWORD" DEL \
  shanpu:seckill:activity:1002 shanpu:seckill:stock:1002 shanpu:seckill:bought:1002
docker compose exec -T redis redis-cli -a "$REDIS_PASSWORD" SET shanpu:seckill:stock:1002 "$STOCK_1002"

curl -s -X POST "$API/seckills/1002/grab" \
  -H "Authorization: Bearer $TOKEN" | jq
```

验收点：响应 `tier=3` 且 `status=PENDING`。

切换到普惠档：

```bash
HOT_USER="hot_$(date +%s)"
curl -s -X POST "$API/users/register" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$HOT_USER\",\"password\":\"123456\",\"nickname\":\"$HOT_USER\"}" | jq
HOT_TOKEN=$(curl -s -X POST "$API/users/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$HOT_USER\",\"password\":\"123456\"}" | jq -r '.data.token')

docker compose exec -T mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE" \
  -e "UPDATE t_seckill_activity SET tier=1 WHERE id=1002;"
docker compose exec -T redis redis-cli -a "$REDIS_PASSWORD" DEL \
  shanpu:seckill:activity:1002 shanpu:seckill:stock:1002 shanpu:seckill:bought:1002

curl -s -X POST "$API/seckills/1002/grab" \
  -H "Authorization: Bearer $HOT_TOKEN" | jq
```

验收点：响应 `tier=1` 且 `status=PAID`。
