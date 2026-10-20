# 闪铺(shanpu) 后端技术设计

## 1. 项目定位

### 1.1 项目介绍

闪铺（shanpu）是一个面向高并发秒杀场景的积分制商城系统。

系统支持用户注册、登录、浏览商品、参与秒杀活动、创建订单、积分支付、订单查询、订单取消、超时关单、活动缓存、压测对比等能力。

项目重点不只是实现基础电商链路，而是围绕秒杀高并发场景，设计 Redis、Lua、Kafka、MySQL、补偿任务、缓存与压测体系，保证系统在高并发下具备正确性、可靠性与可观测性。

### 1.2 技术栈

| 组件        | 版本                       | 用途                                    |
| ----------- | -------------------------- | --------------------------------------- |
| Java        | 17 (Temurin 17.0.18)       | 运行时                                  |
| Spring Boot | 3.x                        | Web / DI / 事务 / 调度                  |
| MyBatis     | 3.x（spring-boot-starter） | 持久层                                  |
| MySQL       | 8.0                        | 业务数据库                              |
| Redis       | 7                          | 缓存 / Lua 原子操作 / 限流 / 分布式状态 |
| Kafka       | 3.x（KRaft 单节点）        | 秒杀异步下单消息                        |
| Lombok      | latest                     | 实体样板代码精简                        |
| Hutool      | 5.8.x                      | Snowflake ID / 字符串 / JSON 工具       |
| Caffeine    | 3.1.x                      | 本地二级缓存（活动详情 L1）             |
| JJWT        | 0.12.x                     | JWT 签发与解析                          |
| BCrypt      | spring-security-crypto     | 密码哈希                                |

### 1.3 设计亮点

> 技术栈：Java 17 + Spring Boot 3 + MySQL 8 + Redis 7 + Kafka 3 + Caffeine 3

面向高并发秒杀场景的积分商城，核心解决秒杀链路的防超卖、流量削峰、热点缓存与最终一致问题。

- **Redis + Lua 原子库存扣减**：将库存校验、一人一单判断与预扣减放到 Redis，过滤重复和售罄请求。成功订单仍需执行数据库库存事务，实际收益由对照实验验证。
- **Kafka 异步削峰下单**：接口等待 Kafka ack 后返回 PENDING，消费者异步创建已支付订单。受理延迟与最终完成延迟分别测量。
- **Caffeine（L1）+ Redis（L2）缓存**：热点活动详情通过本地缓存减少 Redis 访问，收益需结合负载、命中率和实际报告评估。
- **缓存防穿透 + 防击穿拆分**：商品和活动不存在态使用独立空值 Key 拦截恶意 ID 扫描；热点详情使用"逻辑过期 + 后台异步重建"防击穿；订单详情使用互斥锁压住轮询回源并避免异步落库窗口写空值。
- **滑动窗口限流（ZSet + Lua + AOP）**：基于 Redis ZSet 实现原子滑动窗口，自定义 `@RateLimit` 注解支持 USER / IP / API 三维度，挂到登录、抢购等敏感接口，业务代码零侵入。
- **DLQ + 幂等定时任务保证最终一致**：消息消费失败重试 3 次后入 DLQ，定时回投主 Topic 并累加失败计数，超 5 次写 ERROR 日志告警；周期对账修复 Redis 库存与 MySQL 真实订单数偏差，覆盖消息丢失与 Redis 数据丢失场景。

#### 1.3.1 实验与数据口径

Redis 前置过滤和 Kafka 异步受理分别按 [实验计划](./experiments/PLAN.md)设计对照实验。通过 GitHub Actions 的 Performance experiments 工作流选择 A/B 或 B/C 执行；使用独立活动、固定到达率和事务提交后的逐笔核对。C 的 PENDING 响应只表示受理，不能与同步完成吞吐直接比较。运行参数与报告口径见 [实验说明](./experiments/README.md)。

正式运行的报告、参数、指标及资源采样保存在 Actions 的 HTML 和证据 artifacts 中，配置 Pages 后发布可在线浏览的 HTML 报告。执行方式和现有场景限制见 [实验说明](./experiments/README.md)。本文不再列出缺少实测报告支持的性能数字。

## 2. 目录结构

后端单 Maven 模块，`spring-boot-starter-parent` 直接作为 parent。包按业务模块切分，横切关注点单独抽离。

```text
backend/
├── pom.xml
├── Dockerfile
├── src/main/java/com/shanpu/
│   ├── ShanpuApplication.java
│   ├── common/
│   │   ├── result/Result.java
│   │   ├── result/ResultCode.java
│   │   ├── exception/BusinessException.java
│   │   ├── exception/GlobalExceptionHandler.java
│   │   ├── util/JwtUtil.java
│   │   ├── util/UserContext.java
│   │   ├── annotation/RateLimit.java
│   │   └── aop/RateLimitAspect.java
│   ├── config/
│   │   ├── WebMvcConfig.java
│   │   ├── RedisConfig.java
│   │   ├── KafkaConfig.java
│   │   └── MybatisConfig.java
│   ├── interceptor/
│   │   └── AuthInterceptor.java
│   ├── cache/
│   │   ├── ActivityLocalCache.java
│   │   ├── ActivityLocalCacheProperties.java
│   │   └── CacheClient.java
│   ├── module/
│   │   ├── admin/
│   │   ├── user/
│   │   ├── product/
│   │   ├── seckill/
│   │   └── job/
│   └── infra/
│       ├── kafka/KafkaTopics.java
│       └── redis/RedisKeys.java
└── src/main/resources/
    ├── application.yml
    ├── application-dev.yml
    ├── application-test.yml
    ├── application-prod.yml
    ├── mapper/
    └── lua/
        ├── seckill.lua
        ├── seckill-rollback.lua
        └── rate-limit.lua
```

每个业务模块 `module/<name>/` 内部沿用四层结构：`controller / service / mapper / domain`，`domain` 下再分 `entity / dto / vo / enums`。

## 3. 分层与统一响应

### 3.1 分层职责

| 层         | 职责                                                  |
| ---------- | ----------------------------------------------------- |
| controller | HTTP 入参校验、JWT 鉴权依赖于拦截器、返回 `Result<T>` |
| service    | 业务编排、事务边界、缓存访问编排                      |
| mapper     | MyBatis 单表 CRUD，XML 与接口一一对应                 |
| domain     | 实体 / DTO / VO / 枚举                                |
| infra      | 与 Redis、Kafka 等外部系统的具体适配                  |
| 横切       | AOP / 拦截器 / 全局异常 / 通用工具                    |

### 3.2 统一响应体

```java
public class Result<T> {
    private int code;     // 0 = 成功，其他业务错误码
    private String msg;
    private T data;

    public static <T> Result<T> ok(T data);
    public static Result<Void> ok();
    public static <T> Result<T> fail(ResultCode rc);
    public static <T> Result<T> fail(int code, String msg);
}
```

`ResultCode` 枚举集中维护业务错误码与默认文案：

| code  | 名称               | HTTP | 含义                         |
| ----- | ------------------ | ---- | ---------------------------- |
| 0     | SUCCESS            | 200  | 成功                         |
| 40001 | PARAM_INVALID      | 200  | 入参非法（含 `@Valid` 失败） |
| 40100 | UNAUTHORIZED       | 401  | 未登录或 token 失效          |
| 40300 | FORBIDDEN          | 200  | 越权访问                     |
| 40400 | NOT_FOUND          | 200  | 资源不存在                   |
| 40900 | DUPLICATE_ORDER    | 200  | 一人一单冲突                 |
| 41000 | STOCK_SHORTAGE     | 200  | 库存不足 / 已售罄            |
| 41001 | ACTIVITY_NOT_START | 200  | 秒杀活动未开始               |
| 41002 | ACTIVITY_ENDED     | 200  | 秒杀活动已结束               |
| 41100 | POINTS_NOT_ENOUGH  | 200  | 积分不足                     |
| 42900 | RATE_LIMITED       | 429  | 触发限流                     |
| 50000 | INTERNAL_ERROR     | 500  | 兜底错误                     |

