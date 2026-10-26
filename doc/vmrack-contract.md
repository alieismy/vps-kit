# 固定 30 天与 VMRack 接管契约 E1

公开整理说明（2026-10-26）：`0.4.0a3` 沿用本文件的配置、执行和迁移契约。下文 `0.4.0a2` 验证记录保持其历史范围；E0.2 链接现为公开整理版，已批准原稿另行保留。

对应 `0.4.0a2`，控制输入为用户已批准的 [E0.2](vmrack-fixed-30-day-extension-proposal.md)。本文件说明实现接口；当前验证见 [E1-2 复查记录](vmrack-review-20261025.md)，首次实现证据保留在 [0.4.0a1 本地验证记录](vmrack-validation.md)。所有 Linux 管理命令均为后续获授权隔离验收的接口示例，**本次不在 VPS 执行，不创建实验环境**。

## 1. 配置与日历

保留 `vps-kit.traffic.config/v1` 的月日历及摘要；新增 `vps-kit.traffic.config/v2`，顶层字段仍按 [P1](p1-contract.md) 严格校验，只有 schedule 增加判别类型。v1 禁止 `kind`，v2 必须有 `kind`。未知键、重复 JSON 键、错误类型、非官方面板构建均拒绝。

| kind | schedule 的完整字段集合 | 语义 |
|---|---|---|
| `monthly-calendar` | kind、day、short_month、time、timezone、first_boundary_utc | 原月日历，短月与 DST 拒绝规则不变 |
| `fixed-interval` | kind、anchor_utc、interval_seconds、timezone、first_boundary_utc | UTC 锚点＋2592000 秒，IANA 时区仅用于展示 |

固定周期示例（只展示 schedule，完整配置还须绑定真实主机及对象）：

```json
{
  "kind": "fixed-interval",
  "anchor_utc": "2026-11-09T01:50:00Z",
  "interval_seconds": 2592000,
  "timezone": "Asia/Singapore",
  "first_boundary_utc": "2026-12-09T01:50:00Z"
}
```

`anchor_utc`、`first_boundary_utc` 使用 `YYYY-MM-DDTHH:MM:SSZ` 整秒格式；首期必须为 `A+n×2592000`，n≥0。锚点前无前一业务边界，下一业务边界为锚点；历史分析可以反推负序号，执行授权不可以。只有固定 30 天获支持，不接受 30 秒等实验缩放。锚点不会随成功时间或重启移动。

本地时间转换是离线命令，无文件写入、无 API，也不授予重置资格：

```powershell
python -B bin/vps-kit traffic anchor --local-time '2026-11-16 10:05:00' --timezone Asia/Singapore
python -B bin/vps-kit traffic validate-config --config candidate.json
python -B bin/vps-kit traffic calendar --config candidate.json
python -B bin/vps-kit traffic units --config candidate.json
```

`candidate.json` 是调用者准备的候选文件占位名。`anchor` 拒绝 DST 重叠、不存在时间和缺少日期/秒的输入；`validate-config` 输出配置及对象摘要，Token 不在配置中。`calendar` 分别给出 `schedule_kind`、下一次唤醒、`next_24_wakeups_utc`、未来 24 个业务边界、远期首次之后另 24 个业务边界。

