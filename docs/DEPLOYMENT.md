# 闪铺(shanpu) 部署与压测

## 1. 仓库顶层布局

```text
shanpu/
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── .sdkmanrc
├── docker-compose.yml
├── .env.example
├── backend/                 # Spring Boot 工程（详见 BACKEND.md）
├── frontend/                # Next.js 工程（详见 FRONTEND.md）
├── mysql/
│   └── init/
│       ├── 01_schema.sql      # DDL，自动执行
│       └── 02_seed.sql        # 种子数据
└── docs/
    ├── BACKEND.md
    ├── FRONTEND.md
    ├── QUICKSTART.md
    ├── DEPLOYMENT.md
    └── ROADMAP.md
```

## 2. Docker Compose 服务清单

根目录 `docker-compose.yml` 定义 5 个服务。每个服务通过 `deploy.resources.limits` 显式声明 CPU 与内存上限（见 §2.4 设计逻辑）。下表"暴露端口"列引用 `.env` 变量，默认值见 §2.3。

| 服务 | 镜像 | 内部端口 | 暴露端口 | CPU 上限 | 内存上限 | 卷 | 健康检查 |
|---|---|---|---|---|---|---|---|
| `mysql` | `mysql:8.0` | 3306 | `${MYSQL_PORT}` | 1.5 | 1g | `./mysql/init:/docker-entrypoint-initdb.d:ro`、`mysql-data:/var/lib/mysql` | `mysqladmin ping -p$MYSQL_ROOT_PASSWORD` |
| `redis` | `redis:7-alpine` | 6379 | `${REDIS_PORT}` | 1.0 | 384m | `redis-data:/data` | `redis-cli -a $REDIS_PASSWORD ping` |
| `kafka` | `apache/kafka:3.7.2` | 9092 | `${KAFKA_PORT}` | 1.0 | 768m | `kafka-data:/var/lib/kafka/data` | `/opt/kafka/bin/kafka-topics.sh --bootstrap-server 127.0.0.1:9092 --list` |
| `backend` | 本仓 `backend/Dockerfile` | 8080 | `${BACKEND_PORT}` | 2.0 | 1.5g | - | `wget -q --spider http://127.0.0.1:8080/api/health` |
| `frontend` | 本仓 `frontend/Dockerfile` | 3000 | `${FRONTEND_PORT}` | 0.5 | 384m | - | `wget -q --spider http://127.0.0.1:3000/` |

### 2.1 依赖关系

`backend.depends_on`：

```yaml
depends_on:
  mysql:
    condition: service_healthy
  redis:
    condition: service_healthy
  kafka:
    condition: service_healthy
```

`frontend.depends_on`：

```yaml
depends_on:
  backend:
    condition: service_healthy
```

### 2.2 MySQL / Redis 命令参数

MySQL 调优通过 `docker-compose.yml` 的 `mysql.command` 维护：

```yaml
command:
  - --innodb-buffer-pool-size=384M
  - --max-connections=200
  - --innodb-log-buffer-size=16M
  - --innodb-flush-log-at-trx-commit=2
  - --character-set-server=utf8mb4
  - --collation-server=utf8mb4_0900_ai_ci
  - --default-time-zone=+08:00
```

`--default-time-zone=+08:00` 与 backend 容器 `TZ=Asia/Shanghai` 统一为东八区，保证 DB 写入的 `created_at` 与应用写入的 `paid_at` 时区一致。

Redis 调优通过 `docker-compose.yml` 的 `redis.command` 维护：

```yaml
command:
  - redis-server
  - --requirepass
  - ${REDIS_PASSWORD}
  - --maxmemory
  - 256mb
  - --maxmemory-policy
  - allkeys-lru
  - --save
  - ""
  - --appendonly
  - "no"
```

### 2.3 环境变量集中维护

根目录 `.env.example` 提供模板，部署时拷贝为 `.env`：

