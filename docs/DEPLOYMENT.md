# 闪铺开发、生产与压测部署

## 1. 运行方式

本地 Linux 环境中，Docker 运行 MySQL、Redis、Kafka 和 Nginx；Java 17 后端与 Next.js 前端运行在宿主机，由 VS Code tasks 或终端启动。

| 服务 | 运行位置 | 默认地址 | 用途 |
|---|---|---|---|
| MySQL 8.0 | Docker | `127.0.0.1:3306` | 订单、积分、商品、活动与死信日志 |
| Redis 7 | Docker | `127.0.0.1:6379` | 缓存、登录态、限流、库存与已购集合 |
| Kafka 3.7.2 | Docker | `127.0.0.1:9092` | 超级秒杀异步下单与死信 Topic |
| Nginx 1.28 | Docker，host 网络 | `127.0.0.1:8089` | 浏览器统一入口 |
| Spring Boot | 宿主机 Java 17 | `127.0.0.1:8081` | 后端 API |
| Next.js | 宿主机 Node.js 22 / pnpm 10.11.0 | `127.0.0.1:3000` | SSR、前端开发与热更新 |
| 压测端 | 独立 `experiments/compose.runner.yml` | 访问独立待测端 | 2 个逻辑 CPU / 2 GiB |

`docker-compose.yml` 固定项目名为 `shanpu`，使用 `shanpu_mysql-data`、`shanpu_redis-data`、`shanpu_kafka-data` 三个数据卷。Nginx 和 k6 使用 Linux host 网络，直接访问宿主机回环地址；Nginx 不配置 `ports`。

生产基线为 `deploy/compose.2c2g.yml`，包含 backend、frontend、Nginx、MySQL、Redis、Kafka 六个容器。本地 Compose 继承其中的中间件，保留宿主机前后端；压测待测端 `experiments/compose.sut.yml` 继承全部六个服务，发压端另用 `experiments/compose.runner.yml`，不占用待测端预算。

前端包管理器版本固定在 `frontend/package.json` 的 `packageManager`（pnpm 10.11.0）。镜像按该字段安装工具，依赖安装和应用构建复用同一阶段，不在构建时解析 pnpm 最新版；依赖仍使用 `pnpm install --frozen-lockfile`。

应用性能参数统一在 `backend/src/main/resources/application-2c2g.yml`；`dev` 和 `prod` profile 都自动加载。Tomcat 使用 32 个处理线程、8192 个连接、512 个连接排队位，连接及 keep-alive 超时均为 5 秒；数据库池最多 8 个连接、等待上限 3 秒。本地调试可显式覆盖参数，但其结果不作为容量证据。运行时镜像固定 digest，后端 JRE 版本与 `.sdkmanrc` 的 Java 17.0.18+8 对齐；生产和压测共用同一份 Dockerfile、JVM 参数及启动脚本。

执行 `python3 scripts/experiments/lib/runtime_alignment.py` 可比较展开后的三套 Compose，不启动服务；Actions checks 和正式执行前都会核验。仅允许本地手动重启策略、宿主地址/端口，以及压测观测组件、专属数据和凭据等明确差异。

## 2. 生命周期

本地四个容器均设置 `restart: "no"`，随用随开；容器退出、Docker 或电脑重启后，需要重新执行启动任务。生产和压测待测端使用 `restart: unless-stopped`，异常退出或 Docker 重启后自动恢复，显式手动停止后保持停止。发压容器仍为 `restart: "no"`，避免失败后自动重复发压；测量期间待测服务重启会使结果无效并保留证据。

`up -d` 表示容器在后台运行，关闭终端或 VS Code 后仍会继续运行。健康检查判断服务是否就绪，不会单凭 unhealthy 状态触发 Docker 重启。

日常停止使用 `bash scripts/dev/stop.sh`，保留容器和数据卷。Java 与 Next.js 使用 VS Code 的“终止任务”或所在终端的 Ctrl+C 停止。打开工作区不会自动启动任何服务。

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

