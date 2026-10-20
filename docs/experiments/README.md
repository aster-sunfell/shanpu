# 秒杀性能实验

设计依据见 [PLAN.md](PLAN.md)。当前实现只有两组独立计划：Redis 前置过滤（A/B）与 Kafka 异步缓冲（B/C）。不生成三档总对比表，也不运行旧缓存 D 场景。

## 独立待测端与压测端

待测端使用 `experiments/compose.sut.yml`，项目名 `shanpu-sut`，仅含后端、MySQL、Redis、Kafka；拥有独立网络、数据库、数据卷和凭证。压测端使用 `experiments/compose.runner.yml`，项目名 `shanpu-load`，Python 编排、k6 和离线核对共享一个有上限的容器，不挂载 Docker socket，不启动额外的发压容器。日常开发的根 Compose 不再提供 k6 入口。

待测机器固定预算为 **2 核 / 2 GiB**，以下数值直接写在 Compose 中，不能通过环境变量调大：

| 服务 | CPU 上限 | 容器内存上限 | 内部预算 |
|---|---:|---:|---|
| 后端 | 0.8 核 | 512 MiB | JVM 堆 128～256 MiB，直接内存 48 MiB，Tomcat 32 线程，DB 连接池 8 |
| MySQL | 0.5 核 | 384 MiB | InnoDB buffer pool 128 MiB，最多 32 连接 |
| Redis | 0.1 核 | 96 MiB | 数据 maxmemory 48 MiB，noeviction |
| Kafka | 0.6 核 | 448 MiB | JVM 堆 128～192 MiB，网络/IO 线程各 2，日志清理去重缓存 16 MiB |
| 容器合计 | **2 核** | **1440 MiB** | |
| 系统及 Docker 预留 | | **608 MiB** | 与容器合计 2048 MiB |

Compose 没有跨平台的“项目总内存上限”，这里以四个服务的固定上限控制总额，未写死父 cgroup 路径。608 MiB 是预算预留，不是 Docker 替系统保留的内存；其他程序仍可能消耗它。各服务 `memswap_limit` 等于 `mem_limit`，禁止通过 swap 扩张；限制进程数、日志大小，关闭自动重启。脚本先检查 Docker 引擎支持所需限制，再在创建后、启动前检查实际 CPU/内存/swap 配额，不支持限制的环境直接报错。

压测端另计 **1 核 / 512 MiB**。正式实验在另一台 Linux 主机运行压测端；在同一电脑分两个 Compose 运行仅用于联调，仍共享宿主机资源，不能当作独立 2 核机器的容量结论。各服务不能借用其他服务闲置的 CPU 配额，这种分配也不等价于专用物理双核。两台机器需同步系统时间；工具保存服务端与压测端的时钟偏差估计和请求往返不确定性，能确定偏差超过 50 ms 时拒绝运行。该检查不能替代系统时钟同步，最终完成延迟仍受残余偏差影响。

### 单机低负载联调

需要 Linux、Docker Engine / Compose v2、Python 3 和 Git。Java 编译放在单独的构建容器内，限制 2 核 / 768 MiB，Maven 堆 384 MiB、测试 JVM 堆 192 MiB；构建时待测端必须停止。镜像和依赖下载需要网络。构建运行 Java 单元与观测装配测试；需要 Docker socket 的两个 Testcontainers 集成测试不在这个无 socket 的构建容器内运行。

```bash
bash scripts/experiments/sut.sh config  # 生成私有凭证，仅输出公共预算
bash scripts/experiments/sut.sh build
bash scripts/experiments/sut.sh up      # 先校验硬配额，再启动并等待健康
bash scripts/experiments/sut.sh verify

# 先低负载验证两条链路；正式压力另行校准
bash scripts/experiments/run-redis-filtering.sh --rates 2 --seconds 2 --repeats 1 --vus 2
bash scripts/experiments/sut.sh status
bash scripts/experiments/sut.sh stop    # 停服务，保留证据和卷
```

凭证写入忽略目录 `.local/experiments/sut.env`（权限 600），不会加载或改写开发环境的 `.env`。默认仅发布回环地址：API `18081`、MySQL `13306`、Redis `16379`、Kafka `19092`。脚本会检查可用内存；运行时压测主机可用内存低于 128 MiB、连续三次无法获取后端指标或提交日志写入失败时中止发压，保留现场。

