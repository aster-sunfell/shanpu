# 闪铺(shanpu) 前端技术设计

## 1. 定位与参考

闪铺 Web 是用户与商城交互的唯一入口，承担三件事：

- 浏览商品与秒杀活动（含倒计时、库存预览，三档活动以徽标区分）。
- 普通商品下单（直连 DB 链路）与三档秒杀活动抢购（普惠 / 限时 / 超级，对应 DB / Redis+Lua / Redis+Lua+Kafka）。
- 个人中心：登录、注册、积分余额、我的订单。

页面与组件结构参考开源项目 [chatgptuk/ldc-shop](https://github.com/chatgptuk/ldc-shop) 的视觉与交互风格，但**仅参考布局与组件命名**，所有业务数据通过后端 REST API（[BACKEND.md](./BACKEND.md)）获取，不复用其 Drizzle ORM 直连数据库的实现路径。

## 2. 技术栈

| 组件 | 版本 | 用途 |
|---|---|---|
| Next.js | 15 (App Router) | RSC + Client Component 混用 |
| TypeScript | 5.x | strict 模式 |
| Tailwind CSS | v4 | CSS-first，配置写在 `globals.css` |
| Shadcn UI | latest (Radix UI + Tailwind) | 通过 CLI 按需生成到 `components/ui/` |
| lucide-react | latest | 图标 |
| Zustand | 4.x | 仅维护登录态 |
| 原生 fetch | - | HTTP 封装，不引入 axios / TanStack Query |
| react-hook-form + zod | latest | 登录 / 注册表单校验 |
| sonner | latest | toast 提示 |
| pnpm | latest | 包管理 |

## 3. 目录结构

```text
frontend/
├── package.json
├── pnpm-lock.yaml
├── Dockerfile
├── next.config.ts          # output: 'standalone'
├── tsconfig.json
├── tailwind.config.ts      # 占位文件，主要配置在 globals.css
├── components.json         # shadcn 配置
├── app/
│   ├── layout.tsx          # 根布局，挂载 Providers / SiteHeader / SiteFooter
│   ├── page.tsx            # 首页：聚合秒杀与热门商品
│   ├── error.tsx           # 全局页面错误兜底
│   ├── not-found.tsx       # 全局 404 兜底
│   ├── globals.css
│   ├── (auth)/
│   │   ├── login/page.tsx
│   │   └── register/page.tsx
│   ├── products/
│   │   ├── page.tsx
│   │   └── [id]/
│   │       ├── page.tsx
│   │       ├── loading.tsx
│   │       └── buy-button.tsx
│   ├── seckill/
│   │   ├── page.tsx
│   │   └── [id]/
│   │       ├── page.tsx
│   │       └── grab-button.tsx
│   ├── orders/
│   │   ├── page.tsx
│   │   └── [id]/page.tsx
│   ├── me/page.tsx
│   └── api/                # 仅放 /api/health 等 BFF 端点，不放业务
├── components/
│   ├── ui/                 # shadcn 生成
│   ├── product-card.tsx
│   ├── seckill-card.tsx
│   ├── seckill-countdown.tsx
│   ├── seckill-tier-badge.tsx
│   ├── points-badge.tsx
│   ├── order-status-badge.tsx
│   ├── auth-guard.tsx
│   ├── site-header.tsx
│   ├── site-footer.tsx
│   └── providers.tsx
├── lib/
│   ├── api/client.ts       # fetch 封装
│   ├── api/api-error.ts    # ApiError 定义
│   ├── api/errors.ts       # 组件层错误文案映射
│   ├── api/user.ts
│   ├── api/product.ts
│   ├── api/seckill.ts
│   ├── api/order.ts
│   ├── auth/token-store.ts # Zustand 持久化
│   ├── config/public-config.ts # 运行时配置注入读取（详见 §7.3）
│   └── utils.ts            # cn() 等
├── hooks/
│   ├── use-auth.ts
│   ├── use-countdown.ts
│   ├── use-seckill-result.ts
│   └── use-public-config.ts
└── types/
    ├── api.ts              # Result<T> 泛型
    ├── user.ts
    ├── product.ts
    ├── seckill.ts
    └── order.ts
```

## 4. 路由与页面

| 路径 | 类型 | 鉴权 | 内容 |
|---|---|---|---|
| `/` | RSC | 否 | 顶部 banner + "正在秒杀"卡片轮播 + "热门商品"网格 |
| `/products` | RSC | 否 | 商品列表，分页 |
| `/products/[id]` | RSC + Client | 否 | 商品详情 + "用积分兑换"按钮（点击触发 client 端 fetch） |
| `/seckill` | RSC | 否 | 秒杀专区：进行中 + 即将开始（按 tier 过滤展示，徽标区分普惠/限时/超级三档） |
| `/seckill/[id]` | RSC + Client | 否 | 活动详情 + 倒计时 + "立即抢"按钮（按响应 status 分流：PAID 刷新积分并跳订单详情，PENDING 跳订单详情等待确认） |
| `/login` | Client | 否 | 用户名 + 密码登录表单 |
| `/register` | Client | 否 | 注册表单 |
| `/orders` | Client | 是 | 我的订单列表，状态 tab 切换 |
| `/orders/[id]` | Client | 是 | 订单详情，超级秒杀订单在此轮询直到 `PAID` / `CANCELLED` / 次数上限 |
| `/me` | Client | 是 | 个人中心，展示用户名、可编辑昵称、积分；进入页面自动同步积分余额 |

需要鉴权的页面在 `app/(authed)/layout.tsx` 或页面顶部包裹 `<AuthGuard>`，未登录跳转 `/login?next=<encoded>`。

## 5. 关键组件

### 5.1 展示组件

| 组件 | 用途 |
|---|---|
| `ProductCard` | 商品卡片：封面、标题、积分价、库存徽标 |
| `SeckillCard` | 秒杀卡片：商品图、原价 / 秒杀价、倒计时、库存进度条；右上角挂 `SeckillTierBadge` |
| `SeckillTierBadge` | 档位徽标：读活动 VO 的 `tier` 字段渲染 shadcn `Badge`——`tier=1` 普惠（secondary 绿）/`tier=2` 限时（default 橙）/`tier=3` 超级（destructive 红）。**不根据 ID 推断 tier**，运营可热切档位，ID 段仅为视觉惯例 |
| `SeckillCountdown` | 倒计时 `HH:mm:ss`，到时切按钮状态 |
| `PointsBadge` | 带金色金币图标的积分徽标，用于个人中心与订单支付反馈 |
| `OrderStatusBadge` | 订单状态徽标，对应 4 个 shadcn `Badge` variant：`PENDING`（黄）/`PAID`（绿）/`CANCELLED`（灰）/`FAILED`（红） |

### 5.2 容器与守卫

| 组件 | 用途 |
|---|---|
| `SiteHeader` | 顶部导航：Logo、商品、秒杀、订单、我的。未登录显示"登录"按钮；已登录显示带边框的用户名或昵称按钮 + 下拉菜单（个人中心、登出） |
| `SiteFooter` | 站点品牌名 |
| `AuthGuard` | 客户端组件，挂在需登录页面，无 token 时重定向 `/login?next=...` |
| `Providers` | 包裹 `Toaster`、Zustand 持久化、主题 |

### 5.3 表单

登录 / 注册表单基于 `react-hook-form` + `zod`：

- 用户名：3~32 位字母数字下划线。
- 密码：6~32 位，不限字符集（教学版不强制复杂度）。
- 昵称（可选，注册）：1~32 位。

## 6. 状态管理

### 6.1 Zustand `useAuthStore`

```ts
type AuthState = {
  token: string | null;
  user: { userId: number; username: string; nickname: string; points: number } | null;
  hydrated: boolean;
  setHydrated: (hydrated: boolean) => void;
  setSession: (token: string, user: AuthState['user']) => void;
  clearSession: () => void;
  logout: () => Promise<void>;       // 调 POST /api/users/logout 后清空 session 并跳转 /login
  updatePoints: (points: number) => void;
  patchUser: (patch: Partial<AuthState['user']>) => void;
};
```

通过 `zustand/middleware` 的 `persist` 写入 `localStorage`，key 为 `shanpu.auth`。`hydrated` 表示浏览器端持久化登录态恢复完成，`AuthGuard` 与 `SiteHeader` 基于该状态决定展示加载态、登录入口或已登录用户信息。

`logout` 实现：调用 `userApi.logout()`，无论后端是否成功（网络异常也无所谓，因为本地清空已足够保护用户），始终在 `finally` 中执行 `clearSession()` 并通过 `router.push('/login')` 跳转登录页，最后 `toast.success('已登出')`。

### 6.2 选型理由

- React Context 在跨页面共享登录态时频繁触发整树重渲染，Zustand 提供 `useStore(selector)` 细粒度订阅。
- 体积约 1KB，远低于 Redux Toolkit，上手成本最低。
- 业务数据（商品列表、订单列表）由 RSC fetch 或客户端组件按需 fetch；Zustand 仅维护登录态；超级秒杀订单等待逻辑由 `use-seckill-result` 在订单详情页局部维护，保持页面局部状态。

## 7. HTTP 客户端

`lib/api/client.ts` 封装 `fetch`，对外暴露 `request<T>(method, path, init?)`：

```ts
export class ApiError extends Error {
  readonly code: number;
  readonly msg: string;
  readonly httpStatus?: number;

  constructor(code: number, msg: string, httpStatus?: number) {
    super(msg);
    this.name = 'ApiError';
    this.code = code;
    this.msg = msg;
    this.httpStatus = httpStatus;
  }
}

export async function request<T>(
  method: 'GET' | 'POST',
  path: string,
  init?: { body?: unknown; signal?: AbortSignal }
): Promise<T>;
```

### 7.1 通用行为

| 场景 | 处理 |
|---|---|
| 自动注入 token | 浏览器侧从 `useAuthStore.getState().token` 取；服务端侧不带（RSC 仅访问无鉴权接口） |
| Content-Type | 默认 `application/json`；GET 不带 body |
| baseURL | 浏览器侧从 `usePublicConfig().apiBase` 取（根 layout 注入，详见 §7.3）；服务端侧 `process.env.INTERNAL_API_BASE_URL` |
| 响应解析 | 读 `Result<T>`，`code !== 0` 抛 `ApiError(code, msg)` |
| HTTP 401 | 调用 `useAuthStore.getState().clearSession()` 并 `router.push('/login')` |
| HTTP 429 | 抛 `ApiError(42900, '操作过于频繁', 429)` |
| 网络异常 | 抛 `ApiError(0, '网络异常，请稍后重试')`，组件层 toast |

### 7.2 业务封装

`lib/api/<module>.ts` 一一对应后端 API：

```ts
// lib/api/seckill.ts
export const seckillApi = {
  list: (params?: { tier?: 1 | 2 | 3; status?: 'ACTIVE' | 'UPCOMING' }) =>
    request<Activity[]>('GET', `/seckills${toQuery(params)}`),
  detail: (id: number) => request<Activity>('GET', `/seckills/${id}`),
  grab: (id: number) =>
    request<{ orderId: string; status: 'PAID' | 'PENDING'; tier: 1 | 2 | 3 }>('POST', `/seckills/${id}/grab`),
};

// lib/api/user.ts
export const userApi = {
  register: (body: RegisterReq) => request<UserSession>('POST', '/users/register', { body }),
  login: (body: LoginReq) => request<UserSession>('POST', '/users/login', { body }),
  logout: () => request<void>('POST', '/users/logout'),
  me: () => request<UserInfo>('GET', '/users/me'),
  myPoints: (options?: { force?: boolean }) => Promise<{ points: number }>,
  updateNickname: (body: { nickname: string }) =>
    request<UserInfo>('POST', '/users/me/nickname', { body }),
};
```

`userApi.myPoints()` 使用模块级内存缓存，按当前 token 维度缓存 5 秒；`useAuth().refreshPoints()` 在下单成功后传 `force: true` 绕过缓存，保证积分余额立即刷新。

### 7.3 运行时配置注入

`PUBLIC_API_BASE_URL` 与部署目标的主机名 / 端口耦合，不能在 build-time 烤进 JS bundle，否则用户改 `.env` 后必须重 build 镜像。采用 SSR 注入 + client 端解析机制：

1. **server 侧渲染**：`app/layout.tsx` 作为 server component，每次请求时读取 `process.env.PUBLIC_API_BASE_URL`，序列化为：
   ```tsx
   <script id="__shanpu_config__" type="application/json">
     {JSON.stringify({ apiBase: process.env.PUBLIC_API_BASE_URL })}
   </script>
   ```
2. **client 侧解析**：`lib/config/public-config.ts` 提供 `readPublicConfig()`，浏览器侧 `JSON.parse(document.getElementById('__shanpu_config__')!.textContent!)`，模块级缓存避免重复解析；SSR/RSC 路径依优先级回退 `process.env.INTERNAL_API_BASE_URL` → `process.env.PUBLIC_API_BASE_URL` → `process.env.NEXT_PUBLIC_API_BASE_URL`，不缓存避免容器跨请求污染。
3. **Hook 暴露**：`hooks/use-public-config.ts` 暴露 `usePublicConfig()` 供 client 组件订阅 `apiBase`。
4. **client.ts 适配**：`lib/api/client.ts` 在 `request()` 内按运行环境分流——浏览器侧 lazy 调 `readPublicConfig().apiBase`；服务端侧（SSR/RSC）直接读 `process.env.INTERNAL_API_BASE_URL`，不走 `<script>` 注入路径。

效果：用户在根目录 `.env` 改 `PUBLIC_API_HOST` 或 `BACKEND_PORT` 后，`docker compose up -d frontend` 重启容器即可生效，无需重 build 镜像。

容器化部署接管运行时注入；本地开发（`pnpm dev`）下 `lib/config/public-config.ts` 直接读 `NEXT_PUBLIC_API_BASE_URL` 这个 build-time 变量，浏览器与后端跨域由后端 CORS（[BACKEND.md §4.6](./BACKEND.md)）放行，链路与 §7.3 一致。

## 8. 环境变量

`.env.local`（开发） / 容器 `environment:` 段（部署）：

| 变量 | 范围 | 默认值 | 说明 |
|---|---|---|---|
| `PUBLIC_API_BASE_URL` | 服务端读取→注入到浏览器 | `http://localhost:8080/api` | 浏览器调用后端的基地址。由 docker-compose 拼自 `${PUBLIC_API_HOST}:${BACKEND_PORT}`；server component 读取后嵌入 HTML 供 client hydrate 时消费（详见 §7.3） |
| `INTERNAL_API_BASE_URL` | frontend 容器服务端 | `http://backend:8080/api` | SSR/RSC 在 Docker 网络内调用后端，硬编码在 docker-compose `environment:` 段 |
| `NEXT_PUBLIC_SITE_NAME` | 浏览器 | `闪铺` | 站点名，build-time 烤进 bundle |
| `NEXT_PUBLIC_POLL_INTERVAL_MS` | 浏览器 | `1000` | 订单轮询间隔，build-time 烤进 bundle |
| `NEXT_PUBLIC_POLL_MAX_TIMES` | 浏览器 | `10` | 订单最大轮询次数，build-time 烤进 bundle |

`PUBLIC_API_BASE_URL` 与 `INTERNAL_API_BASE_URL` 走运行时环境变量，与部署目标耦合，改值后重启容器即生效；`NEXT_PUBLIC_*` 三项与部署无关，build-time 编译进静态产物。

## 9. 页面与 API 映射

| 页面 | 调用接口 |
|---|---|
| `/` | `GET /api/seckills?status=ACTIVE` + `GET /api/products?page=1&size=8` |
| `/products` | `GET /api/products?page=&size=` |
| `/products/[id]` | `GET /api/products/{id}`；下单按钮 `POST /api/products/{id}/order` |
| `/seckill` | `GET /api/seckills`（可附带 `tier` 过滤） |
| `/seckill/[id]` | `GET /api/seckills/{id}`；抢购按钮 `POST /api/seckills/{id}/grab`，按响应 `status` 分流：`'PAID'` 直接 `toast.success('抢购成功')` + 刷新积分 + `router.push('/orders/'+orderId)`；`'PENDING'` 直接 `router.push('/orders/'+orderId)`，订单详情页执行 1 秒间隔、最多 10 次查询等待订单落库 |
| `/login` | `POST /api/users/login` |
| `/register` | `POST /api/users/register` |
| `<SiteHeader>` 登出按钮 | `POST /api/users/logout` |
| `/orders` | `GET /api/orders?status=&page=&size=` |
| `/orders/[id]` | `GET /api/orders/{orderId}`（仅超级档需要轮询） |
| `/me` | `GET /api/users/me`、`GET /api/users/me/points`；昵称保存调用 `POST /api/users/me/nickname` |

商品详情通过 `loading.tsx` 展示骨架屏；商品不存在时展示稳定空态，后端或网络异常时展示可刷新错误态。订单详情使用 `loading / missing / error / ready` 四类状态，错误态保留返回订单列表入口，超级秒杀待确认状态达到轮询上限后展示手动刷新入口。登录、注册、普通商品下单按钮在提交中禁用并显示提交中文案。

## 10. 超级秒杀异步轮询

`seckillApi.grab(id)` 返回 `status === 'PENDING'`（即超级档 `tier=3`）后直接跳转订单详情。`use-seckill-result` 对初始 `40400` 与订单 `PENDING` 状态统一按 1 秒间隔查询，最多 10 次；状态转为 `PAID` 后停止轮询并强制刷新 `/api/users/me/points`，状态转为 `CANCELLED` 后停止轮询并展示失败状态，达到次数上限仍为 `PENDING` 时展示手动刷新入口。

后端 `/api/orders/{orderId}` 命中 30 秒用户级缓存；超级档 Kafka ack 后会先写入 `PENDING` 订单详情占位，消费落库后失效该缓存，单用户单订单的最大 QPS 不会击穿到 DB。

## 11. 错误与 Toast

### 11.1 业务码 → Toast 文案

| code | 默认文案 | UI 行为 |
|---|---|---|
| 40001 | 入参非法 | toast.error |
| 40100 | 请先登录 | toast.warning + 跳转 `/login` |
| 40400 | 资源不存在 | toast.error |
| 40900 | 该活动您已参与 | toast.warning |
| 41000 | 已售罄 | toast.error，按钮置灰 |
| 41001 | 活动尚未开始 | toast.info |
| 41002 | 活动已结束 | toast.info |
| 41100 | 积分不足 | toast.error |
| 42900 | 操作过于频繁 | toast.warning |
| 50000 | 系统繁忙 | toast.error |

后端返回的 `msg` 字段优先于上表默认文案。

### 11.2 网络异常

`fetch` 抛错（断网、CORS、JSON 解析失败）由 `client.ts` 转 `ApiError(0, '网络异常，请稍后重试')`，组件层统一 `toast.error('网络异常，请稍后重试')`。

### 11.3 不白屏原则

- 所有 `await request(...)` 包在 `try/catch` 中。
- RSC 中使用 Next.js 的 `error.tsx`、`not-found.tsx` 兜底。
- 商品详情、订单详情、商品列表、订单列表在 loading、empty、error 状态下渲染稳定内容，不出现纯白页。

## 12. 性能与体验细节

- 列表页使用 RSC 服务端渲染，首屏不依赖客户端 JS。
- 商品图片走 Next.js `<Image>` 自动懒加载与尺寸优化。
- 倒计时使用 `requestAnimationFrame` 节流到 1 秒一次，避免重渲染。
- 登录态持久化基于 `localStorage`，刷新页面不丢登录。
- 个人中心积分徽标 5 秒内复用内存缓存，减少重复 `/api/users/me/points` 请求。
- 秒杀按钮在前端做一次轻量节流（按下后 1 秒禁用），减轻服务端限流压力。
- 小屏布局以单栏为基准：header 自动换行，商品列表一列展示，秒杀与订单列表内容纵向排布，按钮和徽标允许换行。
