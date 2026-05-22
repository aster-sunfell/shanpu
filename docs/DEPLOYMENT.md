# 闪铺(shanpu) 部署与压测

## 1. 仓库顶层布局

```text
shanpu/
├── AGENTS.md
├── CLAUDE.md
├── README.md
├── .sdkmanrc
├── backend/                 # Spring Boot 工程（详见 BACKEND.md）
├── web/                     # Next.js 工程（详见 FRONTEND.md）
├── deploy/
│   ├── docker-compose.yml
│   ├── .env.example
│   ├── mysql/
│   │   ├── conf/my.cnf
│   │   └── init/
│   │       ├── 01_schema.sql      # DDL，自动执行
│   │       └── 02_seed.sql        # 种子数据
│   ├── redis/redis.conf
│   └── kafka/
│       └── server.properties      # KRaft 3 节点共享基线（每节点 NODE_ID 由 compose env 注入）
├── load/
│   ├── lib/auth.js                       # 批量登录拿 JWT
│   ├── scenario-a-inclusive.js           # 场景 A：普惠档（tier=1，DB 直连）
│   ├── scenario-b-redis-sync.js          # 场景 B：限时档（tier=2，Redis+Lua 同步）
│   ├── scenario-c-redis-kafka.js         # 场景 C：超级档（tier=3，Redis+Lua+Kafka 异步）
│   ├── scenario-d-cache-comparison.js    # 场景 D：缓存层级对比（与三档压测正交）
│   └── reports/                          # 压测产物
├── scripts/
│   ├── dev-up.sh                   # 拉起依赖（不含后端 / 前端）
│   ├── seed-users.sh               # 注入 500 个种子用户
│   ├── k6-bench-reset.sh           # 压测前置：重置库存与压测用户订单
│   ├── k6-bench-restore.sh         # 压测后置：恢复种子库存
│   └── k6-run.sh                   # 一键串跑三场景（自动调 bench-reset）
└── docs/
    ├── BACKEND.md
    ├── FRONTEND.md
    ├── DEPLOYMENT.md
    └── ROADMAP.md
```

## 2. Docker Compose 服务清单

`deploy/docker-compose.yml` 定义 8 个服务，其中 `k6` 仅在 `loadtest` profile 中拉起。每个服务通过 `deploy.resources.limits` 显式声明 CPU 与内存上限（见 §2.4 设计逻辑）。

| 服务 | 镜像 | 内部端口 | 暴露端口 | CPU 上限 | 内存上限 | 卷 | 健康检查 |
|---|---|---|---|---|---|---|---|
| `mysql` | `mysql:8.0` | 3306 | 3306 | 1.5 | 1g | `./mysql/init:/docker-entrypoint-initdb.d:ro`、`./mysql/conf/my.cnf:/etc/mysql/conf.d/my.cnf:ro`、`mysql-data:/var/lib/mysql` | `mysqladmin ping -p$MYSQL_ROOT_PASSWORD` |
| `redis` | `redis:7-alpine` | 6379 | 6379 | 1.0 | 384m | `./redis/redis.conf:/etc/redis/redis.conf:ro`、`redis-data:/data` | `redis-cli ping` |
| `kafka-1` | `bitnami/kafka:3.7` | 9092、9093 | 9094 | 0.7 | 512m | `kafka-1-data:/bitnami/kafka` | `kafka-topics.sh --bootstrap-server localhost:9092 --list` |
| `kafka-2` | `bitnami/kafka:3.7` | 9092、9093 | 9095 | 0.7 | 512m | `kafka-2-data:/bitnami/kafka` | `kafka-topics.sh --bootstrap-server localhost:9092 --list` |
| `kafka-3` | `bitnami/kafka:3.7` | 9092、9093 | 9096 | 0.7 | 512m | `kafka-3-data:/bitnami/kafka` | `kafka-topics.sh --bootstrap-server localhost:9092 --list` |
| `backend` | 本仓 `backend/Dockerfile` | 8080 | 8080 | 2.0 | 1.5g | - | `curl -sf http://localhost:8080/api/health` |
| `web` | 本仓 `web/Dockerfile` | 3000 | 3000 | 0.5 | 384m | - | `curl -sf http://localhost:3000/` |
| `k6` | `grafana/k6:0.50` | - | - | 1.0 | 256m | `../load:/scripts:ro`、`../load/reports:/reports` | - |

