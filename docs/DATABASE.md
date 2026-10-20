# 数据库结构与数据初始化

MySQL 保存用户、积分、商品、活动、订单和死信日志。Redis 承担缓存与秒杀前置过滤，Kafka 承担异步受理；最终业务写入仍由 MySQL 事务保证。

## 职责

| 内容 | 位置 | 执行方式 |
|---|---|---|
| 业务表结构与升级 | `backend/src/main/resources/db/migration` | Spring Boot 启动时执行 Flyway |
| 演示账号、商品、活动 | `scripts/db/fixtures/demo.sql` | 显式执行 `scripts/db/seed-demo.sh` |
| 测试业务数据 | `backend/src/test/resources/fixtures` | 测试自行加载，与演示数据无关 |
| 实验用户、库存和会话 | `scripts/experiments` | 实验脚本显式准备 |

Compose 使用 `MYSQL_DATABASE` 创建空数据库并保留数据卷，不再挂载初始化 SQL。迁移 SQL 不包含 `CREATE DATABASE` 或 `USE`，跟随实际 datasource 连接。MySQL 保持原有的版本、持久卷及资源配置。

## 新环境

1. 执行 `bash scripts/dev/up.sh` 启动中间件。
2. 执行 `bash scripts/dev/backend.sh`，Flyway 自动执行 `V1__initial_schema.sql`。
3. 后端启动成功后，在另一个终端执行 `bash scripts/db/seed-demo.sh`。

演示账号是 `summer / 123456`。导入命令可重复执行，只补齐缺失数据；不会重置已有用户密码、积分、商品库存、活动状态或时间。活动过期后也不会因重新导入而续期。需要全新演示状态时应使用独立的空开发数据库；性能实验在 GitHub Actions 使用临时独立数据库、活动和用户，证据归档后销毁专属数据卷。

生产环境仅执行正式迁移，不导入演示账号或实验数据。这里的数据库操作脚本默认针对本地 Compose；生产环境应由部署工具配置连接信息并运行同一套正式迁移。

## 已有数据库接入 Flyway

旧数据卷没有 `flyway_schema_history`。后端遇到非空且没有迁移历史的库会拒绝自动迁移，不会自动标记为 V1。

1. 停止使用该数据库的后端及实验进程，备份数据库。
2. 对照 `V1__initial_schema.sql`，逐表检查六张业务表的字段类型、默认值、可空性、主键、唯一索引及普通索引，确认实际结构一致。可使用 `SHOW CREATE TABLE` 检查；如不一致，先准备并核对修正 SQL，不能直接跳过。
3. 确认 `.env` 的数据库名和端口指向待接入的本地库，然后显式执行：

   ```bash
   bash scripts/db/baseline.sh --schema-verified
   ```

4. 重新启动后端，确认迁移校验通过，原有业务数据保留。

`--schema-verified` 表示操作者已完成结构核对，脚本本身不执行结构比较。baseline 只建立版本 1 的历史记录，不创建或修改业务表，也不清理数据。不要对空库执行 baseline，否则 V1 会被跳过；空库直接启动后端即可。

## 后续升级与验证

结构变更新增 `V2__description.sql`、`V3__description.sql` 等，已执行的迁移不再修改。运行时保持 `validate-on-migrate: true`、`baseline-on-migrate: false`、`clean-disabled: true`。迁移校验失败时应查明差异，不使用自动 repair 绕过。

执行 `cd backend && mvn verify`。独立 MySQL 测试容器覆盖：自定义数据库名、空库建表、重复启动不重跑、无演示数据、旧库拒绝自动接管、显式 baseline 保留数据、已执行脚本被修改后校验失败，以及真实事务并发防超卖。

MySQL DDL 可能隐式提交。迁移执行失败后应检查实际表结构并修复现场，不能假定全部 DDL 都已回滚。

参考：[Spring Boot 数据库初始化](https://docs.spring.io/spring-boot/docs/3.2.5/reference/html/howto.html#howto.data-initialization.migration-tool.flyway)、[Flyway baseline](https://documentation.red-gate.com/fd/baseline-277578867.html)。
