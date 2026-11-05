# vps-kit

`vps-kit` 是给 Linux VPS 维护者使用的命令行工具集，目前有两项工具：

1. **`traffic`：3X-UI 流量重置与调度管理。** 按指定账期重置本机面板中一个 VLESS 入站及其客户端计数，提供配置校验、执行记录、暂停恢复、安装升级和已知旧脚本迁移。
2. **`reality probe`：REALITY 候选 Target/SNI 探测。** 检查指定连接目标与服务器名称的组合是否满足 TLS 基线，帮助筛选需要进一步实测的候选。

仓库包含 Python 源码、合成测试、离线打包工具和操作文档。它不安装或升级 3X-UI/Xray，不创建代理节点，也不修改服务商的流量账单；流量重置操作的对象是 **3X-UI 面板计数**。

**当前版本：`0.5.0a6`，Alpha 预发布。** 修复首次安装时 systemd 空匹配被误判为主机命令失败的问题，并保留 a4 的 3X-UI `3.11.0` 本机来源 GUID 修复，保留 `3.7.0`、`3.10.2`；详见 [适配说明](doc/3x-ui-3.11.0-compatibility.md)。源码核对和本地测试不代表真实面板写入、Linux/systemd 或生产业务验收完成。

## 从哪里开始

| 你的目标 | 使用入口 | 会发生什么 |
|---|---|---|
| 先了解流量工具 | 下方“离线体验” | 读取仓库合成数据，无网络、无系统修改 |
| 检查候选 Target/SNI | 下方“使用 REALITY 探针” | 发起 DNS/TCP/TLS 探测，输出 JSON |
| 为自己的 VPS 准备流量重置 | 下方“配置与部署流程” | 准备真实配置，逐步核验和安装 |
| 核对第三台 VMRack 的时间 | 下方“VMRack 三台 VPS 需求” | 核对名称、时区、30 天周期及首期边界 |
| 开发、验证或构建离线包 | 下方“开发与打包” | 本地测试或生成新 ZIP，不发布、不部署 |
| 查完整参数 | `python -B bin/vps-kit traffic <动作> --help` | 只输出帮助；完整动作索引见 [CLI 参考](doc/cli.md) |

## 获取代码与运行条件

本版发布 a4/a5 的两项修复；a4/a5 保留为未发布本地候选，原包不覆盖。历史 `0.5.0a3` 包含本机非空 `originNodeGuid` 被拒绝的已知缺陷，不应用于继续该失败场景。

