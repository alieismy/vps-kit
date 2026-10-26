# P2 日历、控制记录、执行器与 timer 生命周期契约

公开整理说明（2026-10-26）：`0.4.0a3` 沿用本文业务契约，仅调整公开文档、许可、元数据和发行包内容；本文所引历史测试结果不自动成为新版本结果。

`0.4.0a2` 引入 `anchor`、固定周期的 `calendar/units` 分支和日唤醒/业务授权分离，见 [E1 契约](vmrack-contract.md)。原月日历及本文 P2 历史契约保留；新增来源与格式事件只由新版解释。

版本：P2-2，2026-10-24，对应 `0.2.0a2`。上游为已批准 [v0.1.1 方案](vps-traffic-reset-tool-proposal.md)及已接受的 P1 `0.1.0a2`。本文件固定 P2 实现接口；验证证据见 [P2 验证记录](p2-validation.md)，阶段状态见 [开发记录](p2-work-record.md)，本次修正见 [P2 复查记录](p2-review-20261024.md)。本轮完成范围是 L1 本地模拟，不授权部署或真实清零。

## 1. 与 P1/P3 的边界

[P1 配置与读取契约](p1-contract.md)继续控制配置 v1、双版本字段、Token、GET 和本机绑定。P1 的“尚无执行器”等描述保留为历史阶段事实；当前状态以本文为准。

P2 增加原生任务的 `plan → prepare → apply`，或 prepared 后 `cancel`；增加 `pause/resume/remove/check/audit/resolve`，以及仅供生成的 service 使用的 `execute`。修改计划采用 revision+1，不另设重复的 `edit` 实现。首版仍是一台主机一个本机 VLESS Inbound、1—16 个 Client，共用月账期；每个 Client 的配额和恢复策略独立。

本节原 P2 交付时未含安装/迁移。`0.3.0a2` 已增加 [P3 契约](p3-contract.md)所述安装、版本切换、r2 导入、双锁兼容、退役/mask、回退和凭据管理，验证仍限 L1。P2 接口拒绝未完成接管的旧 r2；已接管时允许读取受保护旧材料，但每次写操作及执行仍须通过 P3 部署/退役守卫。交互式选择向导未实现，不能删除旧标志绕过迁移门槛。

## 2. 日历与授权周期

`calendar.py` 是月边界计算的单一来源。支持 1—31、`last`、短月 `skip/clamp-last-day`、IANA 时区和首次允许 UTC 边界。DST（夏令时）不存在或重复的本地时刻直接拒绝，不选择某个 fold，也不在跳过小时后补跑。

计划展示普通 timer 的未来 24 次唤醒和独立的首次允许边界。首次边界很远时另检查其后 24 次；仍不能推断无限未来都没有 DST 风险。执行时会重新计算。以下有限表达式没有额外日轮询：

| 规则 | 日期表达式（后接 HH:MM:SS 与时区） |
|---|---|
| 1—28 或 29—31 skip | `*-*-DD` |
| last / 31 clamp | `*-*~01` |
| 29/30 clamp | `*-02~01` 与 `*-01,03..12-DD` 两条 |

