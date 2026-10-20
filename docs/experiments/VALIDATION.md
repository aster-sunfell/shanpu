# Actions 压测迁移验证

日期：2026-10-20。旧环境的验证记录不作为当前 runner 的性能证据；原始记录保留在原工作目录。

## 已完成的离线验证

- 52 个 Python 回归通过，覆盖证据关联、SLO、容量细化、基线来源、Actions 环境限制、CPU/内存配额、异常排空、截止时间与 HTML 归档。
- 4 个 Node 调度验证通过，覆盖突发边界、用户隔离、固定混合流量及计划到达时刻。
- actionlint 1.7.12 检查通过；两个 Compose 文件解析并通过原有资源预算验证，未在开发环境启动压测服务。
- HTML 验证覆盖中断状态、验证标记、文本转义、凭证脱敏、历史保留，以及最近完整正式报告不被失败运行替换。
- 实验执行入口、参数和说明已移除开发者本机及跨机器部署模式；正常业务开发服务保持独立。

## 真实 Actions 验证

首次迁移提交 `09a1c2f` 的 [Actions 运行 39435245137](https://github.com/aster-sunfell/shanpu/actions/runs/39435245137) 已通过 checks 和完整 smoke 流程，包含真实镜像构建、资源准入、服务启动、A/B 容量、B/C 容量与同 job 基线突发、逐笔核对、HTML 生成、证据上传和环境销毁。publish 按分支规则跳过。

该运行生成 `performance-html` 和 `performance-evidence` 两个 artifacts。smoke 使用 validation 标识，不作为正式容量或收益证据。报告复核后进一步限制 validation HTML 不输出容量区间或增幅；最终提交的云端验证结果另行补充。

Pages 需要管理员完成一次性设置。未发布到线上前，不将 Website 地址标记为可用；完整正式实验与 Pages 验收尚待执行。
