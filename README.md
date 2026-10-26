# vps-kit

面向 Linux VPS 的工具集，当前提供 **3X-UI 流量重置调度与迁移管理**。它将月账期或固定 30 天周期、对象身份、配额策略和执行历史绑定在一起，帮助维护者核对配置、管理调度并追踪异常。

**当前版本：`0.4.0a3`，Alpha 开发版。** 已于 2026-10-26 发布 [v0.4.0a3 预发布](https://github.com/alieismy/vps-kit/releases/tag/v0.4.0a3)。当前阶段适合源码审阅、合成数据体验和本地开发验证。Debian 12/13、Ubuntu 24.04 上的真实 Linux/systemd、3X-UI API 写入及业务集成仍未验收，不能将本地测试结果视为生产可用承诺。

## 预发布下载

- [开发包：vps-kit-0.4.0a3-b93669c.zip](https://github.com/alieismy/vps-kit/releases/download/v0.4.0a3/vps-kit-0.4.0a3-b93669c.zip)
- [SHA256 摘要：vps-kit-0.4.0a3-b93669c.zip.sha256](https://github.com/alieismy/vps-kit/releases/download/v0.4.0a3/vps-kit-0.4.0a3-b93669c.zip.sha256)

上述附件固定对应提交 `b93669c50bd1e172761c69149c97727031705195`。校验方法、已验证范围及未验收边界见 [发布说明](https://github.com/alieismy/vps-kit/releases/tag/v0.4.0a3)。GitHub 自动生成的 `Source code` 归档是标签源码快照，与上述经过 manifest 校验的开发包不同。

## 主要能力

- 按 IANA 时区计算月日历，或以 UTC 锚点按固定 `2592000` 秒（30 天）计算业务周期。
- 管理一个本机 VLESS Inbound（入站）及其 1—16 个 Client（客户端），核对身份、独立配额、共享入口配额和到额策略。
- 提供配置校验、只读发现、状态审计、候选计划、timer 生命周期、已知旧部署接管及文件回退接口。
- 在写请求前持久记录意图；同一授权周期每个步骤最多发起一次请求。不确定状态会阻断后续执行，交由维护者核查。
- 使用固定清单构建离线 ZIP 包，并核对成员、版本和 SHA256 摘要。

完整动作、参数入口及副作用见 [CLI 参考](doc/cli.md)。迁移适配只接受已审核来源；通用工具名称不代表能自动接管任意旧脚本。

## 快速体验

需要 Python 3.11+ 和 IANA 时区数据库。运行期 Python 代码仅使用标准库；Linux 通常由系统 `tzdata` 提供时区数据，Windows 开发环境也需要预先提供该数据。缺失或名称无效时返回 `TIMEZONE_DATA_OR_NAME`，工具不会自动安装依赖。

在仓库根目录执行：

```powershell
python -B bin/vps-kit --version
python -B bin/vps-kit traffic validate-config --config tests/traffic/fixtures/3.7.0/config.json
python -B bin/vps-kit traffic discover --config tests/traffic/fixtures/3.7.0/config.json --fixture tests/traffic/fixtures/3.7.0/panel.json
python -B bin/vps-kit traffic discover --config tests/traffic/fixtures/3.10.2/config.json --fixture tests/traffic/fixtures/3.10.2/panel.json
python -B bin/vps-kit traffic inspect-state --config tests/traffic/fixtures/3.10.2/config.json --state tests/traffic/fixtures/3.10.2/ledger.json
python -B bin/vps-kit traffic calendar --config tests/traffic/fixtures/3.7.0/config.json
python -B bin/vps-kit traffic units --config tests/traffic/fixtures/3.7.0/config.json
```

Linux 可将 `python` 换成 `python3`。这些命令只读取仓库内的合成 JSON，不读取真实凭据、不发送 HTTP、不操作 timer/service。`units` 只输出 unit 文本。

预期输出包括：版本 `0.4.0a3`、`CONFIG_VALID`、`READ_CONTRACT_PASS`（`evidence_mode=SYNTHETIC`、`http_calls=0`、每版 `read_calls=5`），以及 `STATE_CONTRACT_VALID`。合成账本中的 `COMPLETED_RECORDED` 只表示样例结构有效，不表示真实请求已经执行。

源码入口会在导入包前关闭字节码写入，无需 `pip install`；当前未注册 pip console script。详见 [开发与贡献](CONTRIBUTING.md)。

## 环境与验证范围

| 范围 | 当前状态 |
|---|---|
| Python | 3.11+；Windows 可用于离线开发与测试 |
| 真实管理目标 | Linux，PID 1 为 systemd，Python 3.11+、systemd 252+、curl、IANA tzdata；本机写管理需要 root |
| 目标 OS | Debian 13、Debian 12、Ubuntu 24.04 LTS；各环境真实集成未验收 |
| 面板版本 | 仅精确适配官方 3X-UI `3.7.0`、`3.10.2`；未知版本及定制后缀拒绝 |
| 源码与 L1 测试 | 已有双版本字段契约、合成响应、执行/恢复模型及故障注入覆盖；历史结果按对应版本保留 |
| L2/L3 | Linux 系统接口、双版本面板与隔离代理业务验收均为 `NOT_RUN` |
| 生产业务 | 自然账期、真实商家计量、真实用户流量与扩大部署未验收 |

本地测试运行方法及其边界见 [CONTRIBUTING.md](CONTRIBUTING.md)。历史 `0.4.0a2` 的 180 项 L1 测试记录保留在 [E1-2 复查记录](doc/vmrack-review-20261025.md)，不作为 `0.4.0a3` 的测试结果。版本变化见 [CHANGELOG.md](CHANGELOG.md)。

## 重要限制

- 一个任务只管理显式绑定的本机入口与完整 Client 集合。Remark（显示名称）不作为唯一身份，发现结果不会自动写回配置。
- 交互式入口/Client 选择和友好向导尚未提供；候选配置由维护者准备。
- 工具不补跑错过账期、不自动重试写请求，没有通用“立即清零”入口。`execute` 只供匹配本任务身份的 systemd 调用。
- 服务端没有幂等键，不能承诺严格 exactly-once（恰好一次）。响应丢失、晚提交及一致的整机快照回卷仍需外部证据和人工处置。
- 文件回退保留历史与计数，两侧调度保持暂停；恢复文件不表示自动化或业务已恢复。
- `3.7.0+traffic.2` 写适配延期。已有旧部署适配的标签、时间和摘要用于来源核对，不能当作通用预设照搬。

## 文档与反馈

从 [文档索引](doc/README.md)进入 CLI、配置/状态契约、安装迁移契约、历史研究及隔离验收计划。文档中的历史批准或验证状态只对相应版本及范围有效。

普通问题可通过项目实际提供的 Issues 渠道反馈；提交复现所需的最小脱敏信息。漏洞报告方式及私密渠道尚未配置时的处理见 [SECURITY.md](SECURITY.md)。不要公开 Token、完整生产配置、数据库或原始日志。

## 许可证与来源

本项目代码、文档及获授权的旧脚本测试材料采用 [MIT License](LICENSE)，版权署名为 `alieismy`。JSON 配置、API 响应和账本 fixture 是合成数据；旧月脚本 fixture 是获授权并经脱敏的兼容测试材料，只读取字节，不执行 Bash。详见 [fixture 说明](tests/traffic/fixtures/README.md)。

本项目以独立 API 客户端实现对接，不随包分发 3X-UI 软件或其上游实现代码。适配依据和固定来源见 [上游源码证据](doc/p1-source-evidence.json)。3X-UI 的 [v3.7.0 LICENSE](https://github.com/MHSanaei/3x-ui/blob/v3.7.0/LICENSE) 与 [v3.10.2 LICENSE](https://github.com/MHSanaei/3x-ui/blob/v3.10.2/LICENSE) 为 GPLv3；本项目的 MIT 声明不替代上游许可证。