### 分机正式运行

1. 待测主机设置 `SUT_BIND_ADDRESS=<待测机私网IP>`、`SUT_HOST=<同一可达IP>` 后执行 build/up/verify，Kafka 对外地址必须是压测主机可达的地址。端口仅向压测主机开放。
2. 压测主机使用相同源码，私下复制 `.local/experiments/sut.env` 和公共的 `target.json`；保持凭证文件权限 600，不提交凭证。设置 `SUT_HOST=<待测机私网IP>`，执行 `bash scripts/experiments/sut.sh build-runner`，无需在压测机启动待测服务。
3. 待测机每次重建或改变配置后重新 verify，并更新压测机的 `target.json`。它记录实际镜像 ID、源码指纹和已核验的配额。运行前还核对业务侧实例 ID 和数据库名，拒绝误连开发服务。
4. 在压测机运行下面的两个计划。两组对照都使用同一资源和持久化配置，测量期间无其他流量；记录时钟同步与宿主机负载。修改工具或业务源码后必须重建镜像，旧指纹会阻止运行。

业务包不再包含实验实现。`experiments/observer` 只依赖业务事件并在 `AFTER_COMMIT` 写入证据，通过 AOP 保留事务尝试统计；`experiments/application` 装配观察器与业务库，专用于待测部署。普通业务可用 `mvn -f backend/pom.xml clean package` 单独构建，首次迁移需 clean 移除旧编译目录里的观察器；其可执行包为 `backend/target/shanpu-backend-1.0.0-exec.jar`。观察器关闭自动对账、超时关单、预热与 DLQ 回投调度，保留 Kafka 消费和消费失败重试。

提交证据保存在待测端 evidence 卷，经带鉴权的分页游标接口远程采集，不需要共享文件系统。每页最多 1000 条，增量读取，避免每次扫描和加载整个历史日志。

## A/B：Redis 前置过滤

```bash
# 先预览，不访问数据库、不创建数据
bash scripts/experiments/run-redis-filtering.sh --dry-run

# 每个流量档位分别比较 A/B；每点 60 秒，重复 3 轮并交错顺序
bash scripts/experiments/run-redis-filtering.sh --rates 10,25,50 --seconds 60 --repeats 3
```

默认档位为 `valid,duplicate,soldout`，可用 `--workloads` 选择子集。全部有效时每人只请求一次；重复档位提前创建合法已购订单及流水，以固定的九次重复、一次有效交错请求；售罄档位在测量前库存已为零。用户池不会取模复用。相同压力点的两条链路使用等价的业务数据及流量构成。

重点查看有效订单延迟、重复/售罄延迟、DB 事务尝试数及 mapper 调用减少量。全部有效时仍保留 DB 热点库存行锁，允许出现 Redis 收益不明显或额外开销。

## B/C：Kafka 异步缓冲

先在本计划中校准同步 B 的完成容量。校准只跑 B 的稳定有效请求，并按所有重复轮次的 SLO 结果提出 Q 候选；需检查持续运行和资源曲线后采用。

```bash
bash scripts/experiments/run-kafka-buffering.sh --calibrate --rates 25,50,100,200 --seconds 60

# Q 为示例，必须替换为校准后确认的容量
bash scripts/experiments/run-kafka-buffering.sh --q 100 --workloads steady
bash scripts/experiments/run-kafka-buffering.sh --q 100 --workloads burst
```

默认平稳压力为 `0.5Q,0.8Q,1.2Q`；突发为 `0.5Q` 运行 10 秒后，切换到 `2Q` 或 `5Q` 持续 10 秒，再停止注入并等待排空。每个组合均以相同过程分别运行 B/C，每点使用独立活动和独立用户。只使用库存、积分充足的新用户请求。

可通过 `--rates` 覆盖平稳压力；`--workloads steady` 或 `--workloads burst` 单独观察某个过程。响应超时默认 10 秒，停止注入后的排空观察上限默认 120 秒，可分别用 `--request-timeout`、`--drain-seconds` 修改。

接口受理和最终完成分开记录。B 返回 PAID，C 返回 PENDING；最终完成时间取事务事件 `AFTER_COMMIT` 监听器写入日志时的观察时间。请求发起时间来自压测主机的 k6 容器，分机运行时必须确认两侧时钟同步。该值包含提交返回及事件回调调度的观察误差，不是数据库内部的精确提交时间，也不是前端缓存可见时间。

