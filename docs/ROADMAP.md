# 闪铺(shanpu) 开发里程碑

闪铺按 M1~M6 顺序推进，每个里程碑覆盖一组紧密相关的后端、前端、部署与验证能力。前一个里程碑达到交付标准后再进入下一个里程碑。

任意里程碑涉及 [BACKEND.md](./BACKEND.md)、[FRONTEND.md](./FRONTEND.md)、[DEPLOYMENT.md](./DEPLOYMENT.md) 中的内容时，对应文档章节与代码在同一里程碑内同步落地。

---

## M1：基础骨架 + 用户模块 + 前端登录 + Docker 一键启动

**后端**：

- `backend/` Spring Boot 单模块脚手架：`pom.xml`、`ShanpuApplication`、`application.yml`，配置 `dev` / `test` / `prod` 三套 profile，密钥与连接参数全部走环境变量。
- MyBatis、Druid 连接池、Jackson 序列化配置、统一 JSON 日志。
- 通用基础设施：`Result<T>`、`ResultCode`、`BusinessException`、`GlobalExceptionHandler`、参数校验。
- JWT + Redis session 白名单 + `AuthInterceptor` + `UserContext`，登出通过删除服务端 session 让 token 立即失效。
- 数据表：`t_user`、`t_point_log`；新用户注册自动获得 10000 初始积分。
- 用户接口：`POST /api/users/register`、`POST /api/users/login`、`POST /api/users/logout`、`GET /api/users/me`、`GET /api/users/me/points`、`GET /api/health`。
- `backend/Dockerfile` 多阶段构建（Maven build → JRE alpine runtime，`SPRING_PROFILES_ACTIVE=prod`、`JAVA_TOOL_OPTIONS=-Xmx1g -Xms512m -XX:MaxMetaspaceSize=128m`）、`backend/.dockerignore`。

**前端**：

- `frontend/` Next.js 15 + TypeScript + Tailwind CSS v4 + shadcn/ui 脚手架。
- `app/layout.tsx`、`app/globals.css`、`components/providers.tsx`、`SiteHeader`、`SiteFooter`。
- 基础视觉系统：站点色板、字体、按钮、卡片、Badge、表单控件。
- `/login`、`/register`、`/me` 三个页面。
- `lib/api/client.ts`：统一 `fetch`、`Result<T>` 解析、Bearer token 注入、401 清理登录态、429 统一错误。
- `lib/api/user.ts`、`types/api.ts`、`types/user.ts`、`hooks/use-auth.ts`。
- Zustand `useAuthStore` 持久化 token 与用户信息，localStorage key 为 `shanpu.auth`。
- `AuthGuard` 保护 `/me`，未登录跳转 `/login?next=<encoded>`。
- `next.config.ts` 启用 `output: 'standalone'`；`frontend/Dockerfile` 三阶段（deps/build/runtime，corepack pnpm）；`frontend/.dockerignore`。
- 运行时配置注入：`app/layout.tsx` 作为 server component 读取 `process.env.PUBLIC_API_BASE_URL` 并写入 `<script id="__shanpu_config__" type="application/json">`；`lib/config/public-config.ts` 浏览器侧从 script 标签读取、SSR 侧依优先级读 `INTERNAL_API_BASE_URL` → `PUBLIC_API_BASE_URL` → `NEXT_PUBLIC_API_BASE_URL`。

**部署**：

- 根目录 `docker-compose.yml` 编排 mysql / redis / backend / frontend 四个服务，前两个声明 `deploy.resources.limits`，backend 与 frontend 通过 healthcheck 串联 `depends_on` 启动顺序。
- 根目录 `.env.example` 包含 `MYSQL_ROOT_PASSWORD` / `MYSQL_DATABASE` / `REDIS_PASSWORD` / `JWT_SECRET` 凭据与 `MYSQL_PORT` / `REDIS_PORT` / `BACKEND_PORT` / `FRONTEND_PORT` / `PUBLIC_API_HOST` 端口主机配置。
- `mysql/init/01_schema.sql` 创建 `t_user` / `t_point_log`，`mysql/init/02_seed.sql` 写入测试用户 `summer`（密码 `123456`，初始积分 10000，带注册赠送积分流水）；MySQL 与 Redis 运行参数写在 `docker-compose.yml` 的对应服务 `command` 中。