后端脚本先加载 SDKMAN，在仓库根目录执行 `sdk env`，校验 `.sdkmanrc` 指定的 `17.0.18-tem` 后才运行 Maven clean 打包。默认使用 `dev -> 2c2g` profile、东八区时区与回环监听，将 `MYSQL_ROOT_PASSWORD` 映射为 `MYSQL_PASSWORD`。JVM 参数读取共用的 `backend/docker/java.options`，默认按 512 MiB 内存预算和两个可见处理器启动；可用 `JAVA_OPTS` 覆盖。宿主进程没有容器的 CPU/PID/内存硬隔离，不能将本地运行解释为等价压测。默认日志为 INFO，调试时可设置 `SHANPU_LOG_LEVEL=DEBUG`。PID 写入 `.local/run/backend.pid`。

前端脚本通过 `pnpm dev` 启动，注入：

- `PUBLIC_API_BASE_URL=/api`：浏览器使用当前站点的 API。
- `NEXT_PUBLIC_API_BASE_URL=/api`：浏览器配置回退值。
- `INTERNAL_API_BASE_URL=http://127.0.0.1:${BACKEND_PORT}/api`：SSR 使用绝对地址直接调用 Java。

进程环境优先于 Next.js 的 `.env.local`。前端依赖安装使用 `cd frontend && pnpm install --frozen-lockfile`。

## 4. 网关与热更新

`deploy/nginx.conf.template` 由 Nginx 官方镜像在启动时按环境变量生成配置。模板替换仅处理三个端口变量，保留 `$http_host` 等 Nginx 变量。

本地、生产和待测端共用 `deploy/nginx.api.conf`，统一 API 请求头、上游连接复用和超时。网关使用一个 worker、16384 个连接位（包含客户端和上游），nofile 32768；本地仅页面代理保留 WebSocket、关闭缓冲和热更新需要的长超时。

后端上游连接池共用 `deploy/nginx.backend-keepalive.conf`，由本地和生产的 `upstream backend` 引用，压测继承生产挂载。池内最多保留 16 条空闲连接，空闲超时为 3 秒，必须小于 Tomcat 的 5 秒，避免复用正在被后端关闭的连接。`http` 中的 `keepalive_timeout 15s` 控制客户端连接，不控制这个上游连接池；`nginx.api.conf` 在 `location` 中加载，也不能放置上游连接池指令。配置一致性检查会核验共享文件引用、只读挂载和超时关系，并将共享文件纳入报告配置指纹。

首次引入共享文件后，本地执行 `docker compose up -d --force-recreate nginx`；生产执行 `docker compose --env-file .env.production -f deploy/compose.2c2g.yml up -d --force-recreate nginx`，让新增挂载生效。后续 Actions 压测会自动使用该配置；此次仅调整网关，无需重建后端镜像。

- `/api/` 转发到宿主机 Java，保留 `/api` 路径。
- `/` 转发到宿主机 Next.js，支持 WebSocket Upgrade，并关闭代理缓冲。
- `/gateway-health` 由 Nginx 直接返回 200，用于独立判断网关是否运行。

浏览器使用 `http://127.0.0.1:8089`。Next.js 同时提供 `/api` rewrite，直接访问 3000 端口时也能调用 API；开发来源限定为 `127.0.0.1` 和 `localhost`。SSR 使用内部绝对地址，不经过浏览器的相对 URL。

网关可以先于前后端启动，此时页面和 API 上游尚未就绪；“检查开发环境”会分别检查容器、后端依赖以及经网关访问的前端页面。

## 5. 中间件

### MySQL

Compose 只负责创建 `MYSQL_DATABASE` 指定的空库。表结构由后端启动时执行 Flyway 迁移，SQL 位于 `backend/src/main/resources/db/migration`。演示数据通过 `bash scripts/db/seed-demo.sh` 显式补齐，不会重置已有库存或积分。

已有非空数据库必须先核对结构并显式 baseline，再启动后端；操作步骤见 [数据库管理](./DATABASE.md)。原有 MySQL 数据卷继续保留。

共享两个逻辑 CPU，内存上限为 384 MiB，buffer pool 为 128 MiB，最多 32 个连接，关闭 Performance Schema 并限制内部临时表。Java 健康接口使用实际 SQL 连接检查数据库。

### Redis

Redis 启用密码、AOF 与 `appendfsync everysec`，使用 `redis-data` 持久卷，数据内存上限为 48 MiB，淘汰策略为 `noeviction`。达到内存上限时返回写入错误，业务 Key 依照各自 TTL 过期。容器上限为 0.1 CPU / 96 MiB。

