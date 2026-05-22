# 闪铺(shanpu) 开发里程碑

闪铺按 M1~M6 顺序推进，每个里程碑覆盖一组紧密相关的能力，前一个未达到验收标准则不进入下一个。每个里程碑包含**目标 / 交付物 / 验收标准 / 前置依赖**四个固定子节。

任意里程碑内涉及到 [BACKEND.md](./BACKEND.md)、[FRONTEND.md](./FRONTEND.md)、[DEPLOYMENT.md](./DEPLOYMENT.md) 中的内容时，对应文档章节与代码在同一里程碑内同步落地。

---

## M1 脚手架与用户模块

### 目标

仓库可启动，用户能注册登录，JWT + ThreadLocal 链路打通，新用户自动获得 10000 初始积分。

### 交付物

- `backend/` Maven 工程：`pom.xml`、`ShanpuApplication`、`application.yml`（含 `local` / `docker` 两 profile）、MyBatis 与 Druid 连接池、Jackson 序列化配置。
- 通用基础设施：`Result<T>`、`ResultCode`、`BusinessException`、`GlobalExceptionHandler`、`JwtUtil`、`UserContext`、`AuthInterceptor`、`WebMvcConfig`。
- 数据表：`t_user`、`t_point_log`。
- 业务代码：`UserController`、`UserService(Impl)`、`UserMapper`、`PointLogMapper`、相关 DTO/VO。
- 接口：`POST /api/users/register`、`POST /api/users/login`、`POST /api/users/logout`、`GET /api/users/me`、`GET /api/users/me/points`、`GET /api/health`。
- 文档：BACKEND.md 第 1~4、10（t_user/t_point_log 部分）、13.1 节内容落地完成。

### 验收标准

- `mvn -B clean test` 全绿。
- 调用 `POST /api/users/register` 注册新用户，响应 `data.points = 10000`。
- 数据库 `t_point_log` 中可查到一条 `biz_type=1`、`delta=10000`、`balance_after=10000` 的记录。
- 调用 `POST /api/users/login` 返回的 token 携带 `Authorization: Bearer <token>` 请求 `/api/users/me` 成功返回用户信息。
- 不带 token 或 token 过期访问 `/api/users/me` 返回 HTTP 401。
- 调用 `POST /api/users/logout` 后，使用同一枚 token 再次请求 `/api/users/me` 返回 HTTP 401（服务端 session 已删除）；同一用户重新登录拿到的新 token 立即可用，不受影响。
- 登录成功后 Redis 中可看到 `shanpu:user:session:{tokenHash}` 存在且 TTL ≤ 7 天；调用 logout 后该 Key 立即被 DEL，再次 `GET` 返回 nil。
- 同一 IP 60 秒内连续 6 次调用 `POST /api/users/login`，第 6 次返回 HTTP 429（验证限流，限流注解可在 M3 接入；M1 阶段如未实现则记录为已知项进入 M3）。
- `/api/health` 返回 `mysql=UP`，其他依赖在 M3/M4 接入后变为 `UP`。

### 前置依赖

无。

---

## M2 商品与普通下单

### 目标

商品只读接口完成，普通商品下单链路以 MySQL 行锁事务直连方式跑通，作为后续 k6 压测基线。

### 交付物

- 数据表：`t_product`、`t_order`。
- 种子数据：5 个普通商品（库存与价格已在 [DEPLOYMENT.md §5.1](./DEPLOYMENT.md) 给出）。
- 业务代码：`ProductController`、`ProductService(Impl)`、`OrderController`、`OrderService(Impl)`、`ProductMapper`、`OrderMapper`、相关 DTO/VO/枚举（`OrderStatus`、`OrderSource`）。
- 接口：`GET /api/products`、`GET /api/products/{id}`、`POST /api/products/{id}/order`、`GET /api/orders`、`GET /api/orders/{orderId}`。
- 文档：BACKEND.md 第 10（t_product/t_order 部分）、13.2、13.4 节内容落地完成。

### 验收标准

