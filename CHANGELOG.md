# 变更记录

本文件记录面向使用者的版本变化及发布状态。测试结果只对实际运行时对应的源码和环境有效。

## 0.5.0a1 — 预发布，2026-11-01

发布类型为 Alpha 开发版，标签 `v0.5.0a1`。来源提交、CI 与附件摘要见 [Release 与发布说明](https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a1)；下载 [开发包 ZIP](https://github.com/alieismy/vps-kit/releases/download/v0.5.0a1/vps-kit-0.5.0a1.zip) 和 [SHA256 摘要](https://github.com/alieismy/vps-kit/releases/download/v0.5.0a1/vps-kit-0.5.0a1.zip.sha256)。

- 新增独立的 `reality probe`，对明确 Target/SNI 或 RealiTLScanner v0.2.3 CSV 候选进行有限、串行的逐地址复验。
- 使用 Python 标准库检查 TLS 1.3、X25519、h2 及证书链、有效期和 SAN 名称；保留失败、环境问题与未完成项，不输出伪装或抗封锁评分。
- 增加离线输入/CLI 检查与合成回环 TLS 验证入口，接入既有测试和发行包流程。修复前快照已取得指定 Debian 13 环境的探针运行/合成回环及有限美国出口候选测量、外部 curl HTTP 检查证据；其他地区路径、实际 REALITY/Xray 和生产业务仍未验收。
- 将握手提前 EOF 或 `close_notify` 归为 `INCOMPLETE / TLS_CONNECTION_FAILED`，保留明确协议、证书和 ALPN 失败的既有分类；补充提前关闭回归、运行环境说明和采用前人工复验步骤。
- 修复后的完整离线回归、Windows / Debian 13 合成 TLS 回环与既有 curl 回环已通过，环境跳过项和具体证据范围见 [CLI 验证说明](doc/cli.md#采用前人工复验)。
- 修复包另从一个美国出口对 56 个命名域名作有限初筛，8 组固定 IPv4/SNI 各三轮基线复验通过，并独立检查 HTTP；不作为其他地区路径、整个域名或实际 REALITY 验收。
- 沿用现有 `traffic` 业务和安装迁移契约。新模块改变整包程序摘要，已部署环境需要正常整包升级，不能手动覆盖现役目录。

发行附件按固定清单从标签源码构建；历史预发布及其验证结果保持原版本范围。

## 0.4.0a3 — 预发布，2026-10-26

发布类型为 Alpha 开发版，标签 `v0.4.0a3` 对应提交 `b93669c50bd1e172761c69149c97727031705195`。查看 [Release 与发布说明](https://github.com/alieismy/vps-kit/releases/tag/v0.4.0a3)，下载 [开发包 ZIP](https://github.com/alieismy/vps-kit/releases/download/v0.4.0a3/vps-kit-0.4.0a3-b93669c.zip) 和 [SHA256 摘要](https://github.com/alieismy/vps-kit/releases/download/v0.4.0a3/vps-kit-0.4.0a3-b93669c.zip.sha256)。

- 将 README 调整为用途、合成数据体验、支持状态和文档入口；完整命令与副作用说明移至 CLI 参考。
- 增加 MIT 许可证，版权署名 `alieismy`，以及贡献、安全报告和文档索引。
- 明确 Git 与新发行包的公开内容边界，排除本机环境报告，保留历史原件和旧包。
- 完善 Python 项目元数据及新包的许可证收录，保持历史包的校验兼容性。
- 增加复用现有测试入口的最小 CI 和公开材料/打包回归检查。

该预发布聚焦公开准备，流量任务配置、API 白名单、调度、执行、防重复及迁移行为沿用既有契约。真实 Linux/systemd、3X-UI 写入、六组合 L2/L3 和生产业务仍未验收。该预发布的具体验证范围见发布说明，不沿用下述 a2 的通过结果。

## 0.4.0a2 — 历史开发包，2026-10-25

- 补齐锁身份校验、每日唤醒临界重采样与回退前来源核对，并处理最小锚点时间的边界。
- 保留月日历、固定 30 天周期、VMRack 迁移和首期再确认契约。
- 对应历史源码的 180 项 L1 测试通过；Linux/systemd、真实面板及业务未验收。

详细修正和证据见 [E1-2 复查记录](doc/vmrack-review-20261025.md)。更早版本的设计、验证和阶段状态按 [文档索引](doc/README.md)进入原记录；其内容不视为当前版本结果。
