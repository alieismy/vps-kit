# 文档索引

当前公开整理版本为 `0.4.0a3`。从 [项目首页](../README.md)了解用途和快速体验，从 [CLI 参考](cli.md)核对动作及副作用，从 [变更记录](../CHANGELOG.md)了解当前整理范围。

历史文档保留其形成时的版本、批准范围和验证层级。文中的“当前”“本轮”通常指该记录的时间点，不能自动升级为本版本状态。较早计划不因源码已存在而变成真实运行验收记录。

## 使用与维护

| 入口 | 用途 |
|---|---|
| [CLI 参考](cli.md) | 所有已实现动作、参数入口及副作用 |
| [开发与贡献](../CONTRIBUTING.md) | 环境、离线回归、curl 回环与贡献边界 |
| [安全报告](../SECURITY.md) | 漏洞报告及最小材料要求 |
| [fixture 说明](../tests/traffic/fixtures/README.md) | 合成 JSON、脱敏旧脚本和固定摘要 |

## 实现契约

这些文档按阶段叠加描述接口，不表示对应目标系统已完成运行验证。

| 文档 | 范围 |
|---|---|
| [P1 配置、状态与双版本适配](p1-contract.md) | v1 字段、身份、预算、API 读取与账本校验 |
| [P2 执行与配置切换](p2-contract.md) | 日历、控制历史、执行状态机及 timer 生命周期 |
| [P3 安装、r2 接管与回退](p3-contract.md) | 包、部署计划、凭据、文件回退与旧来源 |
| [固定周期与 VMRack 接管](vmrack-contract.md) | 配置 v2、30 天周期、来源核对与首期再确认 |
| [固定上游源码证据](p1-source-evidence.json) | 两个精确 tag 的契约检查定位；不是现场兼容证明 |

## 设计与未执行的集成验收

| 文档 | 状态与效力 |
|---|---|
| [总体方案](vps-traffic-reset-tool-proposal.md) | 历史设计基线；后续实施及验证按相应记录理解 |
| [固定 30 天扩展方案](vmrack-fixed-30-day-extension-proposal.md) | E0.2-public.1 公开整理版；已移除个人部署关联及用量快照，原稿摘要见文首 |
| [L2/L3 隔离验收计划](l2-l3-isolated-acceptance-plan.md) | Debian 12/13、Ubuntu 24.04 × 双版本面板六组合；仍为 `NOT_RUN` |
| [验收计划首次复查](l2-l3-isolated-acceptance-review-20261024.md) / [后续复查](l2-l3-isolated-acceptance-review-20261025.md) | 计划文档的历史复查，不是集成运行结果 |

个人电脑盘点及私有原始证据不属于公共文档和新发行包。公开文档中保留的来源模板标签、调度常量或摘要用于技术兼容识别，不证明任一主机的当前安装状态。

## 历史开发与复查

| 阶段 | 开发、验证及复查 |
|---|---|
| P1 | [工作记录](p1-work-record.md)、[复查](p1-review-20261024.md) |
| P2 | [工作记录](p2-work-record.md)、[本地验证](p2-validation.md)、[复查](p2-review-20261024.md) |
| P3 | [工作记录](p3-work-record.md)、[本地验证](p3-validation.md)、[复查](p3-review-20261024.md) |
| 固定周期扩展 | [方案复查](vmrack-fixed-30-day-extension-review-20261024.md)、[工作记录](vmrack-work-record.md)、[首次本地验证](vmrack-validation.md)、[E1-2 复查](vmrack-review-20261025.md) |

这些记录中的测试数量、源码或包摘要只适用于其指定版本。公开整理后仍保留历史原件和旧包；后续改动使用新版本和新验证记录，不覆盖旧结果。E0.2 历史复查的结论与定位对应原稿，不是公开整理版的逐字节验收；本机证据路径仅说明历史留存位置，不是公开下载入口或运行依赖。