- 单元测试覆盖 `OrderService.createNormalOrder`：库存不足抛 `STOCK_SHORTAGE`、积分不足抛 `POINTS_NOT_ENOUGH`、正常路径写入订单与流水。
- 集成验证：取一个库存 10 的商品，启动 50 并发持续 30 秒下单，最终：
  - `t_order` 中该商品 source=1、status=PAID 的记录数恰好为 10。
  - 商品 `stock` 归 0。
  - `t_point_log` 中扣分流水数等于成功订单数。
  - 无任何超卖记录。
- 列表接口分页正确，`page=1 size=20` 返回前 20 条 `status=1` 商品。
- 普通下单接口在压力下错误率 < 5%（业务错误如积分不足不计入）。

### 前置依赖

M1 完成。

---

## M3 缓存与限流

### 目标

`CacheClient` 上线，商品 / 活动详情查询走缓存空值 + 逻辑过期；`@RateLimit` 注解全链路可用，登录与下单接口接入限流。

### 交付物

- 业务代码：`CacheClient`（三种查询模式 + 工具方法）、`RedisConfig`（含 `DefaultRedisScript` 两个：`seckill.lua`、`rate-limit.lua` 的 Bean）、`RateLimit` 注解、`RateLimitAspect`、`RedisKeys` 常量类。
- Lua 脚本：`rate-limit.lua`。
- 商品详情接入逻辑过期、商品列表接入空值缓存、订单详情接入用户级 30s 缓存。
- 接口注解：登录 / 注册按 IP 限流，下单按用户限流（具体参数见 [BACKEND.md §5.4](./BACKEND.md)）。
- 文档：BACKEND.md 第 5、6、11 节内容落地完成。

### 验收标准

- 缓存空值：DB 中不存在的 `product_id=999`，连续 100 次 `GET /api/products/999`，MyBatis Mapper 调用次数仅 1（首次穿透），后续命中空值占位。
- 逻辑过期：将某商品 Redis 缓存的 `expireTime` 改至过去 1 分钟，发起一次查询：响应仍为旧数据，后台日志显示重建任务被触发，2~3 秒后 Redis 中 `expireTime` 已更新。
- 限流：60 秒内同一 IP 连续 6 次 `POST /api/users/login`，第 6 次响应 HTTP 429、`code=42900`。
- 单元测试覆盖 `CacheClient` 三种模式的命中、未命中、空值占位、过期重建分支。
- `/api/health` 中 `redis=UP`。

### 前置依赖

M1、M2 完成。

---

## M4 三档秒杀核心与 Kafka 异步

### 目标

三档秒杀活动全链路跑通——普惠 DB 直连、限时 Redis+Lua 同步、超级 Redis+Lua+Kafka 异步；统一 `/grab` 入口由 Service 按 `activity.tier` 自动路由；一人一单与防超卖在 Redis 与 MySQL 双层兜底；超级档异步消息通过 Kafka 落库，DLQ 与定时补偿可演示。

### 交付物

- 数据表：`t_seckill_activity`（含 `tier` 列与 `idx_tier_status` 索引）、`t_order_dlq_log`。
- 种子数据：9 条三档秒杀活动（普惠 `1001/1002/1003`、限时 `2001/2002/2003`、超级 `3001/3002/3003`，详见 [DEPLOYMENT.md §5.1](./DEPLOYMENT.md)）。
- Lua 脚本：`seckill.lua`、`seckill-rollback.lua`（限时与超级两档复用）。
- 业务代码：`SeckillController`（统一 `/grab` 入口）、`SeckillService` 含 `grab(activityId)` 路由方法与三档私有方法 `grabInclusive` / `grabFlash` / `grabSuper` 及共享事务模板 `executeSyncOrder`、`SeckillActivityMapper`、`SeckillProducer`、`SeckillConsumer`、`KafkaConfig`、`KafkaTopics` 常量、`SeckillOrderMsg` DTO。
- 定时任务：`SeckillWarmupJob`（含 `warmupOne(id)` 供档位热切换调用）、`StockReconcileJob`。
- 接口：`GET /api/seckills`（支持 `tier` 与 `status` 过滤）、`GET /api/seckills/{id}`、`POST /api/seckills/{id}/grab`。
- 文档：BACKEND.md 第 7、8、9（warmup/reconcile）、10（t_seckill_activity 含 tier 列）、12、13.3 节内容落地完成。