```bash
# ===== 凭据 =====
MYSQL_ROOT_PASSWORD=123456
MYSQL_DATABASE=shanpu
REDIS_PASSWORD=123456
JWT_SECRET=replace-me-with-a-32-byte-random-secret-for-development

# ===== 暴露端口 =====
MYSQL_PORT=3306
REDIS_PORT=6379
KAFKA_PORT=9092
BACKEND_PORT=8080
FRONTEND_PORT=3000

# ===== 前端浏览器侧调用后端的主机名 =====
PUBLIC_API_HOST=localhost
```

`.env.example` 只承载用户必须感知的三类：凭据、对外暴露端口、前端浏览器侧主机名。以下配置由 `docker-compose.yml` 派生：

- **Compose 内部地址不进 `.env`**：backend 连 `mysql:3306` / `redis:6379` / `kafka:9092`，frontend SSR 连 `http://backend:8080/api`，全部由 `docker-compose.yml` 在容器 `environment:` 段直接硬编码。这些值由 Compose 网络内的 service name 与内部端口决定，用户无须也不应调整。
- **后端容器以 root 账号连库**：`docker-compose.yml` 在 backend 容器 `environment:` 段拼装 `MYSQL_USERNAME=root` / `MYSQL_PASSWORD=${MYSQL_ROOT_PASSWORD}` 注入 Spring；mysql 镜像不创建额外应用账号，root 承担初始化、健康检查与应用连接三件事，配置最小化便于教学。
- **Redis 密码强制开启**：`redis.command` 含 `--requirepass ${REDIS_PASSWORD}`（详见 §5.2），后端容器 `REDIS_PASSWORD` 通过 `${REDIS_PASSWORD}` 注入，Redis 健康检查命令为 `redis-cli -a $REDIS_PASSWORD ping`。
- **前端 BASE_URL 运行时拼接**：浏览器侧调用后端的完整地址 `http://${PUBLIC_API_HOST}:${BACKEND_PORT}/api` 由 `docker-compose.yml` 在 frontend 容器 `environment:` 段拼接后作为运行时变量 `PUBLIC_API_BASE_URL` 注入。前端根 layout 作为 server component 读取 `process.env.PUBLIC_API_BASE_URL`，注入到 HTML 供 client 端 hydrate 时消费（详见 FRONTEND.md §7.3）。**不走 Next.js build-time `NEXT_PUBLIC_*` 通道**，用户改 `PUBLIC_API_HOST` 或 `BACKEND_PORT` 后只需 `docker compose up -d frontend` 重启容器即可生效，无需重 build 镜像。

### 2.4 资源限制设计

`docker-compose.yml` 显式声明每服务 `deploy.resources.limits`，以 7.6 GiB WSL2 单机为基线，全栈总和 ≈ 3.3 GiB，留 ≥ 2.5 GiB 给宿主与压测突发：

| 服务 | CPU | 内存 | 内部硬约束 |
|---|---|---|---|
| `backend` | 2.0 | 1.5g | `JAVA_TOOL_OPTIONS=-Xmx1g -Xms512m -XX:MaxMetaspaceSize=128m` 留 25% 余量避 cgroup OOM Kill；Tomcat `server.tomcat.threads.max=100` |
| `frontend` | 0.5 | 384m | Next.js standalone SSR，压测期间几乎纯静态响应 |
| `mysql` | 1.5 | 1g | `innodb_buffer_pool_size=384M`、`max_connections=200`、`innodb_log_buffer_size=16M` |
| `redis` | 1.0 | 384m | `maxmemory 256mb`、`maxmemory-policy allkeys-lru`、`save ""` |
| `kafka` | 1.0 | 768m | 单节点 KRaft，内部 listener `kafka:9092`，宿主调试端口 `${KAFKA_PORT}` |
| 合计 | 6.0 | 4.1g | 留 ≥ 2 GiB 内存给宿主 |

按机器规模缩放指引：
- **内存 ≥ 16 GiB**：可把 `backend` 提到 3g（`-Xmx2g`）、`mysql` 提到 2g（buffer pool 1g）。
- **内存 ≥ 32 GiB**：`backend` 提到 4g、`mysql` 提到 4g（buffer pool 2g）。
- **CPU 核心紧张（< 8 核）**：优先保持 `backend` 2.0 CPU，MySQL 可下调到 1.0 CPU。

## 3. 后端 Dockerfile