### 3.3 全局异常

`GlobalExceptionHandler` 使用 `@RestControllerAdvice`：

| 异常                              | 处理                                                                              |
| --------------------------------- | --------------------------------------------------------------------------------- |
| `BusinessException`               | 取出 `ResultCode`，按上表规则映射 HTTP（401 / 429 / 200），其余 200 + `code != 0` |
| `MethodArgumentNotValidException` | 拼接字段错误信息，返回 `PARAM_INVALID`                                            |
| `Exception`                       | 记录 trace 日志，返回 `INTERNAL_ERROR`，HTTP 500                                  |

只有 401（未鉴权）与 429（限流）使用 HTTP 状态码区分，便于 k6 与浏览器统一识别；其他业务错误一律 HTTP 200 + `code != 0`，前端 toast 按 `msg` 提示。

## 4. JWT + ThreadLocal 鉴权

### 4.1 链路

1. `POST /api/users/login` 校验用户名密码（BCrypt 对比）成功后，`JwtUtil.sign(userId, username)` 生成 HS256 token。
2. Payload：`{ "uid": <Long>, "uname": "<String>", "iat": <epoch>, "exp": <epoch + 7d> }`。
3. 登录路径的最后一步：`SET shanpu:user:session:{tokenHash} = userId`，TTL 同 JWT（7 天），作为服务端会话白名单。
4. 客户端在后续请求头携带 `Authorization: Bearer <token>`。
5. `AuthInterceptor#preHandle` 解析 token，校验签名与过期；通过后查 `shanpu:user:session:{tokenHash}` 是否存在，**不存在**抛 `BusinessException(UNAUTHORIZED)`；存在则 `UserContext.set(uid)`。
6. `AuthInterceptor#afterCompletion` 强制 `UserContext.remove()`，避免 Tomcat 线程复用造成串号。

`tokenHash = sha256(token).substring(0, 32)`（前 16 字节十六进制），避免明文 token 落 Redis。

### 4.2 `UserContext`

```java
public final class UserContext {
    private static final ThreadLocal<Long> HOLDER = new ThreadLocal<>();
    public static void set(Long userId);
    public static Long get();
    public static Long requireUserId();   // 为空时抛 UNAUTHORIZED
    public static void remove();
}
```

### 4.3 白名单

`WebMvcConfig` 注册 `AuthInterceptor` 时排除以下路径：

- `POST /api/users/register`
- `POST /api/users/login`
- `GET /api/products`、`GET /api/products/{id}`
- `GET /api/seckills`、`GET /api/seckills/{id}`
- `GET /api/health`

### 4.4 选型说明

使用 `HandlerInterceptor` 而非 `OncePerRequestFilter`：

- 拦截器可直接拿到 `HandlerMethod`，为按注解放行（如 `@PermitAll`）留出扩展点。
- 后端不承担静态资源，无需在 Filter 层做特殊放行。
- Spring MVC 拦截器栈对实习生而言上手成本最低。

JWT 不引入刷新令牌：7 天过期后重新登录。登出通过删除 Redis session 记录实现（详见 §4.5），session TTL 与 JWT 同步自然过期，无后台清理任务；如需强制踢出全员，运维直接更换 `jwt.secret` 触发全员重登。

### 4.5 登出

`POST /api/users/logout` 是让未过期 token 提前失效的唯一入口，需要登录态：

1. 拦截器已完成校验，`UserContext.requireUserId()` 拿到 `userId`。
2. `UserController#logout` 从请求头取出原始 token，计算 `tokenHash`，执行 `DEL shanpu:user:session:{tokenHash}`。
3. 返回 `Result.ok()`。

服务端会话记录被删除后，同一枚 token 再次请求会在 §4.1 第 5 步因 session 不存在而被拒绝。会话按 `tokenHash` 维度独立，多设备登录互不影响，登出仅作废本次的那枚 token。

### 4.6 CORS

`WebMvcConfig#addCorsMappings` 为 `/api/**` 开启跨域，供调试时浏览器从 Next.js 端口直连宿主机后端 `http://127.0.0.1:8081/api`：

| 项                 | 值                                                       |
| ------------------ | -------------------------------------------------------- |
| 路径               | `/api/**`                                                |
| `allowedOrigins`   | `http://localhost:3000`, `http://127.0.0.1:3000`         |
| `allowedMethods`   | `GET`, `POST`, `OPTIONS`                                 |
| `allowedHeaders`   | `Authorization`, `Content-Type`                          |
| `exposedHeaders`   | `Authorization`                                          |
| `allowCredentials` | `false`                                                  |
| `maxAge`           | `3600` 秒                                                |

token 经浏览器 `Authorization: Bearer <token>` 头明文携带，不依赖 cookie，故 `allowCredentials=false`。浏览器发起的 `OPTIONS` CORS 预检请求由 `AuthInterceptor` 直接放行，不要求携带 `Authorization`；真实业务请求继续按 §4.1 校验 JWT 与 Redis session。

默认浏览器入口为 Nginx 的 `http://127.0.0.1:8089`，前端使用同源的 `/api`。直接访问 Next.js 时由 rewrite 转发 API；CORS 配置用于显式指定后端绝对地址的调试请求。

## 5. 限流：滑动窗口 + AOP

### 5.1 注解

```java
@Target(ElementType.METHOD)
@Retention(RetentionPolicy.RUNTIME)
public @interface RateLimit {
    int window() default 1;          // 滑动窗口，单位秒
    int max() default 10;            // 窗口内最大请求数
    Dimension dim() default Dimension.USER;
    String key() default "";         // 自定义后缀，默认取方法签名
    enum Dimension { USER, IP, API }
}
```

### 5.2 切面

`RateLimitAspect` 切 `@annotation(RateLimit)`，在 controller 方法执行前完成：

1. 根据 `dim` 拼装 Redis Key：
   - `USER` → `shanpu:rate:user:{userId}:{methodSig}`（要求登录）
   - `IP` → `shanpu:rate:ip:{clientIp}:{methodSig}`（适用于登录 / 注册）
   - `API` → `shanpu:rate:api:{methodSig}`（全局阈值）
2. 调用 `rate-limit.lua`，返回 1 放行、0 拒绝。
3. 拒绝时抛 `BusinessException(RATE_LIMITED)`，由全局异常处理器映射为 HTTP 429。

### 5.3 滑动窗口 Lua

```lua
-- KEYS[1] = rateLimitKey
-- ARGV[1] = nowMs
-- ARGV[2] = windowSeconds
-- ARGV[3] = max
-- ARGV[4] = member

local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2]) * 1000
local max = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - window)
local count = redis.call('ZCARD', KEYS[1])
if count >= max then
    redis.call('PEXPIRE', KEYS[1], window)
    return 0
end
redis.call('ZADD', KEYS[1], now, member)
redis.call('PEXPIRE', KEYS[1], window)
return 1
```

ZSET 的 score 存毫秒时间戳，`ZREMRANGEBYSCORE` 滚动清理过期点，`ZCARD` 计数，`ZADD` 写入新请求。单 Key 内最多保留 `max` 条记录，内存开销可忽略。

### 5.4 默认接口策略

| 接口                            | 注解                                     |
| ------------------------------- | ---------------------------------------- |
| `POST /api/users/login`         | `@RateLimit(window=60, max=5, dim=IP)`   |
| `POST /api/users/register`      | `@RateLimit(window=60, max=3, dim=IP)`   |
| `POST /api/products/{id}/order` | `@RateLimit(window=1, max=10, dim=USER)` |
| `POST /api/seckills/{id}/grab`  | `@RateLimit(window=1, max=5, dim=USER)`  |

## 6. 缓存模板 CacheClient

`CacheClient` 封装 Redis 访问，对外暴露四种单一职责查询模式。序列化统一走 Jackson。

