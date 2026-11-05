# 变更记录

本文件记录面向使用者的版本变化及发布状态。测试结果只对实际运行时对应的源码和环境有效。

## 0.5.0a6-support-r1 — 配套预发布，2026-11-05

独立配套修订，标签 [v0.5.0a6-support-r1](https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a6-support-r1)；程序版本仍为 `0.5.0a6`，命名程序 ZIP 与发布摘要保持原字节。

- 修复 Windows 配套打包继承 `0777/0666` 权限的问题：tar 显式固定 root:root、目录 `0700`、普通文件 `0600`，拒绝额外成员和已有输出目录。
- 纳入配套构建器、原始 helper/模板及归档回归；保留 helper 的原始换行与固定摘要，避免 Git 转换导致重建归档变化。
- CI 增加 Windows 归档检查和 Linux root 解包/权限拒绝路径检查。实际结果与下载验证见 Release；历史本地验证快照保持当时状态。
- 本次不重建 a6 程序、不修改运行守卫、不操作 VPS；已受管部署无需因附件修订重装或重新启用。

## 0.5.0a6 — 预发布，2026-11-05

发布类型为 Alpha 开发版，标签 `v0.5.0a6`。来源提交、验证和附件摘要见 [Release 与发布说明](https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a6)；下载 [开发包 ZIP](https://github.com/alieismy/vps-kit/releases/download/v0.5.0a6/vps-kit-0.5.0a6.zip) 和 [SHA256 摘要](https://github.com/alieismy/vps-kit/releases/download/v0.5.0a6/vps-kit-0.5.0a6.zip.sha256)。

- 发布 a4 的 3.11.0 本机来源 GUID 校验修复和 a5 的 systemd 空任务清单修复；远程关联、身份不符、查询错误、冲突任务仍停止。
- 相对 a5，运行代码仅更新版本标识；同步下载、独立安装、升级入口及验证边界，配置、配额、账期、POST 和历史格式不变。
- a4/a5 保留为未发布的本地候选，不覆盖其固定摘要包。已受管部署仍须正常整包升级，未安装者生成新的匹配版本计划。
- 明确测试替身的覆盖限制：计划回归不覆盖完整原生冷启动，模型安装不证明真实 systemd 安装；本次不操作 VPS、不启用调度、不执行真实面板写入。

## 0.5.0a5 — 本地修复候选，未发布，2026-11-05

- 修复新装系统没有匹配任务时 `list-unit-files` 返回 1 被误报 `HOST_COMMAND_FAILED` 的问题：无模式枚举成功后本地筛选原任务命名空间，保留已加载任务检查。
- 查询非零退出、超时、非法编码、超限或异常行仍失败；所有相关 unit 类型的冲突继续拒绝，不创建占位 unit、不重试、不放宽通用退出码判断。
- 全量已安装 unit 查询的 stdout 上限单独设为 256 KiB，其他命令保持原上限。补充原生清单检查、首次安装计划调用链的相关分支、任务冲突、查询失败及大清单末尾冲突回归。
- 保留 a4 的 GUID 修复和原包；配置、Token、人工确认、配额、网络及账期继续复用。尚未发布或进行 VPS 安装验收。

## 0.5.0a4 — 本地修复候选，未发布，2026-11-05

- 修复 3.11.0 `GetInbounds` 读取时补入本机 `originNodeGuid` 后被误报 `API_REMOTE_ORIGIN` 的适配缺陷。a3 的模型字段检查遗漏了服务层返回加工；原 a3 发布包保留不变。
- 非空来源必须与同一回环面板 `/server/status` 的 `panelGuid` 一致；远程 nodeId、不同 GUID、异常类型、无效身份及请求失败继续拒绝。每份快照最多增加一次 GET，无自动重试或新增 POST。
- 发现和执行器共用来源判断及读取预算，补充初始化、写前身份变化、旧版兼容和 HTTP 回环回归。配置 schema、配额、网络、账期及历史格式保持不变。
- 本地候选不代表已发布或现场验收。候选的本地验证记录另行交付；没有运行 GitHub CI，没有操作 VPS。

## 0.5.0a3 — 预发布，2026-11-04

发布类型为 Alpha 开发版，标签 `v0.5.0a3`。来源提交、CI 与附件摘要见 [Release 与发布说明](https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a3)；下载 [开发包 ZIP](https://github.com/alieismy/vps-kit/releases/download/v0.5.0a3/vps-kit-0.5.0a3.zip) 和 [SHA256 摘要](https://github.com/alieismy/vps-kit/releases/download/v0.5.0a3/vps-kit-0.5.0a3.zip.sha256)。

- 新增官方 3X-UI `3.11.0` 精确适配，保留 `3.7.0`、`3.10.2`；同步本机版本守卫、离线包白名单和包内示例。
- 按上游源码核对字段、逐对象重置顺序、权限及副作用，新增固定来源证据与三份合成 fixture；扩展版本相关回归。旧来源接管仍限已审核 3.7.0。
- 修复 a3 回退 a2 后新包 manifest 导致 `PACKAGE_PATH` 的兼容问题：新增 JSON 使用旧版已允许的 `doc/fixtures/3.11.0/` 路径，保留历史与旧包不变；增加基于真实 a2 发布包的独立回退回归。
- README 重整为用途、工具清单、运行条件、命令示例、配置/部署流程和验证边界，明确已发布 0.5.0a2 不包含新适配。
- 补充第三台 VMRack 名称 `L1.VPS.1C1G.Base`，确认对应 VMRack-18；重置锚点为 `2026-11-16 10:05:00 Asia/Singapore`，即 `2026-11-16T02:05:00Z`，沿用固定 30 天周期。

本次未升级实际面板、接管 VPS 或启用调度。真实 Linux/systemd、3.11.0 API 写入、数据库升级与业务验收仍未执行，见 [适配说明](doc/3x-ui-3.11.0-compatibility.md)。

## 0.5.0a2 — 预发布，2026-11-01

发布类型为 Alpha 开发版，标签 `v0.5.0a2`。来源提交、CI 与附件摘要见 [Release 与发布说明](https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a2)；下载 [开发包 ZIP](https://github.com/alieismy/vps-kit/releases/download/v0.5.0a2/vps-kit-0.5.0a2.zip) 和 [SHA256 摘要](https://github.com/alieismy/vps-kit/releases/download/v0.5.0a2/vps-kit-0.5.0a2.zip.sha256)。

- 在现有 CLI 文档中收录独立安装、版本升级、七地区候选 Target/SNI、当地 DNS 与固定 IP 复验、HTTP 检查、实际 REALITY 验收及结果记录，并更新文档索引。
- 明确八组候选是单一美国出口的历史测量，各地区路径和实际 REALITY 仍待验证；保留 CDN、托管机房、IPv6、跳转和未通过候选的证据边界。
- 补充受管 `traffic` 的计划、应用、检查、恢复和回退步骤，明确现场配置、计划摘要、保持暂停及失败停止条件。
- 安装示例同步到本版本，包摘要从对应 Release 正文复制；拒绝覆盖已有版本目录，摘要失败时在解压前停止。
- 除版本标识外，程序源码、依赖及业务逻辑与 `0.5.0a1` 相同。版本标识仍会改变整包程序摘要，已部署环境继续使用正常整包升级流程。

本版本不新增地区实测或生产验收结论，不修改历史标签或附件，也不部署 VPS。最终提交的回归、回环和附件验证结果以本版本发布说明为准。

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