多阶段构建，最终镜像仅含 JRE。

```dockerfile
# ===== build =====
FROM maven:3.9-eclipse-temurin-17 AS build
WORKDIR /workspace
COPY pom.xml .
RUN mvn -B -DskipTests dependency:go-offline
COPY src ./src
RUN mvn -B -DskipTests package

# ===== runtime =====
FROM eclipse-temurin:17-jre-alpine
WORKDIR /app
COPY --from=build /workspace/target/*.jar app.jar
EXPOSE 8080
ENV JAVA_OPTS="-XX:+UseG1GC"
ENV JAVA_TOOL_OPTIONS="-Xmx1g -Xms512m -XX:MaxMetaspaceSize=128m"
ENV SPRING_PROFILES_ACTIVE=prod
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /app/app.jar"]
```

镜像大小约 220 MB。`JAVA_TOOL_OPTIONS` 显式锁定堆与 Metaspace，避免 JVM 自适应突破 cgroup 内存上限被 OOM Kill；运行时可通过 compose `environment` 覆盖以适配更大机器。

## 4. 前端 Dockerfile

利用 Next.js `output: 'standalone'` 产出最小运行时。

```dockerfile
# ===== deps =====
FROM node:22-alpine AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm config set minimumReleaseAge 0 \
 && pnpm config set strictDepBuilds false \
 && pnpm config set verifyDepsBeforeRun false \
 && pnpm install --frozen-lockfile

# ===== build =====
FROM node:22-alpine AS build
WORKDIR /app
RUN corepack enable \
 && pnpm config set verifyDepsBeforeRun false
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN pnpm build

# ===== runtime =====
FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
```

`deps` 阶段三项 `pnpm config set` 关闭 pnpm 11 默认开启的"最小发布年龄"、"严格依赖构建脚本"与"运行前 deps 校验"三条策略——容器构建场景下 lockfile 已在本地验证过，无需在 CI 镜像内重复审计；`build` 阶段同样关闭 `verifyDepsBeforeRun`，避免 `pnpm build` 触发隐式 `pnpm install`。

构建期仅烤进与部署无关的 `NEXT_PUBLIC_*` 静态项（站点名、轮询间隔等）。后端调用地址 `PUBLIC_API_BASE_URL` 与 `INTERNAL_API_BASE_URL` 走运行时 `environment:` 注入，由 server component 在每次请求时读取后嵌入 HTML，机制详见 [FRONTEND.md §7.3](./FRONTEND.md)。

镜像大小约 160 MB。

## 5. 初始化挂载

### 5.1 MySQL

`mysql/init/` 下两个脚本由 `mysql:8.0` 镜像在首次启动时自动执行：

- `01_schema.sql`：DDL（与 `docs/BACKEND.md` 第 10 节一致），创建用户、商品、秒杀活动、订单、积分流水与 DLQ 失败日志及回投状态表。
- `02_seed.sql`：写入测试用户 `summer`（密码 `123456`，初始积分 10000，带注册赠送积分流水）、5 个普通商品种子数据（`id` 1~5、`status=1`，其中一个商品库存为 10 供并发防超卖验收）与 9 个三档秒杀活动（`1001/1002/1003`、`2001/2002/2003`、`3001/3002/3003`）。

两个脚本均以 `SET NAMES utf8mb4;` 开头：`mysql:8.0` 镜像内的 `mysql` 客户端默认 `character_set_client=latin1`，`docker-entrypoint-initdb.d` 按该默认连接导入 UTF-8 脚本，缺少显式声明会把中文按 latin1 双重编码后落库。`SET NAMES utf8mb4` 将导入会话的 client / connection / results 字符集对齐到 utf8mb4，保证种子数据与列注释中的中文按原字节写入。

MySQL 运行参数写在 `docker-compose.yml` 的 `mysql.command`：

```yaml
command:
  - --innodb-buffer-pool-size=384M
  - --max-connections=200
  - --innodb-log-buffer-size=16M
  - --innodb-flush-log-at-trx-commit=2
  - --character-set-server=utf8mb4
  - --collation-server=utf8mb4_0900_ai_ci
  - --default-time-zone=+08:00
```

