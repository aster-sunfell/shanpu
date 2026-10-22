# 实验验证

常规回归：

```bash
python3 scripts/experiments/lib/runtime_alignment.py
python3 -m unittest discover -s scripts/experiments/tests -v
node scripts/experiments/tests/schedule.test.mjs
python3 scripts/experiments/lib/runner.py redis-filtering --dry-run
python3 scripts/experiments/lib/runner.py kafka-burst --dry-run
```

正式 workflow 在启动业务容器之前，执行 `actions.py check-load`，使用实际 runner 镜像、选定发压 CPU、2 CPU / 4 GiB / 128 PIDs / 65536 文件描述符运行 Redis 600 RPS 或 Kafka 1000 RPS、12 秒注入、9 秒响应延迟检查。`check.json` 绑定当前源码、机器、job、实验和 CPU 集合。通过后只启动一次业务环境，初始空库/Redis/日志检查、实际数据点的准备核对、固定预热和正式测量在同一实例内完成。

独立 `experiment-regressions.yml` 在相关代码变更或手动触发时执行完整回归：

1. 实际默认 Redis 600 RPS × 60 秒或 Kafka 三段 60 秒形状；额外以所选实验最高 RPS（Redis 600、Kafka 1000）进行 9 秒延迟验证；人为限制 VU 的过载与恢复。
2. `actions.py fixture-check` 验证整场规模加余量的造数、两条业务路径及守恒关系；回归结束销毁临时环境，不再重建另一套正式实例。
3. 使用生产请求脚本、逐笔 JSONL、SQLite 核对，不模拟或补写 k6 到达时间戳。结果只标记 validation，不发布成性能报告。

HTTP 替身只证明生成器与关联分析可以工作；它不代表实际业务吞吐。正式点仍逐点检验实际输入和业务一致性。

Kafka 使用单一 constant-arrival-rate 调度时钟，时钟频率为三段最大 RPS；低流量段只选取均匀分布的时隙发 HTTP，其余时隙不发送请求。三段共用一个 VU 池，用户范围互斥，额外边界时隙不会使用热身用户。选择该实现是为了避免 ramping-arrival-rate 在首尾边界少发一条，并避免为三段分别分配 VU 池。

测试覆盖：固定矩阵与交替次序、非达标/负收益结果、正式无效点立即停止、数据错误中止、阶段用户及时间映射、突发请求 cohort、配对样本门槛、独立工作流、当前机器发压证据绑定、空环境核验、宿主安全、敏感信息脱敏、独立报告与按实验保留历史。

本地校验只能验证执行机制；真实 Redis 减负和 Kafka 削峰收益需要在 Actions 上分别运行两项实验确认。

不需要先校准：在 Actions 选择一个实验直接运行。固定热身、正式对照与诊断均在同一次 run 内完成。本地 HTTP 替身通过不能替代实际业务收益测量。

资源回归要求六个服务拥有相同的两个逻辑 CPU、无服务级 CPU 配额或自定义权重；Docker inspect 和 cgroup 实际设置都必须符合。内存、PID、文件描述符、重启策略及生成器隔离仍单独检查。热身回归覆盖固定等量执行、延迟趋势不阻断、早期无效点排空后继续、任一路径最终块不健康不能进入正式测量、数据损坏立即停止。报告函数在单测中不写 Actions summary。

2026-10-22 报告与流程重构验证：118 项 Python 回归、3 项 Node 调度测试、配置对齐、actionlint；两份真实 artifact 的全部 12 个正式点初始数据核对；桌面/移动页面布局与轮次切换；真实 Docker BuildKit 本地缓存导出与复用；1 CPU / 2 GiB / GOMAXPROCS=1 的短时 k6 验证发出 12000/12000；完整 Kafka 形状发出 11000/11000，人为 VU 不足正确判无效，恢复点 100/100。测试不等于新版云端流程耗时实测，首次 Actions 运行仍需核对 timings.json。

Kafka 削峰重构回归补充：观察器测试覆盖真实 MyBatis 拦截入口、连接等待不计入事务、同一事务不重复计数、并发短峰、跨秒、回滚/重试、外层事务与活动切换；Python 检查恢复阶段高峰不能被遗漏、计数与并发守恒、缺失压力证据停止后续点并禁用收益、Kafka 无任意响应/完成时限、超时后提交仍计入完成。旧 artifact 重生成只能验证兼容性，不能补出未采集的事务峰值。观察器只在 experiment profile 下生效，两方案承担同样的采集开销。

本次 Kafka 重构本地验证：124 项 Python 回归、3 项 Node 调度测试；Docker 中 Maven reactor 构建通过，新增 Spring/MyBatis/JDBC 事务生命周期及并发采样用例通过；旧 artifact 离线报告重生成与桌面/移动端检查通过。按 1 CPU / 2 GiB / GOMAXPROCS=1 配置运行真实 k6 短校验，12000/12000 请求、无迟发与 VU 耗尽。云端业务削峰数值尚未采集，不能从旧报告生成新峰值。


Actions 可靠性修复：Kafka 新窗口同时隔离准备请求的计数、峰值与桶，覆盖同秒准备/测量、活跃事务拒绝重置和窗口变化检测；正式发压或统计证据无效时第一点即停止，SLO 不达标但证据有效仍可继续。发压资源为 2 vCPU / 2 GiB / 6400 VU，验证实际慢响应占用，入口连接预算统一扩大，处理线程和数据库池保持不变。

`scripts/experiments/checks/business_pipeline.py` 是开发时的真实业务短回归入口，必须在隔离待测服务、受限发压容器和宿主 watchdog 下执行。它以 200 VU、短时低流量复用正式的 prepare / measure / report，分别检查 Redis A/B 和 Kafka B/C，并要求 Kafka 正式事务数等于正式完成订单数（不包含准备请求）。输出始终为 validation，不进入正式收益比较，不替代正式 Redis 6400 / Kafka 10400 VU 预算的生成器验证，也不增加正式 workflow 的准备步骤。

本次修复验证：125 项 Python 回归、3 项 Node 调度检查、Compose 配置对齐与 actionlint 通过。受限发压容器（2 vCPU / 2 GiB / 6400 VU）完成 Redis 默认形状 36000/36000、Kafka 默认形状 11000/11000；各自 9 秒慢响应检查均为 7200/7200，人为 VU 耗尽被判无效，恢复点均为 100/100。独立完整 Docker 服务栈的短链路中，Redis A/B 各 100 请求、10 个有效购买完成，Kafka B/C 各 140 请求与 140 个正式事务，测前计数均为 0；无 OOM/重启，HTML 重生成通过。这些是机制回归，未运行新版 Actions 的正式三轮收益实验。

Kafka 1000 RPS 配置验证：125 项 Python 回归、3 项 Node 调度测试、环境对齐和 actionlint 通过。2 vCPU / 4 GiB / 10400 VU 的真实 k6 容器完成三段形状 15000/15000 请求，以及 1000 RPS、9 秒响应延迟的 12000/12000 请求，均无迟发或 VU 耗尽；人为 VU 不足正确判无效，恢复点 100/100。k6 子进程峰值 RSS 2459028 KiB，容器无 OOM。Java 配置核验使用新连接上限，实际 socket 回归保留 6400 个空闲连接；额外尝试 10400 个空闲连接超出单测 192 MiB 堆，不能将配置上限视为业务承载保证。尚未运行 1000 RPS 的正式业务对照实验。