### 验收标准

- **普惠档**：启动 100 并发对 `activity_id=1001`（库存 5000）持续 60 秒同步抢购：
  - 成功 `code=0` 响应数 ≤ 5000，库存归零后所有请求返回 `code=41000`。
  - 同一用户 2 次成功后再次请求返回 `code=40900`（一人一单由 `uk_user_activity` 兜底）。
  - 数据库 `t_order` 中 `source=2 AND activity_id=1001 AND status=PAID` 的记录数等于成功响应数，无任何超卖。
  - `t_seckill_activity.total_stock` 与"5000 - 成功订单数"严格一致（行锁事务正确性）。
- **限时档**：启动 100 并发对 `activity_id=2001`（库存 500）异步抢购 30 秒：
  - 成功 `code=0` 响应数恰好 500。
  - 之后所有请求返回 `code=41000`（已售罄）。
  - 同一用户 2 次成功后再次请求返回 `code=40900`（重复购买）。
  - 数据库 `t_order` 中 `source=3 AND activity_id=2001 AND status=PAID` 的记录数恰好 500，无任何超卖。
  - 扣分流水数等于成功订单数。
- **超级档**：启动 100 并发对 `activity_id=3001`（库存 200）异步抢购 30 秒：
  - 成功 `code=0` 响应数恰好 200，全部返回 `status: 'PENDING'`。
  - 之后所有请求返回 `code=41000`（已售罄）。
  - 同一用户 2 次成功后再次请求返回 `code=40900`（重复购买）。
  - 数据库 `t_order` 中 `source=4 AND activity_id=3001 AND status=PAID` 的记录数恰好 200，无任何超卖。
  - `SELECT COUNT(*) FROM t_point_log WHERE biz_type=2 AND biz_ref IN (SELECT order_id FROM t_order WHERE activity_id=3001 AND source=4 AND status='PAID')` 恰好 200——扣分流水数等于成功订单数，验证消费端幂等门生效、无重复扣积分。
- **档位热切换**：手动执行 `UPDATE t_seckill_activity SET tier = 3 WHERE id = 1002`，调 `CacheClient.evict(...)` 与 `SeckillWarmupJob.warmupOne(1002)` 后，发起 `POST /api/seckills/1002/grab` 响应包含 `status: 'PENDING'` 且 Redis `shanpu:seckill:stock:1002` 已写入。
- **故障注入（仅超级档）**：临时在 `SeckillConsumer.handle` 抛 `RuntimeException`，触发 3 次重试后消息进入 `shanpu.seckill.order.dlq`；恢复异常后，启动 `KafkaDlqRescueJob` 手动触发，消息回到主 topic 被消费、订单成功落库、`t_order_dlq_log` 中记录失败信息。
- **故障注入（仅超级档）**：压测进行中执行 `docker compose stop kafka-1`，秒杀接口持续成功率 ≥ 99%（验证 `acks=all` + 副本 3 下单 broker 故障消息不丢）；恢复后 `docker compose start kafka-1` + `kafka-topics.sh --describe --topic shanpu.seckill.order` 显示 ISR 自动恢复到 3 副本。
- `SeckillWarmupJob` 在活动开始前 60 分钟内执行后，限时与超级档活动的 `shanpu:seckill:stock:{id}` 与 `shanpu:seckill:activity:{id}` 已写入 Redis；普惠档仅 `shanpu:seckill:activity:{id}` 写入。
- `/api/health` 中 `kafka=UP`。

### 前置依赖

M3 完成。

---

## M5 前端开发与兜底任务

### 目标