从 [v0.5.0a6 发布页](https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a6)下载带 manifest 的 [ZIP](https://github.com/alieismy/vps-kit/releases/download/v0.5.0a6/vps-kit-0.5.0a6.zip) 和 [SHA256](https://github.com/alieismy/vps-kit/releases/download/v0.5.0a6/vps-kit-0.5.0a6.zip.sha256)，核对摘要后解压运行。历史 `v0.5.0a2` 附件保持原样，不包含此次 `3.11.0` 适配。GitHub 的 `Source code` 归档与带 manifest 的开发包不同。

运行期 Python 代码仅使用标准库，无需 `pip install`；当前未注册 pip console script。以下命令均在源码根目录执行，Linux 将 `python` 换成 `python3`。

使用 VMISS/VMRack 首次安装手册配套附件时，下载独立的 [v0.5.0a6-support-r1 配套修订](https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a6-support-r1)。它修复外层 tar 权限，内含原字节 a6 程序 ZIP；程序版本未变，已受管部署不因此重装或重复启用。配套标签不作为程序升级版本。

| 用途 | 环境要求 |
|---|---|
| 离线配置、日历、合成测试 | Python 3.11+ 和可供 `zoneinfo` 使用的 IANA 时区数据；Windows 也可运行 |
| REALITY 探针 | 支持 TLS 1.3、ALPN、X25519 和证书信任库的 CPython/OpenSSL 3；官方 CPython 建议 3.11.4+，发行版以实际能力检查为准；无需 OpenSSL CLI |
| 本机流量管理 | Linux、root、PID 1 为 systemd、systemd 252+、Python 3.11+、curl、tzdata；目标 OS 为 Debian 12/13、Ubuntu 24.04 LTS |
| 面板 | 官方精确 `3.7.0`、`3.10.2`、`3.11.0`；未知版本、定制后缀及 `3.7.0+traffic.2` 拒绝 |

Linux 通常由系统 `tzdata` 提供时区数据；Windows 开发环境需预先准备相应数据。缺失或名称无效会返回 `TIMEZONE_DATA_OR_NAME`，工具不会自行安装依赖。探针能力要求见 [CLI](doc/cli.md#reality-候选探针)。

## 使用 traffic

### 离线体验

```powershell
python -B bin/vps-kit --version
python -B bin/vps-kit traffic --help
python -B bin/vps-kit traffic validate-config --config doc/fixtures/3.11.0/config.json
python -B bin/vps-kit traffic discover --config doc/fixtures/3.11.0/config.json --fixture doc/fixtures/3.11.0/panel.json
python -B bin/vps-kit traffic inspect-state --config doc/fixtures/3.11.0/config.json --state doc/fixtures/3.11.0/ledger.json
python -B bin/vps-kit traffic calendar --config doc/fixtures/3.11.0/config.json
python -B bin/vps-kit traffic units --config doc/fixtures/3.11.0/config.json
```

这些命令不读真实凭据、不发送 HTTP、不修改 timer/service；`units` 只输出 unit 文本。预期依次可见 `0.5.0a6`、帮助、`CONFIG_VALID`、`READ_CONTRACT_PASS`、`STATE_CONTRACT_VALID`、日历预览和 unit 文本。合成发现应标记 `evidence_mode=SYNTHETIC`、`http_calls=0`、`read_calls=5`。样例账本的“已完成”仅是测试数据。

### 支持什么

| 能力 | 具体行为 |
|---|---|
| 月日历 | 按 IANA 时区的月日和时刻计算，保留短月与夏令时检查 |
| 固定 30 天 | 以不可变 UTC 锚点每 `2592000` 秒计算账期；每天唤醒，只有合法账期才重置 |
| 对象与额度 | 每台主机一个受管任务、一个本机 VLESS Inbound、1—16 个 Client；核对 ID/UUID、完整客户端集合、独立额度和共享入口额度 |
| 恢复策略 | `stop` 要求启用状态符合契约；`quota-recovery` 只允许满足本人有限额度耗尽条件的恢复；`hold` 保持禁用且不重置 |
| 防重复 | 写前持久记录意图，同一授权周期每一步最多发一次请求；不确定结果阻断后续执行，不能自动重试 |
| 生命周期 | 配置计划、暂停/恢复、审计、整包升级、凭据轮换及文件回退 |
| 旧部署接管 | 只接收已审核的 VMISS r2、VMRack 35/49 旧来源；旧来源正向接管仍限官方 `3.7.0` |

### 配置与部署流程

完整 JSON 结构可从 [3.11.0 合成配置](doc/fixtures/3.11.0/config.json)阅读，字段契约见 [P1](doc/p1-contract.md)，固定周期见 [VMRack 契约](doc/vmrack-contract.md)。样例中的 `.invalid` 地址、UUID、主机摘要和额度都不能直接用于实际部署。

准备配置时，需要核对以下内容：

- `task_id`、`host_id_sha256`、`panel_binding_id`：任务、主机和面板的稳定绑定。
- `panel`：精确版本、本机面板 URL 与回环连接地址；Token 单独保存在受保护文件中。
- `inbound`、`clients`：真实入口和完整客户端身份、配额字节数、恢复策略；显示名称不能替代 ID/UUID。
- `schedule`：月日历使用 v1 或 v2；固定 30 天使用 `vps-kit.traffic.config/v2`，并明确锚点和首次允许边界。
- `provider`：商家单位、计量模型、预留和其他业务预算。工具校验声明的预算关系，不证明商家计量与面板相等。

建议按以下顺序操作；现场写操作应在完成隔离验收及恢复准备后进行：

1. 用 `validate-config`、`calendar`、`units` 离线核对配置、业务边界和 unit。
2. 在目标 Linux 上用 `status` 检查环境，再用 `discover --local --token-file …` 读取已绑定的对象；它不会选择或创建入口。
3. 初次安装或旧来源接管，使用 `deployment-plan → deployment-apply`；候选先选择 `--activation paused`。参数和证据文件见 [P3](doc/p3-contract.md) 与 [VMRack 接管契约](doc/vmrack-contract.md)。
4. 安装后通过 `deployment-audit`、`check`、`audit` 核对文件、面板条件与历史；满足条件后才显式 `resume`。日常暂停使用 `pause`。
5. 后续业务配置修改使用 revision+1 的 `plan → prepare → apply`；程序更新使用整包 `--kind upgrade`。**工具升级不等于面板升级**：面板版本变化还需配置修订和字段/历史复核，不能只改版本字符串放行。

`execute` 是受 systemd 调用身份约束的自然账期入口，不能手工立即清零。漏期不自动补跑；响应丢失、晚提交或历史损坏需人工审计。文件回退保留账本及计数，调度保持暂停，不表示自动化已经恢复。完整副作用和参数见 [CLI 动作表](doc/cli.md)。

### VMRack 三台 VPS 需求

固定周期需求包含三个条目，第三台名称已确认为 **`L1.VPS.1C1G.Base`**，对应原 `VMRack-18`。以下为已登记需求，未表示已安装或启用任务；前两台保留既有历史标签和锚点，现场状态仍需核实。

| 条目 | 名称 / 历史标签 | 重置锚点（Asia/Singapore） | UTC |
|---|---|---|---|
| 1 | VMRack-35 | 2026-11-09 09:50:00 | `2026-11-09T01:50:00Z` |
| 2 | VMRack-49 | 2026-11-20 10:35:00 | `2026-11-20T02:35:00Z` |
| 3 | **L1.VPS.1C1G.Base / VMRack-18** | **2026-11-16 10:05:00** | **`2026-11-16T02:05:00Z`** |

三台均沿用固定 `2592000` 秒周期。第三台后两次边界为新加坡时间 `2026-12-16 10:05:00`、`2027-01-15 10:05:00`，不是每月 16 日。可离线核对换算：

```powershell
python -B bin/vps-kit traffic anchor --local-time '2026-11-16 10:05:00' --timezone Asia/Singapore
```

输出应包含 `anchor_utc=2026-11-16T02:05:00Z`、`authorization=NOT_GRANTED`。第三台的 v2 `schedule` 示例、首期规则和配额效力见 [需求补充](doc/vmrack-contract.md#第三台-vps-需求补充2026-11-04)。历史 1000/500/500 GiB 仍是预算候选，名称确认不等于实际配额验证。此条目使用原生固定周期；未知旧脚本不能自动迁移。

## 使用 REALITY 探针

Target 是实际连接的 `域名:端口` 或 `IP:端口`；SNI 是 TLS 握手中的服务器名称。两者应成对测试，不能把某个域名的成功结果套到另一个 IP。

以下域名仅为输入格式示例，不表示已推荐或实测通过；执行时会发起网络连接，默认输出 JSON，重定向会保存到本地文件：

```powershell
python -B bin/vps-kit reality probe --help
python -B bin/vps-kit reality probe --target example.com:443 --sni example.com
python -B bin/vps-kit reality probe --target example.com:443 --sni example.com > probe-result.json
```

已有 RealiTLScanner v0.2.3 CSV 时，可以显式读取并复验；`candidates.csv` 是你提供的文件：

```powershell
python -B bin/vps-kit reality probe --from-scanner candidates.csv --port 443 > probe-result.json
```

探针逐地址检查 TLS 1.3、X25519、ALPN h2、证书链/有效期/SAN 名称匹配。逐项查看结果：`BASELINE_PASS` 为本次基线通过，`BASELINE_FAIL` 为明确不符合，`INCOMPLETE` 为证据不完整。顶层 `PROBE_COMPLETE` 只代表流程结束。退出码 `0/1/2` 分别对应全部基线通过、存在失败或未完成、输入/环境/运行错误；详情见 [结果边界](doc/cli.md#检查与结果边界)。

它不发送 HTTP、不修改面板或 Xray、不运行扫描器。TLS 通过后仍要在实际 VPS 上验证 HTTP 行为与 Xray/客户端连接；不证明长期稳定性或抗封锁效果。七地区候选和手工复验见 [安装与人工复验指南](doc/cli.md#vps-安装与七地区人工复验)，安装命令固定为 `0.5.0a6`。

## 开发与打包

```powershell
python -B tests/run.py
python -B tests/loopback_transport.py
python -B tests/reality_loopback.py
python -B bin/vps-kit traffic package-build --help
python -B bin/vps-kit traffic package-verify --help
```

完整离线回归禁止外网，面板写入和系统管理使用替身；两项回环测试只在本机临时监听。测试方法与 CI 边界见 [CONTRIBUTING](CONTRIBUTING.md)。打包工具按固定清单生成带 manifest 的 ZIP；不会下载依赖、发布或部署。已受管主机更新必须走整包事务，不覆盖现役目录。

如需生成离线包，在仓库根目录执行下列命令，输出文件必须尚不存在；将构建输出的 `sha256` 值代入校验命令。包内同时包含三版本合成示例，不能直接当作生产配置：

```powershell
python -B bin/vps-kit traffic package-build --source . --output vps-kit-0.5.0a6.zip
python -B bin/vps-kit traffic package-verify --package vps-kit-0.5.0a6.zip --expect-sha256 '<构建输出的sha256>'
```

| 目录 | 内容 |
|---|---|
| `bin/vps-kit` | 统一 CLI 入口 |
| `src/vps_kit/traffic/` | 配置、调度、面板适配、执行/历史、部署与迁移 |
| `src/vps_kit/reality/` | 候选输入、TLS 探测与命令输出 |
| `tests/` | 合成契约、故障注入和本机回环测试 |
| `doc/` | 使用文档、实现契约、固定上游证据和历史评审/验收计划 |

当前证据范围：三版本适配有源码核对和合成测试；Linux 权限/flock/fsync、systemd/cgroup、真实 3X-UI POST、数据库升级、自然账期和代理业务仍需目标环境验收。历史测试记录只对当时版本有效；新增 `3.11.0` 的边界见 [适配说明](doc/3x-ui-3.11.0-compatibility.md)。

## 文档、反馈与许可证

从 [文档索引](doc/README.md)查阅契约与历史记录，版本变化见 [CHANGELOG](CHANGELOG.md)。普通问题提供最小脱敏复现；漏洞报告按 [SECURITY](SECURITY.md)处理，不公开 Token、完整生产配置、数据库或原始日志。

本项目代码、文档及获授权的旧脚本测试材料采用 [MIT License](LICENSE)，署名 `alieismy`。JSON fixture 是自行构造的合成数据；旧脚本 fixture 是获授权的脱敏兼容测试材料，仅读取字节，不执行 Bash，详见 [fixture 说明](tests/traffic/fixtures/README.md)。

本项目独立实现 API 客户端，不随包分发 3X-UI。上游 [v3.7.0](https://github.com/MHSanaei/3x-ui/blob/v3.7.0/LICENSE)、[v3.10.2](https://github.com/MHSanaei/3x-ui/blob/v3.10.2/LICENSE)、[v3.11.0](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/LICENSE) 采用 GPLv3；本项目 MIT 声明不替代上游许可。固定依据见 [源码证据](doc/p1-source-evidence.json)。REALITY 探针不包含 SNIProbe 或 RealiTLScanner 实现；读取 [RealiTLScanner v0.2.3](https://github.com/XTLS/RealiTLScanner/tree/e5e114747aa8c6dfa4b394a0782f241bf72bdce6) 的 CSV 不表示分发该软件，其上游许可证为 MPL-2.0。