三套餐的预算和锚点以 [E0.2 §2](vmrack-fixed-30-day-extension-proposal.md#2-vmrack-来源参数与周期计算示例) 为权威：18 刀 1000/500/500 GiB、35 刀 600/300/300 GiB、49 刀 1000/500/500 GiB，零预留仅为候选。不得把原始旧脚本的同额配额静默换为此候选。实际商家字节单位、最新锚点、面板精确构建及全部对象仍需逐机核实。

## 2. 每日唤醒、授权与多 Client

固定模式的 unit 每日按锚点 UTC 时分秒唤醒：18 刀 `*-*-* 02:05:00 UTC`、35 刀 `*-*-* 01:50:00 UTC`、49 刀 `*-*-* 02:35:00 UTC`。VMISS Basic/Core 保留各自 Toronto 月日历，未增加每日唤醒。unit 不在执行过程中续排或自改。

原生校验分别核对连续 24 次日唤醒和每个未来业务边界（含远期 first 后另 24 个）；不能把 24 天当作 24 账期。`NextElapseUSecRealtime` 比较下一唤醒。校验跨过一次正常日唤醒时只允许一次本地重新采样，重新核对两份 unit 原字节、timer/service effective 属性、来源和 override/reload、两侧无 Job 及 timer enabled/active/waiting；再次跨日直接拒绝，不重试面板请求。首次日期采用包含端点的枚举，不先对最小日期作减法。

`audit` 输出分开：`schedule_kind`、`next_wakeup_utc`、`next_business_boundary_utc`、`authorized_boundary_utc`、`first_allowed_utc`、`last_completed_period` 及其 `last_completed_boundary_utc`、`eligibility`。资格分类为 `FUTURE/IN_WINDOW/BLOCKED`；owner 非活动、维护未完成、未决历史或漏期均报告阻断原因。`eligibility_scope=CONTROL_AND_TIME_ONLY` 明确它只是控制历史与时间判断，不证明 timer 已启用、面板可写或实际执行成功。当前授权可能已在过去，不能用严格未来预览自动替换。普通非账期日返回 `BEFORE_FIRST_BOUNDARY` 或 `NOT_DUE`，零面板 API、零控制日志追加，但仍核对主机、程序、退役来源并获取锁；systemd 的凭据加载、runtime 目录及 journal 不在“零业务写入”承诺内。

沿用 [P2 执行契约](p2-contract.md)：稳定 period_id 与配置 revision、真实边界、对象、调用身份绑定；每步先落盘 pending 再发一次 POST；响应丢失、晚提交、读回失败、损坏/未决历史均阻断，禁止自动重试或次日补跑。240 秒写窗口、290 秒进程预算、墙钟/单调钟偏差≤2秒、NTP 同步及 300 秒 service 超时保持。`Persistent=false` 不能替代防重。

一个入口 1—16 个 Client，共用账期，独立配额/策略：`hold` 保持禁用且不重置；`stop` 要求两处启用，可不限额；`quota-recovery` 只恢复本人有限额度已满的禁用对象。父池满不能代替 Client 已满；每期父入口只重置一次，需要时再单独启用一次。Client 配额之和可大于父池，不保证人人同时用完，也不将 Client 总额再计入商家预算。

原生配置修改仍走 revision+1 的 plan→prepare→apply。改变规则不能提前于旧授权；仅改变固定周期展示时区不改变 first 或当前授权。维护禁区围绕业务边界，不围绕每天唤醒；活动/排队进程及锁竞争仍直接拒绝。

## 3. VMRack 来源与证据输入

VMISS r2/V2.1.2 混合历史仍走 `--kind migrate` 与 [P3](p3-contract.md) 的独立固定摘要契约。VMRack 走 `--kind vmrack-migrate`，不把月 marker 伪装成新 confirmed。

当前来源类型 `vmrack-monthly/v1` 只识别本地已审核 35/49 刀模板族、官方 3.7.0；3.10.2 支持原生固定周期，但不继承该旧脚本的审核资格。18 刀未知旧脚本拒绝自动接管。35/49 只允许将源码中已审核的 loopback resolve 字面量替换为候选 HTTPS 主机/端口，随后逐字节匹配独立模板摘要；其他修改、CRLF、额外脚本、unit/drop-in 或环境覆盖均不猜测放行。来源规范化 SHA256：

| 来源 | SHA256 |
|---|---|
| 35 刀源码族 | `657b23d23dc371d660a4215c105602ffec4107b2521a2faeb3890195f39dd70e` |
| 49 刀源码族 | `82a6d6a82f8e063e7865b1623b9a04950b49b3b1f3b5eb60b25951d7efe27a8b` |
| 已审核 service 原字节 | `3acfeb13c335a4ca1b6a26138d41b53d473b18e33ceaf03dc3d9223cbbb8020e` |

这些是源码族识别条件，不是任何主机当前安装摘要。原脚本不会执行；配置只允许五个单引号字面量字段：`PANEL_BASE_URL/PANEL_RESOLVE/CLIENT_EMAIL/INBOUND_REMARK/EXPECTED_TOTAL_BYTES`。GET 另行核对候选完整对象、配额、策略、内置重置/到期条件。

私有 evidence JSON schema 为 `vps-kit.vmrack-evidence/v1`，全部键必须存在：

| 字段 | 输入要求 |
|---|---|
| `schema`, `profile` | 上述 schema；profile 为 `VMRack-35` 或 `VMRack-49` |
| `candidate_sha256`, `object_sha256` | `validate-config` 返回的对应摘要 |
| `source_images_sha256` | 来源文件及权限集合摘要，由只读 source-audit 返回；首次审计可填 64 个 `0`，最终 plan 必须为真实结果 |
| `provider_anchor_utc`, `provider_amount_gib` | 与候选锚点及配置中的商家总字节数一致；600 或 1000 整数，不能串用 profile。缺失商家预算或混用 GB/GiB 后字节数不一致均拒绝 |
| `mode`, `actor` | M1 或 M2；负责核对证据的人/角色，最多 100 字符，不填写秘密 |
| `evidence_files` | 1—16 个 `/root/` 下受保护证据文件路径→SHA256，证据按原字节备份；不得用空文件冒充确认 |
| `retire_cron` | 显式授权退役的专用 cron 文件数组，默认空；只接受 `/etc/cron.d/vmrack-reset` 或 `/etc/cron.d/vmrack-traffic-reset`，且文件仅有一个已识别旧 runner `--run` 命令和注释 |
| `inventory_reviewed`, `no_other_entries`, `official_build_confirmed`, `late_effects_closed` | 均须人工核实后为 true，不是工具自动得出的结论 |
| `processes_closed_at`, `server_closed_at` | 已证实进程/队列结束和服务端延迟副作用关闭的 UTC 时间；不能在未来 |
| `expected_cutover_utc` | 仅供 plan 资格预估；apply 用实际退役完成时间重新计算 |
| `quarantine_accepted` | M2 为 true，明确接受可能延长一周期以上；M1 为 false |
| `no_resets_after_actions` | M1 为 true，确认所列最后一组动作后没有其他清零/未决副作用；M2 为 null |
| `last_boundary_utc`, `actions` | M1 给实际所属边界及逐对象证据；M2 必须 null / [] |

M1 actions 每项严格为 `object/first_possible_at/last_possible_at/evidence_sha256`，对象为 `inbound` 或 `client:<record_id>`，包括当前全部 Client，且不能重复。摘要必须指向 evidence_files 中的证明文件；最早和最晚实际/可能副作用时间均须落在审核旧来源的同一 300 秒窗口、与商家 30 天网格相符。完整 marker、service 启动时间或 mtime 均不能独立作为这些证据。旧重试可能晚于窗口；新工具不会据“五分钟启动门槛”推断服务端写入已取消。

M2 需先证实所有旧入口和副作用关闭。实际 cutover 取退役完成、进程/队列结束、服务端副作用关闭三者最晚时间，first 至少再隔 2592000 秒并满足 900 秒维护距离。等待期间外部手动清零/DB 恢复会使资格失效，应暂停并重新评审；程序不自动刷新 cutover。

源审计和计划示例（均仅供后续隔离验收；各路径为候选文件占位）：

```bash
python3 -B bin/vps-kit traffic vmrack-source-audit --local \
  --config /root/candidate.json --vmrack-evidence /root/vmrack-evidence.json

python3 -B bin/vps-kit traffic deployment-plan --local --kind vmrack-migrate \
  --config /root/candidate.json --vmrack-evidence /root/vmrack-evidence.json \
  --package /root/vps-kit-0.4.0a2.zip --package-sha256 "$PACKAGE_SHA256" \
  --activation paused --attestations /root/attestations.json --save /root/migration-plan.json
```

`attestations.json` 仍是 `admin_scope/local_accounting/other_reset_jobs_absent` 三项真值确认。`PACKAGE_SHA256` 是已核验本地包摘要变量；不是 Token。源审计零 API、无写入，不读取 Token 内容用于认证；只对来源文件做本地汇总 hash。它不代替历史证据审查或 provider 核对。源审计之后应将真实摘要写回私有证据候选，再创建计划。工具不会用“未知来源”自动生成可迁移证明。

## 4. 应用、持续退役守卫与首期再确认

新锁→暂停旧 timer→确认 service/cgroup/Job 完全静止→取得旧锁并核对 inode→备份→mask 旧 units/退役 stub/指定 cron→再次检查→实际首期资格→安装新版和新 units→全量 GET→提交来源和授权。旧脚本无能力继续写后，其错误月日不再阻止维护。所有迁移默认暂停，未提供自动启用 VMRack 接管选项。

```bash
python3 -B bin/vps-kit traffic deployment-apply --local \
  --plan /root/migration-plan.json --expect-sha256 "$PLAN_SHA256" \
  --package /root/vps-kit-0.4.0a2.zip

python3 -B bin/vps-kit traffic deployment-audit --local
```

输出 `WAITING_CANDIDATE_RECONFIRMATION` 时没有执行授权；旧入口已退役，新 timer 未安装或暂停。输出新候选配置摘要、实际 cutover 和 first。核对后以**同一 operation_id**继续：

```bash
python3 -B bin/vps-kit traffic deployment-reconfirm --local \
  --operation "$OPERATION_ID" --expect-sha256 "$CANDIDATE_SHA256" \
  --package /root/vps-kit-0.4.0a2.zip
```

再确认只能改变原计划的 first，不能改变对象、配额、锚点或 Token；确认以追加记录保留，原计划不覆盖。过期、来源漂移、已有控制历史或 failed 阶段均拒绝。若候选再次过期，应审计并受控文件退出，不自动在同一次失败事务内多次顺延。

已提交来源类型决定旧锁和持续守卫，VMISS 与 VMRack 路径隔离。每次写管理、执行入口和每个 POST 前核对退役字节、unit 文件及实际 mask、无运行/排队、source 配置和状态文件集合及摘要、受保护备份、已登记 cron 以及本进程持有锁的 inode。普通日醒也执行本地守卫。旧目录删除不会解除双锁；RuntimeDirectoryPreserve=yes 保留目录，重启后安全重建目录与锁，不能 unlink 现存锁。

锁身份检查适用于原生任务、VMISS 和 VMRack；锁路径被替换或删除均返回 `LOCK_REPLACED`。执行守卫首尾、POST 的最后时钟采样后、账本/部署文件原子发布前及 systemctl 管理调用前均检查已持有的锁。pending 已落盘后发现异常仍保留未决记录，不能因没有发出 POST 而自行重试。

cron 清单扫描覆盖指定系统 cron 位置的已知重置文本及标准来源名；未知条目拒绝，但不声称能穷尽任意 root 脚本副本/面板插件。变更文件权限、root 恶意绕过或整机一致快照回卷不由本地锁/哈希链提供绝对保证。

## 5. 0.3.0a2 升级、回退与凭据

包能力为 `config/v1, control/v1, ledger/v2, deployment/v1, config/v2, fixed-30-day/v1, vmrack-import/v1, deployment/v2`。新版兼容读取旧 manifest 和原始记录，旧版拒绝新增能力/计划/事件。新部署计划使用 v2；旧 v1 计划只接受原 v1 配置。新增 `vmrack_import` 与 `deployment_v2` 事件类型独立于旧 `legacy_import`，不扩写旧严格对象。

升级由新包 CLI 的 `deployment-plan --kind upgrade` 和 `deployment-apply` 执行，须传原活动配置、保留 Token，暂停、备份和验证通过后追加 `format` 转换记录再更新 program 摘要。原始事件、旧配置、当前授权和 period 不重写。已 pending/损坏历史不能通过升级跳过。将已有月日历改为固定周期是随后独立 revision，不在程序升级中自动发生。

回退入口仍为：

```bash
python3 -B bin/vps-kit traffic deployment-rollback --local \
  --operation "$OPERATION_ID" --task "$TASK_ID"
```

只恢复最近符合资格操作登记的原/计划字节，拒绝未知文件修改、后续配置 revision、owner 退役、活动/排队进程及坏备份。未登记或不完整新文件不会被当作旧文件覆盖。保持全部控制历史、来源原始证据及计数；新旧 timer 均暂停。

回退在暂停前检查完整目标允许字节、来源文件集合/摘要及备份；暂停和静止核对后、恢复任一正式目标前再次核对。初始迁移的文件退出要求不可变来源原件（含 `/root/` 下原证明）仍与计划一致；运行期因 `ProtectHome=true` 使用受保护的证明备份，不依赖原 `/root/` 文件。请保留原件直至决定不再使用该迁移的文件退出路径，缺失时停止并人工评审，不自动从备份重建证明。迁移后的程序升级或凭据回退必须仍通过完整持续退役守卫。

部分迁移失败、旧 runner/units/登记 cron 尚未全部退役时，回退同时避开新业务边界和旧月日历的 900 秒维护区；全部退役并通过来源守卫后只检查新业务边界。日唤醒不扩成维护禁区。任何未知变化都不能被当成“尚未退役”而绕过文件一致性检查；暂停后出现漂移时保留暂停，不开始正式恢复。

- 只读 plan 未提交：无正式改变，可重新制订。
- 暂停/备份或部分退役/安装：从已提交 manifest 恢复登记文件，保留失败记录，不自动重试。
- 等待再确认：可拒绝新 first 并走同一操作的文件回退；不能删除记录另换 UUID 接管。
- 新格式已提交而该新版尚未产生执行账本：可退至不理解新格式的旧程序文件，但明确为 `incompatible_format_file_exit=true`、自动化暂停，保留格式阻断；不宣称旧程序可继续解释新日志。
- 新版已有 not_sent/pending/confirmed/completed 账本：旧程序不具备所有当前格式能力时，普通程序降级拒绝；兼容程序回退仍保留未决阻断且受最近操作/配置条件限制。
- 恢复 VMRack 月脚本或 VMISS r2 源文件属于 **FILES_RESTORED_AUTOMATION_PAUSED**。`legacy_resume=NOT_AUTHORIZED`；报告旧 runner 已恢复，手工执行可绕开新账本，不能声称退役 stub 仍有效。重新接管需保留记录另行评审。

不回滚流量或面板数据库。仅 DB 回卷且当前对象/计数未触发已有检查时可能检测不到；整机一致回卷也需外部证据。Token 不进入 argv/公开报告，首次接管保留旧凭据，后续独立 `--kind credential` 轮换；恢复已撤销 Token 的文件不等于恢复 API 授权。

## 6. 验收边界

当前只支持官方 3.7.0/3.10.2；`3.7.0+traffic.2` 拒绝，不能剥离后缀，也不能仅靠可伪造版本字符串证明真实二进制来源。VMRack 旧源审核还要求 `official_build_confirmed` 与独立证据；traffic.2 倍率、raw/billed/余数写契约继续延期。

本地测试使用实际临时文件读写、文件 fsync/replace；Linux 目录 fsync/flock、systemd/cgroup、面板 API 和时钟均明确使用替身。源码族 fixture 的域名为 `.invalid`，凭据为合成值。已有六组合 L2/L3 与自然固定账期验收尚未执行；此开发版不作为现有 VPS 迁移批准。