### 2.1 依赖关系

`backend.depends_on`：

```yaml
depends_on:
  mysql:
    condition: service_healthy
  redis:
    condition: service_healthy
  kafka-1:
    condition: service_healthy
  kafka-2:
    condition: service_healthy
  kafka-3:
    condition: service_healthy
```

`web.depends_on`：

```yaml
depends_on:
  backend:
    condition: service_healthy
```

`k6` 无 `depends_on`，由人工在所有服务健康后手动 `run`。

### 2.2 Kafka KRaft 关键环境变量

`bitnami/kafka:3.7` 3 节点 KRaft 模式，三节点共享同一份 `KAFKA_CFG_CONTROLLER_QUORUM_VOTERS`，每节点独立 `NODE_ID` 与 `ADVERTISED_LISTENERS`。以下为 `kafka-1` 完整配置，`kafka-2 / kafka-3` 仅 `NODE_ID` 与 `ADVERTISED_LISTENERS` 中 host 不同：

```yaml
kafka-1:
  image: bitnami/kafka:3.7
  environment:
    KAFKA_CFG_PROCESS_ROLES: controller,broker
    KAFKA_CFG_NODE_ID: 1
    KAFKA_CFG_CONTROLLER_QUORUM_VOTERS: 1@kafka-1:9093,2@kafka-2:9093,3@kafka-3:9093
    KAFKA_CFG_LISTENERS: PLAINTEXT://:9092,CONTROLLER://:9093,EXTERNAL://:9094
    KAFKA_CFG_ADVERTISED_LISTENERS: PLAINTEXT://kafka-1:9092,EXTERNAL://localhost:9094
    KAFKA_CFG_LISTENER_SECURITY_PROTOCOL_MAP: PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT,EXTERNAL:PLAINTEXT
    KAFKA_CFG_CONTROLLER_LISTENER_NAMES: CONTROLLER
    KAFKA_CFG_INTER_BROKER_LISTENER_NAME: PLAINTEXT
    KAFKA_CFG_AUTO_CREATE_TOPICS_ENABLE: "false"
    KAFKA_CFG_DEFAULT_REPLICATION_FACTOR: "3"
    KAFKA_CFG_MIN_INSYNC_REPLICAS: "2"
    ALLOW_PLAINTEXT_LISTENER: yes
  volumes: [kafka-1-data:/bitnami/kafka]

# kafka-2: NODE_ID=2, EXTERNAL 端口 9095, advertised host=kafka-2
# kafka-3: NODE_ID=3, EXTERNAL 端口 9096, advertised host=kafka-3
```

Topic 由后端启动时通过 `KafkaAdmin` Bean 显式创建，指定 `replicationFactor=3` 与 `min.insync.replicas=2`，不依赖自动创建。

### 2.3 环境变量集中维护

`deploy/.env.example` 提供模板，部署时拷贝为 `.env`：

```bash
# MySQL
MYSQL_ROOT_PASSWORD=shanpu_root
MYSQL_DATABASE=shanpu
MYSQL_USER=shanpu
MYSQL_PASSWORD=shanpu_pwd

# JWT
JWT_SECRET=replace-me-with-a-32-byte-random

# Compose 内部地址（backend / web 使用）
SPRING_DATASOURCE_URL=jdbc:mysql://mysql:3306/shanpu?useSSL=false&serverTimezone=Asia/Shanghai&characterEncoding=utf8mb4
SPRING_REDIS_HOST=redis
SPRING_KAFKA_BOOTSTRAP_SERVERS=kafka-1:9092,kafka-2:9092,kafka-3:9092

NEXT_PUBLIC_API_BASE_URL=http://localhost:8080/api
INTERNAL_API_BASE_URL=http://backend:8080/api

# 资源限制（按机器规模调整，见 §2.4）
BACKEND_MEMORY_LIMIT=1.5g
BACKEND_JAVA_OPTS=-Xmx1g -Xms512m -XX:MaxMetaspaceSize=128m
KAFKA_HEAP=320m
MYSQL_BUFFER_POOL=384M
```

### 2.4 资源限制设计

