# P1 配置、状态与双版本适配契约

当前扩展 `0.4.0a2` 的配置 v2 与固定周期见 [E1 契约](vmrack-contract.md)。本文件的 v1 配置与双版本字段规则保留，旧历史不补写 `kind` 或重算摘要。

版本：P1-2，2026-10-24；对应实现 `0.1.0a2`。控制输入：[v0.1.1 方案](vps-traffic-reset-tool-proposal.md)；实现入口：[bin/vps-kit](../bin/vps-kit)。本文件将已批准的 P1 具体化，不授权生产操作。复查变更及证据见 [P1 复查记录](p1-review-20261024.md)。

阶段说明：本文保留 P1 历史边界。当前 `0.2.0a2` 的日历、控制日志、执行账本 v2 和 timer 写接口见 [P2 契约](p2-contract.md)；配置 v1、双版读取与严格类型约束仍沿用本文。P1 纯解析不能代替 P2 执行授权。

## 1. 边界与职责

| 模块 | 输入 → 输出 | 本阶段保证 |
|---|---|---|
| `traffic/config.py` | UTF-8 JSON → 不可变规范化配置 | 严格字段、类型、身份、预算、首次边界；无文件写入 |
| `traffic/state.py` | 账本＋产生时配置 → 记录分类 | 绑定、顺序、证据结构和跳过理由；无执行、无修复 |
| `traffic/panel.py` | 版本及 API JSON → 已校验表示 | 双版本差异、对象集合、关联、周期、额度、双态启用 |
| `traffic/panel_http.py` | 固定 GET 枚举＋受保护凭据 → 有界字节串 | 仅本机，调用预算，无重试、代理、重定向或 TLS 降级 |
| `traffic/discovery.py` | 配置＋读取器 → 脱敏报告 | 首错停止，成功预算 `1+2N`，不写回配置 |
| `traffic/host.py` | 固定本机路径/命令 → 环境摘要 | 只读，已知 unit 清单不等于完整任务扫描 |
| `cli.py` | 参数 → JSON stdout 与退出码 | 最小动作集；错误不回显参数、URL、Token 或原始 API 响应 |

配置解析不依赖 VMISS/Basic/Core 分支。一个本机 VLESS Inbound、1—16 个 Client，共用月账期。`stop`、`quota-recovery`、`hold` 必须逐项显式写入配置；后续向导的新任务默认值为 `stop`。本阶段不提供静默默认填充。

配置和状态的机器可执行权威为解析器，版本化 fixture 是示例和回归输入。本文件给出语义、生命周期和责任边界；变更解析规则须同步契约与测试，不另建未经实现支持的 JSON Schema。

## 2. 配置 v1

所有对象禁止未知键；所有列出的键必需，只有 `provider` 可以为 null。所有整数字段拒绝 bool、字符串和浮点数；计数字节范围为 `[0, 2^63−1]`，Inbound 配额和记录 ID 必须大于 0。JSON 只接收 UTF-8；大小按 UTF-8 字节计，最多 2 MiB。拒绝重复键、NaN、Infinity、溢出为无穷大的指数、孤立 Unicode surrogate；根节点深度为 0，最大深度为 64。此共同解析边界也用于 API 和账本。

| 字段 | 类型与约束 |
|---|---|
| `schema_version` | 固定 `vps-kit.traffic.config/v1`，不接受 legacy 的数字 1/2 |
| `task_id` | 规范化小写 UUID；稳定任务身份，不能通过改名重建空历史 |
| `revision` | 正整数；更改配置产生新修订 |
| `host_id_sha256` | 64 位小写十六进制；SHA256 对 machine-id 去掉末尾换行后的 ASCII 字节计算 |
| `panel_binding_id` | 规范化小写 UUID；同一面板的稳定注册身份，不随 URL/Token 轮换重建 |
| `panel` | `version/url/connect_address/allow_loopback_http`，见下表 |
| `inbound` | `id/remark/listen/port/protocol/quota_bytes/policy` |
| `clients` | 按 `record_id` 排序的 1—16 项；`record_id/uuid/email/quota_bytes/policy` |
| `schedule` | `day/short_month/time/timezone/first_boundary_utc` |
| `provider` | null 或 `amount/unit/model/reserve_percent/other_reserved_bytes` |