```java
public class CacheClient {

    // 1. 普通 cache-aside，只缓存真实值
    public <R> R queryWithCacheAside(
        String key, JavaType type, Supplier<R> dbFallback,
        Duration ttl);

    // 2. 独立空值 Key，防穿透
    public <R> R queryWithNullCache(
        String nullKey, Supplier<R> valueSupplier,
        Duration nullTtl);

    // 3. 逻辑过期，防击穿
    public <R, ID> R queryWithLogicalExpire(
        String keyPrefix, ID id, Class<R> type,
        Function<ID, R> dbFallback,
        Duration logicalTtl);

    // 4. 互斥锁，防并发回源
    public <R, ID> R queryWithMutex(
        String keyPrefix, ID id, Class<R> type,
        Function<ID, R> dbFallback,
        Duration ttl);

    public void set(String key, Object value, Duration ttl);
    public void setWithLogicalExpire(String key, Object value, Duration logicalTtl);
    public JavaType javaType(Class<?> rawClass, Class<?>... parameterClasses);
}
```

### 6.1 空值缓存

`queryWithNullCache` 专门解决缓存穿透，空值占位使用独立 `nullKey`，不写入真实数据 Key。

1. 查 `nullKey`，命中空值占位（约定值 `""`）则直接返回 `null`。
2. 调用 `valueSupplier` 获取真实数据：
   - 返回非空：直接返回，由内部真实读取策略负责缓存。
   - 返回空：写入 `nullKey = ""` 并设置 `nullTtl`，返回 `null`。

适用：商品详情、秒杀活动详情等“资源稳定不存在”的读路径。订单详情不使用空值缓存。

### 6.2 逻辑过期

`RedisData` 结构：

```java
public class RedisData {
    private LocalDateTime expireTime;
    private Object data;
}
```

`queryWithLogicalExpire` 流程：

1. 查 Key，命中则解析 `RedisData`。
2. 未命中则同步调用 `dbFallback` 回源：
   - DB 返回非空：写入 `RedisData` 并设置逻辑过期时间，返回。
   - DB 返回空：直接返回 `null`，不写空值。
3. 命中后解析 `RedisData`：
   - `expireTime > now`：直接返回 `data`。
   - 已过期：返回旧 `data`，同时提交一个后台任务异步重建（尝试获取 `{keyPrefix}lock:{id}` 锁，拿到则 `dbFallback` 后 `setWithLogicalExpire`；DB 返回空则删除真实数据 Key；未拿到说明其他线程在重建，跳过）。

后台线程池：`ExecutorService rebuildExecutor = Executors.newFixedThreadPool(4)`。

适用：商品详情、秒杀活动详情等热点查询。

### 6.3 互斥锁

`queryWithMutex` 用于订单详情等低 TTL 读路径：未命中时尝试获取 Redis 互斥锁，拿到则查 DB 并回填真实缓存，未拿到则短暂等待后重试缓存；DB 返回空直接返回 `null`，不写空值占位。

### 6.4 商品 / 活动落点

| 数据         | 模式     | TTL                    | VO 字段                                                                                                         |
| ------------ | -------- | ---------------------- | --------------------------------------------------------------------------------------------------------------- |
| 商品详情     | 独立空值 Key + 逻辑过期 | 30 分钟                | 完整字段（含 `description`）                                                                                    |
| 秒杀活动详情 | 独立空值 Key + 逻辑过期 | 10 分钟                | 完整字段                                                                                                        |
| 商品列表     | 普通 TTL | 5 分钟（列表查询级别） | 仅 `id / title / coverUrl / price / stock / status`，**不携带 `description`**，防止分页缓存体积随 size 倍数放大 |
| 我的订单详情 | 互斥锁   | 30 秒                  | 完整字段；Key 包含 `userId`，防止订单跨用户泄露                                                                 |

普通商品下单事务提交后清理对应 `shanpu:product:detail:{id}` 与 `shanpu:product:list:*` 缓存。缓存清理由事务 `afterCommit` 触发，保证后续商品详情与列表查询回源读取扣减后的库存；Redis 异常仅记录告警，不改变已提交订单的接口响应。

### 6.5 本地二级缓存（活动详情）

在 Redis 之上为 `shanpu:seckill:activity:{id}` 叠一层 Caffeine L1，形成「L1 Caffeine → L2 Redis → L3 MySQL」三层结构。同一场秒杀的全部抢购前置查询都会扫这一个 Key，L1 命中率显著高于其他 Key 类型。

`shanpu:product:detail:{id}` 不进入 L1，商品详情使用单层 Redis 缓存。当前两组秒杀实验固定活动 L1 配置，不测试缓存层级切换。

#### Caffeine 实例

| 实例            | 容量 | 过期策略              | 用途                                      |
| --------------- | ---- | --------------------- | ----------------------------------------- |
| `activityLocal` | 256  | `expireAfterWrite=1s` | `shanpu:seckill:activity:{id}` 的 L1 旁路 |

容量按"全平台同时进行中的秒杀活动数 × 安全系数"估算；选 `expireAfterWrite` 而非 `expireAfterAccess`，避免热点持续访问导致永不刷新。

#### 配置

```yaml
shanpu:
  cache:
    local:
      activity:
        enabled: true
        max-size: 256
        expire-ms: 1000
```

`enabled: false` 时 `CacheClient` 回退到纯 L2 路径。管理接口提供运行时开关，可在同一进程内切换 L1 与纯 Redis 访问路径。

#### CacheClient 旁路逻辑

`queryWithLogicalExpire(keyPrefix, id, ...)` 内部包含 L1 旁路：

1. `ActivityLocalCache.supports(key)` 只接受 `shanpu:seckill:activity:{id}` 与 `shanpu:seckill:activity:null:{id}`，锁 Key 与其他 Redis Key 直接跳过 L1。
2. L1 命中时返回 Redis 中同格式的 JSON 字符串，由 `CacheClient` 继续按逻辑过期结构反序列化。
3. L1 未命中走 §6.2 逻辑过期流程；Redis 命中或 DB 回源写入后把相同 JSON 回填 L1。
4. 独立活动空值 Key `shanpu:seckill:activity:null:{id}` 也可进入 L1，避免不存在活动在 L1 过期窗口内反复打到 Redis。

写操作（活动状态变更、`SeckillWarmupJob` 预热）调用 `CacheClient.delete` 或 `setWithLogicalExpire`：删除时同步失效 L1，写入时同步回填 L1。允许非统一入口的运维手工 Redis 操作存在 ≤ 1s 不一致窗口（与 L1 TTL 同量级）。

#### 不进入 L1 的 Key

| Key                                   | 角色            | 原因                                         |
| ------------------------------------- | --------------- | -------------------------------------------- |
| `shanpu:product:detail:{id}`          | 商品详情      | 使用单层 Redis 缓存 |
| `shanpu:product:list:page:{page}:size:{size}` | 对照组基线 | 同上                                      |
| `shanpu:seckill:stock:{activityId}`   | RW 强一致计数器 | Lua 原子扣减不允许应用层旁路                 |
| `shanpu:seckill:bought:{activityId}`  | RW 集合         | 一人一单语义不允许 stale 读                  |
| `shanpu:rate:*`                       | 滑动窗口限流    | 需 Redis 全局视角                            |
| `shanpu:user:session:{tokenHash}`     | 登录态          | 登出要求实时失效                             |
| `shanpu:order:detail:user:{uid}:{orderId}` | 用户级订单  | 命中率低 + 已有 30s TTL                      |
| `shanpu:*:lock:*`                     | 分布式互斥锁    | 本地缓存语义不适用                           |

#### 可观测

`ActivityLocalCache` 维护 L1 请求数与命中数，`GET /api/admin/cache/stats` 输出：

```json
{
  "activityLocal": {
    "enabled": true,
    "hitRate": 0.97,
    "requestCount": 12450,
    "size": 3
  }
}
```

该接口用于查看活动详情本地缓存状态。

以下管理接口用于切换与清理 L1；接口沿用 JWT 登录态鉴权。