`docker-compose.yml` 显式声明每服务 `deploy.resources.limits`，以 7.6 GiB WSL2 单机为基线，全栈总和 ≈ 5 GiB，留 ≥ 1.5 GiB 给宿主与压测突发：

| 服务 | CPU | 内存 | 内部硬约束 |
|---|---|---|---|
| `backend` | 2.0 | 1.5g | `JAVA_TOOL_OPTIONS=-Xmx1g -Xms512m -XX:MaxMetaspaceSize=128m` 留 25% 余量避 cgroup OOM Kill；Tomcat `server.tomcat.threads.max=100` |
| `web` | 0.5 | 384m | Next.js standalone SSR，压测期间几乎纯静态响应 |
| `mysql` | 1.5 | 1g | `innodb_buffer_pool_size=384M`、`max_connections=200`、`innodb_log_buffer_size=16M` |
| `redis` | 1.0 | 384m | `maxmemory 256mb`、`maxmemory-policy allkeys-lru`、`save ""` |
| `kafka-1/2/3` | 0.7 × 3 | 512m × 3 | `KAFKA_HEAP_OPTS=-Xmx320m -Xms320m`、`num.network.threads=2`、`num.io.threads=2`、`log.segment.bytes=64MB`、`log.retention.ms=3600000` |
| `k6` | 1.0 | 256m | k6 单进程 500 VU 实测约 100 MB |
| 合计 | 8.1 | 5.0g | 留 ≥ 6 CPU 核、≥ 1.6 GiB 内存给宿主 |

按机器规模缩放指引：
- **内存 ≥ 16 GiB**：可把 `backend` 提到 3g（`-Xmx2g`）、`mysql` 提到 2g（buffer pool 1g）、压测库存 `PRESSURE_STOCK` 改 100000、VU 上限翻倍。
- **内存 ≥ 32 GiB**：`backend` 提到 4g、`kafka` 每节点 1g、用户池扩到 1000、VU 上限再翻倍。
- **CPU 核心紧张（< 8 核）**：把 `kafka-2/3` 缩到 0.4 CPU，`backend` 不变（吞吐瓶颈通常在内存而非 CPU）。

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
ENV SPRING_PROFILES_ACTIVE=docker
ENTRYPOINT ["sh", "-c", "java $JAVA_OPTS -jar /app/app.jar"]
```

镜像大小约 220 MB。`JAVA_TOOL_OPTIONS` 显式锁定堆与 Metaspace，避免 JVM 自适应突破 cgroup 内存上限被 OOM Kill；运行时可通过 compose `environment` 覆盖以适配更大机器。

## 4. 前端 Dockerfile

利用 Next.js `output: 'standalone'` 产出最小运行时。

```dockerfile
# ===== deps =====
FROM node:20-alpine AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# ===== build =====
FROM node:20-alpine AS build
WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_BASE_URL
ARG NEXT_PUBLIC_SITE_NAME
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NEXT_PUBLIC_SITE_NAME=$NEXT_PUBLIC_SITE_NAME
RUN pnpm build

