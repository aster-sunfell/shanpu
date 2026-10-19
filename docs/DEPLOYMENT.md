# 闪铺本地开发与压测

## 1. 运行方式

本地 Linux 环境中，Docker 运行 MySQL、Redis、Kafka 和 Nginx；Java 17 后端与 Next.js 前端运行在宿主机，由 VS Code tasks 或终端启动。

| 服务 | 运行位置 | 默认地址 | 用途 |
|---|---|---|---|
| MySQL 8.0 | Docker | `127.0.0.1:3306` | 订单、积分、商品、活动与死信日志 |
| Redis 7 | Docker | `127.0.0.1:6379` | 缓存、登录态、限流、库存与已购集合 |
| Kafka 3.7.2 | Docker | `127.0.0.1:9092` | 超级秒杀异步下单与死信 Topic |
| Nginx 1.28 | Docker，host 网络 | `127.0.0.1:8089` | 浏览器统一入口 |
| Spring Boot | 宿主机 Java 17 | `127.0.0.1:8081` | 后端 API |
| Next.js | 宿主机 Node.js 22 / pnpm 10 | `127.0.0.1:3000` | SSR、前端开发与热更新 |
| k6 | Docker，`loadtest` profile，host 网络 | 访问本机后端 | 按需压测 |

`docker-compose.yml` 固定项目名为 `shanpu`，使用 `shanpu_mysql-data`、`shanpu_redis-data`、`shanpu_kafka-data` 三个数据卷。Nginx 和 k6 使用 Linux host 网络，直接访问宿主机回环地址；Nginx 不配置 `ports`。

## 2. 生命周期

所有 Compose 服务显式设置 `restart: "no"`，由开发者手动启动和停止。容器退出、Docker 重启或电脑重启后，需要重新执行启动任务。`up -d` 表示容器在后台运行，关闭终端或 VS Code 后仍会继续运行。健康检查判断服务是否就绪，不负责自动重启。

日常停止使用 `bash scripts/dev-stop.sh`，保留容器和数据卷。Java 与 Next.js 使用 VS Code 的“终止任务”或所在终端的 Ctrl+C 停止。打开工作区不会自动启动任何服务。

## 3. 环境变量与宿主机应用

首次执行开发脚本时，如果根目录 `.env` 不存在，会从 `.env.example` 创建。该文件包含数据库名、MySQL / Redis 密码、JWT 密钥，以及六个端口；实际文件不入库。修改端口后重新运行相应启动任务。

| 变量 | 默认值 | 作用 |
|---|---|---|
| `MYSQL_PORT` | `3306` | MySQL 宿主机端口 |
| `REDIS_PORT` | `6379` | Redis 宿主机端口 |
| `KAFKA_PORT` | `9092` | Kafka 宿主机 listener 端口 |
| `BACKEND_PORT` | `8081` | Java 监听端口、网关 API 上游、SSR API 上游 |
| `FRONTEND_PORT` | `3000` | Next.js 监听端口与网关页面上游 |
| `GATEWAY_PORT` | `8089` | 浏览器访问入口 |

`scripts/lib/dev-common.sh` 统一加载环境变量，并固定 Compose 文件和项目名，使启动脚本可从任意目录调用。

后端脚本先加载 SDKMAN 初始化脚本，在仓库根目录执行 `sdk env`，应用 `.sdkmanrc` 指定的 `17.0.18-tem`，并校验 `JAVA_HOME` 与实际 `java` 命令均指向该 SDK。初始化失败或版本不符时直接退出。环境确认后才运行 Maven 打包，通过 `exec java -jar` 启动；设置 `dev` profile、东八区时区与回环监听地址，将 `MYSQL_ROOT_PASSWORD` 映射为 `MYSQL_PASSWORD`。默认 JVM 参数为 `-Xms256m -Xmx1g`，可通过 `JAVA_OPTS` 调整。进程 PID 写入忽略目录 `.local/run/backend.pid`，供压测采样使用。

前端脚本通过 `pnpm dev` 启动，注入：

- `PUBLIC_API_BASE_URL=/api`：浏览器使用当前站点的 API。
- `NEXT_PUBLIC_API_BASE_URL=/api`：浏览器配置回退值。
- `INTERNAL_API_BASE_URL=http://127.0.0.1:${BACKEND_PORT}/api`：SSR 使用绝对地址直接调用 Java。

进程环境优先于 Next.js 的 `.env.local`。前端依赖安装使用 `cd frontend && pnpm install --frozen-lockfile`。

## 4. 网关与热更新

`deploy/nginx.conf.template` 由 Nginx 官方镜像在启动时按环境变量生成配置。模板替换仅处理三个端口变量，保留 `$http_host` 等 Nginx 变量。

- `/api/` 转发到宿主机 Java，保留 `/api` 路径。
- `/` 转发到宿主机 Next.js，支持 WebSocket Upgrade，并关闭代理缓冲。
- `/gateway-health` 由 Nginx 直接返回 200，用于独立判断网关是否运行。

浏览器使用 `http://127.0.0.1:8089`。Next.js 同时提供 `/api` rewrite，直接访问 3000 端口时也能调用 API；开发来源限定为 `127.0.0.1` 和 `localhost`。SSR 使用内部绝对地址，不经过浏览器的相对 URL。

网关可以先于前后端启动，此时页面和 API 上游尚未就绪；“检查开发环境”会分别检查容器、后端依赖以及经网关访问的前端页面。

