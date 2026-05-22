# 闪铺(shanpu) 前端技术设计

## 1. 定位与参考

闪铺 Web 是用户与商城交互的唯一入口，承担三件事：

- 浏览商品与秒杀活动（含倒计时、库存预览，三档活动以徽标区分）。
- 普通商品下单（直连 DB 链路）与三档秒杀活动抢购（普惠 / 限时 / 超级，对应 DB / Redis+Lua / Redis+Lua+Kafka）。
- 个人中心：登录、注册、积分余额、我的订单。

页面与组件结构参考开源项目 [chatgptuk/ldc-shop](https://github.com/chatgptuk/ldc-shop) 的视觉与交互风格，但**仅参考布局与组件命名**，所有业务数据通过后端 REST API（[BACKEND.md](./BACKEND.md)）获取，不复用其 Drizzle ORM 直连数据库的实现路径。

## 2. 技术栈

| 类别 | 选型 | 备注 |
|---|---|---|
| 框架 | Next.js 15（App Router） | RSC + Client Component 混用 |
| 语言 | TypeScript 5.x | strict 模式 |
| 样式 | Tailwind CSS v4 | CSS-first，配置写在 `globals.css` |
| UI 组件 | Shadcn UI（Radix UI + Tailwind） | 通过 CLI 按需生成到 `components/ui/` |
| 图标 | lucide-react | - |
| 状态管理 | Zustand 4.x | 仅维护登录态 |
| HTTP | 原生 `fetch` 封装 | 不引入 axios / TanStack Query |
| 表单 | react-hook-form + zod | 登录 / 注册表单校验 |
| 通知 | sonner | toast 提示 |
| 包管理 | pnpm | - |

## 3. 目录结构

```text
web/
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
│   ├── globals.css
│   ├── (auth)/
│   │   ├── login/page.tsx
│   │   └── register/page.tsx
│   ├── products/
│   │   ├── page.tsx
│   │   └── [id]/page.tsx
│   ├── seckill/
│   │   ├── page.tsx
│   │   └── [id]/page.tsx
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
│   ├── points-badge.tsx
│   ├── order-status-badge.tsx
│   ├── auth-guard.tsx
│   ├── site-header.tsx
│   ├── site-footer.tsx
│   └── providers.tsx
├── lib/
│   ├── api/client.ts       # fetch 封装
│   ├── api/user.ts
│   ├── api/product.ts
│   ├── api/seckill.ts
│   ├── api/order.ts
│   ├── auth/token-store.ts # Zustand 持久化
│   └── utils.ts            # cn() 等
├── hooks/
│   ├── use-auth.ts
│   ├── use-countdown.ts
│   └── use-seckill-result.ts
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
| `/seckill/[id]` | RSC + Client | 否 | 活动详情 + 倒计时 + "立即抢"按钮（按响应 status 分流：PAID 直跳订单详情，PENDING 启动轮询） |
| `/login` | Client | 否 | 用户名 + 密码登录表单 |
| `/register` | Client | 否 | 注册表单 |
| `/orders` | Client | 是 | 我的订单列表，状态 tab 切换 |
| `/orders/[id]` | Client | 是 | 订单详情，超级秒杀订单在此轮询直到 PAID |
| `/me` | Client | 是 | 个人中心，展示用户名、昵称、积分 |

需要鉴权的页面在 `app/(authed)/layout.tsx` 或页面顶部包裹 `<AuthGuard>`，未登录跳转 `/login?next=<encoded>`。

## 5. 关键组件

### 5.1 展示组件

| 组件 | 用途 |
|---|---|
| `ProductCard` | 商品卡片：封面、标题、积分价、库存徽标 |
| `SeckillCard` | 秒杀卡片：商品图、原价 / 秒杀价、倒计时、库存进度条；右上角挂 `SeckillTierBadge` |
| `SeckillTierBadge` | 档位徽标：读活动 VO 的 `tier` 字段渲染 shadcn `Badge`——`tier=1` 普惠（secondary 绿）/`tier=2` 限时（default 橙）/`tier=3` 超级（destructive 红）。**不根据 ID 推断 tier**，运营可热切档位，ID 段仅为视觉惯例 |
| `SeckillCountdown` | 倒计时 `HH:mm:ss`，到时切按钮状态 |
| `PointsBadge` | header 右侧常驻徽标，显示当前积分 |
| `OrderStatusBadge` | 订单状态徽标，对应 4 个 shadcn `Badge` variant：`PENDING`（黄）/`PAID`（绿）/`CANCELLED`（灰）/`FAILED`（红） |

### 5.2 容器与守卫

| 组件 | 用途 |
|---|---|
| `SiteHeader` | 顶部导航：Logo、商品、秒杀、订单、我的、积分徽标。未登录显示"登录"按钮；已登录显示用户名 + 下拉菜单（个人中心、登出） |
| `SiteFooter` | 站点信息、备案文案占位 |
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
  setSession: (token: string, user: AuthState['user']) => void;
  clearSession: () => void;
  logout: () => Promise<void>;       // 调 POST /api/users/logout 后清空 session 并跳转 /login
  updatePoints: (points: number) => void;
};
```

通过 `zustand/middleware` 的 `persist` 写入 `localStorage`，key 为 `shanpu.auth`。

`logout` 实现：调用 `userApi.logout()`，无论后端是否成功（网络异常也无所谓，因为本地清空已足够保护用户），始终在 `finally` 中执行 `clearSession()` 并通过 `router.push('/login')` 跳转登录页，最后 `toast.success('已登出')`。

### 6.2 选型理由

- React Context 在跨页面共享登录态时频繁触发整树重渲染，Zustand 提供 `useStore(selector)` 细粒度订阅。
- 体积约 1KB，远低于 Redux Toolkit，上手成本最低。
- 业务数据（商品列表、订单列表）**不进 Zustand**，由 RSC fetch 或客户端组件按需 fetch；秒杀订单轮询单独由 `use-seckill-result` Hook 维护 `setInterval`，无需全局 store。

## 7. HTTP 客户端

`lib/api/client.ts` 封装 `fetch`，对外暴露 `request<T>(method, path, init?)`：

```ts
class ApiError extends Error {
  constructor(public code: number, public msg: string) { super(msg); }
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
| baseURL | 浏览器侧 `NEXT_PUBLIC_API_BASE_URL`；服务端侧 `INTERNAL_API_BASE_URL` |
| 响应解析 | 读 `Result<T>`，`code !== 0` 抛 `ApiError(code, msg)` |
| HTTP 401 | 调用 `useAuthStore.getState().clearSession()` 并 `router.push('/login')` |
| HTTP 429 | 抛 `ApiError(42900, '请求过于频繁，请稍后再试')` |
| 网络异常 | 抛 `ApiError(0, '网络异常')`，组件层 toast |

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
  myPoints: () => request<{ points: number }>('GET', '/users/me/points'),
};
```

## 8. 环境变量

`.env.local`（开发） / `.env.production` / Docker 构建参数：

| 变量 | 范围 | 默认值 | 说明 |
|---|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | 浏览器 | `http://localhost:8080/api` | 浏览器调用后端的基地址 |
| `INTERNAL_API_BASE_URL` | 服务端 | `http://backend:8080/api` | SSR/RSC 在 Docker 网络内调用 |
| `NEXT_PUBLIC_SITE_NAME` | 浏览器 | `闪铺` | 站点名 |
| `NEXT_PUBLIC_POLL_INTERVAL_MS` | 浏览器 | `1000` | 订单轮询间隔 |
| `NEXT_PUBLIC_POLL_MAX_TIMES` | 浏览器 | `10` | 订单最大轮询次数 |

## 9. 页面与 API 映射

| 页面 | 调用接口 |
|---|---|
| `/` | `GET /api/seckills?status=ACTIVE` + `GET /api/products?page=1&size=8` |
| `/products` | `GET /api/products?page=&size=` |
| `/products/[id]` | `GET /api/products/{id}`；下单按钮 `POST /api/products/{id}/order` |
| `/seckill` | `GET /api/seckills`（可附带 `tier` 过滤） |
| `/seckill/[id]` | `GET /api/seckills/{id}`；抢购按钮 `POST /api/seckills/{id}/grab`，按响应 `status` 分流：`'PAID'` 直接 `toast.success('抢购成功')` + `router.push('/orders/'+orderId)`；`'PENDING'` 直接 `router.push('/orders/'+orderId)` 由订单详情页 `use-seckill-result` 启动轮询 |
| `/login` | `POST /api/users/login` |
| `/register` | `POST /api/users/register` |
| `<SiteHeader>` 登出按钮 | `POST /api/users/logout` |
| `/orders` | `GET /api/orders?status=&page=&size=` |
| `/orders/[id]` | `GET /api/orders/{orderId}`（仅超级档需要轮询） |
| `/me` | `GET /api/users/me`、`GET /api/users/me/points` |
| `<SiteHeader>` 积分徽标 | `GET /api/users/me/points`（5 秒缓存于内存） |

## 10. 超级秒杀异步轮询

仅当 `seckillApi.grab(id)` 返回 `status === 'PENDING'`（即超级档 `tier=3`）时启动轮询；普惠 / 限时档直接命中订单详情 PAID 状态，无需轮询。

`use-seckill-result(orderId)` Hook 流程：

1. `useEffect` 启动定时器，间隔 `NEXT_PUBLIC_POLL_INTERVAL_MS`，调用 `orderApi.detail(orderId)`。
2. 最多轮询 `NEXT_PUBLIC_POLL_MAX_TIMES` 次。
3. 命中 `status === 'PAID'`：停止轮询，触发 `onPaid` 回调，更新积分余额。
4. 命中 `status === 'CANCELLED'`：停止轮询，触发 `onCancelled`。
5. 超过最大次数仍 `PENDING`：停止轮询，显示"系统繁忙，请稍后到订单列表查看"。
6. 组件卸载时 `clearInterval` 防止泄漏。

后端 `/api/orders/{orderId}` 命中 30 秒用户级缓存，单用户单订单的最大 QPS 不会击穿到 DB。

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

`fetch` 抛错（断网、CORS、JSON 解析失败）由 `client.ts` 转 `ApiError(0, '网络异常')`，组件层统一 `toast.error('网络异常，请稍后重试')`。

### 11.3 不白屏原则

- 所有 `await request(...)` 包在 `try/catch` 中。
- RSC 中使用 Next.js 的 `error.tsx`、`not-found.tsx` 兜底。
- 关键页面（首页、登录页）若数据获取失败，渲染骨架与"重试"按钮，不出现纯白页。

## 12. 性能与体验细节

- 列表页使用 RSC 服务端渲染，首屏不依赖客户端 JS。
- 商品图片走 Next.js `<Image>` 自动懒加载与尺寸优化。
- 倒计时使用 `requestAnimationFrame` 节流到 1 秒一次，避免重渲染。
- 登录态持久化基于 `localStorage`，刷新页面不丢登录。
- 秒杀按钮在前端做一次轻量节流（按下后 1 秒禁用），减轻服务端限流压力。