# ===== runtime =====
FROM node:20-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
CMD ["node", "server.js"]
```

镜像大小约 160 MB。

## 5. 初始化挂载

### 5.1 MySQL

`deploy/mysql/init/` 下两个脚本由 `mysql:8.0` 镜像在首次启动时自动执行：

- `01_schema.sql`：DDL（与 `docs/BACKEND.md` 第 10 节一致），创建 `t_user / t_product / t_seckill_activity / t_order / t_point_log / t_order_dlq_log` 六张表。
- `02_seed.sql`：注入：
  - 2 个测试用户（`alice` / `bob`，密码 BCrypt 哈希后写入）。
  - 500 个压测用户（`u0001` ~ `u0500`，统一密码 `pwd123456`，初始积分 10000）。
  - 5 个普通商品（价格 50~500 积分，库存 100~10000）。
  - 9 条三档秒杀活动，**ID 仅为视觉惯例（1xxx / 2xxx / 3xxx），路由权威依据为 `tier` 列**。`seckill_price = product.price × tier_discount`，`start_at` / `end_at` 用 `NOW()` 锚定（进行中：`NOW() - INTERVAL 1 HOUR` ~ `NOW() + INTERVAL 6 HOUR`；即将开始：`NOW() + INTERVAL 30 MINUTE` ~ `NOW() + INTERVAL 6 HOUR`）：
    - **普惠专场**（`tier=1`，9 折，库存 5000）：
      - `id=1001`：进行中，绑 product 1（场景 A 压测目标）
      - `id=1002`：进行中，绑 product 2
      - `id=1003`：即将开始，绑 product 3
    - **限时抢购**（`tier=2`，7 折，库存 500）：
      - `id=2001`：进行中，绑 product 1（场景 B 压测目标）
      - `id=2002`：进行中，绑 product 4
      - `id=2003`：即将开始，绑 product 5
    - **超级秒杀**（`tier=3`，3 折，库存 200）：
      - `id=3001`：进行中，绑 product 1（场景 C 压测目标）
      - `id=3002`：进行中，绑 product 2
      - `id=3003`：即将开始，绑 product 5

`deploy/mysql/conf/my.cnf` 集中调优：

```ini
[mysqld]
innodb_buffer_pool_size = 384M
max_connections         = 200
innodb_log_buffer_size  = 16M
innodb_flush_log_at_trx_commit = 2
character-set-server    = utf8mb4
collation-server        = utf8mb4_unicode_ci
```

### 5.2 Redis

`deploy/redis/redis.conf`：

```conf
maxmemory 256mb
maxmemory-policy allkeys-lru
save ""                     # 关闭 RDB 快照，避免压测期间 fork 抖动
appendonly no
```

无独立预热脚本。后端 `ApplicationRunner` 在启动后立即扫描 `t_seckill_activity` 中 `tier IN (2, 3) AND status IN (0, 1) AND start_at < now + 60min` 的活动，写入库存与活动详情，与 `SeckillWarmupJob` 等价。普惠档（`tier=1`）无需库存预热，仅活动详情逻辑过期缓存按需写入。

### 5.3 Kafka

`deploy/kafka/server.properties` 三节点共享基线：

```properties
num.network.threads = 2
num.io.threads      = 2
log.segment.bytes   = 67108864
log.retention.ms    = 3600000
```

每 broker JVM 堆 `KAFKA_HEAP_OPTS=-Xmx320m -Xms320m`，三节点合计约 1.5 GiB 内存预算。后端 `KafkaConfig` 声明 `NewTopic` Bean，`KafkaAdmin` 启动时自动创建 `shanpu.seckill.order`（3 分区 3 副本）与 `shanpu.seckill.order.dlq`（1 分区 3 副本），两 topic 均显式指定 `min.insync.replicas=2`。3 节点 broker 启动后由 controller quorum 自动选举 leader 与同步 ISR。

## 6. 一键启动流程

```bash
# 1. 准备环境变量
cd deploy
cp .env.example .env
# 按需修改 .env 中密码与 JWT_SECRET

# 2. 拉起全栈
docker compose up -d

# 3. 等待健康检查通过（约 60~90 秒）
docker compose ps

# 4. 浏览器访问
open http://localhost:3000

