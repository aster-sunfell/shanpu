# 性能测量工具 v2 验证

验证日期：2026-10-20。本文件记录工具实现检查，不是 Redis/Kafka 性能报告。旧版全有效、重复、售罄及小流量演示数字已从正式文档移除，历史私有证据保留在 `.local/experiments/validation/`，不能用于 v2 容量或突发基线。

## 本机模式改造验证

本机正式性能模式已加入 `--deployment same-host`，不再将同机一律当作开发验证；分机正式模式保留。以下是工具检查，未执行正式业务性能实验。

- Python 回归更新为 44 项，覆盖部署身份、CPU 集合/超线程兄弟、实际 Docker 绑定核验、环境/模式基线隔离、Linux/Windows 内存门槛、压力恢复后仍保持失败、监控缺失/过期、时钟回跳、紧急停止、磁盘不可写时仍执行保护，以及中止 k6 后继续采集提交。宿主最终失败还能将 runner 已写 completed 的报告改为 failed、移除收益结论，防止成为后续基线。
- 新 runner 镜像已重建；44 项 Python 回归在 1 核 / 128 MiB 容器内通过。Docker 实际 CPU 集合、CPU quota 和内存上限由容器内读取核验。JavaScript 调度回归 4 项通过，Python/Shell 语法与 diff 空白检查通过。本次没有修改 Java 业务与观察器，也没有重跑 Java 构建。
- 已在本机持久化 CPU 选择：SUT `0,2`、runner `4`，避开系统报告的超线程兄弟。两个 Compose 配置解析通过；创建独立 runner 并核验 Docker 实际 `CpusetCpus=4`、CPU 1 核、内存/swap 合计 512 MiB；容器中 dry-run 返回 `scope=performance`、`deployment=same-host`，正式窗口与三轮规则保留。该容器已删除，未发出业务请求。
- 独立宿主看门狗实际生成并读取了新鲜的 Linux/Windows 内存和宿主资源记录；压力与强制停止路径只使用模拟数值验证，没有耗尽真实内存。
- 早先真实预检曾因 Windows 可用内存约 2.1 GiB 低于 3 GiB 而拒绝启动；稍后内存余量恢复时，正式命令在旧待测容器 CPU 绑定不匹配处拒绝，未创建正式实验报告。现有待测容器仍停止，未重建启动或绕过检查。CPU 绑定变更需要按 README 重新 build/up，且发压前再做预检，历史时钟回跳检查继续保留。

本机检查日志位于 `.local/experiments/same-host-runner-build.log`、`same-host-container-check.log`、`same-host-preflight.log`、`same-host-formal-guard.log` 与 `same-host-watch-check/`；它们都是实现验证证据，不是性能数据。

## v2 初版自动化检查

- Python 回归 28 项通过：分类 SLO、逐请求完成期限、超时后提交、响应前提交、未完成覆盖率、数据守恒、积压增长不能靠排空掩盖、注入时序失效、容量下界/区间/非单调、样本门槛、配对差与区间误差、基线来源和紧凑凭证。
- JavaScript 调度/用户映射回归 4 项通过：阶段边界额外迭代、用户范围隔离、ID 段缺口、计划注入时刻。
- 固定 k6 0.50 镜像内 `k6 inspect` 加载最终脚本和紧凑用户配置通过。
- 受限 Java 构建通过：业务单元测试 76 项、观察器测试 13 项。两个需 Docker socket 的 Testcontainers 集成测试未在无 socket 构建容器内执行。本次没有修改业务和 Java 观察器实现。
- Python/Shell 语法与 `git diff --check` 通过。

## 有界内存核对

最终镜像在 1 核 / 128 MiB 容器内，并将 Python 地址空间限制为 128 MiB，生成并处理 30000 条请求的流式快照、请求/提交关联、精确分位数、SLO、CSV 和 SVG。全部记录完成核对，峰值 RSS 26768 KiB，处理约 2.49 秒。

这是离线处理内存验证，不是业务吞吐实验，也不能外推 k6 在任意压力下的资源使用。原始脚本和结果位于 `.local/experiments/performance-v2-large-check.py` / `performance-v2-large-check.log`。压测容器整体仍有 1 核 / 512 MiB 硬上限；k6 的内存内汇总/阈值计算已关闭。

## 容器流程与拒绝条件

开发期间，同机 `--validation-only` 的 A/B 混合流量、B/C 稳态和引用验证基线的突发流程均曾完整通过；成功运行的显式分批清理也已验证。证据均在忽略目录 `.local/experiments/validation/performance-v2/`，标题与 scope 明确为 validation，比较列表为空，不输出容量提升或延迟改善结论。

- `redis-filtering/20261020-082555-67542c/`、`20261020-083845-952103/`：混合流量流程通过、数据已显式清理。
- `kafka-buffering/20261020-083101-438305/`：稳态流程通过、数据已显式清理。
- `kafka-buffering/20261020-083128-4c7653/`：读取匹配的验证基线、阶段突发、排空与清理流程通过。

随后补充的基线源码绑定、调度时序门槛及报告口径由协议/调度回归和最终镜像加载检查覆盖。最终完整联调复查受主机时钟回跳影响，未强行绕过判定：

- 实际检测到约 1.8 秒的 wall clock 回跳，monotonic 时钟继续前进。
- 受影响点被标为无效；相应失败运行（包括 `20261020-082952-9d8d6f`、`20261020-084033-5d31a0`、`20261020-084107-412869`）保留 DB 和逐笔证据，不参与容量结论，也未执行失败现场清理。
- v2 初版的同机正式运行在发压前被拒绝（该分机硬门槛已由上述双部署模式替代）；验证基线不能用于正式突发，手填 Q、旧演示参数和短时正式运行均被拒绝。

时钟回跳不是服务性能数据。正式实验需要稳定时钟与充足资源，选择本机 CPU 隔离模式或独立主机模式，再执行足够窗口和重复次数；本轮没有产生正式容量或性能收益报告。

资源能力及实际配额复核通过，见 `.local/experiments/performance-v2-post-verify.json`。本次新增待测容器已停止；数据卷、失败现场和磁盘证据保留。操作及统计边界见 [PLAN.md](PLAN.md) 和 [README.md](README.md)。