### 5.2 Redis

Redis 运行参数写在 `docker-compose.yml` 的 `redis.command`：

```yaml
command:
  - redis-server
  - --requirepass
  - ${REDIS_PASSWORD}
  - --maxmemory
  - 256mb
  - --maxmemory-policy
  - allkeys-lru
  - --save
  - ""
  - --appendonly
  - "no"
```

`${REDIS_PASSWORD}` 由 Compose 变量插值注入 Redis 启动命令。后端 `REDIS_PASSWORD` 与健康检查 `redis-cli -a $REDIS_PASSWORD ping` 使用同一变量，三处保持一致。

## 6. 一键启动流程

```bash
# 1. 准备环境变量
cp .env.example .env
# 按需修改 .env 中密码与 JWT_SECRET

# 2. 拉起全栈
docker compose up -d

# 3. 等待健康检查通过（约 60~90 秒）
docker compose ps

# 4. 浏览器访问
open http://localhost:3000

# 5. 使用测试账号 summer / 123456 登录，或通过注册接口创建账号
```

### 6.1 停止与重置

```bash
docker compose down            # 停止容器，保留数据卷
docker compose down -v         # 同时清空 MySQL / Redis 数据
```

`down -v` 后再次 `up -d` 会重新跑 `01_schema.sql` 与 `02_seed.sql`，得到干净环境。

## 7. k6 压测：三档秒杀横向对比

### 7.1 目标

在 7.6 GiB WSL2 单机基础设施下，三档秒杀活动同走 `/api/seckills/{id}/grab` 入口，由 `SeckillService.grab(activityId)` 按 `activity.tier` 自动路由 `grabInclusive` / `grabFlash` / `grabSuper`。A/B/C 三场景采用固定 `100 VU / 30s` 的统一压力模型，量化成功请求链路的平均 RPS 与 p50/p95/p99，定位瓶颈位置（DB 行锁 / Redis CPU / Kafka producer ack）。正确性由 M4 验收覆盖，本章节保留压测后的超卖断言。

场景 D 缓存层级对比与三档秒杀压测正交，独立执行（详见 §7.6 / §7.8 与 ROADMAP M6 缓存层级对比表）。

### 7.2 场景

三场景同接口同 setup，仅替换 `__ENV.ACTIVITY_ID`：

| 场景 | 接口 | tier | 路由分支 | 链路 | 压力模型 | 预期瓶颈 |
|---|---|---|---|---|---|---|
| A 普惠 | `POST /api/seckills/1001/grab` | 1 | `grabInclusive` | MySQL 行锁 + 同步事务 | 100 VU / 30s | DB 单行 `id=1001` 行锁串行化 |
| B 限时 | `POST /api/seckills/2001/grab` | 2 | `grabFlash` | Redis+Lua → 同步事务 | 100 VU / 30s | DB 行锁（Redis 减负后落库仍同步） |
| C 超级 | `POST /api/seckills/3001/grab` | 3 | `grabSuper` | Redis+Lua → Kafka 同步 `send.get(3s)` → 异步消费 | 100 VU / 30s | Kafka producer send ack |

每轮压测前由 `scripts/seed-users.sh` 确保 `u0001` ~ `u20000` 压测用户存在，由 `scripts/k6-bench-reset.sh`（详见 §7.9）把目标活动 `total_stock` 重置为 **20000**，清理 20000 压测用户在目标活动的旧订单与积分日志，并补足用户积分。20000 用户与 20000 库存覆盖 C 场景预估 `350 RPS × 30s ≈ 10500` 成功请求并保留余量，避免一人一单与库存售罄把正式样本切入业务失败快速路径。压测后由 `scripts/k6-bench-restore.sh` 恢复种子库存。

统一 VU 峰值 100 取自 backend Tomcat 100 线程上限，三档共享同一压力刻度，RT 与 RPS 可 apples-to-apples 直接横向对比。固定 100 VU 是正式报告口径；容量探底使用独立脚本按更高 VU 运行，并归档到独立报告目录。

### 7.3 通用 setup：批量登录

`load/lib/auth.js`：