| 方法 | 路径 | 用途 |
| ---- | ---- | ---- |
| GET | `/api/admin/cache/stats` | 读取 `activityLocal.enabled / hitRate / requestCount / size` |
| POST | `/api/admin/cache/activity-local/enabled?enabled=true|false` | 运行时开启或关闭活动 L1 |
| POST | `/api/admin/cache/activity-local/reset` | 清空活动 L1 条目与统计计数 |

## 7. 秒杀核心

### 7.1 活动档位

`t_seckill_activity` 通过 `tier` 列承载三档活动，档位决定链路：

| tier | 名称               | 折扣 | 单场库存 | 链路                     | 落库方式      |
| ---- | ------------------ | ---- | -------- | ------------------------ | ------------- |
| 1    | 普惠专场 INCLUSIVE | 9 折 | 5000     | MySQL 行锁事务           | 同步直接 PAID |
| 2    | 限时抢购 FLASH     | 7 折 | 500      | Redis + Lua → 本地事务   | 同步直接 PAID |
| 3    | 超级秒杀 SUPER     | 3 折 | 200      | Redis + Lua → Kafka 异步 | 异步 PENDING  |

档位由 `tier` 列权威决定，ID 命名按 `1xxx / 2xxx / 3xxx` 视觉分段便于人工识别，但不参与路由判断；运营可通过 `UPDATE t_seckill_activity SET tier = ? WHERE id = ?` 热切档位应对突发流量（详见 §7.5）。

### 7.2 数据准备

`SeckillWarmupJob` 在活动开始前 60 分钟将以下数据写入 Redis：

- `shanpu:seckill:activity:{id}` = 活动详情（含 `tier` 字段，逻辑过期 10 分钟）——三档全部写入。
- `shanpu:seckill:stock:{activityId}` = `total_stock`（使用 `SETNX`，已存在不覆盖）——**仅限时与超级两档写入**。
- `shanpu:seckill:bought:{activityId}` = 空 Set（首次抢购自动创建，无需预写）——仅限时与超级两档使用。

普惠档完全跳过 Redis 库存层，库存与一人一单全部由 MySQL 行锁与 `uk_user_activity` 唯一索引承担。

本设计假设单场限时/超级活动的 `bought` 集合上界 ≤ 20000；超过需将 `bought` 改用 Bitmap（key 内按 userId 落位）以规避大 key 与 `SREM` 慢命令风险。

### 7.3 `seckill.lua`

适用于限时与超级两档，普惠档不调用：

```lua
-- KEYS[1] = shanpu:seckill:stock:{activityId}
-- KEYS[2] = shanpu:seckill:bought:{activityId}
-- ARGV[1] = userId
-- ARGV[2] = orderId

-- 返回:
--   0 = 成功
--   1 = 库存不足
--   2 = 已购买（一人一单冲突）
--   3 = 活动不存在或未预热

if redis.call('EXISTS', KEYS[1]) == 0 then
    return 3
end

if tonumber(redis.call('GET', KEYS[1])) <= 0 then
    return 1
end

if redis.call('SISMEMBER', KEYS[2], ARGV[1]) == 1 then
    return 2
end

redis.call('DECR', KEYS[1])
redis.call('SADD', KEYS[2], ARGV[1])
return 0
```

### 7.4 统一抢购入口与三档分支

`SeckillService` 对外暴露唯一方法：

```java
public OrderVO grab(long activityId);
```

实现按以下流程路由：

1. 通过 `CacheClient.queryWithNullCache(activityNullKey, () -> queryWithLogicalExpire(...))` 取活动详情，不存在活动命中独立空值 Key，真实活动命中 L1 Caffeine / L2 Redis / L3 MySQL 任一层。
2. 检查 `activity != null`（否则抛 `ACTIVITY_NOT_FOUND`）、`activity.startAt <= now`（否则抛 `ACTIVITY_NOT_START`）、`activity.endAt > now`（否则抛 `ACTIVITY_ENDED`）。
3. 由 `Snowflake.nextIdStr()` 生成 `orderId`。
4. 按 `activity.tier` 分支：

| tier | 私有方法        | 关键步骤                                                                                                                                                                   | 响应                                   |
| ---- | --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| 1    | `grabInclusive` | 直接调 `executeSyncOrder(activity, source=2)`                                                                                                                              | `{ orderId, status: 'PAID', tier }`    |
| 2    | `grabFlash`     | 调 `seckill.lua` 扣 Redis 库存与 bought；成功后 `executeSyncOrder(activity, source=3)`；事务回滚则调回滚 Lua 回补 Redis                                                    | `{ orderId, status: 'PAID', tier }`    |
| 3    | `grabSuper`     | 调 `seckill.lua`；成功后 `kafkaTemplate.send("shanpu.seckill.order", orderId, payload).get(3, SECONDS)`，payload 内携带 `source=4`；3 秒未拿到 ack 则调回滚 Lua 回补 Redis | `{ orderId, status: 'PENDING', tier }` |

`executeSyncOrder(activity, source)` 是三档共享的事务模板，签名为 `@Transactional(rollbackFor = Exception.class) private OrderVO executeSyncOrder(ActivityVO activity, int source)`：

1. `UPDATE t_seckill_activity SET total_stock = total_stock - 1 WHERE id = ? AND total_stock > 0`，受影响行数为 0 抛 `STOCK_SHORTAGE`。
2. `UPDATE t_user SET points = points - ? WHERE id = ? AND points >= ?`，受影响行数为 0 抛 `POINTS_NOT_ENOUGH`。
3. `INSERT INTO t_order(...)`，`source` 由参数决定（普惠=2、限时=3、超级=4），`activity_id` 写入，唯一索引 `uk_user_activity` 在并发重复下兜底抛 `DUPLICATE_ORDER`。
4. `INSERT INTO t_point_log(...)`。

`OrderService.createNormalOrder` 走同一份事务模板的另一个重载（`source=1` 写死、扣的是 `t_product.stock` 而非 `total_stock`、不写 `activity_id`），下层 Mapper 调用一致。

### 7.5 失败补偿

限时与超级两档共享同一份回滚脚本：

```lua
-- KEYS[1] = stock  KEYS[2] = bought  ARGV[1] = userId
local removed = redis.call('SREM', KEYS[2], ARGV[1])
if removed == 1 then
    redis.call('INCR', KEYS[1])
end
return removed
```

调用时机：

- 限时档 `executeSyncOrder` 抛异常导致事务回滚后，`grabFlash` 在 `catch` 中调用回滚脚本，再抛 `BusinessException` 给上层。
- 超级档 `kafkaTemplate.send().get(3s)` 失败后，`grabSuper` 在 `catch` 中调用回滚脚本，再抛 `BusinessException(INTERNAL_ERROR)`。
- 回滚脚本以 `bought` 集合成员作为幂等门，只有实际移除已购用户时才回补库存。

普惠档事务回滚由 MySQL 自身处理，不涉及 Redis 回补。

### 7.6 档位热切换

运营通过 `UPDATE t_seckill_activity SET tier = ? WHERE id = ?` 切换档位。热切换入口为运维手工命令，流程固定为 SQL 更新、Redis 缓存处理、库存 Key 预热、`/grab` 验证，确保下一次抢购按数据库中的当前 tier 路由：

**升级为限时或超级档（1→2 / 1→3 / 2→3）**

```sql
-- 例：1001 普惠升超级
UPDATE t_seckill_activity SET tier = 3 WHERE id = 1001;
```

随后按活动 ID 处理 Redis：

```bash
redis-cli DEL shanpu:seckill:activity:1001 shanpu:seckill:activity:lock:1001
redis-cli DEL shanpu:seckill:stock:1001 shanpu:seckill:bought:1001
redis-cli SET shanpu:seckill:stock:1001 <t_seckill_activity.total_stock>
```

下一次 `grab(1001)` 拿到 `tier=3` 走超级链路。

