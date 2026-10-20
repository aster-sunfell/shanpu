# 秒杀性能实验

设计依据见 [PLAN.md](PLAN.md)。当前实现只有两组独立计划：Redis 前置过滤（A/B）与 Kafka 异步缓冲（B/C）。不生成三档总对比表，也不运行旧缓存 D 场景。

## 启动实验环境

先启动中间件，再以实验配置启动后端。后端保持前台运行；在另一终端执行实验。

```bash
bash scripts/dev/up.sh
SPRING_PROFILES_ACTIVE=dev,experiment bash scripts/dev/backend.sh
```

实验配置将业务日志设为 INFO，关闭库存对账、订单对账、超时关单、预热和 DLQ 自动回投的定时调度；Kafka 消费和消费失败重试保留。提交日志、事务尝试计数切面、SQL 计数器和指标接口均只在 `experiment` Profile 下装配，普通 dev 环境不创建这些组件。

秒杀订单写入器只在订单事务内发布业务事件 `SeckillOrderPaidEvent`，不依赖 `experiment` 包。实验观察器通过 `@TransactionalEventListener(AFTER_COMMIT)` 接收最终提交的订单；回滚、提交失败或事务外发布的事件不计入完成。事务尝试数由实验侧 AOP 统计，保留失败尝试和幂等跳过的原有口径；提交日志和指标字段保持兼容现有报告工具。

可使用已创建的独立空数据库：启动后端和运行实验时均设置 `EXPERIMENT_DATABASE=<库名>`，脚本会核对两侧数据库一致。旧开发库需要先按 [数据库说明](../DATABASE.md)处理 Flyway 基线，工具不会自动绕过迁移校验。

仅在独占的本地环境运行；测量期间不得有其他请求源或人工补偿。依赖 Docker Compose、Python 3 标准库和项目 Java/Maven 环境。k6 由 Compose 运行，直接访问宿主机后端。

## A/B：Redis 前置过滤

```bash
# 先预览，不访问数据库、不创建数据
bash scripts/experiments/run-redis-filtering.sh --dry-run

# 每个流量档位分别比较 A/B；每点 60 秒，重复 3 轮并交错顺序
bash scripts/experiments/run-redis-filtering.sh --rates 50,100,200 --seconds 60 --repeats 3
```

默认档位为 `valid,duplicate,soldout`，可用 `--workloads` 选择子集。全部有效时每人只请求一次；重复档位提前创建合法已购订单及流水，以固定的九次重复、一次有效交错请求；售罄档位在测量前库存已为零。用户池不会取模复用。相同压力点的两条链路使用等价的业务数据及流量构成。

重点查看有效订单延迟、重复/售罄延迟、DB 事务尝试数及 mapper 调用减少量。全部有效时仍保留 DB 热点库存行锁，允许出现 Redis 收益不明显或额外开销。

## B/C：Kafka 异步缓冲

先在本计划中校准同步 B 的完成容量。校准只跑 B 的稳定有效请求，并按所有重复轮次的 SLO 结果提出 Q 候选；需检查持续运行和资源曲线后采用。

```bash
bash scripts/experiments/run-kafka-buffering.sh --calibrate --rates 25,50,100,200 --seconds 60

# Q 为示例，必须替换为校准后确认的容量
bash scripts/experiments/run-kafka-buffering.sh --q 100
```

默认平稳压力为 `0.5Q,0.8Q,1.2Q`；突发为 `0.5Q` 运行 10 秒后，切换到 `2Q` 或 `5Q` 持续 10 秒，再停止注入并等待排空。每个组合均以相同过程分别运行 B/C，每点使用独立活动和独立用户。只使用库存、积分充足的新用户请求。

可通过 `--rates` 覆盖平稳压力；`--workloads steady` 或 `--workloads burst` 单独观察某个过程。响应超时默认 10 秒，停止注入后的排空观察上限默认 120 秒，可分别用 `--request-timeout`、`--drain-seconds` 修改。

接口受理和最终完成分开记录。B 返回 PAID，C 返回 PENDING；最终完成时间取事务事件 `AFTER_COMMIT` 监听器写入日志时的观察时间。请求发起时间来自同宿主机的 k6 容器，两者使用同一系统时钟。该值包含提交返回及事件回调调度的观察误差，不是数据库内部的精确提交时间，也不是前端缓存可见时间。

不会在发压迭代内轮询订单。请求日志、提交日志与最终 DB 快照在测量后离线关联，包括响应超时但实际落库的请求、缺失提交证据和截止时未完成的订单。完成分位数必须结合样本覆盖率和未完成数解释。