### 2.1 面板和对象

| 项目 | 精确规则 |
|---|---|
| 版本 | `3.7.0` 或 `3.10.2`；不接受版本范围或自定义后缀 |
| URL | ASCII `https://host[:port][/base]`；无凭据（包括空 userinfo）、查询、片段、空白；端口 1—65535，拒绝尾部空端口；域名 label 合法，拒绝单整数、缩写、八/十六进制等非规范数字 IP 主机名 |
| base path | 空根路径或 `/` 分隔的 ASCII 字母/数字/下划线/连字符段；无尾 `/`、百分号、点段、通配符；P1 明确收窄以防路由歧义 |
| 连接地址 | 仅 `127.0.0.1`、`::1`；curl `--connect-to ::127.0.0.1:<port>` 或 `::[::1]:<port>` 通配原主机/端口，固定 TCP 目标且保留原 URL 的 TLS/SNI/Host；IP URL 必须与此地址一致 |
| HTTP | `allow_loopback_http=true` 且 URL 主机恰好是选定回环 IP 才允许；不是域名解析失败的回退 |
| Remark | 非空，最多 256 字符，精确比较；不把显示名称当唯一身份 |
| listen | 空串或 IP 字面量；必须与 API 原值一致，不能把空监听与某个 IP 偷换 |
| port/protocol | 1—65535 / 固定 `vless` |
| Client 身份 | 记录 ID、UUID、Email 三者分别唯一；API 的 `inboundIds` 恰好包含一个绑定入口 |
| Email | ASCII `[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}`；作为一个 URL 路径段编码，不能输入 `/` 或编码后的路径 |
| Client 配额 | 0 表示个人不限额；不能采用 `quota-recovery`；总和可超过入口总池 |
| 策略 | Inbound：`stop/quota-recovery`；Client 再允许 `hold`，至少一人不是 hold |

所有文本字段拒绝 ASCII 控制字符。输出不展示 URL、listen、machine-id、UUID、Token、原始客户端记录及错误响应；Client 用排序后的 index 表示。索引只供本次报告显示，持久身份仍为记录 ID/UUID。

`Config` 保存规范化 JSON 快照，每次 `data` 返回独立对象。配置摘要包括全部字段；对象摘要由主机、面板注册身份、入口 ID/listen/port/protocol、排序后的 Client 记录 ID/UUID 组成。任务 ID、Remark、Email、配额、修订号和日历不改变对象摘要。**P1 只检查身份格式，不创建注册表，也不能阻止用户伪造新的 panel_binding_id；稳定注册与同对象唯一所有者必须由 P2/P3 维护流程落实。**

摘要规范：先按 record_id 排序 Client，再以 UTF-8、JSON 键排序、`ensure_ascii=False`、分隔符 `(',', ':')`、无末尾换行序列化并计算 SHA256；对象摘要只包含上面列出的身份字段。普通文件原字节 SHA256 与配置语义摘要不能互换。

### 2.2 月日历与商家预算

`day` 为 1—31 或 `last`。1—28 和 last 要求 `short_month=not-applicable`；29—31 必须显式为 `skip` 或 `clamp-last-day`。时间严格为 `HH:MM:SS`；时区为 `UTC` 或带 `/` 的 IANA 名称，拒绝 `CST` 等缩写。

首次边界采用带 `Z` 的 UTC ISO 时间；换算后必须符合上述月日和本地时刻。DST 重叠时刻拒绝，跳跃中不存在的时刻不能通过 UTC 回算匹配。配置解析**不要求边界晚于当前时间**，因为历史配置必须仍可校验；面向未来的计划窗口、后续月份 DST、240 秒运行窗口和维护前 15 分钟门槛由 P2 日历/执行器实现。不能把 P1 单点对齐检查当成后续日历已生成。

`provider.amount` 使用正十进制字符串，允许最多 9 位小数，换算结果必须是精确整数字节；`GB=10^9`，`GiB=1024^3`。`reserve_percent` 为 0—90 的整数，`other_reserved_bytes` 为非负整数。`model=manual` 只保存用户预算说明；`proxy-duplex-2` 检查：

