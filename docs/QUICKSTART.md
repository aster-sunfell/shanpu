# 快速启动

```bash
cp .env.example .env       # 按需修改密码与 JWT_SECRET
docker compose up -d
```

约 90 秒后 `docker compose ps` 全部 `(healthy)`，浏览器访问 [http://localhost:3000](http://localhost:3000) 完成注册、登录、浏览商品、用积分下单、查看订单、查看个人中心、登出。

停止容器：

```bash
docker compose down
```

清空 MySQL / Redis 数据卷并重新执行 `01_schema.sql` / `02_seed.sql`（重建表结构与商品种子）：

```bash
docker compose down -v
docker compose up -d
```
