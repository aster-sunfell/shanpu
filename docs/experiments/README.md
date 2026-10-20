# 秒杀性能测量

仅保留两个正式计划：A/B 测固定混合流量下的延迟与容量；B/C 测稳定完成容量及突发业务收益。设计、判据和结论边界见 [PLAN.md](PLAN.md)。旧演示场景和手填 Q 已移除。

## 部署与固定预算

| 服务 | CPU 硬上限 | 内存硬上限 | 内部预算 |
|---|---:|---:|---|
| backend | 0.8 核 | 512 MiB | 堆 256 MiB，直接内存 48 MiB，Tomcat 32，DB 池 8 |
| MySQL | 0.5 核 | 384 MiB | buffer pool 128 MiB，32 连接 |
| Redis | 0.1 核 | 96 MiB | 数据 maxmemory 48 MiB，noeviction |
| Kafka | 0.6 核 | 448 MiB | 堆 192 MiB，清理去重缓存 16 MiB |
| 容器合计 | 2 核 | 1440 MiB | 加系统及 Docker 预留 608 MiB，按 2 GiB 机器预算 |

两个 Compose 项目：`experiments/compose.sut.yml` 包含独立业务与中间件；`experiments/compose.runner.yml` 包含 Python/k6，另占 1 核 / 512 MiB。各服务 swap 上限与内存相同，不自动重启；启动前检查引擎能力与实际配额。预留内存是预算，不是 Docker 对系统的硬预留；各服务不能借用空闲 CPU quota。

需要 Linux、Docker Compose v2、Python 3、Git；每台机器使用自己的本地 Docker Engine，不能通过远程 Docker context 冒充本机。Java 构建容器限制 2 核 / 768 MiB，构建时停止待测端。

待测主机：

```bash
# 分机运行时设置为私网地址；仅向压测机开放端口
export SUT_BIND_ADDRESS=<待测机私网IP>
export SUT_HOST=<待测机私网IP>
bash scripts/experiments/sut.sh config
bash scripts/experiments/sut.sh build
bash scripts/experiments/sut.sh up
bash scripts/experiments/sut.sh verify
```

私有凭证 `.local/experiments/sut.env` 权限 600，不加载开发 `.env`。私下复制到压测主机，并复制公共的 `target.json`。工具改动只需重建 runner；业务/待测配置改动需停止后重新 build/up/verify，再复制新的 target.json。基线还绑定发压工具源码指纹，改工具后需重测基线。两边使用相同源码；压测主机设置 `SUT_HOST`，执行：

```bash
export SUT_HOST=<待测机私网IP>
bash scripts/experiments/sut.sh build-runner
```

默认端口：API 18081、MySQL 13306、Redis 16379、Kafka 19092。默认绑定回环地址用于联调。正式测量要求主机身份摘要不同，并人工确认时钟同步、独占机器及网络条件。工具保存时钟偏差估计，能确定偏差超过 50 ms 时拒绝运行；仍存在回调和残余时钟误差。

业务模块不依赖实验模块；观察器在 `experiments/observer`，实验应用在 `experiments/application`，复用业务启动类。普通业务包为 `backend/target/shanpu-backend-1.0.0-exec.jar`，使用 `mvn -f backend/pom.xml clean package` 单独构建。实验关闭定时补偿，保留消费与失败重试。提交证据经鉴权分页接口读取，不挂载 Docker socket。

## A/B：同压延迟与容量

```bash
bash scripts/experiments/run-redis-filtering.sh --rates 25,50,100,200 --dry-run
bash scripts/experiments/run-redis-filtering.sh --rates 25,50,100,200 --seconds 60 --repeats 3
```

固定 10% 有效、90% 已购重复；同一到达率、窗口、顺序构成和独立等价数据。各类响应延迟与错误率单独统计。最高通过档位不足 1000 个有效响应样本时，增加时长/重复轮数重跑；不能把该档位作为容量失败。

低速档会按重复次数与有效样本比例自动延长窗口，最高 600 秒。某条链路取得有效失败上界后停止继续升压。初始阶梯后默认最多四轮中点细化（`--refine-steps 0..6`）。所有档位都通过时仅报告容量下界，需要提高输入阶梯继续测量；没有有效失败上界不报告精确容量提升。

## B/C：稳定容量与突发收益

```bash
bash scripts/experiments/run-kafka-buffering.sh --stage capacity --rates 25,50,100,200 --seconds 60 --repeats 3

# 指向上一轮完成且基线区间足够窄的正式报告
bash scripts/experiments/run-kafka-buffering.sh --stage burst \
  --baseline-report docs/experiments/kafka-buffering/<运行编号>/capacity.json
```

容量阶段 B/C 都使用可实际落单的新用户。突发从已测 B 基线读取 Q_B；禁止手填 `--q`。基线必须是完成的正式 v2 B/C 容量报告、有通过和失败上界、区间宽度 ≤10%（最小 1 RPS），且 SLO、实例、镜像、资源及运行配置一致。修改环境后重测基线。

默认 0.5Q_B 运行 10 秒，再以 2Q_B/5Q_B 分别突发 10 秒，停止注入后观察最多 120 秒。每个组合重复三轮、交错 B/C。分别比较响应期限内受理量和每个请求的完成期限内 PAID 数量，报告最终完成、完成延迟、积压、未完成及排空代价。