# 5. 默认账号
#    alice / pwd123456
#    bob   / pwd123456
```

### 6.1 停止与重置

```bash
docker compose down            # 停止容器，保留数据卷
docker compose down -v         # 同时清空 MySQL / Redis / 3 节点 Kafka 数据
```

`down -v` 后再次 `up -d` 会重新跑 `01_schema.sql` 与 `02_seed.sql`，得到干净环境。

## 7. k6 压测：三档秒杀横向对比

### 7.1 目标

在 7.6 GiB WSL2 单机基础设施下，三档秒杀活动同走 `/api/seckills/{id}/grab` 入口，由 `SeckillService.grab(activityId)` 按 `activity.tier` 自动路由 `grabInclusive` / `grabFlash` / `grabSuper`。统一压力模型量化三档极限吞吐与延迟分布（QPS、p50/p95/p99），定位瓶颈位置（DB 行锁 / Redis CPU / Kafka 消费）。正确性由 M4 验收覆盖，本章节仅做一次性超卖断言。

场景 D 缓存层级对比与三档压测正交，独立执行（详见 §7.6 / 7.8 与 ROADMAP M6 缓存层级对比表）。

### 7.2 场景

三场景同接口同 setup，仅替换 `__ENV.ACTIVITY_ID`：

| 场景 | 接口 | tier | 路由分支 | 链路 | VU 峰值 | 预期瓶颈 |
|---|---|---|---|---|---|---|
| A 普惠 | `POST /api/seckills/1001/grab` | 1 | `grabInclusive` | MySQL 行锁 + 同步事务 | 50 | DB 单行 `id=1001` 行锁串行化 |
| B 限时 | `POST /api/seckills/2001/grab` | 2 | `grabFlash` | Redis+Lua → 同步事务 | 150 | DB 行锁（Redis 减负但落库仍同步） |
| C 超级 | `POST /api/seckills/3001/grab` | 3 | `grabSuper` | Redis+Lua → Kafka 同步 `send.get(3s)` → 异步消费 | 300 | Kafka producer send ack |

每轮压测前由 `scripts/k6-bench-reset.sh`（详见 §7.9）把目标活动 `total_stock` 重置为 **20000**（远超种子值 5000/500/200，确保稳态期不被库存卡断），并清理 500 压测用户在目标活动的旧订单与积分日志；压测后由 `scripts/k6-bench-restore.sh` 恢复种子库存。

VU 峰值按 §2.4 资源限制反推：backend JVM 1g 堆配 Tomcat 100 线程，>100 VU 后只增延迟无吞吐；mysql 200 连接限制下 A 场景 50 VU 已饱和单行锁；kafka 三 broker 各 0.7 CPU 下 C 场景 300 VU 即可探到 producer ack 拐点。

### 7.3 通用 setup：批量登录

`load/lib/auth.js`：

```js
import http from 'k6/http';
import { check } from 'k6';