`web/` 全部页面可承载完整用户旅程；`OrderTimeoutJob` 在生产链路兜底未支付 / 异步落单失败的订单。

### 交付物

- 前端工程：`web/` 下 `package.json`、`next.config.ts`、`tsconfig.json`、`tailwind.config.ts`、`components.json` 及全部 `app/`、`components/`、`lib/`、`hooks/`、`types/` 文件（详见 [FRONTEND.md §3](./FRONTEND.md)）。
- 全部页面：`/`、`/products`、`/products/[id]`、`/seckill`、`/seckill/[id]`、`/login`、`/register`、`/orders`、`/orders/[id]`、`/me`。
- 全部关键组件：`ProductCard`、`SeckillCard`、`SeckillCountdown`、`PointsBadge`、`OrderStatusBadge`、`AuthGuard`、`SiteHeader`、`SiteFooter`、`Providers`。
- HTTP 客户端：`lib/api/client.ts` + `lib/api/{user,product,seckill,order}.ts`。
- 后端补强：`OrderTimeoutJob`、`KafkaDlqRescueJob` 完整可调度（M4 阶段框架已搭，本里程碑补完）。
- 文档：FRONTEND.md 全部章节内容落地完成；BACKEND.md 第 9（OrderTimeoutJob、KafkaDlqRescueJob）节内容落地完成。

### 验收标准

- 端到端用户旅程：浏览器手动注册新用户 → 自动跳转登录 → 登录成功后 header 显示 `10000` 积分 → 在 `/products/1` 点击下单成功，header 积分扣减并 toast 提示 → 在 `/orders` 看到该订单 → 进入普惠活动 `/seckill/1001` 点击抢购，响应 `PAID` 直跳订单详情，header 积分再次扣减 → 进入超级活动 `/seckill/3001`，倒计时到 0 后点击抢购，响应 `PENDING` → `/orders/<orderId>` 在 5 秒内通过轮询变为 `PAID`，header 积分第三次扣减 → 在 `SiteHeader` 下拉菜单点击"登出"，跳转 `/login` 并 toast"已登出"，回退后访问 `/me` 被 `AuthGuard` 拦截回登录页。
- 关闭后端 8080，前端任意操作均 toast 网络异常，无白屏。
- 注入一条 `status=PENDING_PAY`、`created_at = now - 20min` 的订单（手动 SQL），1 分钟内被 `OrderTimeoutJob` 改为 `CANCELLED`；若该订单是秒杀订单（`source != 1`），对应 Redis 库存回补 1、`bought` 集合移除该用户。
- 限流触发场景：同一用户对秒杀活动 1 秒内连续点击 6 次抢购按钮，前端 toast"操作过于频繁"。

### 前置依赖

M4 完成。

---

## M6 Docker 一键启动与 k6 压测

### 目标

`docker compose up` 一键启动全栈；k6 三场景压测可重复执行并产出对比报告；场景 D 缓存层级对比量化 Caffeine L1 收益；超卖检测通过。

### 交付物

- 部署工件：`deploy/docker-compose.yml`（每服务 `deploy.resources.limits` 显式声明 CPU 与内存上限）、`deploy/.env.example`、`deploy/mysql/init/01_schema.sql`、`deploy/mysql/init/02_seed.sql`（含 500 压测用户 `u0001~u0500`）、`deploy/mysql/conf/my.cnf`、`deploy/redis/redis.conf`、`deploy/kafka/server.properties`。
- 容器化构建：`backend/Dockerfile`（含 `JAVA_TOOL_OPTIONS=-Xmx1g -Xms512m`）、`web/Dockerfile`。
- 压测脚本：`load/lib/auth.js`、`load/scenario-a-inclusive.js`、`load/scenario-b-redis-sync.js`、`load/scenario-c-redis-kafka.js`、`load/scenario-d-cache-comparison.js`。
- 辅助脚本：`scripts/dev-up.sh`、`scripts/seed-users.sh`、`scripts/k6-bench-reset.sh`、`scripts/k6-bench-restore.sh`、`scripts/k6-run.sh`。
- 文档：DEPLOYMENT.md 全部章节内容落地完成；本里程碑末尾的"压测对比表"与"缓存层级对比表"由人工填入实测数据。