**降级为普惠或限时档（3→2 / 3→1 / 2→1）**

```sql
UPDATE t_seckill_activity SET tier = 1 WHERE id = 3001;
```

随后按目标档位处理 Redis：

```bash
redis-cli DEL shanpu:seckill:activity:3001 shanpu:seckill:activity:lock:3001

# 目标 tier=1
redis-cli DEL shanpu:seckill:stock:3001 shanpu:seckill:bought:3001

# 目标 tier=2
redis-cli DEL shanpu:seckill:stock:3001 shanpu:seckill:bought:3001
redis-cli SET shanpu:seckill:stock:3001 <t_seckill_activity.total_stock>
```

- 后续 `grab` 请求按当前 tier 路由，降级后的链路不读取这两个 Key。
- 在途 Kafka 消息（已 `send` 但未消费）由 `SeckillConsumer` 处理时按消息体内携带的 `source` 字段落库，`source` 在 `grabSuper` 时已固化，不受 tier 后续变更影响。
- Redis 残留 Key 由 `StockReconcileJob` 周期性维护，过期自然清理。

切换完成后使用新登录用户调用 `POST /api/seckills/{id}/grab` 验证返回：`tier=1/2` 对应 `PAID`，`tier=3` 对应 `PENDING`。

## 8. Kafka 设计

### 8.1 部署形态

- 单节点 KRaft 模式，进程同时担任 controller 与 broker。
- 本地开发通过 `127.0.0.1:${KAFKA_PORT}` 连接，不开 SASL；只有宿主机 listener 发布到回环地址。
- 副本因子 1，无 `min.insync.replicas`；消息持久化由容器卷 `kafka-data:/var/lib/kafka/data` 落盘承担。
- Java 17 运行在宿主机，Kafka 在 Docker 中运行，使用 `restart: "no"` 和持久卷。KRaft controller 与 broker 共享一个进程；JSON 消息由 Spring Kafka 处理，死信回投由 Java 定时任务执行。单节点不提供节点级容错，业务补偿机制详见 §14.4。

### 8.2 Topic

| Topic                      | 分区 | 副本 | Key       | Value（JSON）                                                          |
| -------------------------- | ---- | ---- | --------- | ---------------------------------------------------------------------- |
| `shanpu.seckill.order`     | 3    | 1    | `orderId` | `{ orderId, userId, activityId, productId, price, source, createdAt }` |
| `shanpu.seckill.order.dlq` | 1    | 1    | `orderId` | 原消息；失败详情同步保存在 `t_order_dlq_log`                           |

### 8.3 生产者

| 配置                                    | 值                | 说明                                                    |
| --------------------------------------- | ----------------- | ------------------------------------------------------- |
| `acks`                                  | all               | 幂等 Producer 要求所有 ISR ack；单 broker 下即 leader 写入 |
| `enable.idempotence`                    | true              | broker 基于 producer epoch + 序列号去重内部重试         |
| `retries`                               | Integer.MAX_VALUE | 配合幂等性，重试直到 `delivery.timeout.ms`              |
| `max.in.flight.requests.per.connection` | 5                 | 幂等性允许的最大未确认请求数                            |
| `delivery.timeout.ms`                   | 120000            | 总投递超时硬上限，2 分钟内重试失败才视为终态失败        |
| `linger.ms`                             | 0                 | 秒杀链路要求最低延迟                                    |

发送方式：`kafkaTemplate.send(topic, key, payload).get(3, TimeUnit.SECONDS)`。3 秒内未拿到 ack 视为前台失败并按 §7.4 回滚 Redis；producer 在后台继续重试到 120 秒，若消息最终送达由消费端幂等门兜底。

### 8.4 消费者

```java
@KafkaListener(
    topics = KafkaTopics.SECKILL_ORDER,
    groupId = "shanpu-seckill-consumer",
    containerFactory = "manualAckListenerContainerFactory",
)
public void consume(ConsumerRecord<String, SeckillOrderMsg> record, Acknowledgment ack);
```

消费逻辑封装在 `SeckillConsumer.handle(SeckillOrderMsg)` 中，单个本地事务完成：

1. 先查 `uk_order_id`；订单已存在说明本条为重投，直接 `ack.acknowledge()` 返回。
2. `INSERT IGNORE INTO t_order(...)`，依赖 `uk_order_id` 与 `uk_user_activity` 兜底幂等与一人一单。
3. `UPDATE t_seckill_activity SET total_stock = total_stock - 1 WHERE id = ? AND total_stock > 0`，受影响行数为 0 抛 `STOCK_SHORTAGE`。
4. 扣减用户积分（行锁 `UPDATE t_user SET points = points - ? WHERE id = ? AND points >= ?`，受影响行数为 0 抛业务异常）。
5. 写 `t_point_log` 流水。

成功后调用 `ack.acknowledge()`；抛异常则由 `DefaultErrorHandler` 处理。

**关于手动 ack**：`manualAckListenerContainerFactory` 使用 `AckMode = MANUAL_IMMEDIATE`，每条消息独立提交 offset，避免 batch 内 offset 未提交窗口造成重复消费。手动 ack 是显式控制选择，与 step 1 的幂等门共同保证"至少一次送达 + 业务一次性"。

**关于消费并发**：`concurrency = 3` 与主 topic 分区数对齐，单 JVM 3 线程并行消费 3 分区，用满分区并行能力。

### 8.5 重试与 DLQ

`KafkaConfig` 声明：

```java
DefaultErrorHandler errorHandler = new DefaultErrorHandler(
    new DeadLetterPublishingRecoverer(kafkaTemplate,
        (record, ex) -> {
            SeckillOrderMsg payload = record.value() instanceof SeckillOrderMsg msg ? msg : null;
            orderDlqLogService.record(String.valueOf(record.key()), payload, ex.getMessage());
            return new TopicPartition("shanpu.seckill.order.dlq", 0);
        }),
    new FixedBackOff(1000L, 3)   // 间隔 1s，重试 3 次
);
```

重试耗尽后消息进入 DLQ，并同步写入 `t_order_dlq_log`：完整 `SeckillOrderMsg` payload、失败原因、失败次数、状态、最后失败时间。`KafkaDlqRescueJob` 每分钟扫描 `status=0` 的记录，反序列化 payload 后发回 `shanpu.seckill.order`；回投成功标记 `status=1` 并写入 `republished_at`，解析或发送失败累加 `fail_count`，达到 5 次标记 `status=2` 并输出 ERROR 日志。回投消息仍经过消费端 `uk_order_id`、`uk_user_activity` 与 `INSERT IGNORE` 幂等门，不会重复扣积分。

## 9. 定时任务

使用 Spring `@Scheduled`，单实例运行，10 人规模无需引入 XXL-Job。所有任务自带幂等。

| 任务                | Cron            | 行为                                                                                                                                                                                                                                                                                                                       | 幂等性                                                |
| ------------------- | --------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| `OrderTimeoutJob`   | `0 */1 * * * *` | 扫描 `t_order` 中 `status=PENDING_PAY` 且 `created_at < now - 15min` 的订单 → 条件更新为 `CANCELLED`；限时 / 超级秒杀订单调用回滚 Lua 回补 Redis 库存与移除已购集合；清理对应用户订单详情缓存                                                                                                                              | 状态机：只有 `PENDING_PAY` 才被改写；回滚 Lua 以 `SREM` 结果保证重复执行无副作用 |
| `KafkaDlqRescueJob` | `0 */1 * * * *` | 扫描 `t_order_dlq_log.status=0` 的 DLQ 记录，反序列化 payload 后回投主 Topic；成功标记已回投，失败累加 `fail_count`，达到 5 次标记错误并写 ERROR 日志                                                                                                                                                                     | 主流程 `INSERT IGNORE` 保证不重复落单                 |
| `StockReconcileJob` | `0 */5 * * * *` | 对每个进行中或刚结束的限时 / 超级活动读取 MySQL 剩余库存 `activity.total_stock` 与 Redis 库存；`Redis 库存 > MySQL 剩余库存` 时 `SET shanpu:seckill:stock:{id}` 修复并写 ERROR 日志                                                                                                                                       | 修复操作幂等（SET 覆写到正确值）                      |
| `SeckillWarmupJob`  | `0 */1 * * * *` | 扫描未来 60 分钟内开始的活动：库存用 `SETNX` 写入、活动详情按逻辑过期写入                                                                                                                                                                                                                                                  | SETNX + 覆盖写，幂等                                  |
| `OrderReconcileJob` | `0 */5 * * * *` | 扫描 `PAID` 订单中缺少 `biz_type=2` 消费流水的记录，按当前用户积分余额补写 `t_point_log` 并输出 ERROR 对账日志                                                                                                                                                                                                              | 写入前再次检查 `biz_type + biz_ref`，单实例调度下幂等 |

