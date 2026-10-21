# 实验验证

常规回归：

```bash
python3 scripts/experiments/lib/runtime_alignment.py
python3 -m unittest discover -s scripts/experiments/tests -v
node scripts/experiments/tests/schedule.test.mjs
python3 scripts/experiments/lib/runner.py redis-filtering --dry-run
python3 scripts/experiments/lib/runner.py kafka-burst --dry-run
```

正式 workflow 在启动业务容器之前，执行 `actions.py check-load`，使用实际 runner 镜像、选定发压 CPU、1 CPU / 2 GiB / 128 PIDs / 8192 文件描述符运行 15 秒延迟负载检查。`check.json` 绑定当前源码、机器、job、实验和 CPU 集合。通过后只启动一次业务环境，初始空库/Redis/日志检查、实际数据点的准备核对、固定预热和正式测量在同一实例内完成。

独立 `experiment-regressions.yml` 在相关代码变更或手动触发时执行完整回归：

1. 实际默认 Redis 600 RPS × 60 秒或 Kafka 三段 60 秒形状；额外 800 RPS、1 秒延迟验证；人为限制 VU 的过载与恢复。
2. `actions.py fixture-check` 验证整场规模加余量的造数、两条业务路径及守恒关系；回归结束销毁临时环境，不再重建另一套正式实例。
3. 使用生产请求脚本、逐笔 JSONL、SQLite 核对，不模拟或补写 k6 到达时间戳。结果只标记 validation，不发布成性能报告。

HTTP 替身只证明生成器与关联分析可以工作；它不代表实际业务吞吐。正式点仍逐点检验实际输入和业务一致性。

Kafka 使用单一 constant-arrival-rate 调度时钟，时钟频率为三段最大 RPS；低流量段只选取均匀分布的时隙发 HTTP，其余时隙不发送请求。三段共用一个 VU 池，用户范围互斥，额外边界时隙不会使用热身用户。选择该实现是为了避免 ramping-arrival-rate 在首尾边界少发一条，并避免为三段分别分配 VU 池。

测试覆盖：固定矩阵与交替次序、非达标/负收益结果、无效点安全继续、数据错误中止、阶段用户及时间映射、突发请求 cohort、配对样本门槛、独立工作流、当前机器发压证据绑定、空环境核验、宿主安全、敏感信息脱敏、独立报告与按实验保留历史。

本地校验只能验证执行机制；真实 Redis 减负和 Kafka 削峰收益需要在 Actions 上分别运行两项实验确认。

不需要先校准：在 Actions 选择一个实验直接运行。固定热身、正式对照与诊断均在同一次 run 内完成。本地 HTTP 替身通过不能替代实际业务收益测量。

资源回归要求六个服务拥有相同的两个逻辑 CPU、无服务级 CPU 配额或自定义权重；Docker inspect 和 cgroup 实际设置都必须符合。内存、PID、文件描述符、重启策略及生成器隔离仍单独检查。热身回归覆盖固定等量执行、延迟趋势不阻断、早期无效点排空后继续、任一路径最终块不健康不能进入正式测量、数据损坏立即停止。报告函数在单测中不写 Actions summary。

2026-10-22 报告与流程重构验证：118 项 Python 回归、3 项 Node 调度测试、配置对齐、actionlint；两份真实 artifact 的全部 12 个正式点初始数据核对；桌面/移动页面布局与轮次切换；真实 Docker BuildKit 本地缓存导出与复用；1 CPU / 2 GiB / GOMAXPROCS=1 的短时 k6 验证发出 12000/12000；完整 Kafka 形状发出 11000/11000，人为 VU 不足正确判无效，恢复点 100/100。测试不等于新版云端流程耗时实测，首次 Actions 运行仍需核对 timings.json。