### 验收标准

- 干净机器（无任何镜像缓存）执行：
  ```bash
  git clone <repo> shanpu && cd shanpu/deploy
  cp .env.example .env
  docker compose up -d
  ```
  在 5 分钟内所有服务健康，`docker compose ps` 全部 `healthy`。
- 浏览器访问 `http://localhost:3000` 可完成 M5 全部端到端流程（注册 → 登录 → 普通下单 → 秒杀抢购 → 订单 PAID）。
- 执行 `bash scripts/k6-run.sh`，四场景全部跑完，`load/reports/<timestamp>/` 下生成 4 份 JSON 摘要。
- 三场景压测后 SQL 验证均无超卖：
  - `SELECT COUNT(*) FROM t_order WHERE source=2 AND activity_id=1001` ≤ 20000（普惠档压测注入库存）。
  - `SELECT COUNT(*) FROM t_order WHERE source=3 AND activity_id=2001` ≤ 20000（限时档压测注入库存）。
  - `SELECT COUNT(*) FROM t_order WHERE source=4 AND activity_id=3001` ≤ 20000（超级档压测注入库存）。
- 压测峰值 `docker stats` 抓拍：`backend` / `mysql` / `kafka-1/2/3` 每服务内存使用 ≤ `deploy.resources.limits.memory` 的 90%，无 OOM Kill 事件（`docker inspect <container> --format '{{.State.OOMKilled}}'` 全为 `false`）。
- 场景 C 压测结束后流水对账：`SELECT COUNT(*) FROM t_point_log WHERE biz_type=2 AND biz_ref IN (SELECT order_id FROM t_order WHERE activity_id=3001 AND source=4 AND status='PAID')` 等于 `SELECT COUNT(*) FROM t_order WHERE source=4 AND activity_id=3001 AND status='PAID'`——验证压测全程消息端到端无丢失、无重复消费导致的积分流水偏差。
- 三场景吞吐与延迟呈预期梯度：A 普惠的 RPS 上限由 MySQL 单行锁串行化决定；B 限时在 Redis 路径减负后吞吐高于 A，但因落库仍同步 DB，提升幅度受 MySQL 写入约束；C 超级因接口 RT 不含 DB 写入，应显著高于 A/B。本基线在 7.6 GiB WSL2 上测得，结论可外推但绝对值随硬件变化。
- 场景 D 双轮对比（`shanpu.cache.local.activity.enabled` true / false 各跑一遍，中间重启应用 + `FLUSHDB`）满足以下四项门槛：
  - **命中率**：`GET /api/admin/cache/stats` 显示 `activityLocal.hitRate ≥ 0.95`。
  - **性能提升**：活动接口 p99 下降 ≥ 60%，活动 Key Redis `GET` ops/s 下降 ≥ 95%（差分自 `redis-cli INFO commandstats`）。
  - **对照组隔离**：商品接口 p99 两轮偏差 ≤ 5%（验证 L1 未影响未注册路径）。
  - **业务正确性**：压测期间任意时刻 `GET /api/seckills/3001` 返回数据与 MySQL 一致（误差窗口 ≤ L1 TTL = 1s）。

### 前置依赖

M5 完成。

### 压测对比表（待 M6 实跑后填入）

| 场景 | 接口 | VU 峰值 | 平均 RPS | p50 (ms) | p95 (ms) | p99 (ms) | HTTP 错误率 | 业务错误率 | 成功订单数 | 超卖 | 瓶颈观察 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A 普惠（tier=1） | `POST /api/seckills/1001/grab` | 50 |  |  |  |  |  |  | ≤500 | 否 | DB 单行 `id=1001` 行锁串行化 |
| B 限时（tier=2） | `POST /api/seckills/2001/grab` | 150 |  |  |  |  |  |  | ≤500 | 否 | DB 行锁（Redis 减负但落库仍同步） |
| C 超级（tier=3） | `POST /api/seckills/3001/grab` | 300 |  |  |  |  |  |  | ≤500 | 否 | Kafka producer send ack 延迟 |

