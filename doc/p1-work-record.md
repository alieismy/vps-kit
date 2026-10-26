# P1 开发与验证记录

状态：**P1 已完成本地实现与复查修正，未部署**；当前版本 `0.1.0a2`。用户于 2026-10-24 在对话中批准 v0.1.1 方案并授权 P1，后续授权全面复查和范围内修正；未授权进入 P2。

控制输入：[v0.1.1 方案](vps-traffic-reset-tool-proposal.md)，批准时 SHA256：`7ff035c587b9d60fd3a686a5b6e03153eca4364880f432c5b11f772263a880ac`。原方案保留评审时的文档状态，本记录登记后续批准及实施进度。

## 范围与完成条件

- 配置、状态、3X-UI v3.7.0/v3.10.2 适配契约，以及最小 CLI 和只读发现。
- 使用合成数据完成正反例、调用预算、脱敏、只读副作用检查。
- 不接触 VPS，不运行清零 API，不部署，不调整任何现有 timer/service。
- P2 的执行器、持久化写入、日历调度和 unit 管理，以及 P3 的迁移、回退与六组合 Linux 集成验收不在本轮。

## 工作计划

1. 固定详细契约和合成 fixture；独立实现，不复制私有部署配置。
2. 实现严格配置/账本解析、版本适配、GET 专用传输和 CLI。
3. 本地测试、修正、核对源码与文档、检查敏感信息及旧部署文件完整性。

本地已确认 Python 3.13.12、Git 2.53.0.windows.2。仓库初始只有方案文档；按获批方案初始化本地 Git，不提交、不设置远程、不推送。

## 已交付内容

1. [详细契约](p1-contract.md)：原生配置/账本 schema、双版本字段、策略、CLI、传输和后续控制记录边界。
2. [源码入口](../bin/vps-kit) 与 `src/vps_kit/traffic/`：严格配置解析、纯账本/历史解析、双版本适配、GET 专用传输、固定本机只读发现。
3. [README](../README.md)：可直接运行的本地合成示例及当前动作清单。
4. [测试入口](../tests/run.py)、两版各三份合成 JSON、[fixture SHA256SUMS](../tests/traffic/fixtures/SHA256SUMS) 与 [上游源码依据](p1-source-evidence.json)。

所有实际配置/账本写入、锁、调度授权、完整注册表及历史接管仍由 P2/P3 实现。本轮只实现其必要的表示契约与只读解析，不宣称已实现清零防重执行器或可进行生产迁移。

## 初次交付验证结果（2026-10-24，0.1.0a1）

以下保留初次交付的实际证据；43 项通过不能证明不存在遗漏，后续复查发现的问题与当前结果见下一节及复查记录。

| 检查 | 命令或证据 | 结果 |
|---|---|---|
| 单元及边界测试 | `python -B -W error::ResourceWarning tests/run.py` | **43 tests，0 failures，0 errors，0 skipped** |
| 双版本 CLI | README 两条 fixture discover 命令 | 两版均 `READ_CONTRACT_PASS`；每版 5 次逻辑 GET，**0 HTTP** |
| 最小支持语法 | 所有产品 Python 以 `ast.parse(feature_version=(3,11))` 检查 | 通过；不是 Python 3.11 运行验收 |
| 字段/策略 | 两版缺键/显式 null/bool/字符串反例、双态真值表、多 Client 关联与配额、16 Client | 通过；16 Client 成功预算 33 次逻辑 GET |
| 状态证据 | pending、部分完成、无最终记录、hold/enable 理由、时间窗、历史修订及同 UTC 不同文本 | 通过；未决不得静默变成完成或重放 |
| 只读与脱敏 | 子进程通过正式入口运行，前后源码/fixture 文件哈希比较，检查 pyc；凭据只在模拟 stdin，未知命令不读文件 | 通过；不产生锁、账本或缓存 |
| 本机状态 | 在 Windows 执行 `traffic status` | 明确 `LINUX_SYSTEMD_REQUIRED`，没有冒充 Linux/systemd 成功 |
| 文件结构 | 新文件逐个 `git diff --no-index --check`；文档链接、JSON、6 个 fixture 哈希 | 通过 |
| 既有基线 | 获批方案与既有 VMISS 主方案、r2 runner/build_config/manage 五文件 SHA256 | 全部保持原值；未修改这些文件 |
| Git | main 初始分支，`git remote -v` 为空 | 无 commit、无远程、无 push |