`StockReconcileJob` 自动修复仅在 `Redis > 应有库存` 方向触发——这是消息丢失或 Redis 数据丢失的明确信号；反向差异交给消费在途自然收敛。

所有兜底任务均暴露 `runOnce()`，定时调度与手动触发接口复用同一实现。手动触发接口返回：

```java
public class JobRunVO {
    private String job;
    private long scanned;
    private long succeeded;
    private long failed;
}
```

DLQ 统计接口返回：

```java
public class DlqStatsVO {
    private long pending;
    private long republished;
    private long error;
}
```

## 10. MySQL DDL

表结构以 `backend/src/main/resources/db/migration` 中的 Flyway 迁移为准，以下仅为说明。初始化、旧库 baseline 与升级规则见 [数据库管理](./DATABASE.md)。

字符集统一 `utf8mb4_0900_ai_ci`，引擎 InnoDB，所有表带 `created_at / updated_at`。

```sql
-- 用户
CREATE TABLE t_user (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  username VARCHAR(32) NOT NULL,
  password VARCHAR(72) NOT NULL COMMENT 'BCrypt 哈希',
  nickname VARCHAR(32) NOT NULL DEFAULT '',
  points INT NOT NULL DEFAULT 10000 COMMENT '注册赠送 10000',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 普通商品
CREATE TABLE t_product (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  title VARCHAR(128) NOT NULL,
  cover_url VARCHAR(255) NOT NULL DEFAULT '',
  description VARCHAR(4096) NOT NULL DEFAULT '' COMMENT '商品描述，硬上限 4KB 以约束缓存体积',
  price INT NOT NULL COMMENT '单位:积分',
  stock INT NOT NULL DEFAULT 0,
  status TINYINT NOT NULL DEFAULT 1 COMMENT '1上架 0下架',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 秒杀活动
CREATE TABLE t_seckill_activity (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  product_id BIGINT UNSIGNED NOT NULL,
  title VARCHAR(128) NOT NULL,
  tier TINYINT NOT NULL COMMENT '1普惠 2限时 3超级',
  seckill_price INT NOT NULL,
  total_stock INT NOT NULL,
  start_at DATETIME NOT NULL,
  end_at DATETIME NOT NULL,
  status TINYINT NOT NULL DEFAULT 0 COMMENT '0待开始 1进行中 2已结束',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_tier_status (tier, status),
  KEY idx_status_start (status, start_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 订单
CREATE TABLE t_order (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  order_id VARCHAR(32) NOT NULL COMMENT '雪花 ID，业务主键',
  user_id BIGINT UNSIGNED NOT NULL,
  product_id BIGINT UNSIGNED NOT NULL,
  activity_id BIGINT UNSIGNED DEFAULT NULL COMMENT '秒杀活动 ID，普通订单为 NULL',
  source TINYINT NOT NULL COMMENT '1普通商品 2普惠 3限时 4超级',
  price INT NOT NULL,
  status TINYINT NOT NULL COMMENT '1待支付 2已支付 3已取消',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  paid_at DATETIME DEFAULT NULL,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_order_id (order_id),
  UNIQUE KEY uk_user_activity (user_id, activity_id),
  KEY idx_user_status (user_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 积分流水
CREATE TABLE t_point_log (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id BIGINT UNSIGNED NOT NULL,
  delta INT NOT NULL COMMENT '正=入账，负=消费',
  balance_after INT NOT NULL,
  biz_type TINYINT NOT NULL COMMENT '1注册赠送 2下单消费 3订单取消回补',
  biz_ref VARCHAR(32) NOT NULL DEFAULT '' COMMENT 'order_id 等',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_user_created (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- DLQ 失败日志
CREATE TABLE t_order_dlq_log (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  order_id VARCHAR(32) NOT NULL,
  payload JSON NOT NULL COMMENT 'SeckillOrderMsg JSON payload',
  fail_reason VARCHAR(512) NOT NULL,
  fail_count INT NOT NULL DEFAULT 1,
  status TINYINT NOT NULL DEFAULT 0 COMMENT '0待回投 1已回投 2错误',
  last_failed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  republished_at DATETIME DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_order_id (order_id),
  KEY idx_status_failed (status, last_failed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 索引设计要点

- `t_order.uk_order_id`：保证 Kafka 消费幂等，`INSERT IGNORE` 依赖该索引。
- `t_order.uk_user_activity`：MySQL 唯一索引中 NULL 值不参与冲突判定（标准行为），三档秒杀订单 `activity_id` 均非 NULL 故每用户每活动至多一行，普通商品订单 `activity_id = NULL` 故不受限制。一人一单的 DB 兜底由此索引承担，三档统一适用。
- `t_seckill_activity.idx_tier_status`：运营按档位筛选活动用，亦可用于"列出某档全部进行中活动"查询。
- `t_seckill_activity.idx_status_start`：`SeckillWarmupJob` 扫描即将开始的活动用。
- `t_point_log.idx_user_created`：用户积分流水分页。
- `t_order_dlq_log.idx_status_failed`：`KafkaDlqRescueJob` 按待回投状态与失败时间顺序扫描。

### `source` 与 `tier` 关系

`t_order.source` 在订单创建时按当时 `activity.tier` 派生并固化：

| 触发入口                         | source | 备注                               |
| -------------------------------- | ------ | ---------------------------------- |
| `OrderService.createNormalOrder` | 1      | 普通商品下单，`activity_id = NULL` |
| `SeckillService.grabInclusive`   | 2      | 当时 `activity.tier = 1`           |
| `SeckillService.grabFlash`       | 3      | 当时 `activity.tier = 2`           |
| `SeckillService.grabSuper`       | 4      | 当时 `activity.tier = 3`           |

后续 §7.6 档位热切换不影响订单创建时写入的 `source`，保留创建时档位证据。超级档异步落库时，`source` 在 `grabSuper` 投递消息前固化进 payload，由 Consumer 写入。

### ID 生成

业务主键 `order_id` 由应用层在落库前生成，承担四重角色：

- Kafka 消息 Key（同订单进同分区，保证消费顺序）
- 消费端幂等键 `uk_order_id`（`INSERT IGNORE` 兜底重复消费）
- `t_point_log.biz_ref` 关联键，串起订单与积分流水
- 对外 API 返回值 / 前端轮询路径 `/api/orders/{orderId}`

因此 `order_id` 不能依赖 MySQL `AUTO_INCREMENT`（要等 INSERT 之后才能拿到）。采用 Hutool `cn.hutool.core.lang.Snowflake`，全局单例由 `IdUtil.getSnowflake(workerId, datacenterId)` 装配。

#### 位结构

| 位段         | 位数 | 说明                                                |
| ------------ | ---- | --------------------------------------------------- |
| 符号位       | 1    | 固定 0                                              |
| 时间戳       | 41   | 毫秒级，Hutool 默认 epoch `2010-11-04 09:42:54.657` |
| datacenterId | 5    | 数据中心 ID，单机部署固定 0                         |
| workerId     | 5    | 工作机器 ID，单机部署固定 0                         |
| 序列号       | 12   | 同毫秒自增，单实例 4096 个/ms 上限                  |

输出 `long`，对外渲染为十进制字符串（≤19 字符，落在 `VARCHAR(32)` 内）。

#### Bean 装配

```java
@Configuration
@ConfigurationProperties(prefix = "shanpu.snowflake")
public class SnowflakeConfig {
    private long workerId = 0L;
    private long datacenterId = 0L;

