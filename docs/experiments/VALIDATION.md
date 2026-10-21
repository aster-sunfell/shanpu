# 实验验证

常规回归：

```bash
python3 scripts/experiments/lib/runtime_alignment.py
python3 -m unittest discover -s scripts/experiments/tests -v
node scripts/experiments/tests/schedule.test.mjs
python3 scripts/experiments/lib/runner.py redis-filtering --dry-run
python3 scripts/experiments/lib/runner.py kafka-burst --dry-run
```

工作流会在启动业务容器之前，用实际 runner 镜像、1 CPU / 1 GiB / 128 PIDs 执行 `checks/k6_recovery.py`：

1. 只校验本次所选实验的完整默认形状：Redis 200 RPS、60 秒、400 VU，或 Kafka 三段 60 秒、400 VU。
2. 人为限制并发验证丢请求确实归类为发压无效，排空后下一个固定点仍可正常执行。
3. 使用生产请求脚本、逐笔 JSONL、SQLite 核对与延迟分析，不模拟或补写 k6 到达时间戳。

HTTP 替身的订单和提交记录只用于验证发压及关联分析，不代表业务吞吐。正式数据预检随后验证实际 MySQL/Redis/Kafka、业务路径、资源余量，完成后重置。

Kafka 使用单一 constant-arrival-rate 调度时钟，时钟频率为三段最大 RPS；低流量段只选取均匀分布的时隙发 HTTP，其余时隙不发送请求。三段共用一个 VU 池，用户范围互斥，额外边界时隙不会使用热身用户。选择该实现是为了避免 ramping-arrival-rate 在首尾边界少发一条，并避免为三段分别分配 VU 池。

测试覆盖：固定矩阵与交替次序、非达标/负收益结果、无效点安全继续、数据错误中止、阶段用户及时间映射、突发请求 cohort、配对样本门槛、独立工作流、预检凭证绑定和重置、宿主安全、敏感信息脱敏、独立报告与按实验保留历史。

本地校验只能验证执行机制；真实 Redis 减负和 Kafka 削峰收益需要在 Actions 上分别运行两项实验确认。

2026-10-21 本地验证：95 项 Python 回归、3 项 Node 调度测试、共享运行配置对齐和 actionlint 通过。1 CPU / 1 GiB 容器内实际 k6 默认 Redis 发出 12000/12000，默认 Kafka 发出 9000/9000；人为 VU 不足正确判为无效，排空后的低速点恢复正常。该校验使用 HTTP 替身，不是业务收益结论。