## 压力与 SLO 参数

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--seconds` | 60 | 平稳压力的注入时长 |
| `--repeats` | 3 | 每个点重复次数 |
| `--vus` | 100 | 每个到达率场景的固定 VU 配额，不自动扩张 |
| `--response-p99-ms` | 500 | 响应延迟 SLO，实验假设值，可修改 |
| `--completion-p99-ms` | 5000 | B/C 请求发起至提交观察时间的 SLO，可修改 |
| `--max-error-rate` | 0.001 | 系统异常、网络故障、限流等占比上限 |
| `--base-seconds` | 10 | 突发前的低负载时长 |
| `--burst-seconds` | 10 | 突发注入时长 |
| `--burst-multipliers` | 2,5 | 相对 Q 的突发倍数 |

默认值不代表系统已达到该性能。固定输入未达到时记录 dropped iterations，标记该点无效；不能把实际发送不足解释为服务承接了目标流量。高压时需先确认 k6 与宿主机未受限，再解释服务端瓶颈。

## 数据隔离和现场保留

每轮新建专属商品，每个测量点新建活动和一组带随机前缀的实验用户，不修改演示活动 1001/2001/3001。用户初始积分相同；重复档位的准备购买先扣积分并写入流水。预热使用独立的备用用户，其订单保存在测量前快照，排除正式新增量。

每轮开始前检查应用连接池、Kafka 三个分区与 DLQ。测量点共享同一执行锁；失败或未排空时保留现场并阻止后续可能受污染的测量。Kafka lag 归零不能替代订单核对；即使 DLQ 已标记回投，实验也要求先明确处理。

正常完成和失败退出均不自动清理。确认完整报告后，可显式删除本轮独立数据：

```bash
bash scripts/experiments/cleanup.sh docs/experiments/redis-filtering/<运行编号>
```

清理只接受已完成且逐点核对通过的运行，重新检查空闲状态和数据归属，一次事务删除对应订单、积分流水、活动、商品及用户，并清理其 Redis 数据和临时会话。报告保留。失败或未解决的运行必须先调查和人工核对，清理工具不会丢弃其证据。

## 产物及统计口径

| 位置 | 内容 |
|---|---|
| `scripts/experiments/` | 两个入口、共用 k6、采集、离线核对及测试 |
| `docs/experiments/redis-filtering/<运行编号>/` | A/B 报告及各点证据 |
| `docs/experiments/kafka-buffering/<运行编号>/` | B/C 或 B 容量校准报告及证据 |
| `.local/experiments/<运行编号>/` | 忽略的临时 Token 和用户清单 |
| `.local/experiments/commits.jsonl` | 后端提交观察日志，按活动分流归档 |

每次运行保留 `report.md`、`manifest.json`、资源配额和镜像版本。每个点保留：

- `point.json`：流量、活动和参数，不含 Token。
- `requests.jsonl`：请求开始和结果，包含用户索引、时间、状态和订单号，不含凭证。
- `commits.json`：对应活动的事务提交观察记录。
- `before.json`、`after.json`：库存、订单、积分及流水证据，识别准备数据和新增订单。
- `result.json`：分类结果、响应及完成分位数、覆盖率、未完成数、正确性核对。
- `timeline.csv`：每秒请求、受理、完成及待完成量；峰值待完成数另按原始事件顺序计算。
- `k6.out`、`k6-summary.json`：原始执行输出、实际压力、丢弃迭代等指标。
- `backend-before/after.json`、`mysql-before/after.json`、`samples.jsonl`、`kafka-final.json`：mapper 调用、事务尝试、连接池等待、GC、DB 行锁、容器 CPU/内存、宿主机 CPU/磁盘和消费 lag。

请求和受理 RPS 按配置的实际注入窗口计算，不把排空时间混入分母；窗口完成 RPS 只统计该窗口内提交的订单，窗口后的提交计入排空和完成延迟。缺失延迟样本为 null/NA，不填零。重复、售罄为预期业务拒绝，网络、超时、鉴权、限流和内部异常分别统计。

Mapper 指标是调用次数，包含失败尝试；MySQL 全局计数包含采样查询，不能视为单条业务的精确 SQL 数。容器、宿主机、日志与逐笔观察会产生共同开销；同机测试和单副本 Kafka 的结果不能外推为独立部署或高可用配置的能力。资源采样失败记录为错误，不静默填零。

报告默认纳入版本管理。原始数据较大时可归档并记录校验值；不得提交 `.local` 中的 Token。工作区未提交时，报告记录的源码指纹需与源代码一起保留，才能复现。

## 检查工具

```bash
python3 -m unittest discover -s scripts/experiments/tests -v
node scripts/experiments/tests/schedule.test.mjs
bash scripts/experiments/run-redis-filtering.sh --rates 5 --seconds 2 --repeats 1 --vus 5 --dry-run
```

本次重构的自动化检查和低负载联调记录见 [VALIDATION.md](VALIDATION.md)。