    @Bean
    public Snowflake snowflake() {
        return IdUtil.getSnowflake(workerId, datacenterId);
    }
}
```

`application.yml`：

```yaml
shanpu:
  snowflake:
    worker-id: 0
    datacenter-id: 0
```

`IdUtil.getSnowflake` 内部按 `(workerId, datacenterId)` 缓存单例，全 JVM 共享同一序列源，保证不重号。

#### 调用点

| 调用点                                                 | 用法                                       |
| ------------------------------------------------------ | ------------------------------------------ |
| `SeckillService.grabInclusive / grabFlash / grabSuper` | 注入 `Snowflake`，`nextIdStr()` 取 orderId |
| `OrderService.createNormalOrder`                       | 同上                                       |
| `seckill.lua` 的 `ARGV[2]`                             | Java 侧生成后传入                          |
| Kafka `kafkaTemplate.send` 的 Key                      | 直接复用 orderId 字符串                    |

#### 时钟回拨

Hutool `Snowflake` 检测到 `currentMs < lastMs` 直接抛 `IllegalStateException`。异常向上抛由 §7.4 的 Redis 回补流程兜底回滚库存与已购集合，对外返回 `INTERNAL_ERROR`。

## 11. Redis Key 规范

所有 Key 以 `shanpu:` 为前缀，冒号分层。

| Key 模板                               | 类型                   | TTL                | 用途                                                      |
| -------------------------------------- | ---------------------- | ------------------ | --------------------------------------------------------- |
| `shanpu:user:session:{tokenHash}`      | String(userId)         | 7 天               | 登录态会话白名单；登出 DEL，自然过期同 JWT                |
| `shanpu:product:detail:{id}`           | String(JSON+RedisData) | 逻辑过期 30 分钟   | 商品详情真实数据                                          |
| `shanpu:product:detail:null:{id}`      | String("")             | 2 分钟             | 商品详情空值占位                                          |
| `shanpu:product:detail:lock:{id}`      | String                 | 10 秒              | 商品详情逻辑过期重建锁                                    |
| `shanpu:product:list:page:{page}:size:{size}` | String(JSON)   | 5 分钟             | 商品列表分页缓存                                          |
| `shanpu:seckill:activity:{id}`         | String(JSON+RedisData) | 逻辑过期 10 分钟   | 秒杀活动详情真实数据（含 `tier` 字段），三档统一写入      |
| `shanpu:seckill:activity:null:{id}`    | String("")             | 2 分钟             | 活动空值占位                                              |
| `shanpu:seckill:stock:{activityId}`    | String(int)            | 活动 `end_at + 1h` | 秒杀剩余库存，仅 `tier=2/3` 写入；`tier=1` 普惠档无此 Key |
| `shanpu:seckill:bought:{activityId}`   | Set                    | 活动 `end_at + 1h` | 已购用户 ID 集合，仅 `tier=2/3` 写入；单场上限 20000      |
| `shanpu:order:detail:user:{userId}:{orderId}` | String(JSON)   | 30 秒              | 订单详情用户级缓存，缓解轮询风暴；超级档 Kafka ack 后会先写 `PENDING` 占位，落库成功后删除并回源真实订单 |
| `shanpu:order:detail:lock:user:{userId}:{orderId}` | String        | 10 秒              | 订单详情互斥重建锁                                        |
| `shanpu:rate:user:{userId}:{methodSig}` | ZSet                  | window             | 用户维度限流                                              |
| `shanpu:rate:ip:{clientIp}:{methodSig}` | ZSet                  | window             | IP 维度限流                                               |
| `shanpu:rate:api:{methodSig}`          | ZSet                   | window             | API 全局限流                                              |

`RedisKeys` 类集中维护 Key 模板常量，业务代码禁止硬编码字符串。

`shanpu:seckill:activity:{id}` 与对应独立空值占位 Key 同时在 JVM 内驻留 Caffeine L1（容量 256、`expireAfterWrite=1s`），其余 Key 仅驻留 Redis 单层；详见 §6.5。

## 12. Kafka Topic 设计

| Topic                      | 分区 | 副本 | Key       | Header                    | Value                                                          |
| -------------------------- | ---- | ---- | --------- | ------------------------- | -------------------------------------------------------------- |
| `shanpu.seckill.order`     | 3    | 1    | `orderId` | -                         | `{ orderId, userId, activityId, productId, price, source, createdAt }` |
| `shanpu.seckill.order.dlq` | 1    | 1    | `orderId` | Spring Kafka DLT headers  | 同主 Topic 消息体；失败详情同步保存在 `t_order_dlq_log`                |

消费组：`shanpu-seckill-consumer`。

消息体定义：

```java
public class SeckillOrderMsg {
    private String orderId;       // 雪花 ID
    private Long userId;
    private Long activityId;
    private Long productId;
    private Integer price;        // 积分
    private Integer source;       // 固化订单来源，超级档为 4
    private LocalDateTime createdAt;
}
```

## 13. REST API 清单

所有接口前缀 `/api`，响应体统一为 `Result<T>`，下表仅展示 `data` 字段结构。

### 13.1 用户

| 方法 | 路径                       | 鉴权 | 请求体                              | 响应 data                                | 限流       |
| ---- | -------------------------- | ---- | ----------------------------------- | ---------------------------------------- | ---------- |
| POST | `/api/users/register`      | 否   | `{ username, password, nickname? }` | `{ userId, username, points }`           | 60s/3 IP   |
| POST | `/api/users/login`         | 否   | `{ username, password }`            | `{ token, userId, username, points }`    | 60s/5 IP   |
| POST | `/api/users/logout`        | 是   | -                                   | -                                        | 1s/5 USER  |
| GET  | `/api/users/me`            | 是   | -                                   | `{ userId, username, nickname, points }` | 1s/20 USER |
| GET  | `/api/users/me/points`     | 是   | -                                   | `{ points }`                             | 1s/20 USER |
| POST | `/api/users/me/nickname`   | 是   | `{ nickname }`                      | `{ userId, username, nickname, points }` | 1s/5 USER  |

### 13.2 商品

| 方法 | 路径                       | 鉴权 | 请求/响应                                                                                     | 限流       |
| ---- | -------------------------- | ---- | --------------------------------------------------------------------------------------------- | ---------- |
| GET  | `/api/products`            | 否   | Query: `page=1, size=20` → `{ list: [{ id, title, coverUrl, price, stock, status }], total }` | 无         |
| GET  | `/api/products/{id}`       | 否   | → `{ id, title, coverUrl, description, price, stock, status }`                                | 无         |
| POST | `/api/products/{id}/order` | 是   | `{}` → `{ orderId, status: "PAID" }`                                                          | 1s/10 USER |

普通下单事务边界：单个 `OrderService.createNormalOrder` 方法 + `@Transactional(rollbackFor = Exception.class)`，依次执行 `UPDATE t_product SET stock = stock - 1 WHERE id = ? AND stock > 0`（受影响行数为 0 抛 `STOCK_SHORTAGE`）、`UPDATE t_user SET points = points - ? WHERE id = ? AND points >= ?`（为 0 抛 `POINTS_NOT_ENOUGH`）、`INSERT t_order`、`INSERT t_point_log`。事务提交后失效商品详情与商品列表缓存，下一次商品查询返回扣减后的库存。

### 13.3 秒杀

| 方法 | 路径                      | 鉴权 | 请求/响应                                                        | 限流      |
| ---- | ------------------------- | ---- | ---------------------------------------------------------------- | --------- |
| GET  | `/api/seckills`           | 否   | Query: `tier?, status?` → 活动列表（含 `tier` 字段）             | 无        |
| GET  | `/api/seckills/{id}`      | 否   | → 活动详情（含 `tier` 字段，逻辑过期缓存）                       | 无        |
| POST | `/api/seckills/{id}/grab` | 是   | `{}` → `{ orderId, status: 'PAID' \| 'PENDING', tier: 1\|2\|3 }` | 1s/5 USER |

统一抢购入口 `/grab` 由 `SeckillService.grab(activityId)` 内部按 `activity.tier` 路由（详见 §7.4）：

- `tier=1` 普惠：行锁事务直接落库，响应 `status: 'PAID'`。
- `tier=2` 限时：Redis+Lua 扣减后本地事务落库，响应 `status: 'PAID'`。
- `tier=3` 超级：Redis+Lua 扣减后 Kafka 异步落库，响应 `status: 'PENDING'`，前端轮询订单详情转 PAID。

`GET /api/seckills` 支持 `tier` 与 `status` 双重过滤，前端按需展示"全部三档进行中"或"仅超级档进行中"。响应中的 `tier` 字段供前端 `SeckillTierBadge` 渲染档位徽标。

### 13.4 订单

| 方法 | 路径                    | 鉴权 | 请求/响应                                | 限流       |
| ---- | ----------------------- | ---- | ---------------------------------------- | ---------- |
| GET  | `/api/orders`           | 是   | Query: `status?, page, size` → 列表      | 1s/10 USER |
| GET  | `/api/orders/{orderId}` | 是   | → 订单详情（前端轮询用，30s 用户级缓存） | 1s/10 USER |

### 13.5 健康检查

| 方法 | 路径          | 鉴权 | 响应                                                                               |
| ---- | ------------- | ---- | ---------------------------------------------------------------------------------- |
| GET  | `/api/health` | 否   | `{ status: "UP", mysql: "UP", redis: "UP", kafka: "UP" }` |

`/api/health` 探测 MySQL（`SELECT 1`）、Redis（`PING`）与 Kafka（AdminClient `describeCluster`），供 Docker `depends_on: condition: service_healthy` 使用。任一依赖 `DOWN`，整体 `status` 转为 `DOWN`。

### 13.6 管理任务

管理任务接口沿用 JWT 登录态鉴权，鉴权粒度与普通登录态接口一致。所有手动触发接口返回 `JobRunVO`，DLQ 统计接口返回 `DlqStatsVO`。

| 方法 | 路径 | 鉴权 | 响应 data |
| ---- | ---- | ---- | --------- |
| POST | `/api/admin/jobs/order-timeout/run` | 是 | `{ job, scanned, succeeded, failed }` |
| POST | `/api/admin/jobs/dlq-rescue/run` | 是 | `{ job, scanned, succeeded, failed }` |
| POST | `/api/admin/jobs/stock-reconcile/run` | 是 | `{ job, scanned, succeeded, failed }` |
| POST | `/api/admin/jobs/seckill-warmup/run` | 是 | `{ job, scanned, succeeded, failed }` |
| POST | `/api/admin/jobs/order-reconcile/run` | 是 | `{ job, scanned, succeeded, failed }` |
| GET  | `/api/admin/jobs/dlq/stats` | 是 | `{ pending, republished, error }` |
| GET  | `/api/admin/cache/stats` | 是 | `{ activityLocal: { enabled, hitRate, requestCount, size } }` |
| POST | `/api/admin/cache/activity-local/enabled?enabled=true|false` | 是 | `{ activityLocal: { enabled, hitRate, requestCount, size } }` |
| POST | `/api/admin/cache/activity-local/reset` | 是 | `{ activityLocal: { enabled, hitRate, requestCount, size } }` |

## 14. 设计取舍

### 14.1 主动简化

- Redis 单实例 + AOF everysec，不上集群与哨兵。
- Kafka 单节点 KRaft + 单副本，消息不丢由 Producer 幂等 + 同步 `send().get(3s)` + 回滚 Lua + Consumer 幂等门 + DLQ + `StockReconcileJob` 对账兜底，不依赖跨 broker 副本。
- 不引入 Spring Cloud / Nacos / Sentinel / SkyWalking / Seata。
- JWT 无刷新令牌；登录写入 Redis session 白名单、登出 DEL 即时生效，session TTL 同 JWT 自然过期，无后台清理任务。
- 限流自实现，不引入第三方限流框架。
- 不接入真实支付，全部以积分结算。

### 14.2 保留教学复杂度

- 三档秒杀活动：业务可见档位驱动技术选型，普惠 / 限时 / 超级三档逐层引入 Redis Lua、Kafka 异步，演示"档位决定链路"。
- 逻辑过期 + 后台线程异步重建：覆盖热点 Key 击穿心智模型。
- Lua 一人一单 + 防超卖：演示 Redis 原子操作（限时与超级两档复用同一脚本）。
- Kafka 单节点异步下单 + Producer 幂等 + Consumer 幂等门 + DLQ + 库存自动对账：覆盖"消息严格不丢 + 不重复消费 + 端到端最终一致"完整链路（仅超级档触发）。
- 两组秒杀实验：A/B 验证 Redis 前置过滤，B/C 验证 Kafka 受理与最终完成的差异。
- `CacheClient` 模板封装：避免每个 Service 重复书写双重检查与锁释放。

### 14.3 仅文档提及不做强约束

- 限流 API 全局维度默认不挂任何接口，留作演示。

### 14.4 已知坑位与对策

| 风险                                   | 对策                                                                                                                                                                                                          |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 限时/超级活动开始前缓存未预热          | `SeckillWarmupJob` 每分钟扫描未来 60 分钟内 `tier IN (2,3)` 活动，自动 SETNX 写库存与活动详情；普惠档无需库存预热                                                                                             |
| Lua 扣减成功但 Kafka 发送失败          | `kafkaTemplate.send().get(3s)` 同步等 ack，失败则用回滚 Lua（`INCR` + `SREM`）回补                                                                                                                            |
| send 超时但消息后台重试最终送达 broker | Redis 已被回滚 Lua 还原；消息被消费时由消费端幂等门（INSERT IGNORE 受影响行数=0 跳过）丢弃；Redis 库存差异由 `StockReconcileJob` 自动修复                                                                     |
| Redis 数据丢失导致一人一单失效         | `t_order.uk_user_activity` 在 DB 层兜底（三档统一适用）；消费者 `INSERT IGNORE` 在唯一冲突时静默丢弃；`StockReconcileJob` 自动修复 Redis 库存与已购集合                                                       |
| 档位热切换中途丢请求                   | `UPDATE tier` 后立即 evict 详情缓存；升级需手动调 `SeckillWarmupJob.warmupOne(id)` 写入库存 Key；在途 Kafka 消息按 payload 内 `source` 字段落库，不受切换影响（详见 §7.6）                                    |
| 三档活动数据互相污染                   | 各测量点创建独立活动和用户，按实际 tier 准备 Redis 状态，不重置演示活动                                                                                                         |
| 前端轮询订单造成接口风暴               | `/api/orders/{orderId}` 命中 30s 用户级缓存；超级档前端轮询 1s × 最多 10 次后改为手动刷新；普惠 / 限时档直接 PAID 无需轮询                                                                                    |
| ThreadLocal 串号                       | `AuthInterceptor#afterCompletion` 强制 `UserContext.remove()`，覆盖异常分支                                                                                                                                   |
| Redis 大 key                           | `t_product.description` 限长 `VARCHAR(4096)`；商品列表 VO 不携带 `description`；`shanpu:seckill:bought:{activityId}` 单场上限 20000，超过改 Bitmap                                                            |
| 单 Key 高 QPS（热 Key）                | 活动详情 `shanpu:seckill:activity:{id}` 叠 Caffeine L1 抗读（详见 §6.5）；库存 / 已购集合 / 限流 ZSet 等强一致 RW Key 不进 L1；Redis 容量需结合实测 CPU、请求量及 Lua 开销评估，不预设性能数字 |