初次交付测试使用标准库，不安装依赖。测试进程通过 audit hook 拒绝真实 socket 连接；实际 curl 入口通过替身核对。真实子进程测试仅运行本机 Python，用于输出限额、超时和 CLI 无 pyc 验证。生产 Token、数据库及实际 VPS 配置均未用于测试。

## 全面复查修正（2026-10-24，0.1.0a2）

完整发现、源码依据、反例和剩余边界见 [P1 复查记录](p1-review-20261024.md)。本次修正八类问题：本地 nodeId 缺键兼容和远程来源检查、traffic.enable 的 null 回填、URL/回环目标限制、逐目标历史查重、取消后子进程回收、JSON 表示约束、空依赖输出及 CLI 参数缩写。

- 52 项标准库测试通过，0 failures、0 errors、0 skipped；增加 9 项反例回归测试，未放宽既有配额、策略、周期或未决账本门槛。
- 独立回环测试通过：两版各 5 次合成读取，另有数字 IP 行为及重定向测试，总计 12 次本地 HTTP GET、0 次 VPS 请求；使用已有本机 curl 和临时回环服务，结束后关闭监听。
- 无网络测试入口与离线 CLI 子进程均设置副作用守卫；源码入口仍在导入产品包前禁止 pyc。已修复测试启动器的 Windows subprocess audit 参数差异，以及 `python -c` 下注册 audit hook 后缺少 `__main__.__file__` 的运行问题；这些是本机测试路径问题，不是 Linux 集成结果。
- 详细契约升为 P1-2，原生 config/v1、ledger/v1 标识不变；六份固定 fixture 及其哈希不变。新增缺键测试覆盖模型的 omitempty 行为，不通过修改原 fixture 规避失败。
- 获批方案及 VMISS 主方案、r2 runner/build_config/manage 保持原字节；未部署、未连接 VPS、未清零、未调整任何现有 timer/service。

## 检查中修正的问题

- 首轮导入发现对象摘要表达式括号错误，修复后全部测试可运行。
- Client 详情发现异常时提前结束，避免再读取其 traffic；现在首错立即停止。
- 将 UTC 边界转换为时间对象查重，避免 `Z` 与 `.000Z` 文本差异产生两个周期记录。
- systemctl 明确返回 `LoadState=not-found` 时允许发现尚未安装的 unit；其他查询失败仍报错。
- Windows 初次生成 fixture 为 CRLF，逐文件 whitespace 检查捕获；生成器、`.gitattributes` 和六份 fixture 已固定 LF，并重新核对哈希。原获批方案保留原字节，不做换行转换。
- 单独核对可选 traffic UUID 的类型，拒绝把 null/bool 当成“无 UUID”；上/下行合计超出 64 位范围也拒绝。

## 未验证与接续条件

- 没有连接任何 VPS；仅新增本机合成 HTTP/curl 验证，没有真实 TLS、IPv6或 3X-UI 实例测试，没有真实 Linux systemd/文件权限/锁/fsync/断电恢复测试。
- Python 3.11/3.12 仅有兼容语法与 API 使用检查；实际运行证据限于 Windows Python 3.13.12。
- Debian 13、Debian 12、Ubuntu 24.04 与两个面板版本的六组合仍需隔离环境验证；当前不自动安装 WSL、Docker 或创建 VM。
- 已读公开 tag 源码不证明现场安装来源、计量拓扑、admin scope、异步写结果或代理业务可用。
- 后续按 P2 实现日历、稳定所有权/配置切换、持久化意图与自然边界执行器、unit 生命周期；P3 再完成 r2 接管/回退、正式包装与集成验证。未经后续授权不推进部署。

本轮没有生产变更，因此没有需要执行的 VPS 回退操作；现有 r2 timer/service 保持用户原部署状态。方案历史正文保留原审批时的状态说明，本记录为后续批准与 P1 交付的状态依据。