```js
import http from 'k6/http';
import { check } from 'k6';

// 20000 用户串行登录在 setup 阶段一次性完成，运行期复用 token。
export function loginAll(baseUrl, prefix = 'u', count = 20000, password = 'pwd123456') {
  const tokens = [];
  for (let i = 1; i <= count; i++) {
    const username = `${prefix}${String(i).padStart(4, '0')}`;
    const res = http.post(`${baseUrl}/users/login`,
      JSON.stringify({ username, password }),
      { headers: { 'Content-Type': 'application/json' } });
    check(res, { 'login 200': r => r.status === 200 });
    tokens.push(res.json('data.token'));
  }
  return tokens;
}
```

### 7.4 三个脚本骨架与固定 100 VU

```js
// load/scenario-a-inclusive.js（普惠档,activity_id=1001,tier=1）
import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';
import { Counter, Trend } from 'k6/metrics';
import { loginAll } from './lib/auth.js';

export const options = {
  scenarios: {
    fixed_100vu: {
      executor: 'constant-vus',
      vus: 100,
      duration: '30s',
      gracefulStop: '5s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    success_req_duration: ['p(99)<2000'],
  },
};

const orderOk = new Counter('order_success_total');
const orderFail = new Counter('order_fail_total');
const dupCnt = new Counter('seckill_duplicate_total');
const soldOut = new Counter('seckill_sold_out_total');
const successDuration = new Trend('success_req_duration');

export function setup() {
  const base = __ENV.API_BASE || 'http://backend:8080/api';
  return { base, tokens: loginAll(base) };
}

export default function (data) {
  const idx = exec.scenario.iterationInTest;
  if (idx >= data.tokens.length) {
    throw new Error(`token pool exhausted: idx=${idx}, size=${data.tokens.length}`);
  }

  const token = data.tokens[idx];
  const activityId = __ENV.ACTIVITY_ID || '1001';
  const res = http.post(`${data.base}/seckills/${activityId}/grab`, '{}', {
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
  });
  const body = res.json();
  if (res.status === 200 && body.code === 0) {
    orderOk.add(1);
    successDuration.add(res.timings.duration);
  } else if (res.status === 200 && body.code === 40900) {
    dupCnt.add(1);
    orderFail.add(1);
  } else if (res.status === 200 && body.code === 41000) {
    soldOut.add(1);
    orderFail.add(1);
  } else {
    orderFail.add(1);
  }
  check(res, { '200 status': r => r.status === 200 });
}
```

场景 B 与 C 复用同一份骨架，默认 `ACTIVITY_ID` 分别为 `2001` 与 `3001`。C 场景中 `body.code === 0 && body.data.status === 'PENDING'` 计入 `order_success_total` 与 `success_req_duration`，因为超级档接口成功语义是完成 Redis 预扣与 Kafka 发送确认，DB 落库由消费端异步完成。

A/B/C 三场景均以固定 `100 VU / 30s` 的 `success_req_duration` 为正式延迟口径。`http_req_duration` 保留为 HTTP 全量观测指标，`BACKEND.md` 中成功链路 p99 估算的填表来源为 `success_req_duration`。

### 7.5 自定义指标

| 指标名 | 类型 | 含义 |
|---|---|---|
| `success_req_duration` | Trend | `code == 0` 的成功请求耗时；C 场景包含 `PENDING` 成功响应 |
| `order_success_total` | Counter | `code == 0` 的成功请求数；C 场景包含 `PENDING` 成功响应 |
| `order_fail_total` | Counter | 业务失败请求数（库存不足、一人一单冲突等） |
| `seckill_duplicate_total` | Counter | `code == 40900` 计数（一人一单冲突） |
| `seckill_sold_out_total` | Counter | `code == 41000` 计数（库存售罄） |

`http_req_failed` 仅统计 HTTP 5xx/超时，业务错误码（40900/41000）由 `seckill_duplicate_total` / `seckill_sold_out_total` 独立计数。`check(res, { '200 status': r => r.status === 200 })` 仅校验 HTTP 层成功。正式横向对比使用 `success_req_duration` 与 `order_success_total`，业务失败指标用于确认压测数据未被重复购买或售罄路径污染。

