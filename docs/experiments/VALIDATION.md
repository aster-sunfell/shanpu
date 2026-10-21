# 实验验证

常规回归：

```bash
python3 scripts/experiments/lib/runtime_alignment.py
python3 -m unittest discover -s scripts/experiments/tests -v
node scripts/experiments/tests/schedule.test.mjs
python3 scripts/experiments/lib/runner.py redis-filtering --dry-run
python3 scripts/experiments/lib/runner.py kafka-burst --dry-run
```

工作流会在启动业务容器之前，用实际 runner 镜像、1 CPU / 2 GiB / 128 PIDs / 8192 文件描述符 执行 `checks/k6_recovery.py`：

1. 只校验本次所选实验的完整默认形状：Redis 600 RPS、60 秒、1200 VU，或 Kafka 三段 60 秒、1200 VU。
2. 额外验证 800 RPS、固定 1 秒响应延迟、15 秒的发压形状。替身与 k6 共用单 CPU，使用 `GOMAXPROCS=1`；之后人为限制并发验证丢请求确实归类为发压无效，排空后下一个固定点仍可正常执行。
3. 使用生产请求脚本、逐笔 JSONL、SQLite 核对与延迟分析，不模拟或补写 k6 到达时间戳。

HTTP 替身的订单和提交记录只用于验证发压及关联分析，不代表业务吞吐。正式数据预检随后验证实际 MySQL/Redis/Kafka、业务路径、资源余量，完成后重置。

Kafka 使用单一 constant-arrival-rate 调度时钟，时钟频率为三段最大 RPS；低流量段只选取均匀分布的时隙发 HTTP，其余时隙不发送请求。三段共用一个 VU 池，用户范围互斥，额外边界时隙不会使用热身用户。选择该实现是为了避免 ramping-arrival-rate 在首尾边界少发一条，并避免为三段分别分配 VU 池。

测试覆盖：固定矩阵与交替次序、非达标/负收益结果、无效点安全继续、数据错误中止、阶段用户及时间映射、突发请求 cohort、配对样本门槛、独立工作流、预检凭证绑定和重置、宿主安全、敏感信息脱敏、独立报告与按实验保留历史。

本地校验只能验证执行机制；真实 Redis 减负和 Kafka 削峰收益需要在 Actions 上分别运行两项实验确认。

不需要先校准：在 Actions 选择一个实验直接运行。固定热身、正式对照与诊断均在同一次 run 内完成。本地 HTTP 替身通过不能替代实际业务收益测量。

资源回归要求六个服务拥有相同的两个逻辑 CPU、无服务级 CPU 配额或自定义权重；Docker inspect 和 cgroup 实际设置都必须符合。内存、PID、文件描述符、重启策略及生成器隔离仍单独检查。热身回归覆盖固定等量执行、延迟趋势不阻断、早期无效点排空后继续、任一路径最终块不健康不能进入正式测量、数据损坏立即停止。报告函数在单测中不写 Actions summary。

2026-10-22 验证：109 项 Python 回归、3 项 Node 调度测试、Docker 配置对齐和 actionlint 通过。采用 Actions 相同的 Maven 容器资源与测试选择完成 package（排除依赖独立数据库的 DatabaseMigrationTest / OrderConcurrencyTest）。两个真实容器验证 cpu.max 为 max、有效 CPU 集合相同；实际后端入口确认可见处理器数为 2。1 CPU / 2 GiB / GOMAXPROCS=1 的真实 k6 完整 Kafka 形状发出 11000/11000；800 RPS、1 秒延迟验证发出 12000/12000。人为 VU 不足正确标记为发压无效，随后恢复点发出 100/100。
