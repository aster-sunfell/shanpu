# GitHub Actions 性能实验

压测仅在 GitHub 托管 Linux runner 执行，在同一台临时虚拟机中运行 backend、MySQL、Redis、Kafka 和 Python/k6。无需开发者本机运行服务，也无需在仓库配置数据库密码或 SSH 密钥 secrets。

实验设计和结论门槛见 [PLAN.md](PLAN.md)，迁移范围见 [GITHUB_ACTIONS_MIGRATION_PLAN.md](GITHUB_ACTIONS_MIGRATION_PLAN.md)，实现验证状态见 [VALIDATION.md](VALIDATION.md)。

## 触发实验

压测仅通过 `workflow_dispatch` 手动启动，平时向 main 或其他分支推送、提交 PR、创建标签和归档报告都不会自动发压。

需要压测时，打开 [Actions → Performance experiments](https://github.com/aster-sunfell/shanpu/actions/workflows/performance.yml)，点击 **Run workflow**，选择 **main**，再点击 **Run workflow**。每次点击先执行真实环境预检，通过并重建干净环境后才启动完整正式实验，结束后自动发布 HTML；预检失败同样保留诊断并生成报告。仅允许 main 执行；选择其他分支时所有 job 跳过。

验证最新修正时请新建一次 Run workflow，不要在旧失败运行中点击 Re-run jobs；旧运行的重试沿用旧提交及其工作流配置。

运行进度见 [Actions → Performance experiments](https://github.com/aster-sunfell/shanpu/actions/workflows/performance.yml)。构建和测量串行进行，同一 main 分支的运行使用并发组排队，不取消正在进行的实验。

| 固定配置 | 值 | 用途 |
|---|---|---|
| experiment | full | A/B 容量及 B/C 容量与突发 |
| A/B rates | 200,250,300,350,400 | 混合流量初始阶梯 |
| B/C rates | 200,225,250,275,300 | 全购买流量初始阶梯 |
| seconds | 60 | 正式容量点最低注入秒数，低速档按样本目标延长，单点最多 600 秒 |
| repeats | 3 | 正式测量至少三轮 |
| vus | 200 | 容量与突发统一预分配并固定 200 个 VU；不在测量中动态扩容 |
| refine_steps | 4 | 每条路径的有效通过/失败区间最多细化四轮 |
| expand_steps | 2 | 每条路径最多额外搜索两轮：全部通过时升压 25%，起点失败时降压 20% |
| search_minutes | 50 | 每组容量搜索预算，包含造数和核对；启动新档位前预留时间 |
| publish | 自动 | 完成后发布已有 HTML，包括明确标记的中断或失败报告 |

默认阶梯定义在 `scripts/experiments/lib/protocol.py`，VU 数由 `.github/workflows/performance.yml` 的 `BENCH_VUS` 指定；执行器默认及当前上限均为 200。`--rates` 可同时覆盖两组阶梯，省略时使用各自默认值。修改配置后提交到 main，之后按需手动启动；仅推送不会执行压测。正式样本、SLO、环境和基线判据继续由执行器核验，不允许为通过工作流而降低门槛。

预分配 200 个 VU 为启动阶段及慢响应保留并发余量，发压端仍限制为 1 vCPU / 512 MiB。第一档 200 RPS 在同一任务中重新测量，检查扩大 VU 后的实际发压表现；每点日志与结果保存 HTTP 在途峰值，`samples.jsonl` 中 `runner.containerResources` 保存可读的发压容器 CPU/内存计数（不可读时明确标记）。HTTP 在途数不包含完整脚本执行开销，不能当作精确的活跃 VU 数。若 k6 报告 `Insufficient VUs`、计划请求缺失或注入越界，该点仍判为发压无效并停止，不作为服务容量失败上界。200 VU 在新档位和 5 倍突发下是否足够仍需实测。

B/C 的突发只引用本 job 刚完成的容量报告。B 基线必须样本充分、有通过和有效失败上界、区间宽度不超过 10%（最低 1 RPS）。容量与突发的 VU、超时、排空时间及环境指纹必须匹配，旧的 100 VU 报告需要重测。找到有效失败档后，该路径跳过更高档位；只有该路径缺少的细化/扩展档位才继续测量，所有用于基线的点仍至少三轮、每轮 60 秒，不从短时探测推导正式基线。

预算或搜索轮数耗尽、起点始终失败、结果非单调以及尚未找到上界时，保留已完成的观测并把整组标为 `inconclusive`；B 基线不合格或剩余预算不足以完成整套突发轮次时，跳过突发并说明原因。已有有效容量区间与下限继续展示，报告不会成为“最近一次完整正式实验”。代码、SLO 配置、环境、数据/证据或发压错误仍判为 failed，不会因容量未定逻辑而被放行。不手填 Q，不使用其他运行的旧基线。

迁移期间已通过短时 smoke 验证 A/B、B/C 容量和突发的执行、证据关联及业务核对。验证报告标记 validation，不输出性能提升结论；最终工作流在 main 上运行正式 full 实验。

## 正式测量前的真实环境预检

三个 job 保持为 checks、performance、publish。checks 标记为 unit tests，只验证工具逻辑；真实预检位于 performance 中，使用正式镜像、同一 runner 和原有资源配额，构建只执行一次。

1. 读取实际 MySQL 版本和内存配置，检查 cgroup 资源。MySQL 启动时要求至少 64 MiB 工作集估算余量，造数及短时请求阶段要求至少 32 MiB；OOM、重启、计数缺失或余量不足即停止。工作集估算为 memory.current 减 inactive_file，原始占用、内存类型与压力事件同时保留；这不是实际可分配内存保证，也不把正常文件缓存直接当作泄漏。
2. 根据两组各自初始容量阶梯的计划请求量，快速准备 1.2 倍用户规模的混合数据。默认 full 初始请求总量 990000，对应 1188000 个造数用户，按最大单点 24000 分批、交替走 A/B 准备流程，核对快照中的混合比例、预热用户、订单、流水、余额、库存和 Redis 已购集合。预检元数据记录两组阶梯；通过记录绑定同样的阶梯及 200 VU。批间释放 Redis 热数据、保留 DB 历史，以提前覆盖数据累积问题；不等待正式测量窗口。实际增长量会受跳过档位影响，此处按完整初始阶梯保守准备。
3. 在已有大规模数据上，对 A/B/C 分别先执行 10 秒低速预热，再用独立用户和活动执行 10 秒低速验证。速率为 `min(5, 最低初始 RPS)`，默认 5 RPS；即使选择 1 RPS，每阶段仍包含混合流量中的有效购买请求。两阶段复用原 k6、证据关联和排空核对；发压无效、业务错误或未完成受理仍会阻止正式运行，但不以正式 SLO 或最高档 RPS 作为预检通过条件。日志及 HTML 中的预检摘要保存阶段、路径、速率、计划/实际请求数和错误，失败点同样保留。
4. 预检通过后先归档日志与提交证据，再销毁专属容器和数据卷，复用镜像重建。必须确认新实例、镜像与配额一致、六张业务表为空、Redis 为空、提交日志为空。随后签发绑定本次 run/attempt/job、代码、参数和新实例的一次性通过记录，正式执行入口缺少或不匹配时拒绝测量。

造数与短时请求设 12 分钟内部预算，包含保护排空与环境重建的 workflow 步骤上限为 20 分钟，时间仍计入既有全程截止。预算不保证大规模数据一定能准备完成；超时或内存检查失败仍阻断正式测量。失败报告标记 validation，并展示数据规模、内存余量、失败步骤、容器终态和环境重建结果。原始证据位于 `preflight/<run>-<attempt>-preflight/`；通过记录只在 runner 临时目录内。

预检覆盖初始阶梯的数据量和低速运行能力，容量由两组各自的正式阶梯、扩展和细化逐步探索；正式发压有效性、SLO、轮次、窗口和 CPU/内存配额不变。预检不能覆盖全部细化/扩展档位、未知突发规模或所有长时间累积效应。重建清空业务数据和进程状态，预检预热状态不会带入正式实验；宿主缓存与内核仍由同一个 runner 共享。预检结果不替代正式实验，也不能作为突发基线。

## 资源条件

固定使用 `ubuntu-24.04`，要求至少四个可用 vCPU。两个 vCPU 分配给被测服务、一个给发压端，其余用于宿主；两端集合互斥，优先将系统报告的 SMT 兄弟留在同组，记录实际分配。vCPU 不代表物理核独占。

| 服务 | CPU 硬上限 | 内存硬上限 |
|---|---:|---:|
| backend | 0.8 | 512 MiB |
| MySQL | 0.5 | 384 MiB |
| Redis | 0.1 | 96 MiB |
| Kafka | 0.6 | 448 MiB |
| 发压端 | 1 | 512 MiB |

被测容器内存合计 1440 MiB，608 MiB 为原实验的系统预算说明，不是对宿主内存的硬预留。不能将容器配额解释为独立 2 核 / 2 GiB 服务器。环境不足时失败，不降低预算继续。

MySQL 固定为 8.0.46，在 384 MiB 配额内关闭未使用的 Performance Schema，并限制线程缓存和内部临时表内存；InnoDB 缓冲池仍为 128 MiB。每阶段开始前读取并核验实际数据库变量，保存到 `mysql-settings.json`，HTML 中可展开“MySQL 实际配置与版本”。配置不匹配时停止；配置变化会改变基线指纹。这是被测环境调整，结果限定于新配置，不能与旧配置的测量点混合得出收益结论。详见 [PLAN.md](PLAN.md)。

构建和测量不重叠。启动及发压前要求可用内存至少 3 GiB；监控到低于 1 GiB、证据盘不足 1 GiB、容器 OOM/重启、时钟异常或观测缺失时中止并保存已有证据。低于 512 MiB 时优先停止专属容器。

job 上限 180 分钟；正式测量内部最多 135 分钟、每组容量搜索默认 50 分钟，包含构建准备的全程截止仍为 160 分钟。较早的截止优先，执行器提前停止启动新档位，为排空和归档留出余量；硬截止仍由看门狗执行。完整实验耗时取决于阶梯、样本窗口、细化和机器状态，不能保证等于先前环境的一小时。

## 查看 HTML 与原始证据

每次运行结束后，在该 Actions 运行页面下载：

- `performance-html`：HTML 报告与精简索引，保留 30 天；解压后直接打开 `index.html`，无需联网加载图表库。
- `performance-evidence`：结构化报告、逐笔请求/提交/快照、SQLite、CSV、JVM/DB/Kafka 和宿主采样、服务日志，保留 14 天。

artifact 下载可能需要登录 GitHub。HTML 显示实验范围、commit、环境、输入达标情况、分类 p99、容量区间、按时完成订单、积压曲线和无效原因。容量实验中的有效 SLO 失败点用于构成上界，不能与发压无效或样本不足混为一谈。执行中断也会尽量生成部分报告；平台强制终止可能影响收尾。

凭证和用户 token 仅位于 runner 临时目录，未挂入公开站点。Pages 仅发布渲染后的 HTML 和精简索引；原始数据留在 evidence artifact，不写回源码分支。每个测量点核对后释放 Redis 热数据，DB 数据在本 job 中累积；归档后销毁本次容器和数据卷。

数据准备阶段按本批已验证的用户 ID 直接写入订单和积分流水，造数事务每批最多覆盖 100 个用户，单批内检查影响行数并原子更新订单、流水、余额和库存。这不改变 90% 重复请求的测量流量比例，也不改变 RPS、时长、轮次或服务配额。

每点的 `preparation.json` 记录准备步骤、用户索引范围和累计耗时；失败日志显示具体步骤及该批事务耗时。每阶段的 `containers-final.json` 保存退出后、清理前的容器状态（包含 MySQL 的退出码、OOM 标志和重启次数），仅导出必要字段，并在 Actions 日志打印状态摘要。HTML 同时展示未完成的数据准备和结束时容器状态。断连后不自动重放造数事务，提交结果不明时保留证据并停止。

## 一次性配置 Pages 和仓库 Website

1. 仓库 Settings → Actions → General：允许本工作流使用的 GitHub 官方 actions。权限按 job 显式声明，不要求把所有 workflow 的默认权限改成全仓库读写。
2. Settings → Pages → Build and deployment → Source 选择 **GitHub Actions**。
3. 仓库 Code 页右侧 About 的设置中，Website 填写 `https://aster-sunfell.github.io/shanpu/`。
4. 在 Actions → Performance experiments → Run workflow 中选择 main，手动启动正式实验。确认 `publish` job 成功并打开 Website。

发布 job 使用 `contents: write` 保存纯静态报告的 `report-history` 分支，使用 `pages: write` 和 `id-token: write` 部署 Pages；压测 job 仅有 `contents: read`。无需提供个人访问令牌。首次配置仍需仓库管理员在 GitHub 完成。

网站展示运行历史，保留最近 30 次，以及最近一次完整正式实验。验证或失败报告不会覆盖该完整报告入口。workflow 的发布过程串行执行，合并完整历史站点后再部署，避免覆盖旧报告。

## 工具验证

离线单元测试和调度验证可以在开发环境执行；服务部署和发压入口会拒绝非 GitHub 托管 runner：

```bash
python3 -m unittest discover -s scripts/experiments/tests -v
node scripts/experiments/tests/schedule.test.mjs
```

报告 schema v3 绑定 Actions run、attempt、job、环境、镜像、资源、源码指纹和运行参数。旧协议报告需要重测，不能作为本次突发基线。