可选 Trend 指标（若后端在响应 header 回写 `X-Kafka-Send-Ms` 与 `X-Db-Wait-Ms`，则 k6 用 `import { Trend } from 'k6/metrics'` 单独收集）：

| 指标名 | 类型 | 适用场景 |
|---|---|---|
| `kafka_send_duration` | Trend | C 场景：Kafka `send().get()` 同步等待 ack 耗时 |
| `db_row_lock_wait_ms` | Trend | A/B 场景：MySQL 行锁等待耗时 |

调试 header 缺省时，压测中执行 `docker stats backend` 与 `redis-cli INFO commandstats` 差分人工记录；k6 自定义 Trend 采集作为增强指标。

### 7.6 输出与报告

`scripts/k6-run.sh` 串跑三档秒杀场景 + 场景 D 缓存对比，并按时间戳归档：

```bash
#!/usr/bin/env bash
set -e
TS=$(date +%Y%m%d-%H%M%S)
REPORT_DIR=load/reports/$TS
mkdir -p "$REPORT_DIR"

bash scripts/seed-users.sh

# 三档秒杀横向对比
for SCENARIO in a-inclusive:1001 b-redis-sync:2001 c-redis-kafka:3001; do
  NAME=${SCENARIO%:*}
  ID=${SCENARIO#*:}

  echo "[$NAME] reset stock and users for fixed 100 VU..."
  bash scripts/k6-bench-reset.sh "$ID"

  echo "[$NAME] warmup 5s..."
  sleep 5

  echo "[$NAME] run k6..."
  docker compose --profile loadtest run --rm \
    -e ACTIVITY_ID="$ID" \
    -e API_BASE="http://backend:8080/api" \
    k6 run --summary-export=/reports/$TS/scenario-$NAME.json \
           /scripts/scenario-$NAME.js

  echo "[$NAME] snapshot redis commandstats..."
  docker compose exec -T redis redis-cli INFO commandstats \
    > "$REPORT_DIR/redis-cmdstats-$NAME.txt"
done

# 场景 D 缓存层级对比(独立流程,见 ROADMAP M6 缓存层级对比表)
docker compose --profile loadtest run --rm k6 \
  run --summary-export=/reports/$TS/scenario-d-cache-comparison.json \
      /scripts/scenario-d-cache-comparison.js

# 恢复种子库存
bash scripts/k6-bench-restore.sh

echo "Report: $REPORT_DIR"
```

每轮归档目录结构：

```
load/reports/<timestamp>/
├── scenario-a-inclusive.json         # k6 summary export
├── scenario-b-redis-sync.json
├── scenario-c-redis-kafka.json
├── scenario-d-cache-comparison.json
├── redis-cmdstats-a-inclusive.txt    # redis-cli INFO commandstats 抓拍
├── redis-cmdstats-b-redis-sync.txt
├── redis-cmdstats-c-redis-kafka.txt
├── docker-stats-peak.txt             # 高峰期 docker stats 抓拍
├── kafka-consumer-lag.txt            # C 场景 lag 每 10s 抓一次
└── README.md                         # 硬件配置、固定 100 VU 参数、瓶颈观察
```

### 7.7 超卖检测

每场景结束后通过 MySQL 客户端验证：

```sql
-- 场景 A 普惠档（source=2 对应 tier=1）
SELECT COUNT(*) FROM t_order WHERE source = 2 AND activity_id = 1001;
-- 场景 B 限时档（source=3 对应 tier=2）
SELECT COUNT(*) FROM t_order WHERE source = 3 AND activity_id = 2001;
-- 场景 C 超级档（source=4 对应 tier=3）
SELECT COUNT(*) FROM t_order WHERE source = 4 AND activity_id = 3001;
```

三条结果均应 `≤ 20000`（即 `bench-reset` 注入的压测库存上限），绝不超出；若超出，判定为超卖事故，需排查 Lua 脚本或 DB 唯一索引。C 场景压测结束后等待 Kafka 消费完成，再验证超级档订单数与积分流水数一致。

注：20000 为正式 A/B/C 横向压测用户池与临时库存上限，与种子库存（5000/500/200）解耦，避免污染 M4 业务验收语义。压测前 `bench-reset` 注入、压测后 `bench-restore` 恢复。

