# GitHub Actions 性能实验

压测仅在 GitHub 托管 Linux runner 执行，在同一台临时虚拟机中运行 backend、MySQL、Redis、Kafka 和 Python/k6。无需开发者本机运行服务，也无需在仓库配置数据库密码或 SSH 密钥 secrets。

实验设计和结论门槛见 [PLAN.md](PLAN.md)，迁移范围见 [GITHUB_ACTIONS_MIGRATION_PLAN.md](GITHUB_ACTIONS_MIGRATION_PLAN.md)，实现验证状态见 [VALIDATION.md](VALIDATION.md)。

## 触发实验

压测仅通过 `workflow_dispatch` 手动启动，平时向 main 或其他分支推送、提交 PR、创建标签和归档报告都不会自动发压。

需要压测时，打开 [Actions → Performance experiments](https://github.com/aster-sunfell/shanpu/actions/workflows/performance.yml)，点击 **Run workflow**，选择 **main**，再点击 **Run workflow**。每次点击启动一次完整正式实验，结束后自动发布 HTML。仅允许 main 执行；选择其他分支时所有 job 跳过。

验证最新修正时请新建一次 Run workflow，不要在旧失败运行中点击 Re-run jobs；旧运行的重试沿用旧提交及其工作流配置。

运行进度见 [Actions → Performance experiments](https://github.com/aster-sunfell/shanpu/actions/workflows/performance.yml)。构建和测量串行进行，同一 main 分支的运行使用并发组排队，不取消正在进行的实验。

| 固定配置 | 值 | 用途 |
|---|---|---|
| experiment | full | A/B 容量及 B/C 容量与突发 |
| rates | 25,50,100,200 | 初始输入 RPS 阶梯；全部通过时需要提高档位重测 |
| seconds | 60 | 正式容量点最低注入秒数，低速档按样本目标延长，单点最多 600 秒 |
| repeats | 3 | 正式测量至少三轮 |
| vus | 100 | 每个到达率场景预分配并固定 100 个 VU；不在测量中动态扩容 |
| refine_steps | 4 | 有效通过/失败区间的细化轮数，0 至 6 |
| publish | 自动 | 完成后发布已有 HTML，包括明确标记的中断或失败报告 |

修改实验配置时编辑 `.github/workflows/performance.yml` 并提交到 main，之后按需手动启动。仅修改或推送配置不会执行压测。正式样本、SLO、环境和基线判据继续由执行器核验，不允许为通过工作流而降低门槛。

预分配 100 个 VU 为启动阶段及慢响应保留并发余量，发压端仍限制为 1 vCPU / 512 MiB。若 k6 报告 `Insufficient VUs`、计划请求缺失或注入越界，该点仍判为发压无效并停止，不作为服务容量失败上界。

B/C 的突发只引用本 job 刚完成的容量报告。B 基线必须样本充分、有通过和有效失败上界、区间宽度不超过 10%（最低 1 RPS）。无法形成合格基线时停止，不手填 Q，不使用其他运行的旧基线。

迁移期间已通过短时 smoke 验证 A/B、B/C 容量和突发的执行、证据关联及业务核对。验证报告标记 validation，不输出性能提升结论；最终工作流在 main 上运行正式 full 实验。

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

job 上限 180 分钟；测量另有更短截止时间，包含构建准备的全程截止为 160 分钟，为排空和归档留出余量。完整实验耗时取决于阶梯、样本窗口、细化和机器状态，不能保证等于先前环境的一小时。

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