`~01` 表示从月末倒数第一天，月列表/范围由 systemd 日历语法定义。[systemd 252 时间语法](https://www.freedesktop.org/software/systemd/man/252/systemd.time.html) 原生适配器调用 `systemd-analyze calendar --iterations=24 --base-time=…`，合并多表达式的 UTC 结果，与 Python 的未来 24 次比较；这两个选项的含义已按 [systemd-analyze 252](https://www.freedesktop.org/software/systemd/man/252/systemd-analyze.html) 核对。本轮只验证表达式与解析接口的模拟结果，没有在真实 systemd 上执行该命令。

稳定 `period_id` 从 1 单调推进，不使用月份字符串作为唯一周期身份。新日历的有效边界 `B_new >= B_old_next`，并晚于已占用的历史边界。原日历未变时必须保留原 first_boundary。已完成 22 日周期后改每月 1 日，下一次不能提前到紧接着的 1 日；必须选择不早于旧下一边界的 1 日，并明确显示延长的区间。

安装/修改/启用/移除/处置距相关旧、新调度边界至少 900 秒。暂停是停止未来调度的动作，允许单独请求；它仍须获得同一锁，不能强杀正在执行的 service。维护使用墙钟与单调时钟交叉检查，容差 2 秒，较慢 GET 结束后再次检查窗口。

早于首次允许边界或下一授权边界：零 API、零账本追加。已经完成的同一边界：零 API、零账本追加。超过边界 240 秒而没有记录：拒绝清零；操作者须暂停后显式 `resume --skip-to <下一未来月边界>`，记录跳过区间，不能补跑。`Persistent=false` 不补齐停机期间漏掉的 timer 事件。[systemd 252 timer](https://www.freedesktop.org/software/systemd/man/252/systemd.timer.html)

## 3. 稳定所有权与追加式控制记录

| 路径 | 当前实现 |
|---|---|
| `/var/lib/vps-kit/traffic/control/records/00000001.json` 等 | 0700 目录、0600 文件，追加式记录 |
| 同目录 `head.json` | 当前序号和末条记录原字节 SHA256；唯一可替换的提交指针 |
| `/run/vps-kit-traffic/execute.lock` | 执行与全部写管理共用的 root:root 0600 锁，不 unlink |
| `/etc/vps-kit/traffic/api-token` | 本机 GET 的独立 root:root 0600 凭据，P2 不创建/轮换 |
| `/run/credentials/vps-kit-traffic-<task>.service/api-token` | 执行期 LoadCredential 的 root:root 0400 副本 |
| `/etc/systemd/system/vps-kit-traffic-<task>.timer/.service` | 固定命名、root:root 0644，无 Token、URL 或 Client 标签 |

方案中的路径为拟议布局。P2 将逻辑上分离的配置修订、注册、切换、账本、维护和人工处置统一编码到**带类型的追加记录**中，避免再维护一个可能与状态不一致的 `/etc` 活动配置指针。候选输入仍是独立受保护文件，运行配置从已提交注册的 revision 读取。此细化不导入或改写 r2。

事件 schema 为 `vps-kit.traffic.event/v1`，字段固定为 `sequence/previous_sha256/kind/payload` 加 schema。记录种类及约束：

| kind | 主要约束 |
|---|---|
| `maintenance` | 操作 ID、任务、阶段、确认计划摘要、原 head、原系统状态、备份及摘要、实际观测状态、错误码 |
| `revision` | 完整已校验配置、父 revision、配置摘要；首次 revision=1，以后严格 +1，旧记录不覆盖 |
| `registry` | task/host/panel/inbound 稳定绑定、活动 revision、程序摘要、active/removed；不能换 ID 清历史 |
| `transition` | old/new revision、前一/下一 period、旧下一边界、新有效边界、create/change/resume-skip/resolve-future |
| `ledger` | 见第 4 节；每次推进追加快照，首次、pending、confirmed、complete 是不同提交 |
| `resolution` | 精确旧账本摘要、人工证据摘要、责任人、延迟副作用结束/业务对齐确认、最早未来边界 |

每条记录保存前一条原始字节摘要。临时文件先 flush/fsync，原子替换，再 fsync 父目录；记录提交后才同样更新 head。多个文件替换不是单一原子事务。临时残留、孤儿记录、缺记录、摘要或序号不符、读取期间 head 变化均停止。旧记录不以新配置重新解释。

重放不仅验证哈希链，还验证维护阶段图：新操作只能从 preparing 开始，operation_id 不复用；create/change 按 preparing→paused→prepared→installing→verifying→complete，或 prepared→cancelled；remove 按 preparing→prepared→complete；pause/resume/resolve 按 preparing→complete。进行中的任一阶段可转 failed，failed 不可自行转回正常。已有备份不可替换。新 revision、活动注册、transition 必须属于匹配的操作和阶段；活动注册只能递增到本次新 revision，不能将旧 revision 重新设为活动。

root 权限、同文件系统替换、POSIX flock 和目录 fsync 由原生实现检查。本轮 Windows 用实际临时文件和文件 fsync/os.replace，目录同步和锁显式建模。完整一致的外部快照回卷，或者 root 同时改写全部证据，不能由本机哈希链可靠识别；不承诺抵御这些情形。

主机重启后 `/run` 目录可能消失。显式写命令在本机/任务绑定通过后安全创建固定运行目录，再取得同一锁；已存在的锁文件不删除、不替换。只读 check/audit/status 不创建这个目录。实际 Linux 重启与 flock 的验证仍属于 L2。

`remove` 保留注册 tombstone、历史、备份、配置和凭据。它不授权以新 task_id 重新创建空白历史。prepared 新任务取消后保留已选择的任务身份，只允许同一 task_id 重新制订计划。

## 4. 持久化意图与执行器

P2 运行账本为 `vps-kit.traffic.ledger/v2`；增加 systemd `invocation_id`、程序 SHA256，以及每步 `before` 快照。纯 `inspect-state` 保留对 P1 v1 示例的解析，但原生控制日志只接受 v2，v1 不能冒充已获运行授权的账本。

执行顺序：共享锁 → 所有权/维护/历史检查 → 时间、程序和本机运行守卫 → 全部对象 GET → 初始 not_sent 账本 → 按 record_id 重置非 hold Client → 入口重置一次 → 必要时单独启用入口 → 全量 GET → 首次完成提交。

每个目标的步骤是：

1. 核对控制 head、程序摘要、时钟、运行环境，并 GET 全部 Client 和入口。
2. 未处理目标仍须通过本身的策略门槛；已处理目标按已确认阶段核对，不能重新分类后再恢复一次。
3. 保存 `pending + intent_at + before`，文件及目录同步完成。
4. 再核对上述守卫，最多发送一次该步骤的 POST。
5. 全量 GET，核对目标身份/额度/周期/阶段启用状态、其他对象计数没有倒退。目标清零证据要求原用量为 0，或读回用量小于本次发送前用量；不要求一直等于 0。
6. 追加 confirmed 及读回证据。每次提交只允许一个步骤变化；不能跳过 pending 直接写 confirmed。

最终读回仍可能发现失败，即使所有单步已 confirmed。没有完整 completed 记录就阻断，不继续旧步骤。如果 completed/head 已完整提交，但进程在提交返回前故障，下次只读识别已完成，禁止同账期重发；下一个独立授权周期可继续。

pending 表示“可能已发送”，也覆盖落盘后、发送前退出。HTTP 错误、success=false、超时、进程终止、读回异常和延迟队列提交均不得自动重试。阻断是执行器拒绝写入，**不是自动暂停 timer**。

新 POST 仅允许在 `[边界, 边界+240秒)`，相邻步骤和发送前都检查；单调总预算小于 290 秒，service 超时 300 秒。GET 每次检查时钟，发起数有上界。设 Client 总数 N、非 hold 数 M：

- 写请求上界：`M+2`，即 M 个 Client、一次入口、至多一次入口启用。
- GET 上界：`(1+2N) × (2+2×(M+2))`；初始、每步前后、最终各一次全量读取。
- 正常入口已启用时没有额外 enable，两个 Client 正常过程为 40 GET / 3 POST；一个 Client 为 18 GET / 2 POST；16 个 Client 为 1188 GET / 17 POST。
- 16 是配置上限，不是慢接口完成承诺。只读 check 显示本次 API 耗时；不能用它保证完整写流程耗时。

执行器只持有三类 POST 路径：`clients/resetTraffic/{encoded-email}`、`inbounds/{id}/resetTraffic`、`inbounds/setEnable/{id}`。最后一项只发送固定 `{"enable":true}`。读取适配器没有 send/POST 方法；管理流程只拿读取适配器。

3.7.0 与 3.10.2 的 reset/enable 顺序、副作用边界沿用 [源码证据索引](p1-source-evidence.json)。3.10.2 的 embedded resetWeekday 缺失仍拒绝。面板 API 可能设置 Xray 待重启标记；计划输出明确提示这一点。工具不调用 systemctl restart，仍不代表业务连接不会受到面板内部重启影响。

## 5. 多 Client 配额和恢复

商家预算与入口/Client 配额仍分开；字节为运行权威，`GB/GiB` 单位沿用 P1。Client 总额可以大于入口共享总池，不重复累加到商家预算。新增/删除/改额/改 Email 必须新 revision、完整集合核对和未来边界切换，不自动纳入未知 Client。

| 策略 | 初始条件 | 执行 |
|---|---|---|
| Client stop | 配置态、traffic 态均启用；配额可为 0 | 自然账期重置该 Client，读回两态均启用 |
| Client quota-recovery | 有限个人额度；任一态禁用时，本人用量必须达到本人配额 | 重置 API 自带恢复副作用必须读回确认；入口满额不能代替该条件 |
| Client hold | 两态均禁用 | 不发送该 Client 重置/启用，保留计数；检查其他步骤没有误改其状态 |
| Inbound stop | 启用 | 清入口，不额外启用 |
| Inbound quota-recovery | 有限入口额度；禁用时入口本身到额 | 先清入口，仍应保持原禁用态；最后单独 setEnable=true |

至少有一名非 hold Client。人为禁用但不希望恢复时应采用 hold；计数到额不能证明禁用原因一定来自配额。每个步骤都按已确认阶段复核，发现人工编辑、目标集合变化或额外清零即停止。

## 6. timer 创建、修改和维护

CLI 选项必须完整，所有系统写动作显式 `--local`。Windows 会在文件读取、Token 或系统命令之前返回 `P2_REQUIRES_LINUX_ROOT`。本节是接口契约，不是本轮 VPS 操作指令。

| 动作 | 主要参数 | 行为与通过标识 |
|---|---|---|
| `calendar / units` | `--config FILE` | 离线 JSON 输出；`CALENDAR_PREVIEW / UNITS_RENDERED_ONLY` |
| `plan` | `--local --config FILE --activation paused\|enabled --attestations FILE --save NEW_FILE` | 新建或 revision+1；保存受保护候选和语义摘要；`PLAN_SAVED` |
| `prepare` | `--local --plan FILE --expect-sha256 HASH` | 候选未漂移、旧 GET 通过后暂停、备份；`PREPARED` |
| `apply` | 同 prepare | prepared 后核对面板新配额，再提交修订与 unit；`INSTALLED_PAUSED` 或候选授权的 `RESUME_COMPLETE` |
| `cancel` | 同 prepare | 仅 prepared；旧配额/对象/状态契约已恢复且 unit/备份未变；`PREPARED_CHANGE_CANCELLED` |
| `pause / resume / remove` | `--local --task TASK_UUID` | 固定 timer；`PAUSE_COMPLETE / RESUME_COMPLETE / REMOVE_COMPLETE` |
| `resume` 的漏期处置 | 另加 `--skip-to UTC_Z` | 必须恰是下一未来月边界；不补跑、不清零 |
| `check` | `--local --task TASK_UUID` | 当前全量 GET、时间/NTP、程序/unit、历史稳定；`READONLY_CHECK_PASS` |
| `audit` | 同 check | 全历史记录与当时 revision 的结构审计，附当前系统状态；`CONTROL_CONTRACT_VALID` |
| `resolve` | `--local --task TASK_UUID --evidence FILE` | 追加人工未来处置，见第 7 节；`RESOLVE_COMPLETE` |

`attestations` 严格包含三个 true：`admin_scope`、`local_accounting`、`other_reset_jobs_absent`。它们是人工确认，GET 成功不能代替。plan 保存配置、原 head/程序/unit 摘要、差异、日历、activation 与操作 ID。确认用候选的语义 `sha256`，不是 `sha256sum` 文件原字节结果；修改候选必须重新生成并确认。

全新任务没有旧 timer，prepare 只建立维护记录和空任务备份。已有任务必须先 prepare 成功，才提示手工改面板配额；改额后 apply 全量 GET 验证。降低额度时，入口和非 hold Client 当前用量必须低于新配额并启用；个人不限额 stop 例外。不修改计数或面板设置。

原 timer 暂停时新计划不能隐式启用。apply 如按候选选择恢复，会在安装验证完成后走 resume 守卫，安装及候选授权的恢复全程持有同一次共享锁，不启动重置 service。全部耗时核对完成后、enable 命令之前，再检查 NTP/墙钟/单调钟和 900 秒窗口；启用后也复核窗口。暂停失败记录 waiting/other/UNKNOWN 等实际结果，不能假称暂停。unit 写到一半、reload 或恢复失败会留下 failed 维护记录，阻止后续运行；不自动回滚、不重试命令、不自动重启 timer。

输入及无写入预检与已经开始的维护事务分别处理。apply 在安装意图前的 GET/配额/时间/备份检查失败，不追加 failed，保留 prepared 原字节和 cancel 路径；人工对齐后可显式重新 apply，或恢复旧面板契约后 cancel。resume 在追加维护意图或 skip transition 前完成参数、全量 GET 和时间复核；错误参数或只读失败不污染历史。上述入口均不自动重试；一旦尝试持久化安装/维护意图或执行系统写入，错误仍保留阻断，不套用“预检无副作用”的结论。

cancel 只处理尚未安装的 prepared 阶段，不处理已开始安装或 failed 的半安装。已有任务必须由操作者先对齐原面板契约，cancel 只追加取消记录，保留暂停；恢复另行明确请求。半安装的检查与恢复需要具体证据，没有“忽略错误继续”开关。P3 新增的 `deployment-rollback` 仅恢复其自身部署 manifest 登记的文件，不是通用 P2 failed 维护修复入口。

生成 service 固定 oneshot、RemainAfterExit=no、Restart=no、TimeoutStartSec=300s、LoadCredential、RuntimeDirectoryPreserve=yes、受限写目录。timer 固定 Persistent=false、AccuracySec=1s、RandomizedDelaySec=0；只有 timer 的 WantedBy=timers.target。不设置会拉起 x-ui 的 Requires/Wants，也不生成其他触发器。[systemd 252 service](https://www.freedesktop.org/software/systemd/man/252/systemd.service.html)

原生适配器写入 unit 后、reload 前调用 `systemd-analyze verify --man=no`；还检查 unit 原文、属主/权限、FragmentPath、DropInPaths、NeedDaemonReload 和关键有效属性，拒绝额外 override。命令采用固定 argv；写操作只包含目标 timer 的 enable/disable --now、受控 unit 文件维护及 daemon-reload。即使管理界面暂时不可用，pause 的主机绑定检查不要求 x-ui 必须 active；任何 API 读取与执行仍要求面板 active。

## 7. 不确定历史的未来处置

`audit` 是只读证据入口；它显示周期、步骤、旧账本摘要及当前维护状态。P2 审计本身不读取 Token、不查询面板；在 P3 已安装环境中，前置部署守卫会本地校验凭据文件摘要，不输出原文或其摘要。它不把当前 service 属性当成任意旧周期证据；报告明确为 `systemd_journal_and_business_acceptance=NOT_PROVEN`。生产自然账期验收仍需关联日志、服务商及代理业务证据。

首次安装在 registry 或 transition 提交前失败时，audit 仍可按已保存的 task_id 输出结构化维护阶段和错误码。尚无注册配置时 owner_status=NOT_REGISTERED、系统状态为 NOT_QUERIED_NO_REGISTERED_CONFIG；没有 transition 时下一授权 period/边界为 null。不会据不完整资料调用 API 或猜测 timer 状态。CONTROL_CONTRACT_VALID 只表示记录结构有效，包括明确的失败/未完成状态，不代表可以执行。

系统适配层的 `service=inactive` 是“无运行进程”的规范化状态，包括 systemd 原始 inactive，以及 MainPID=0 的 failed；它不表示上次执行成功。账本是否未决独立报告，真实 Result/退出码与 invocation 日志仍须在 L2/L3 验收中核对。

`resolve --evidence FILE` 是已确认处置的写入口。evidence 必须含 `resolution_id/source_ledger_sha256/period_id/actor/evidence_sha256/late_commit_closed/billing_aligned/not_before_boundary_utc/decision`，最后三类约束为两个人工确认 true、未来对齐边界和 `decision=future-only`。它必须精确绑定目前唯一未决账本，timer 已暂停、service 未运行、共享锁可得，且该边界不早于下一未来月边界。

这不是真实副作用的自动诊断：责任人必须先证明服务端延迟提交已经关闭、业务账期可接受该未来边界，并保留证据原文；只把摘要写入记录不能凭空证明这些事实。处置不修改旧 pending/confirmed，不补发旧步骤，只追加新的未来授权，period_id +1。timer 保持暂停，后续 resume 独立复核。

evidence 的完整字段、类型、旧账本绑定、唯一 resolution_id、人工确认及月日历边界对齐均在创建维护记录之前校验。缺字段、错摘要或非日历边界返回受控错误，head 保持不变，可更正输入后显式再提交。未来处置记录落盘后发生故障则保留不完整事务，不能撤销该记录或重放旧请求。

控制日志损坏、孤儿/临时文件、failed 半安装或证据不完整不在这个解除范围内，不能删除 pending、回退 head 或改 first_boundary 绕过。没有任何自动重试、自动修复或无条件 force。

## 8. 退出和验收边界

CLI 成功返回 0；契约错误为 2，输入/环境异常为 3，取消为 130。错误仅输出受控 code，不输出 Token、URL、API 原始消息或 traceback。取消不能声称零副作用，应检查持久记录；杀掉本地 curl 不证明服务端取消了请求。

L1 的测试覆盖和可复现命令见 [验证记录](p2-validation.md)。原生 root 权限、flock、目录 fsync、systemd 属性与实际日历解析、LoadCredential、重启后锁、真实双版本面板、异步队列、Xray 与代理影响均未做真实集成验收。不得将模拟 PASS 转写为三种 OS 或六种 OS/面板组合已经生产可用。