**交付**：

- `mvn -B clean test` 通过。
- `pnpm lint` 与 `pnpm build` 通过。
- 调用注册接口返回 `data.points = 10000`，数据库 `t_point_log` 写入初始积分流水。
- 登录成功后，携带 `Authorization: Bearer <token>` 请求 `/api/users/me` 成功返回用户信息。
- 不带 token 或 token 过期访问 `/api/users/me` 返回 HTTP 401。
- 调用 logout 后，同一枚 token 再次请求 `/api/users/me` 返回 HTTP 401；同一用户重新登录拿到的新 token 可用。
- 登录成功后 Redis 中存在 `shanpu:user:session:{tokenHash}`，TTL 不超过 7 天；logout 后该 key 被删除。
- 浏览器可完成注册、登录、查看个人中心、查看积分余额、登出；后端不可达时显示网络异常提示且页面稳定。
- `/api/health` 返回 `mysql=UP`、`redis=UP`。
- 干净机器在仓库根目录执行 `cp .env.example .env && docker compose up -d`，90 秒内 `docker compose ps` 四个服务均 `(healthy)`；浏览器访问 `http://localhost:3000` 可完成上述全部端到端流程。
- 修改 `.env` 中 `PUBLIC_API_HOST` 或 `BACKEND_PORT` 后 `docker compose up -d frontend` 重启容器即生效，无需重 build 镜像。
- 文档：BACKEND.md 第 1~4、10（`t_user` / `t_point_log` 部分）、13.1 节、FRONTEND.md 基础工程 / 用户页面 / 鉴权状态 / §7.3 运行时配置注入、DEPLOYMENT.md 第 1~4 节、§5.1 / §5.2、第 6 节一键启动与第 8 节运行环境内容落地完成。

**前置依赖**：无。

---

## M2：商品浏览 + 普通下单 + 订单基础

**后端**：

- 数据表：`t_product`、`t_order`。
- 种子数据：5 个普通商品，库存与价格遵循 [DEPLOYMENT.md §5.1](./DEPLOYMENT.md)；初始化用户数据由 M1 的 `02_seed.sql` 提供。
- 业务代码：`ProductController`、`ProductService(Impl)`、`OrderController`、`OrderService(Impl)`、`ProductMapper`、`OrderMapper`、相关 DTO/VO/枚举。
- 商品接口：`GET /api/products`、`GET /api/products/{id}`。
- 普通下单接口：`POST /api/products/{id}/order`。
- 订单接口：`GET /api/orders`、`GET /api/orders/{orderId}`。
- 普通下单使用 MySQL 行锁事务，完成扣库存、扣积分、写订单、写积分流水。

**前端**：

- `/` 首页展示热门商品，读取 `GET /api/products?page=1&size=8`。
- `/products` 商品列表页，支持分页展示。
- `/products/[id]` 商品详情页，展示商品信息与“用积分兑换”按钮。
- `/orders` 我的订单列表页，支持状态 tab 切换。
- `/orders/[id]` 订单详情页。
- `ProductCard`、`OrderStatusBadge`、`PointsBadge`。
- `lib/api/product.ts`、`lib/api/order.ts`、`types/product.ts`、`types/order.ts`。
- 普通下单成功后跳转订单详情，并刷新本地积分余额。

**交付**：

- `mvn -B clean test` 通过，`pnpm lint` 与 `pnpm build` 通过。
- 单元测试覆盖 `OrderService.createNormalOrder`：库存不足、积分不足、正常路径写入订单与流水。
- 集成验证：取一个库存 10 的商品，启动 50 并发持续 30 秒下单，最终 `PAID` 订单数为 10、商品库存归 0、扣分流水数等于成功订单数，无超卖。
- 商品列表 `page=1&size=20` 返回前 20 条 `status=1` 商品。
- 普通下单接口压力测试错误率低于 5%，库存不足与积分不足等业务错误不计入错误率。
- 浏览器可完成商品浏览、商品详情、普通下单、订单列表、订单详情；库存不足、积分不足、未登录、网络异常都有明确提示。
- 文档：BACKEND.md 第 10（`t_product` / `t_order` 部分）、13.2、13.4 节与 FRONTEND.md 的商品、订单基础页面内容落地完成。