业务错误率 = `(seckill_duplicate_total + seckill_sold_out_total) / iterations`，独立于 HTTP 错误率（业务码不计入 `http_req_failed`，见 DEPLOYMENT.md §7.5）。成功订单数上限 500 = 一人一单约束 × 500 压测用户池。

测试硬件、Docker 资源限制（按 DEPLOYMENT.md §2.4）、差异化阶梯（A 0→50/3m、B 0→150/3m、C 0→300/3m，详见 DEPLOYMENT.md §7.4）、瓶颈观察源（`docker stats` / `redis-cli INFO commandstats` 差分 / `kafka-consumer-groups.sh --describe` lag）记录在 `load/reports/<timestamp>/README.md`。

### 缓存层级对比表（场景 D，待 M6 实跑后填入）

`load/scenario-d-cache-comparison.js` 同一次压测内并行打两个 scenario：实验组 `activity_l1_l2` 调用 `GET /api/seckills/3001`（L1 + L2），对照组 `product_l2_only` 调用 `GET /api/products/1`（仅 L2）。两个 scenario 共享 VU 阶梯 0→50→100 持续 3 分钟（与 backend Tomcat 100 线程上限匹配，详见 DEPLOYMENT.md §2.4）。

第 1 轮 `shanpu.cache.local.activity.enabled=true`，第 2 轮 `false`，中间重启应用 + `FLUSHDB` 清缓存。

| 接口 | 缓存层级 | L1 状态 | 平均 RPS | p50 (ms) | p95 (ms) | p99 (ms) | Redis GET ops/s | L1 hitRate |
|---|---|---|---|---|---|---|---|---|
| `GET /api/seckills/3001` | Caffeine → Redis | 开启 |  |  |  |  |  |  |
| `GET /api/seckills/3001` | Redis | 关闭 |  |  |  |  |  | — |
| `GET /api/products/1` | Redis | 开启 |  |  |  |  |  | — |
| `GET /api/products/1` | Redis | 关闭 |  |  |  |  |  | — |

Redis ops 取自 `redis-cli INFO commandstats` 两轮压测前后差分；L1 hitRate 取自 `GET /api/admin/cache/stats`。验收门槛参见 §M6 验收标准最后一条。

---

## 全局风险跟踪

| 风险 | 影响里程碑 | 应对 |
|---|---|---|
| Java 17 / Spring Boot 3.x 与 Lombok 兼容性 | M1 | 使用 Lombok 1.18.32+ |
| 3 节点 KRaft controller quorum 等待 ISR 成立 | M4、M6 | 健康检查间隔 10 秒，单 broker 最多等待 120 秒；`backend.depends_on` 等三个 broker 全部 `service_healthy` 才启动 |
| 实习生不熟悉 RSC / Server Component | M5 | 业务页面默认 client 组件，仅首页与列表用 RSC，且不依赖 cookies |
| 压测期间 Redis 内存增长 | M6 | `maxmemory 256mb` + `allkeys-lru`；单 k6 场景结束后 `k6-bench-restore.sh` 清理对应活动的库存与已购集合 |
| Lua 脚本未挂载 / 路径错误 | M3、M4 | `RedisConfig` 用 `ClassPathResource` 显式加载 `src/main/resources/lua/*.lua`，启动期间日志打印 sha |
| 压测前置脚本未执行导致库存不足以打稳态 | M6 | `scripts/k6-run.sh` 每场景前自动调 `k6-bench-reset.sh` 把目标活动 `total_stock` 重置为 20000；脚本失败立即中止整轮压测 |
| 容器内存超限被 cgroup OOM Kill | M6 | 每服务 `deploy.resources.limits.memory` 显式声明 + JVM `-Xmx` 留 25% 余量；M6 验收新增 `docker stats` 峰值检查与 `OOMKilled=false` 断言 |