## 参数和结论门槛

| 参数 | 默认 | 含义 |
|---|---:|---|
| `--seconds` | 60 | 容量最短注入窗口，正式运行至少 60 秒，最多 600 秒 |
| `--repeats` | 3 | 每档重复轮数，正式运行至少 3，最多 10 |
| `--vus` | 20 | 每个到达率场景固定 VU，最多 100，不自动增长 |
| `--refine-steps` | 4 | 有效容量区间的自动细化轮数 |
| `--min-samples` | 1000 | 每档每链路跨轮累计有效响应样本最低数 |
| `--response-p99-ms` | 500 | 各流量类别响应 p99 要求，也用作及时受理期限 |
| `--completion-p99-ms` | 5000 | 有效请求最终完成 p99 要求，也用作每请求按时完成期限 |
| `--max-error-rate` | 0.001 | 每类请求错误率上限，预期重复拒绝不算错误 |
| `--request-timeout` | 10 | HTTP 超时秒数 |
| `--drain-seconds` | 120 | 注入结束后的观察上限 |
| `--base-seconds` / `--burst-seconds` | 10 / 10 | 突发前基线/突发时长 |
| `--burst-multipliers` | 2,5 | 相对实测 Q_B 的突发倍数 |

默认 SLO 是实验假设，不是已达性能。稳态还要求有效请求最终全部完成，outstanding 首末窗口增长与后半窗口斜率满足 PLAN 中的阈值。排空不替代稳态判定。

每档状态区分 `pass`、`fail`、`invalid`、`insufficient-samples`。只有输入与数据均有效、样本足够的 SLO 失败点才能构成容量失败上界。全部通过写“至少 X”，非单调结果要求重测。提升值报告配对中位数和范围，不把多个 p99 合并成一个分位数。

## 内存、数据与清理

不再将 20000 次/点、100000 次/轮作为请求上限。用户与 Redis 会话以 500 条分批准备，凭证使用紧凑 ID 段，k6 按需计算 JWT；MySQL 流式游标、Redis SSCAN、JSONL 与缓存 8 MiB 的 SQLite 负责证据与精确核对。分位数排序落盘，结果不常驻完整用户/请求数组。k6 0.50 使用 `--no-summary --no-thresholds` 关闭内存内聚合（[对应版本实现](https://github.com/grafana/k6/blob/v0.50.0/cmd/run.go#L158-L172)），由 `load.json` 和原始请求记录核对注入数量与调度时刻，所有分位数在 SQLite 计算。

目标 Redis 数据内存仍为 48 MiB；单点用户/会话过大时可能在准备阶段失败，这是数据规模限制，不能记作服务容量上界。每点结束并排空、核对、保存证据后，释放该点临时会话和 Redis 缓存；DB 行和磁盘证据保留，失败点保留原现场。数据库数据规模会累积，正式报告需结合本次准备规模解读。

发压主机可用内存低于 128 MiB、剩余磁盘不足 512 MiB、连续三次后端观测失败或提交日志异常时中止。时钟回跳/两机时钟分离、OOM/重启、数据错误、未排空时停止后续点，不自动删除现场。出现超时、网络或内部错误时，完整观察到排空截止；C 的异步去向仍不确定时保留现场并停止。只在关闭的完整运行上执行：

```bash
bash scripts/experiments/cleanup.sh docs/experiments/<计划>/<运行编号>
```

清理先核验整轮资源、用户、订单和商品归属，再每批最多 500 用户删除专属 DB 行；不删除报告。中断可能留下已核对运行的部分清理现场，保留剩余数据供人工核对，不清理失败运行。

## 报告与原始证据

正式报告：`docs/experiments/<计划>/<运行编号>/`。包含 `report.md`、`manifest.json`、`capacity.json` 或 `burst.json`。每点保留分类指标、请求/提交 JSONL、before/after 快照 JSONL、SQLite 关联证据、timeline.csv、k6 原始输出、JVM/DB/Kafka/发压主机采样。目标容器 RSS 与目标主机 CPU/磁盘应在目标主机另行采集，如 docker stats，工具不向压测容器开放 Docker socket。

Token、凭证、私有用户 ID 段和临时数据在 `.local/experiments/`。报告中的逐笔记录没有 Token。提交日志逐单 flush、AOP/mapper 计数和采样会产生观测开销；MySQL 全局计数包含采样查询，性能数字限定为这套受观测配置。原始数据较大时归档并记录校验值，不把样本删除后继续引用性能结论。

## 开发期验证

同机、短时或单轮只允许显式 `--validation-only`。这不是正式性能实验：产物进入 `.local/experiments/validation/performance-v2/`，标题标明禁止用于性能结论，比较字段不输出提升比例，正式突发拒绝引用此基线。

```bash
python3 -m unittest discover -s scripts/experiments/tests -v
node scripts/experiments/tests/schedule.test.mjs
bash scripts/experiments/run-redis-filtering.sh --rates 5 --seconds 2 --repeats 1 \
  --refine-steps 0 --validation-only
```

本轮实现验证记录见 [VALIDATION.md](VALIDATION.md)。停止环境使用 `bash scripts/experiments/sut.sh stop`，保留卷和证据。