**前置依赖**：M1 完成。

---

## M3：Redis 缓存 + 限流 + 前端错误体验

**后端**：

- `CacheClient` 上线，支持缓存空值、逻辑过期、互斥重建三种查询模式与通用工具方法。
- `RedisConfig`、`RedisKeys`、`rate-limit.lua`。
- `@RateLimit` 注解、`RateLimitAspect`、滑动窗口限流 Lua 脚本。
- 商品详情接入逻辑过期，商品列表接入空值缓存，订单详情接入用户级 30 秒缓存。
- 登录 / 注册按 IP 限流，下单按用户限流，参数遵循 [BACKEND.md §5.4](./BACKEND.md)。
- `/api/health` 覆盖 Redis 状态。

**前端**：

- `ApiError` 统一错误分流：401 跳转登录，429 toast “操作过于频繁”，业务错误优先展示后端 `msg`。
- 商品详情、订单详情增加 loading、empty、error 状态。
- 登录、注册、普通下单按钮增加提交中禁用状态。
- `error.tsx`、`not-found.tsx` 兜底 RSC 异常与不存在资源。
- 个人中心积分徽标增加 5 秒内存缓存，减少 `/api/users/me/points` 请求。

**交付**：

- `mvn -B clean test` 通过，`pnpm lint` 与 `pnpm build` 通过。
- 缓存空值：DB 中不存在的 `product_id=999` 连续请求 100 次，MyBatis Mapper 仅首次查询 DB。
- 逻辑过期：将某商品 Redis 缓存的 `expireTime` 改至过去 1 分钟，请求仍返回旧数据，后台触发重建，2~3 秒后 Redis 中 `expireTime` 更新。
- 限流：同一 IP 60 秒内连续 6 次 `POST /api/users/login`，第 6 次响应 HTTP 429、`code=42900`。
- 单元测试覆盖 `CacheClient` 命中、未命中、空值占位、过期重建分支。
- `/api/health` 返回 `redis=UP`。
- 前端触发限流时显示“操作过于频繁”；商品不存在时展示稳定空态或 404；Redis 或后端不可达时，业务操作提示网络异常且页面稳定。
- 文档：BACKEND.md 第 5、6、11 节与 FRONTEND.md 错误处理、toast、页面状态内容落地完成。

**前置依赖**：M1、M2 完成。

---

## M4：三档秒杀核心 + Kafka 异步 + 秒杀页面

**后端**：

- 数据表：`t_seckill_activity`（含 `tier` 列与 `idx_tier_status` 索引）、`t_order_dlq_log`。
- 种子数据：9 条三档秒杀活动，普惠 `1001/1002/1003`、限时 `2001/2002/2003`、超级 `3001/3002/3003`。
- Lua 脚本：`seckill.lua`、`seckill-rollback.lua`。
- `SeckillController` 提供统一 `/grab` 入口。
- `SeckillService.grab(activityId)` 按 `activity.tier` 路由 `grabInclusive` / `grabFlash` / `grabSuper`。
- 普惠档：MySQL 行锁 + 同步事务。
- 限时档：Redis + Lua 预扣库存 + 同步事务落库。
- 超级档：Redis + Lua 预扣库存 + Kafka 异步落库，响应 `PENDING`。
- `SeckillActivityMapper`、`SeckillProducer`、`SeckillConsumer`、`KafkaConfig`、`KafkaTopics`、`SeckillOrderMsg`。
- `SeckillWarmupJob`、`StockReconcileJob`。
- 秒杀接口：`GET /api/seckills`、`GET /api/seckills/{id}`、`POST /api/seckills/{id}/grab`。
- `/api/health` 增加 Kafka 状态。