不会在发压迭代内轮询订单。请求日志、提交日志与最终 DB 快照在测量后离线关联，包括响应超时但实际落库的请求、缺失提交证据和截止时未完成的订单。完成分位数必须结合样本覆盖率和未完成数解释。

## 压力与 SLO 参数

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--seconds` | 60 | 平稳压力的注入时长 |
| `--repeats` | 3 | 每个点重复次数 |
| `--vus` | 20 | 每个到达率场景的固定 VU 配额，不自动扩张 |
| `--response-p99-ms` | 500 | 响应延迟 SLO，实验假设值，可修改 |
| `--completion-p99-ms` | 5000 | B/C 请求发起至提交观察时间的 SLO，可修改 |
| `--max-error-rate` | 0.001 | 系统异常、网络故障、限流等占比上限 |
| `--base-seconds` | 10 | 突发前的低负载时长 |
| `--burst-seconds` | 10 | 突发注入时长 |
| `--burst-multipliers` | 2,5 | 相对 Q 的突发倍数 |

每点最多 20000 个计划请求、每轮最多 100000 个、最多 100 个测量点，每个场景最多 100 VU，单段时长最多 600 秒；超预算在创建用户和发压前拒绝，dry-run 也检查预算。A/B 默认输入 10/25/50 RPS。长时间或较大 Q 的实验应拆分为独立运行；先用 dry-run 检查规模。

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
| `shanpu-sut_evidence` Docker 卷 | 后端提交观察日志，按游标远程采集归档 |
| `.local/experiments/target.json` / `source.json` / `sut-source.json` | 公共资源核验和源码指纹，不含凭证 |

每次运行保留 `report.md`、`manifest.json`、资源配额和镜像版本。每个点保留：

- `point.json`：流量、活动和参数，不含 Token。
- `requests.jsonl`：请求开始和结果，包含用户索引、时间、状态和订单号，不含凭证。
- `commits.json`：对应活动的事务提交观察记录。
- `before.json`、`after.json`：库存、订单、积分及流水证据，识别准备数据和新增订单。
- `result.json`：分类结果、响应及完成分位数、覆盖率、未完成数、正确性核对。
- `timeline.csv`：每秒请求、受理、完成及待完成量；峰值待完成数另按原始事件顺序计算。
- `k6.out`、`k6-summary.json`：原始执行输出、实际压力、丢弃迭代等指标。
- `backend-before/after.json`、`mysql-before/after.json`、`samples.jsonl`、`kafka-final.json`：mapper 调用、事务尝试、连接池等待、GC、DB 行锁、JVM CPU/堆、压测端进程内存、压测宿主机 CPU/磁盘和消费 lag。

请求和受理 RPS 按配置的实际注入窗口计算，不把排空时间混入分母；窗口完成 RPS 只统计该窗口内提交的订单，窗口后的提交计入排空和完成延迟。缺失延迟样本为 null/NA，不填零。重复、售罄为预期业务拒绝，网络、超时、鉴权、限流和内部异常分别统计。

各点采样不依赖 Docker socket，因此不提供实时目标容器 RSS；目标配额与镜像来自启动核验，目标宿主机的 CPU/内存/磁盘曲线需另行采集（如 `docker stats`）。

Mapper 指标是调用次数，包含失败尝试；MySQL 全局计数包含采样查询，不能视为单条业务的精确 SQL 数。容器、宿主机、日志与逐笔观察会产生共同开销；同机测试和单副本 Kafka 的结果不能外推为独立部署或高可用配置的能力。资源采样失败记录为错误，不静默填零。

报告默认纳入版本管理。原始数据较大时可归档并记录校验值；不得提交 `.local` 中的 Token。工作区未提交时，报告记录的源码指纹需与源代码一起保留，才能复现。

## 检查工具

```bash
python3 -m unittest discover -s scripts/experiments/tests -v
node scripts/experiments/tests/schedule.test.mjs
bash scripts/experiments/run-redis-filtering.sh --rates 5 --seconds 2 --repeats 1 --vus 5 --dry-run
```

本次重构的自动化检查和低负载联调记录见 [VALIDATION.md](VALIDATION.md)。