### Kafka

单个 `apache/kafka:3.7.2` 容器同时承担 broker 和 KRaft controller，无需 ZooKeeper。Java 直接生产和消费 JSON 消息，无需 Schema Registry、Kafka Connect 或独立死信服务。

| Listener | 地址 | 调用方 |
|---|---|---|
| INTERNAL | `kafka:9092` | Compose 网络客户端 |
| EXTERNAL | 容器 19092，发布为 `127.0.0.1:${KAFKA_PORT}` | 宿主机 Java、发压端 |
| CONTROLLER | `kafka:9093` | KRaft 内部通信，不发布 |

宿主机 listener 的 advertised 地址为 `127.0.0.1:${KAFKA_PORT}`。固定 cluster ID、node ID 和数据卷，JVM 堆为 128–192 MiB，共享两个逻辑 CPU，容器内存上限为 384 MiB。不使用反复创建 JVM 的 CLI 健康检查；Kafka 就绪由后端健康接口和压测协议客户端确认。

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
bash scripts/dev/up.sh
bash scripts/dev/gateway.sh
bash scripts/dev/backend.sh
bash scripts/dev/frontend.sh
bash scripts/dev/status.sh
bash scripts/dev/stop.sh
```

## 7. 全容器生产部署

从仓库根目录准备独立的生产环境文件，设置数据库/Redis 密码、JWT 密钥、端口，并按机器实际可用 CPU 设置 `SUT_CPUSET`（默认 `0,1`）。`.env.production` 已被 Git 忽略。

```bash
cp .env.example .env.production
# 编辑 .env.production，替换开发凭据
docker compose --env-file .env.production -f deploy/compose.2c2g.yml build
docker compose --env-file .env.production -f deploy/compose.2c2g.yml up -d --wait --wait-timeout 300
docker compose --env-file .env.production -f deploy/compose.2c2g.yml ps
```

入口为 `http://服务器地址:8089`；前后端通过容器网络通信，MySQL/Redis/Kafka/后端宿主端口仅绑定回环。项目名为 `shanpu-production`，数据卷与本地开发隔离。六个服务共享同一组两个逻辑 CPU（`SUT_CPUSET`，默认 `0,1`），无单独 CPU 配额，容器内存为 1632 MiB，预算另计宿主余量 416 MiB；宿主余量是预算说明，并非 Docker 硬预留。

## 8. 性能实验

性能实验仅在 GitHub 托管的 Linux Actions runner 执行。在 Actions → Performance experiments 中选择 main，手动 Run workflow，执行 A/B 和 B/C 完整实验并发布 HTML；推送与 PR 不会自动发压。

被测服务共享两个逻辑 CPU，发压容器另限 2 vCPU / 2 GiB；两端使用互斥且避免跨物理核 SMT 争用的 CPU 集合。每个 run 只运行一个固定负载实验，在同一机器内进行三轮交替配对，不进行校准或容量搜索。逻辑 CPU 不等于物理核，绑定 CPU 集合不等于独占宿主 CPU。每次使用独立临时数据库和凭证，归档后销毁容器和数据卷，不使用开发环境。

业务请求经过 Nginx，前端容器计入预算但不施加浏览器负载。指标每秒原子写入证据卷，发压端只读采集，避免与业务争抢 Tomcat 连接；关键核对等待新快照，过期或不完整证据仍使测量无效。实验关闭定时补偿、超时处理、对账与定时预热，启用提交/SQL 观测；这些差异会写入报告，结果不等同于启用全部后台任务的生产容量。

HTML 报告和完整证据分别保存为 Actions artifacts；配置 Pages 后，main 会将 HTML 发布到报告网站，并通过仓库 About 的 Website 打开。参数、设置与结论边界见 [实验说明](./experiments/README.md)。

## 9. 重置数据

日常操作使用 `stop` 保留数据。仅在明确需要清空全部本地数据时执行 `docker compose down -v`，这会删除 MySQL、Redis 和 Kafka 的数据卷。再次启动中间件后，由后端 Flyway 创建业务表；演示数据仍需显式执行 `bash scripts/db/seed-demo.sh` 导入。