**前端**：

- `/` 首页增加正在秒杀活动区，读取 `GET /api/seckills?status=ACTIVE`。
- `/seckill` 秒杀专区，展示进行中与即将开始活动，支持 `tier` 过滤。
- `/seckill/[id]` 活动详情页，展示倒计时、库存进度、档位徽标、抢购按钮。
- `SeckillCard`、`SeckillTierBadge`、`SeckillCountdown`。
- `lib/api/seckill.ts`、`types/seckill.ts`、`hooks/use-countdown.ts`。
- 抢购响应分流：`PAID` toast 成功并跳转 `/orders/{orderId}`；`PENDING` 跳转 `/orders/{orderId}` 等待订单详情轮询。
- 抢购按钮按活动未开始、已结束、售罄、提交中状态禁用，并做 1 秒前端节流。

**交付**：

- `mvn -B clean test` 通过，`pnpm lint` 与 `pnpm build` 通过。
- 普惠档并发验证：`activity_id=1001` 不超卖，一人一单由 DB 唯一键兜底，`t_seckill_activity.total_stock` 与成功订单数一致。
- 限时档并发验证：`activity_id=2001` 成功数等于库存，售罄后返回 `code=41000`，重复购买返回 `code=40900`，DB 订单与扣分流水一致。
- 超级档并发验证：`activity_id=3001` 成功响应均为 `PENDING`，Kafka 消费后订单变为 `PAID`，订单数、扣分流水数与库存扣减一致。
- 档位热切换验证：更新 `tier` 后执行缓存驱逐与预热，`/grab` 按新 `tier` 返回对应状态。
- 故障注入验证：消费端异常触发重试与 DLQ，恢复后 DLQ 回投可完成订单落库。
- `SeckillWarmupJob` 在活动开始前 60 分钟内写入活动缓存；限时与超级档写入 Redis 库存与已购集合。
- `/api/health` 返回 `kafka=UP`。
- 浏览器可浏览三档秒杀活动，徽标严格按响应 `tier` 渲染；普惠与限时成功后进入 `PAID` 订单；超级成功后进入待确认订单详情；重复抢购、售罄、未开始、已结束均显示对应提示。
- 文档：BACKEND.md 第 7、8、9（warmup/reconcile）、10（`t_seckill_activity`）、12、13.3 节与 FRONTEND.md 秒杀页面、组件、API 映射内容落地完成。

**前置依赖**：M3 完成。

---

## M5：完整用户旅程 + 异步轮询 + 兜底任务

**后端**：

- `OrderTimeoutJob` 处理超时未支付与异常待处理订单。
- `KafkaDlqRescueJob` 处理 DLQ 消息回投与失败次数记录。
- 超级秒杀消费链路补齐幂等、补偿与错误记录。
- 订单状态与积分流水对账补强。
- 兜底任务支持手动触发与定时调度。

**前端**：

- `/orders/[id]` 对超级秒杀订单启用 `use-seckill-result`。
- 轮询策略：1 秒一次，最多 10 次；订单转为 `PAID` 后停止轮询并刷新订单与积分。
- 轮询命中 `CANCELLED` 时停止轮询并展示失败状态。
- 轮询达到上限仍为待处理状态时展示手动刷新入口。
- 移动端布局完善：header、商品列表、秒杀列表、订单列表单栏适配。
- 所有页面补齐 loading、empty、error、disabled 状态。
- `sonner` toast 统一成功、失败、限流、网络异常文案。

**交付**：