// 500 用户串行登录约 15~30s,在 setup 阶段一次性完成,运行期不再登录
export function loginAll(baseUrl, prefix = 'u', count = 500, password = 'pwd123456') {
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

### 7.4 三个脚本骨架与差异化阶梯

```js
// load/scenario-a-inclusive.js（普惠档,activity_id=1001,tier=1）
import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';
import { loginAll } from './lib/auth.js';

export const options = {
  stages: [
    { duration: '20s', target: 20 },
    { duration: '40s', target: 50 },
    { duration: '90s', target: 50 },
    { duration: '30s', target: 0 },
  ],
  thresholds: {
    http_req_failed:   ['rate<0.01'],   // 业务错误码不计入,仅统计 HTTP 5xx/超时
    http_req_duration: ['p(95)<800'],   // 小机器放宽,DB 行锁场景
    iteration_duration: ['p(99)<2000'],
  },
};

const orderOk   = new Counter('order_success_total');
const orderFail = new Counter('order_fail_total');
const dupCnt    = new Counter('seckill_duplicate_total');
const soldOut   = new Counter('seckill_sold_out_total');

export function setup() {
  const base = __ENV.API_BASE || 'http://backend:8080/api';
  return { base, tokens: loginAll(base) };
}

export default function (data) {
  // 让所有 VU × 所有迭代覆盖到全部用户,最大化一人一单约束的穿透
  const token = data.tokens[(__VU - 1 + __ITER) % data.tokens.length];
  const activityId = __ENV.ACTIVITY_ID || '1001';
  const res = http.post(`${data.base}/seckills/${activityId}/grab`, '{}', {
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
  });
  const body = res.json();
  if (res.status === 200) {
    if (body.code === 0)        orderOk.add(1);
    else if (body.code === 40900) { dupCnt.add(1);  orderFail.add(1); }
    else if (body.code === 41000) { soldOut.add(1); orderFail.add(1); }
    else                          orderFail.add(1);
  }
  check(res, { '200 status': r => r.status === 200 });
}
```

场景 B 与 C 复用同一份骨架，仅 `options.stages` 与 `thresholds` 不同：

```js
// load/scenario-b-redis-sync.js（限时档,activity_id=2001,tier=2）
stages: [
  { duration: '20s', target: 50 },
  { duration: '40s', target: 150 },
  { duration: '90s', target: 150 },
  { duration: '30s', target: 0 },
],
thresholds: {
  http_req_failed:   ['rate<0.01'],
  http_req_duration: ['p(95)<300'],
  iteration_duration: ['p(99)<2000'],
},
// __ENV.ACTIVITY_ID 默认 '2001'
```

```js
// load/scenario-c-redis-kafka.js（超级档,activity_id=3001,tier=3）
stages: [
  { duration: '20s', target: 100 },
  { duration: '40s', target: 300 },
  { duration: '90s', target: 300 },
  { duration: '30s', target: 0 },
],
thresholds: {
  http_req_failed:   ['rate<0.01'],
  http_req_duration: ['p(95)<200'],
  iteration_duration: ['p(99)<2000'],
},
// __ENV.ACTIVITY_ID 默认 '3001'
```

三场景总时长各 3 分钟，90 秒稳态足以采到稳定 p95/p99 分布。

### 7.5 自定义指标

| 指标名 | 类型 | 含义 |
|---|---|---|
| `order_success_total` | Counter | `code == 0` 的请求数（含 C 场景的 `PENDING` 响应） |
| `order_fail_total` | Counter | 业务失败请求数（库存不足、一人一单冲突等） |
| `seckill_duplicate_total` | Counter | `code == 40900` 计数（一人一单冲突，按预期递增） |
| `seckill_sold_out_total` | Counter | `code == 41000` 计数（库存售罄，按预期递增） |

**关键约定：业务错误码（40900/41000）不计入 `http_req_failed`**，因为压测后期 500 用户全成功一次后，后续请求快速返回业务错误码——这是测量"售罄场景下服务端 RT"的有效数据，与生产真实秒杀爆款一致。`check(res, { '200 status': r => r.status === 200 })` 仅校验 HTTP 层成功。

可选 Trend 指标（若后端在响应 header 回写 `X-Kafka-Send-Ms` 与 `X-Db-Wait-Ms`，则 k6 用 `import { Trend } from 'k6/metrics'` 单独收集）：

| 指标名 | 类型 | 适用场景 |
|---|---|---|
| `kafka_send_duration` | Trend | C 场景：Kafka `send().get()` 同步等待 ack 耗时 |
| `db_row_lock_wait_ms` | Trend | A/B 场景：MySQL 行锁等待耗时 |

若后端不引入 `X-*` 调试 header，则降级为压测中执行 `docker stats backend` 与 `redis-cli INFO commandstats` 差分人工记录，不强制 k6 采集。

### 7.6 输出与报告

`scripts/k6-run.sh` 串跑三档秒杀场景 + 场景 D 缓存对比，并按时间戳归档：

```bash
#!/usr/bin/env bash
set -e
TS=$(date +%Y%m%d-%H%M%S)
REPORT_DIR=load/reports/$TS
mkdir -p "$REPORT_DIR"

# 三档秒杀横向对比
for SCENARIO in a-inclusive:1001 b-redis-sync:2001 c-redis-kafka:3001; do
  NAME=${SCENARIO%:*}
  ID=${SCENARIO#*:}

  echo "[$NAME] reset stock to 20000..."
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
└── README.md                         # 硬件配置、阶梯参数、瓶颈观察(人工填入)
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

三条结果均应 `≤ 20000`（即 `bench-reset` 注入的压测库存上限），绝不超出；若超出，判定为超卖事故，需排查 Lua 脚本或 DB 唯一索引。

注：20000 为压测注入的临时库存，与种子库存（5000/500/200）解耦，避免污染 M4 业务验收语义。压测前 `bench-reset` 注入、压测后 `bench-restore` 恢复。

### 7.8 对比报告

`docs/ROADMAP.md` M6 章节附压测对比表，由人工填入实测值。统一记录 VU 峰值、平均 RPS、p50/p95/p99、HTTP 错误率、业务错误率、成功订单数、是否超卖与瓶颈观察。瓶颈观察通过压测高峰期 `docker stats`、`redis-cli INFO commandstats` 差分、`kafka-consumer-groups.sh --describe --group shanpu-seckill-consumer` lag 三处采集。

### 7.9 压测前置脚本

#### `scripts/k6-bench-reset.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

ACTIVITY_IDS=${1:-"1001 2001 3001"}
PRESSURE_STOCK=${PRESSURE_STOCK:-20000}
USER_RANGE_MAX=${USER_RANGE_MAX:-500}
DB_USER=${MYSQL_USER:-shanpu}
DB_PASS=${MYSQL_PASSWORD:-shanpu_pwd}
DB_NAME=${MYSQL_DATABASE:-shanpu}

# 1. MySQL 重置库存 / 激活活动 / 清旧订单
for ID in $ACTIVITY_IDS; do
  docker compose exec -T mysql mysql -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" <<SQL
UPDATE t_seckill_activity
SET total_stock = $PRESSURE_STOCK, status = 1,
    start_at = NOW() - INTERVAL 1 HOUR,
    end_at   = NOW() + INTERVAL 6 HOUR
WHERE id = $ID;
DELETE FROM t_order WHERE activity_id = $ID AND user_id BETWEEN 1 AND $USER_RANGE_MAX;
SQL
done

# 2. 压测用户积分补足(避免 POINTS_NOT_ENOUGH 干扰)
docker compose exec -T mysql mysql -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" <<SQL
UPDATE t_user SET points = 10000000
WHERE username REGEXP '^u[0-9]{4}\$' AND id BETWEEN 1 AND $USER_RANGE_MAX;
DELETE FROM t_point_log WHERE user_id BETWEEN 1 AND $USER_RANGE_MAX AND biz_type IN (2, 3);
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
DB_USER=${MYSQL_USER:-shanpu}
DB_PASS=${MYSQL_PASSWORD:-shanpu_pwd}
DB_NAME=${MYSQL_DATABASE:-shanpu}

docker compose exec -T mysql mysql -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" <<'SQL'
UPDATE t_seckill_activity SET total_stock = 5000 WHERE id IN (1001, 1002, 1003);
UPDATE t_seckill_activity SET total_stock = 500  WHERE id IN (2001, 2002, 2003);
UPDATE t_seckill_activity SET total_stock = 200  WHERE id IN (3001, 3002, 3003);
DELETE FROM t_order WHERE activity_id IN (1001, 2001, 3001) AND user_id BETWEEN 1 AND 500;
UPDATE t_user SET points = 10000
WHERE username REGEXP '^u[0-9]{4}$' AND id BETWEEN 1 AND 500;
SQL

for ID in 1001 2001 3001; do
  docker compose exec -T redis redis-cli DEL \
    "shanpu:seckill:stock:$ID" "shanpu:seckill:bought:$ID" "shanpu:seckill:activity:$ID"
done

echo "[bench-restore] seed stocks restored"
```

### 7.10 关键设计取舍

- **压测专用临时库存 20000**：种子库存 5000/500/200 太小，单机 VU 峰值下数秒售罄无法采到稳态 RT。`bench-reset` 注入 20000 仅在压测窗口生效，压测后 `bench-restore` 恢复种子值，与 M4 业务验收语义解耦。
- **业务错误码不计入 HTTP 失败**：500 压测用户对单活动一人一单约束下，成功上限即 500，之后所有请求快速返回 `40900` / `41000`——这是生产真实秒杀售罄后的负载形态，服务端仍需以低 RT 响应。`http_req_failed` 仅统计 HTTP 5xx/超时，业务码经 `seckill_duplicate_total` / `seckill_sold_out_total` 独立计数。
- **VU 上限由 §2.4 资源限制反推**：50/150/300 不是经验值，而是 backend Tomcat 100 线程、mysql 200 连接、kafka 三 broker 0.7 CPU 三个硬约束的直接结果。换更大机器后按 §2.4 缩放指引等比调整。

## 8. 运行环境要求

| 项 | 要求 |
|---|---|
| 操作系统 | Linux / macOS / Windows + WSL2 |
| Docker | 24+ |
| Docker Compose | v2.20+ |
| 内存 | ≥ 8 GB 可用（全栈容器合计 ≈ 5 GB，按 §2.4 限制；压测峰值额外吃 ~500 MB） |
| 磁盘 | ≥ 5 GB 可用 |
| 端口 | 3000（web）、8080（backend）、3306（mysql）、6379（redis）、9094 / 9095 / 9096（3 节点 kafka 外部） |

本地开发若希望直接 IDE 启动后端，可仅启 `mysql / redis / kafka-1 / kafka-2 / kafka-3` 五个依赖：

```bash
cd deploy
docker compose up -d mysql redis kafka-1 kafka-2 kafka-3
# 然后在 IDE 中以 spring.profiles.active=local 启动 backend
# web 在 web/ 目录下 pnpm dev
```