```text
2 × Inbound.quota_bytes <= 商家字节数 × (100 − reserve_percent) / 100 − other_reserved_bytes
```

这不是实际商家计量证明，不把 Client 额度之和再加进入口消耗。合成示例使用商家 500 GiB、入口 250 GiB、两个 Client 分别 150/100 GiB；不表示任何实际 VPS 的当前配置。

## 3. 双版本 API 读取

`ReadEndpoint` 仅提供三个 GET：

| 枚举 | 追加到 panel base URL 的路径 |
|---|---|
| INBOUNDS | `/panel/api/inbounds/list` |
| CLIENT | `/panel/api/clients/get/{encoded-email}` |
| TRAFFIC | `/panel/api/clients/traffic/{encoded-email}` |

每份响应要求 HTTP 200、JSON 对象、`success` 严格等于布尔 true、存在 `obj`。数组/对象结构错误、字段漂移、API 错误都停止；不显示服务端 msg。列表按配置中的数值 ID 找入口，再核对 Remark/listen/port/protocol；重复 ID、目标缺失均失败。两个 tag 的 `NodeID *int` 具有 `json:"nodeId,omitempty"`，因此只接受缺键或 null；非 null 值一律拒绝。`originNodeGuid` 只允许缺键或空字符串，非空远程来源及错误类型拒绝。完整内嵌 Client 集合精确匹配，不能自动纳入新成员。这项字段兼容依据两版模型的序列化定义，不放宽 3.10.2 的 resetWeekday 迁移证据要求。

API 中无关扩展字段允许存在并忽略；影响身份、额度、周期和启用状态的已知字段严格校验。settings 接受 JSON 对象或可严格解析的 JSON 文本。Client 详情必须先通过校验，才读取该 Client 的 traffic。

| 表示 | 3.7.0 与 3.10.2 共同行为 | 3.10.2 增量 |
|---|---|---|
| Inbound | total 与入口配额一致；trafficReset=never、expiryTime 为整数 0；up/down 非负整数 | 同左 |
| embedded | id=UUID、email/totalGB 正确；reset/expiryTime 为整数 0；缺 resetDay 或 trafficReset 允许；存在时必须 0/never | resetWeekday 必须整数 0；缺键报 `EMBEDDED_WEEKDAY_MIGRATION_UNVERIFIED` |
| Client detail | 记录 id/uuid/email/totalGB；单入口关联；reset/resetDay/expiryTime 为整数 0、trafficReset=never；enable 与 embedded 一致 | resetWeekday 必须整数 0 |
| traffic | email/total/up/down/enable；reset/resetDay/expiryTime 为整数 0；可用非空 uuid 须匹配；不要求 trafficReset | resetWeekday 必须整数 0 |

3.7.0 不强制拥有 resetWeekday，但意外出现该字段时只接受整数 0。`trafficResetDay=1`、`resetMax=0`、非零历史 resetCount 不等于开启了周期，fixture 保留这些反例。

enable 分成配置态和 traffic 态，均必须是显式 bool；traffic 的缺键/null 不得回填配置态。stop 要求两态都 true；quota-recovery 在有限个人额度已耗尽时允许任一态为 false；hold 要求两态都 false。Inbound 到额仅用于入口自己的恢复条件。发现输出 `quota-recovery-eligible` 只说明当前快照符合该条件，不执行恢复，也不承诺下一时刻仍然符合；计数达到限额不能证明禁用一定由配额触发。采用该策略代表允许在满足这些条件时恢复，如需保留人为禁用须使用 hold。

版本适配器还记录未来写路径的顺序差异：3.7.0 先尝试启用再重置；3.10.2 先重置再尝试启用。P1 **仅保留这一契约元数据，完全没有 POST 方法**。不能据此声称已验证清零、异步落库、Xray 重启或代理连通性。