- `mvn -B clean test` 通过，`pnpm lint` 与 `pnpm build` 通过。
- 浏览器端到端用户旅程可完整完成：注册、登录、商品下单、查看订单、普惠抢购、超级抢购、订单轮询变为 `PAID`、登出。
- 登录后个人中心显示 10000 初始积分；普通下单、普惠抢购、超级订单转 `PAID` 后积分同步扣减。
- 进入普惠活动 `/seckill/1001` 抢购成功后，响应 `PAID` 并跳转订单详情。
- 进入超级活动 `/seckill/3001` 抢购成功后，响应 `PENDING` 并跳转订单详情；订单详情在 5 秒内通过轮询变为 `PAID`。
- 在 `SiteHeader` 下拉菜单点击登出后跳转 `/login`，回退访问 `/me` 被 `AuthGuard` 拦截回登录页。
- 关闭后端 8080，前端任意业务操作均提示网络异常且页面稳定。
- 手动注入 `status=PENDING_PAY`、`created_at = now - 20min` 的订单后，1 分钟内被 `OrderTimeoutJob` 改为 `CANCELLED`；秒杀订单同步回补 Redis 库存并移除已购用户。
- 同一用户对秒杀活动 1 秒内连续点击 6 次抢购按钮，前端提示“操作过于频繁”。
- 文档：FRONTEND.md 全章节内容落地完成；BACKEND.md 第 9 节中 `OrderTimeoutJob`、`KafkaDlqRescueJob` 内容落地完成。

**前置依赖**：M4 完成。

---

## M6：k6 压测全景

**后端**：

- 根目录 `docker-compose.yml` 在既有服务基础上追加 `k6` 服务（`grafana/k6:0.50`，`loadtest` profile，按需 `docker compose --profile loadtest run k6`）。
- 压测脚本：`load/lib/auth.js`、`load/scenario-a-inclusive.js`、`load/scenario-b-redis-sync.js`、`load/scenario-c-redis-kafka.js`、`load/scenario-d-cache-comparison.js`。
- 辅助脚本：`scripts/dev-up.sh`、`scripts/seed-users.sh`、`scripts/k6-bench-reset.sh`、`scripts/k6-bench-restore.sh`、`scripts/k6-run.sh`。

**前端**：M6 不涉及前端代码改动；压测目标是后端链路。

**交付**：

- 干净机器执行：

  ```bash
  git clone <repo> shanpu && cd shanpu
  cp .env.example .env
  docker compose up -d
  ```

  5 分钟内所有服务健康，`docker compose ps` 全部 `healthy`（k6 仅在 `loadtest` profile 中按需拉起，不参与默认健康检查）。
- 浏览器访问 `http://localhost:3000` 可完成 M5 全部端到端流程。
- 修改 `PUBLIC_API_HOST` 或 `BACKEND_PORT` 后，重启 frontend 容器即可生效。
- 执行 `bash scripts/k6-run.sh` 后，四个场景全部跑完，`load/reports/<timestamp>/` 生成 4 份 JSON 摘要。
- 三场景压测后 SQL 验证无超卖：
  - `SELECT COUNT(*) FROM t_order WHERE source=2 AND activity_id=1001` 不超过压测注入库存。
  - `SELECT COUNT(*) FROM t_order WHERE source=3 AND activity_id=2001` 不超过压测注入库存。
  - `SELECT COUNT(*) FROM t_order WHERE source=4 AND activity_id=3001` 不超过压测注入库存。
- 压测峰值 `docker stats` 抓拍：backend、mysql、kafka 每服务内存使用不超过 `deploy.resources.limits.memory` 的 90%，无 OOM Kill。
- 场景 C 压测结束后，超级档订单数与积分流水数一致，验证消息链路无丢失、无重复扣积分。
- 三场景吞吐与延迟呈预期梯度：普惠档受 MySQL 单行锁限制；限时档通过 Redis + Lua 降低库存争用；超级档接口响应不包含 DB 落库开销，尾延迟最低。
- 场景 D 双轮对比验证活动详情 L1 + L2 缓存收益：活动本地缓存命中率不低于 0.95，活动接口 p99 下降不低于 60%，活动 Key Redis `GET` ops/s 下降不低于 95%，商品接口两轮 p99 偏差不超过 5%。
- 文档：DEPLOYMENT.md 全章节内容落地完成；ROADMAP 压测对比表与缓存层级对比表填入实测数据。

**前置依赖**：M5 完成。

### 压测对比表