### 7.8 对比报告

`docs/ROADMAP.md` M6 章节附压测对比表，由人工填入实测值。统一记录固定 VU、持续时间、成功请求平均 RPS、`success_req_duration` p50/p95/p99、HTTP 错误率、业务错误率、成功订单数、是否超卖与瓶颈观察。瓶颈观察通过压测高峰期 `docker stats`、`redis-cli INFO commandstats` 差分、`kafka-consumer-groups.sh --describe --group shanpu-seckill-consumer` lag 三处采集。

### 7.9 压测前置脚本

#### `scripts/seed-users.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

USER_RANGE_MAX=${USER_RANGE_MAX:-20000}
PASSWORD_HASH='$2a$10$7RkPGUSAzrJ8nNu2TK4dAOdGrsLYbMQIkxsRiz2EnKys2YyOVjw8S'
DB_USER=root
DB_PASS=${MYSQL_ROOT_PASSWORD}
DB_NAME=${MYSQL_DATABASE:-shanpu}

docker compose exec -T mysql mysql -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" <<SQL
SET SESSION cte_max_recursion_depth = $USER_RANGE_MAX;
WITH RECURSIVE seq(n) AS (
  SELECT 1
  UNION ALL
  SELECT n + 1 FROM seq WHERE n < $USER_RANGE_MAX
)
INSERT IGNORE INTO t_user (username, password, nickname, points)
SELECT CONCAT('u', IF(n < 10000, LPAD(n, 4, '0'), n)), '$PASSWORD_HASH', CONCAT('压测用户', n), 10000000
FROM seq;
SQL

echo "[seed-users] done: users=1..$USER_RANGE_MAX"
```

#### `scripts/k6-bench-reset.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

ACTIVITY_IDS=${1:-"1001 2001 3001"}
PRESSURE_STOCK=${PRESSURE_STOCK:-20000}
USER_RANGE_MAX=${USER_RANGE_MAX:-20000}
DB_USER=root
DB_PASS=${MYSQL_ROOT_PASSWORD}
DB_NAME=${MYSQL_DATABASE:-shanpu}

# 1. MySQL 重置库存 / 激活活动 / 清旧订单
for ID in $ACTIVITY_IDS; do
  docker compose exec -T mysql mysql -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" <<SQL
UPDATE t_seckill_activity
SET total_stock = $PRESSURE_STOCK, status = 1,
    start_at = NOW() - INTERVAL 1 HOUR,
    end_at   = NOW() + INTERVAL 6 HOUR
WHERE id = $ID;
DELETE o FROM t_order o
JOIN t_user u ON u.id = o.user_id
WHERE o.activity_id = $ID
  AND u.username REGEXP '^u[0-9]{4,5}\$'
  AND CAST(SUBSTRING(u.username, 2) AS UNSIGNED) BETWEEN 1 AND $USER_RANGE_MAX;
SQL
done

# 2. 压测用户积分补足(避免 POINTS_NOT_ENOUGH 干扰)
docker compose exec -T mysql mysql -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" <<SQL
UPDATE t_user SET points = 10000000
WHERE username REGEXP '^u[0-9]{4,5}\$'
  AND CAST(SUBSTRING(username, 2) AS UNSIGNED) BETWEEN 1 AND $USER_RANGE_MAX;
DELETE p FROM t_point_log p
JOIN t_user u ON u.id = p.user_id
WHERE u.username REGEXP '^u[0-9]{4,5}\$'
  AND CAST(SUBSTRING(u.username, 2) AS UNSIGNED) BETWEEN 1 AND $USER_RANGE_MAX
  AND p.biz_type IN (2, 3);
SQL

# 3. Redis 重置(普惠档 tier=1 不写库存,跳过)
for ID in $ACTIVITY_IDS; do
  if [ "$ID" != "1001" ]; then
    docker compose exec -T redis redis-cli DEL "shanpu:seckill:stock:$ID" "shanpu:seckill:bought:$ID"
    docker compose exec -T redis redis-cli SET "shanpu:seckill:stock:$ID" "$PRESSURE_STOCK"
  fi
  docker compose exec -T redis redis-cli DEL "shanpu:seckill:activity:$ID"