## 5. 中间件

### MySQL

首次初始化空数据卷时，自动执行 `mysql/init/01_schema.sql` 和 `02_seed.sql`。已有卷保留现有数据，启动不会重新导入 SQL。测试用户为 `summer / 123456`，提供普通商品和三档秒杀活动。SQL 使用 `SET NAMES utf8mb4`，数据库运行时采用东八区时区。

资源上限为 1.5 CPU / 1 GiB，buffer pool 为 384 MiB。启动健康检查通过后，Java 健康接口使用实际 SQL 连接检查数据库。

### Redis

Redis 启用密码、AOF 与 `appendfsync everysec`，使用 `redis-data` 持久卷，内存上限为 256 MiB，淘汰策略为 `noeviction`。Redis 同时保存库存和已购标记，达到内存上限时返回写入错误，业务 Key 依照各自 TTL 过期。容器内存上限为 384 MiB。

### Kafka

单个 `apache/kafka:3.7.2` 容器同时承担 broker 和 KRaft controller，无需 ZooKeeper。Java 直接生产和消费 JSON 消息，无需 Schema Registry、Kafka Connect 或独立死信服务。

| Listener | 地址 | 调用方 |
|---|---|---|
| INTERNAL | `kafka:9092` | Compose 网络客户端、容器健康检查 |
| EXTERNAL | 容器 9094，发布为 `127.0.0.1:${KAFKA_PORT}` | 宿主机 Java |
| CONTROLLER | `kafka:9093` | KRaft 内部通信，不发布 |

宿主机 listener 的 advertised 地址为 `127.0.0.1:${KAFKA_PORT}`。固定 cluster ID、node ID 和数据卷，JVM 堆为 256–512 MiB，容器上限为 768 MiB。

Spring `KafkaConfig` 在后端启动时声明 `shanpu.seckill.order`（3 分区）和 `shanpu.seckill.order.dlq`（1 分区），副本数均为 1。消费者并发度为 3。Producer 使用幂等发送和 `acks=all`；单节点用于开发联调，不提供节点级容错。

## 6. VS Code tasks 与命令行

通过“终端 → 运行任务”选择：

| 任务 | 行为 |
|---|---|
| 启动中间件 | 启动 MySQL、Redis、Kafka，等待健康检查 |
| 启动网关 | 独立启动 Nginx，等待网关健康检查 |
| 启动后端 | 先完成“启动中间件”，再打包启动 Java 17 |
| 启动前端 | 启动 Next.js 开发服务 |
| 启动开发环境 | 组合启动网关、后端和前端，完成后执行健康检查并显示入口 |
| 检查开发环境 | 显示容器状态，检查后端依赖、网关和前端 |
| 停止中间件与网关 | 停止四个容器，保留数据 |

前后端运行在独立终端，后台任务通过 Spring 的 `Started ShanpuApplication` 与 Next.js 的 `Ready in` 日志判断启动完成，每个启动任务最多运行一个实例。停止前后端使用“终止任务”。

等价命令如下，前后端各占一个终端：

```bash
bash scripts/dev-up.sh
bash scripts/dev-gateway.sh
bash scripts/start-local-backend.sh
bash scripts/start-local-frontend.sh
bash scripts/dev-status.sh
bash scripts/dev-stop.sh
```

## 7. k6 压测

先通过任务启动后端，再运行 `bash scripts/k6-run.sh`。脚本确保中间件就绪，并检查 `/api/health` 响应体中 `status/mysql/redis/kafka` 均为 `UP`，之后才初始化压测数据。压测不依赖前端或 Nginx。

k6 通过 `loadtest` profile 按需启动，使用 host 网络直接访问 `http://127.0.0.1:${BACKEND_PORT}/api`，运行结束后移除临时容器。测试 token 通过只读的 `load/.runtime` 挂载提供。

| 场景 | 活动 | 链路 | 正式压力 |
|---|---|---|---|
| A 普惠 | 1001 | MySQL 同步事务 | 100 VU / 30s |
| B 限时 | 2001 | Redis + Lua → MySQL 同步事务 | 100 VU / 30s |
| C 超级 | 3001 | Redis + Lua → Kafka → 异步落库 | 100 VU / 30s |
| D 缓存 | 活动 3001 / 商品 1 | 活动 L1 开关对比与商品 Redis 对照 | 脚本中的固定压力 |

编排脚本使用 `k6-bench-reset.sh` / `k6-bench-restore.sh` 保存与恢复活动数据，调用 `seed-users.sh` 准备压测用户。A/B/C 报告包括成功 RPS、成功请求 p50/p95/p99、HTTP 与业务错误率、订单数和超卖校验；C 场景额外等待订单与积分流水一致。D 场景记录 L1 hitRate、Redis GET/s 和请求延迟。

`load/reports/<时间戳>-<PID>/resources-*.tsv` 保存 MySQL、Redis、Kafka 容器内存与宿主机 Java 的 PID、RSS（KiB）和进程平均 CPU 使用率；采样 Java 时核对命令行，避免使用失效 PID。报告结尾显示中间件容器的 OOMKilled 状态。两类资源统计分别解释，Java 不使用容器 OOM 指标。

## 8. 重置数据

日常操作使用 `stop` 保留数据。需要明确清空全部本地数据时，可执行 `docker compose down -v`，这会删除 MySQL、Redis 和 Kafka 的数据卷；再次启动 MySQL 会重新导入初始化 SQL。