| 场景 | 接口 | VU 峰值 | 平均 RPS | p50 (ms) | p95 (ms) | p99 (ms) | HTTP 错误率 | 业务错误率 | 成功订单数 | 超卖 | 瓶颈观察 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A 普惠（tier=1） | `POST /api/seckills/1001/grab` | 100 |  |  |  |  |  |  | ≤500 | 否 | DB 单行 `id=1001` 行锁串行化 |
| B 限时（tier=2） | `POST /api/seckills/2001/grab` | 100 |  |  |  |  |  |  | ≤500 | 否 | DB 行锁（Redis 减负后落库仍同步） |
| C 超级（tier=3） | `POST /api/seckills/3001/grab` | 100 |  |  |  |  |  |  | ≤500 | 否 | Kafka producer send ack 延迟 |

业务错误率 = `(seckill_duplicate_total + seckill_sold_out_total) / iterations`，独立于 HTTP 错误率。成功订单数上限 500 = 一人一单约束 × 500 压测用户池。

测试硬件、Docker 资源限制、统一 VU 阶梯、瓶颈观察源记录在 `load/reports/<timestamp>/README.md`。

### 缓存层级对比表

`load/scenario-d-cache-comparison.js` 同一次压测内并行打两个 scenario：实验组 `activity_l1_l2` 调用 `GET /api/seckills/3001`，对照组 `product_l2_only` 调用 `GET /api/products/1`。两个 scenario 共享 VU 阶梯 0→50→100，持续 3 分钟。

第 1 轮 `shanpu.cache.local.activity.enabled=true`，第 2 轮 `false`，中间重启应用并清理缓存。

| 接口 | 缓存层级 | L1 状态 | 平均 RPS | p50 (ms) | p95 (ms) | p99 (ms) | Redis GET ops/s | L1 hitRate |
|---|---|---|---|---|---|---|---|---|
| `GET /api/seckills/3001` | Caffeine → Redis | 开启 |  |  |  |  |  |  |
| `GET /api/seckills/3001` | Redis | 关闭 |  |  |  |  |  | — |
| `GET /api/products/1` | Redis | 开启 |  |  |  |  |  | — |
| `GET /api/products/1` | Redis | 关闭 |  |  |  |  |  | — |

Redis ops 取自 `redis-cli INFO commandstats` 两轮压测前后差分；L1 hitRate 取自 `GET /api/admin/cache/stats`。

---

## 全局风险跟踪

| 风险 | 影响里程碑 | 应对 |
|---|---|---|
| Java 17 / Spring Boot 3.x 与 Lombok 兼容性 | M1 | 使用 Lombok 1.18.32+ |
| 实习生不熟悉 RSC / Server Component | M1~M5 | 业务页面默认 client component，首页与列表页使用 RSC，RSC 不依赖 cookies |
| 前端状态被滥用为业务缓存 | M1~M5 | Zustand 只保存登录态，商品、活动、订单按页面请求或局部 hook 管理 |
| 前后端错误码处理不一致 | M1~M5 | `client.ts` 统一解析 `Result<T>`，页面只处理 `ApiError` |
| 超级秒杀轮询造成接口风暴 | M4、M5 | 仅 `PENDING` 订单启用轮询，1 秒一次，最多 10 次，订单详情命中用户级缓存 |
| 压测期间 Redis 内存增长 | M6 | `maxmemory 256mb` + `allkeys-lru`，单 k6 场景结束后清理目标活动库存与已购集合 |
| Lua 脚本未挂载 / 路径错误 | M3、M4 | `RedisConfig` 使用 `ClassPathResource` 显式加载 `src/main/resources/lua/*.lua`，启动日志打印 sha |
| 压测前置脚本未执行导致库存不足 | M6 | `scripts/k6-run.sh` 每场景前调用 `k6-bench-reset.sh` 重置目标活动库存，脚本失败立即中止 |
| 容器内存超限被 cgroup OOM Kill | M6 | 每服务声明 `deploy.resources.limits.memory`，JVM `-Xmx` 留出内存余量，验收检查 `OOMKilled=false` |