固定来源及 blob SHA 见 [源码证据索引](p1-source-evidence.json)。主要依据：[3.7.0 Client controller](https://github.com/MHSanaei/3x-ui/blob/v3.7.0/internal/web/controller/client.go)、[3.10.2 Client controller](https://github.com/MHSanaei/3x-ui/blob/v3.10.2/internal/web/controller/client.go)、[3.10.2 Client/Inbound 模型](https://github.com/MHSanaei/3x-ui/blob/v3.10.2/internal/database/model/model.go)、[3.10.2 traffic 模型](https://github.com/MHSanaei/3x-ui/blob/v3.10.2/internal/xray/client_traffic.go)。源码检查与合成验证不升级为真实双版本运行验收。

## 4. 传输和本机发现

生产路径的 CLI 必须显式选择 `--local` 并提供 `--token-file`；不能与 `--fixture` 混用。本轮不在 VPS 执行此模式。执行顺序：读取受保护配置 → Linux/root、OS、PID 1、依赖版本、主机摘要、`x-ui -v` 与服务状态核对 → 读取 Token → 固定 GET。

受保护配置和 Token 文件必须 root:root 0600，拒绝符号链接、非普通文件以及不可信祖先目录。Token 为 48 位字母数字，可带一个末尾 LF；不接受命令行明文 Token。它只经受控 stdin 作为 Authorization 头传入 curl，不放进 argv 或输出。两个 tag 的长度依据已固定在源码索引。

curl 固定路径 `/usr/bin/curl`，首参数 `-q` 禁用个人 curlrc，关闭 URL globbing；禁止代理、重定向、自动重试、TLS 跳过验证及备用地址。连接 3 秒、请求 10 秒、外部进程等待 12 秒；响应体最多 2 MiB，stderr 最多 8 KiB 且不显示。超限/超时杀掉本地读取进程并失败，不重发；Ctrl+C 同样先结束并回收子进程、关闭管道，再返回中断结果。GET 成功不证明 admin scope，报告始终保留 `token_admin_scope=NOT_PROVEN`。

完整成功读取为 `1+2N` 次，两个 Client 为 5 次，16 个为 33 次；遇错立即停止。fixture 模式记录逻辑 read_calls，但 `http_calls=0`。返回值无 Token/URL/UUID 和原始 API 对象。各 GET 之间可能有正常计数增长或并发编辑，报告不是原子快照；它不能用于跳过未来写前复核。`script_writes=0` 指本工具不主动持久化配置、报告、锁或账本，不保证 OS 文件访问时间、系统审计或面板日志/缓存完全不变。

`--local` 只使用本机回环；已用替身校验 argv 和调用预算，并用独立的本地 HTTP 服务验证实际 curl 的双版本合成读取、目标固定及重定向拒绝。未做真实 TLS、IPv6、Linux systemd/权限或 3X-UI 实例集成。回环验证入口为 [tests/loopback_transport.py](../tests/loopback_transport.py)，不属于禁止联网的单元测试入口。

`status` 无 API、无凭据，读取 OS/Python/systemd/curl、NTP、x-ui、已知旧 VMISS unit，以及显式任务 ID 的新 unit。只使用 `systemctl show`、`--version`、`timedatectl show`。Windows 返回 `LINUX_SYSTEMD_REQUIRED`，不是模拟成功的 Linux 状态。

`target_platform_match=true` 只表示 OS/依赖版本符合目标范围，不等于生产兼容。unit 状态、历史阻断和首次允许清零属于不同维度；P1 的 `history_blocking/timer_readiness` 仍为 `NOT_ASSESSED`。不扫描任意 cron、不读取旧 Token、不导入旧账本；本地计量拓扑、官方安装来源及外部清零任务仍需要后续核查，单凭 nodeId=null 或版本字符串不能证明。

## 5. 原生账本 v1

`schema_version=vps-kit.traffic.ledger/v1`。P1 只解析用户显式提供的文件，不创建账本、不加锁、不修改状态、不连接面板核实账本内容。

| 字段 | 约束 |
|---|---|
| task_id/revision/config_sha256/object_sha256 | 必须与产生时配置精确匹配，拒绝用新版配置解释旧账本 |
| period_id | 正整数；由未来的稳定注册/切换记录授权，不能仅按新边界时间生成新周期 |
| boundary_utc | UTC Z 时间，符合绑定的月日/时区，且不早于首次允许边界 |
| created_at | 不早于 boundary_utc |
| initial | 入口与完整 Client 快照；字节计数、enabled、Client 的 traffic_enabled；须符合各自策略 |
| steps | 按 record_id 顺序的 `client:<id>`，然后 `inbound`、`enable`；精确数量与顺序 |
| completed_at/final | 要么均 null，要么全部步骤已确认/合法跳过后保存完整最终快照 |

每个 step 精确包含 `object/status/reason/intent_at/confirmed_at/after`。状态意义：

| status | 契约 |
|---|---|
| not_sent | reason、intent_at、confirmed_at、after 都为 null |
| pending | 意图已记录，请求可能发出；有 intent_at，无 confirmed_at/after |
| confirmed | 有先后合法的 intent_at/confirmed_at 和目标读回快照 |
| not_required | Client 仅因 hold；enable 仅因初始入口已启用；三项请求/读回字段均 null |

意图时间必须落在 `[boundary, boundary+240秒)`；先前请求未决或未发送之后，不能出现新的 pending/confirmed。hold 的初始/最终双态都必须禁用；Client confirmed 必须双态启用；入口 reset 后保持初始开关，enable confirmed 才要求入口开启。最终快照要求入口开启、所有非 hold Client 双态开启。

已保存全部单步 confirmed 但没有 completed_at/final 仍返回 `REVIEW_REQUIRED`；部分完成或 pending 同样如此。完整记录返回 `COMPLETED_RECORDED`；尚无任何意图返回 `NOT_EXECUTED_RECORDED`。**这都是记录分类，不是远端执行事实。** 解析器检查非负计数和结构，不从 after=0、下降幅度或完成字符串推断 API 实际执行；保留 `evidence_authenticity=NOT_PROVEN`。

`inspect_history()` 接受多份“原始账本＋各自产生时配置”，在同一主机/面板绑定内，分别按入口 ID、非 hold Client 的记录 ID 和 UUID 查重；任一目标重复 period_id 或相同 UTC 边界均拒绝。不能仅按整个 object_sha256 查重，否则 Client 集合变化会掩盖同一入口，入口变化也会掩盖仍相同的 Client。保留整体摘要用于单账本精确绑定；历史查重单独使用逐目标键。

历史检查聚合未决状态，不把跨月自动看成解除未决。返回 `continuity=NOT_PROVEN`：P1 不能证明历史齐全、登记没有被回卷、对象集合变更已授权，也没有实现人工处置文件；伪造新面板绑定不能靠本函数识别。所有结果均标明 `automatic_replay=FORBIDDEN`，不提供重试接口。历史解析目前是 Python 内部接口，CLI 的 inspect-state 只检查一份记录，不声称扫描完整历史。

### 5.1 P2/P3 必须承接的责任

未来执行器须先持久化 pending、文件与目录同步，再发送唯一允许的 POST；读回和确认失败保持未决，不能自动继续剩余步骤。registry 必须将稳定面板/入口绑定到唯一任务、历史修订和授权周期，配额/日期/标签变化不产生第二次清零机会。配置变化和对象集合变化须经过切换记录，不能只比较当前 object_sha256。原子替换、稳定双锁、history 连续性、时钟门槛、owner 检查是后续执行前置，不由 P1 的返回 PASS 代替。

r2 的 schema 2 配置、旧 schema 1/2 账本、原 first_cycle、独立 Token 和迁移 manifest 必须走专门导入器；P1 原生解析器明确拒绝它们。旧 runner 不认识新账本，新工具有写入意图后不能直接恢复旧自动化。P1 不实现导入器或回退器，不执行备份脚本，不替用户生成回填完成状态。

### 5.2 控制记录的设计接口（P2/P3 实现）

以下固定控制记录的最小字段和责任，不在 P1 读路径中创建这些文件。其解析、持久化、故障注入和运行约束尚未实现，不能以本轮账本测试替代。

| 记录及 schema 后缀 | 最小字段 | 约束与拥有者 |
|---|---|---|
| `registry/v1` | host_id_sha256、panel_binding_id、inbound_id、task_id、active_revision、revision_refs、ownership_status、transition_ref | 全机唯一受管任务；同一 native x-ui 安装的主机/入口键不可因 UUID 或名称变更重建。panel_binding_id 由登记产生，后续配置只引用它；maintenance 管理 |
| `revision/v1` | task_id、revision、config_sha256、parent_revision、effective_period_id | 不可变配置引用；按整数 revision 派生目录，不接收任意路径；旧配置原字节与摘要保留；config 管理 |
| `schedule-transition/v1` | task_id、from_revision、to_revision、previous_period_id、next_period_id、effective_boundary_utc、plan_sha256、status | 只有完成的切换记录才能授权新周期；日期变化本身不分配新的 period_id；calendar/maintenance 管理 |
| `maintenance/v1` | operation_id、task_id、kind、plan_sha256、before_fingerprints、backup_manifest_sha256、stage、observed_timer_state、observed_service_state | 显式阶段记录及期望前态；失败保留实际观察，不能假定恢复或自动重启；maintenance 管理 |
| `resolution/v1` | resolution_id、task_id、source_ledger_sha256、object_binding、evidence_refs、actor、not_before_boundary_utc、decision | 只追加人工处置与未来边界授权，不更改 pending、不授权重放源账本；audit/resolve 管理 |

完整 schema 标识统一为 `vps-kit.traffic.<上表后缀>`。标识使用规范 UUID，revision/period_id 为正整数，SHA256 为 64 位小写十六进制，UTC 时间使用 Z 格式。引用只接收受管记录 ID/摘要，不执行文件内容；所有文件须在已确认的受管目录内，未知 schema 或未知阶段一律阻断写路径。缺失记录和来源不连续与“没有执行过”是不同状态。

登记的所有权查找必须先按主机和本地安装槽位发现已有登记，再核对 panel_binding_id；禁止把用户输入的新 UUID 当成“这是另一套面板”的充分证据。Client 集合变更仍属于同一入口所有者，旧对象摘要要通过修订链保留。首次登记若发现 r2 或未知旧调度只能进入接管/冲突处理，不能直接生成干净历史。

P2 必须补齐各维护阶段的可执行枚举和原子切换顺序，P3 固定旧 r2 证据映射；本阶段不提前宣称这些控制记录的生产 schema 已稳定。P1 中已可执行和已有固定 fixture 的 schema 仅为 config/v1 与 ledger/v1。

## 6. CLI、退出码与验证映射

业务动作的成功输出为 JSON，`--help` 和 `--version` 使用常规文本。退出码 0 表示该只读动作完成，2 为契约/参数/环境守卫失败，3 为未预期输入/环境错误，130 为用户中断。契约失败返回错误码与已消费读取次数；中断返回 CANCELLED。不自动重试。禁止动作及参数缩写通过 argparse 统一拒绝，参数原文不回显；`--token-file` 不接受 `--token` 缩写。

| 上游需求/约束 | P1 验证入口 | 后续缺口 |
|---|---|---|
| R-01、配置/双版本约束 | `test_config.py`、固定两版 fixture | 六组合 Linux 实测 |
| 多 Client、stop/recovery/hold | `test_panel.py` 的双态真值表、集合/配额/关联反例、16 Client | API 写后恢复、异步变化 |
| 防重复、历史和修订 | `test_state.py` 与 `test_review_regressions.py` 的 pending/部分完成/最终缺失、双修订、逐目标重复周期 | fsync、锁、原子替换、注册连续性 |
| 只读、凭据、调用预算 | `test_io_cli.py` 的 argv/stdin、超时/大小、禁用动作、子进程副作用守卫与无 pyc；`test_review_regressions.py` 的 null、URL、JSON、中断；独立 curl 回环测试 | 真实 TLS/IPv6/systemd/文件权限 |
| 证据层级 | fixture 标记、状态/读取报告中的 NOT_PROVEN/NOT_ASSESSED | 真实业务验收 |

固定样例在 [tests/traffic/fixtures](../tests/traffic/fixtures)；其校验清单与源代码依据均提交为普通仓库材料。fixture 是根据 tag 源码独立构造的最小语义响应，不是抓取的生产响应；不得将本地测试计数当成生产调用或验收次数。验证入口不安装任何依赖，不拉取外部环境。