done

echo "[bench-reset] done: activities=$ACTIVITY_IDS stock=$PRESSURE_STOCK users=1..$USER_RANGE_MAX"
```

#### `scripts/k6-bench-restore.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
USER_RANGE_MAX=${USER_RANGE_MAX:-20000}
DB_USER=root
DB_PASS=${MYSQL_ROOT_PASSWORD}
DB_NAME=${MYSQL_DATABASE:-shanpu}

docker compose exec -T mysql mysql -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" <<SQL
UPDATE t_seckill_activity SET total_stock = 5000 WHERE id IN (1001, 1002, 1003);
UPDATE t_seckill_activity SET total_stock = 500  WHERE id IN (2001, 2002, 2003);
UPDATE t_seckill_activity SET total_stock = 200  WHERE id IN (3001, 3002, 3003);
DELETE o FROM t_order o
JOIN t_user u ON u.id = o.user_id
WHERE o.activity_id IN (1001, 2001, 3001)
  AND u.username REGEXP '^u[0-9]{4,5}\$'
  AND CAST(SUBSTRING(u.username, 2) AS UNSIGNED) BETWEEN 1 AND $USER_RANGE_MAX;
UPDATE t_user SET points = 10000000
WHERE username REGEXP '^u[0-9]{4,5}\$'
  AND CAST(SUBSTRING(username, 2) AS UNSIGNED) BETWEEN 1 AND $USER_RANGE_MAX;
SQL

for ID in 1001 2001 3001; do
  docker compose exec -T redis redis-cli DEL \
    "shanpu:seckill:stock:$ID" "shanpu:seckill:bought:$ID" "shanpu:seckill:activity:$ID"
done

echo "[bench-restore] seed stocks restored"
```

### 7.10 关键设计取舍

- **固定 100 VU / 30s**：取 backend Tomcat 100 线程上限作为三档共同压力刻度，30 秒窗口覆盖 C 场景预估 10500 次成功请求并控制本地压测成本。正式报告只使用固定压力下的成功请求指标，保证三档横向对比口径一致。
- **20000 用户与 20000 临时库存**：一人一单约束下，压测用户数决定成功请求上限。20000 用户与库存为 A/B/C 成功链路留出约 2 倍余量，避免压测主体被 `40900` 重复购买或 `41000` 售罄快速路径主导。临时库存仅在压测窗口生效，压测后恢复种子库存。
- **业务错误码独立计数**：`http_req_failed` 仅统计 HTTP 5xx/超时，业务码经 `seckill_duplicate_total` / `seckill_sold_out_total` 独立计数。A/B/C 正式吞吐与延迟取 `order_success_total` 与 `success_req_duration`。
- **Kafka 单节点 + RF=1**：单机宿主部署下跨节点容错无法落地，多节点配置仅徒增内存与启动 quorum 等待。消息不丢由 Producer 幂等 + 同步 `send().get(3s)` + 回滚 Lua + Consumer 幂等门 + DLQ + `StockReconcileJob` 六层兜底，无需依赖跨 broker 副本。代价：单节点 Kafka 宕机时所有超级档抢购返回 `INTERNAL_ERROR`（前台失败 + Redis 回滚），由运维介入重启容器；该形态与"宿主死亡"风险等价，是单机部署可接受的上限。

## 8. 运行环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | Linux / macOS / Windows + WSL2 |
| Docker | 24+ |
| Docker Compose | v2.20+ |
| 内存 | ≥ 8 GB 可用（全栈容器合计 ≈ 3.3 GB，按 §2.4 限制） |
| 磁盘 | ≥ 5 GB 可用 |
| 端口 | 3000（frontend）、8080（backend）、3306（mysql）、6379（redis） |

本地开发若希望直接 IDE 启动后端，可仅启 `mysql / redis` 两个依赖：

```bash
docker compose up -d mysql redis
# 然后在 IDE 中以 spring.profiles.active=local 启动 backend
# frontend 在 frontend/ 目录下 pnpm dev
```
