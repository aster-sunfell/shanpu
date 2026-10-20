# 闪铺(shanpu)

面向高并发秒杀场景的积分制商城。

本地通过 Docker 运行 MySQL、Redis、Kafka 和 Nginx，Java 17 与 Next.js 在宿主机运行。安装前端依赖后，在 VS Code 执行“启动开发环境”任务，访问 `http://127.0.0.1:8089`。

## 文档

- [快速启动](./docs/QUICKSTART.md)
- [部署](./docs/DEPLOYMENT.md)
- [数据库与迁移](./docs/DATABASE.md)
- [性能实验](./docs/experiments/README.md)
- [后端](./docs/BACKEND.md)
- [前端](./docs/FRONTEND.md)

## 目录

- `backend/`：后端、Flyway 迁移和测试。
- `frontend/`：Next.js 前端。
- `scripts/dev/`：开发环境生命周期；`scripts/db/`：数据库数据操作；`scripts/experiments/`：实验执行与采样。
- `deploy/`：部署配置。
- `docs/`：项目文档、实验设计及正式报告。
- `.local/`：不入库的临时凭证与本地运行状态。
