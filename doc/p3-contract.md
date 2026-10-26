# P3 安装、r2 接管、回退与凭据契约

公开整理说明（2026-10-26）：`0.4.0a3` 沿用本文安装迁移契约。新构建收录 MIT 许可证及公开说明、排除本机环境报告；历史包的路径与 manifest 校验仍兼容，不要求历史包补入当时不存在的许可证。旧包原字节及历史验证结果保留。

`0.4.0a2` 引入的 `vmrack-source-audit`、`deployment-plan --kind vmrack-migrate`、`deployment-reconfirm`、部署 plan v2 和格式转换回退边界见 [E1 契约](vmrack-contract.md)。本文保留 `0.3.0a2` 历史基线；安装新版后仍兼容已有 VMISS 计划和历史，但不得直接降级运行新增格式。

版本 P3-2，2026-10-24，对应 `0.3.0a2`。上游为已批准 [v0.1.1 方案](vps-traffic-reset-tool-proposal.md)及已接受的 `0.2.0a2`。配置/面板规则沿用 [P1](p1-contract.md)，日历、执行与配置切换沿用 [P2](p2-contract.md)。本文件说明已实现接口，验证范围见 [P3 验证记录](p3-validation.md)。本轮没有部署授权；下列 Linux 管理命令是后续隔离验收的接口示例，不能将本地模拟通过视为可直接迁移现有 VPS。

## 1. 发行包与安装模型

运行需要 Python 3.11+、本机 IANA tzdata 以及原有系统依赖。没有安装依赖、联网下载或执行远端脚本的命令。包为 ZIP，含固定版本源码、README、契约、合成示例、测试源码及 `manifest.json`；测试不被运行入口导入，unit 由固定模板生成。附带测试和 fixture 使文档链接及本地复现入口在包内闭合。清单逐文件记录 SHA256、大小和权限，Python 文件须通过 Python 3.11 语法解析，源码/元数据版本必须相同。拒绝多余文件、重复成员、文件与目录前缀冲突、缺少固定 CLI 入口、路径穿越、符号链接、加密或超大包。不执行包内代码做验证。

本地离线构建及验证示例（输出文件必须尚不存在）：

```powershell
python -B bin/vps-kit traffic package-build --source . --output .tmp/local/vps-kit-0.3.0a2.zip
python -B bin/vps-kit traffic package-verify --package .tmp/local/vps-kit-0.3.0a2.zip --expect-sha256 <PACKAGE_BUILT输出的sha256>
```

`<…>` 均为必须替换的占位符，不是可直接粘贴的值。父目录须预先存在。摘要证明内容一致，不证明作者身份；公开发布还需仓库所有者确认来源、许可证及公开材料。此实现不复制或执行上游 3X-UI/r2 runner，旧格式由独立解析器读取。

固定路径：

| 路径 | 用途 |
|---|---|
| `/usr/local/lib/vps-kit/releases/<version>/` | root 独占写入的不可变发行目录；半写入目录保留，不覆盖重用 |
| `/usr/local/lib/vps-kit/current` | 只允许 `releases/<合法版本>` 的受控相对链接 |
| `/usr/local/bin/vps-kit` | 固定普通入口 0755、关闭 pyc；动态选择 current 的源码目录 |
| `/etc/vps-kit/traffic/api-token` | 活动 Token，root:root 0600 |
| `/var/lib/vps-kit/traffic/control/` | P2 追加日志：配置修订、稳定所有者、调度授权和执行账本；不是可回退的旧快照 |
| `/var/lib/vps-kit/traffic/deployment/` | 独立追加部署日志，计划、阶段、备份清单及版本身份 |
| `/var/lib/vps-kit/traffic/backups/<operation UUID>/` | 原文件证据副本，0700/0600；不在终端输出 Token 或其内容 |
| `/run/vps-kit-traffic/execute.lock` | 新执行/维护锁 |
| `/run/vmiss-traffic-reset/reset.lock` | r2 兼容锁，迁移及保留回退能力期间共同持有 |

配置的权威副本沿用 P2 控制日志中的不可变 revision，不额外维护一个可漂移的 `/etc` 活动 JSON。用户候选 JSON 只是输入，不会在执行时重新加载。发行目录切换只支持相同配置/控制/账本/部署契约的 `0.3.*` 包；不能把未知未来状态格式或 `0.2.*` 当作兼容回退目标。

## 2. 计划、应用与守卫

`deployment-plan` 的 `--kind` 为 `install / migrate / upgrade / credential`。计划绑定：task/host/panel/Inbound/全部 Client、配置摘要、控制与部署日志 head、程序包、源文件、系统状态、Token 摘要、首次/下一合法边界、启用选择以及人工确认。保存计划为一个新的 root:root 0600 文件，终端只输出摘要。Token 原文不在计划、日志或 argv 中。

共同规则：

1. Linux root、主机绑定、NTP 同步、本机面板、全部对象 GET 契约、无 service 进程/排队任务/drop-in/未知调度。GET 只能证明读取有效；admin、本地计量、没有其他清零源必须人工确认。
2. 计划只读现场并写指定候选文件，不建锁/账本，不暂停。apply 要求计划 SHA256，并在新锁→旧锁内重新检查日志 head、文件、系统和 API，再对比计划。变化或过期立即停止。
3. 维护离月边界至少 900 秒；未来授权边界必须尚未到来。原来暂停的 timer 不能借升级、轮换或迁移隐式启用。配置与配额改变必须独立走 P2 revision+1 流程。
4. 部署日志阶段 `prepared → writing → complete`；异常保存 `failed`，中断或孤立记录同样阻断后续执行，不自动重试、续装、重建备份或清零。备份确认后才写正式目标。
5. 安装/升级后做文件、unit、生效配置、GET 和时间窗口复核，提交程序身份；若计划选择 enabled，在同一锁内调用既有 timer 恢复流程。只启用 timer，不 start/restart 重置 service 或 x-ui。
6. 已完成的安装事实与启用失败分开记录。resume 失败由 P2 维护日志阻断；不能把安装文件成功写成 timer 已启用。
7. 原生 CLI 在写锁内再次核对部署、发行包及旧入口；运行时还核对当前装载源码、systemd InvocationID/MainPID。损坏/半部署不能通过手工调用 execute 绕过。

新安装要求入口和非 hold Client 启用、用量低于额度。迁移/程序升级/凭据轮换不改变对象状态，使用既有逐对象恢复契约：quota-recovery 只有对象自身有限配额确已耗尽才允许保留禁用等待未来账期，hold 继续禁用且不清零；不能因入口已满而恢复其他未到额 Client。

首次安装使用 `revision=1`、新的稳定 task/panel-binding UUID；已有所有者或旧 control 残留拒绝新建。第一允许边界保持候选声明，不因安装月份而改动。正常版本切换追加 `program` 事件，仅更新程序身份，保留所有旧修订和账本。

## 3. 命令参数与示例

先独立准备本机候选配置、受保护凭据和人工确认文件。配置字段见 P1，合成示例不是 VPS 配置，工具不会从 Remark 猜 UUID、record_id 或主机绑定。人工确认 JSON 必须三个值全为 true，且操作者确实完成确认：

```json
{"admin_scope":true,"local_accounting":true,"other_reset_jobs_absent":true}
```

后续隔离 Linux 验收示例；`python3 -B bin/vps-kit` 指向已验证源码目录，初装前稳定入口尚不存在：

```bash
python3 -B bin/vps-kit traffic credential-input --local --save /root/token-candidate.secret
python3 -B bin/vps-kit traffic deployment-plan --local --kind install \
  --config /root/candidate.json --credential-file /root/token-candidate.secret \
  --package /root/vps-kit-0.3.0a2.zip --package-sha256 <包SHA256> \
  --activation paused --attestations /root/attestations.json --save /root/install-plan.json
python3 -B bin/vps-kit traffic deployment-apply --local \
  --plan /root/install-plan.json --expect-sha256 <计划SHA256> \
  --package /root/vps-kit-0.3.0a2.zip --credential-file /root/token-candidate.secret
/usr/local/bin/vps-kit traffic deployment-audit --local
/usr/local/bin/vps-kit traffic check --local --task <任务UUID>
```

`credential-input` 必须 TTY 且能够关闭回显；终端无法隐藏输入时立即拒绝，禁止降级到可见输入。候选只能写在新旧管理目录及 systemd unit 目录之外（P2/P3 的 `--save` 同样适用），仅创建新候选文件，禁止覆盖已有文件；已有受保护文件也可直接作为 `--credential-file`。不将 Token 填进 `<包SHA256>` 或其他参数。包须 0600/0644、候选/计划/人工确认须 0600，父目录不能允许非 root 写入；敏感文件不放共享临时目录。

版本切换使用 `--kind upgrade`，仍提供当前活动配置 JSON、新包及包摘要，**不传** `--credential-file`。apply 同样提供新包。仅程序版本改变，旧目录保留；已存在的完整版本只有与输入包逐文件一致才复用，半写入、同名不同内容或多余文件拒绝覆盖。原本启用的任务可显式选择 `--activation enabled`，原本暂停只能选 paused。

凭据轮换使用 `--kind credential`，提供当前活动配置、新 `--credential-file`，**不传包或包摘要**。apply 只给计划确认与同一凭据文件。新凭据 GET 预检失败时不暂停、不替换；写入后的失败保留暂停及失败记录。此操作不创建或撤销面板 Token，也不能从 GET 推断 admin scope。

未来明确授权启用时使用既有 `traffic resume --local --task <任务UUID>`，先完成 check 和人工确认；不是本轮操作。发现错过合法边界时不会自动补清零，按 P2 的显式未来处置处理。

## 4. r2 接管

只接收当前 runner SHA256 `68f57acf038936a3e9292b7f24920803932abd519ad8205ce993e92d21c3d877`、schema 2 配置、官方 3X-UI 3.7.0、已识别 unit 与状态目录。当前安装为 V2.1.2、未知修改版或面板已升级 3.10.2 均拒绝普通自动接管。新建原生任务和 P3 后续运行仍支持 P1 的双版本契约。

候选必须逐项保持当前 r2 的 host、URL、listen/port、Remark、Client 集合与关联、额度、恢复策略、商家预算、日历和 first_cycle。Basic/Core 只用于解释旧格式，不能据套餐名跨机选择；**不能默认将现有配额改成推荐值**。既有零预留示例：Basic 商家 500 GiB、入口/Client 250 GiB、22 日 Toronto 15:44:25；Core 1000/500 GiB、22 日 Toronto 17:41:14。实际源文件不同就按契约阻断或使用其合法实际值，不替换身份。

现存所有状态逐份读取：

- r2 schema 2 账本以产生时配置绑定验证，完成状态须有按序请求/确认时间、正确对象、计数下降/原本为零、启用策略与最终证据。
- 更早 schema 1 账本要求原 V2.1.2 配置和 r2 安装 manifest，验证旧 runner/config 摘要、当前 r2 候选摘要、主机/套餐、原 Inbound ID、原 Client 绑定、`old_cycle` 与 r2 first_cycle 紧邻关系，以及 `state_before` 登记的全部旧账本和 marker 原字节摘要；删除整个 marker 对也会拒绝。manifest 字段和四文件摘要集合必须符合已知格式，原配置套餐也须相同。V2.1.2 runner 已识别摘要为 `8ea17a5bee2a402627a62b63a115aa7df2ec1ee6f46927b325b5cbb3e5e116e8`。不会执行备份脚本。
- marker 必须成对一致、早于相应首次周期；pending、部分完成、未知文件、缺少应有周期或来源、未来记录均阻断。任意历史配置/Client 集合曾变更而缺少可验证产生时配置，不猜测接管。
- 新控制日志的 `legacy_import` 保留原路径、原字节 hash、完成证据和原首次允许边界。已完成历史不重新签发为新 ledger；下一周期序号在导入记录之后，下一边界只取合法未来边界。没有 r2 首次记录时不能伪造首次成功。

如果有 schema 1，先建立 0600 的来源引用 JSON，例如（值是实际旧备份路径，禁止直接照搬示例目录）：

```json
{"config":"/root/<原r2迁移备份目录>/config","manifest":"/root/<原r2迁移备份目录>/manifest.json"}
```

config/manifest 文件名以原 manifest 和实际备份为准。工具只读取两个明确文件；不扫描 `/root` 猜备份，不声称 r2 原四文件备份含 Token 或完整账本。

```bash
python3 -B bin/vps-kit traffic deployment-plan --local --kind migrate \
  --config /root/candidate.json --r2-prior-evidence /root/prior-evidence.json \
  --package /root/vps-kit-0.3.0a2.zip --package-sha256 <包SHA256> \
  --activation paused --attestations /root/attestations.json --save /root/migrate-plan.json
python3 -B bin/vps-kit traffic deployment-apply --local \
  --plan /root/migrate-plan.json --expect-sha256 <计划SHA256> \
  --package /root/vps-kit-0.3.0a2.zip --r2-prior-evidence /root/prior-evidence.json
```

没有 schema 1 时可省略 `--r2-prior-evidence`。迁移直接读取原本机 Token 并复制到新受保护位置，**禁止同时更换 Token**；未来轮换走独立 credential 操作。新备份包含旧配置/程序/unit/Token/状态及来源证据副本，原历史原地保留。

apply 持有双锁后暂停旧 timer、确认 service 停止，再备份。随后旧 unit 实体移出原路径，在原 timer/service 名建立 `/dev/null` mask，旧 runner 改为拒绝执行的退役脚本。加载并核对实际 masked/inactive；新 unit 加入且只增加旧锁目录的可写权限。旧历史和原配置只读，不授予旧账本写权限。

之后每次管理写操作/执行都核对退役入口、磁盘与生效 mask、旧源材料/状态集合和受保护证据副本。来源证据在 `/root` 的原副本不作为运行期依赖，使用已验证 `/var/lib` 备份，兼容 `ProtectHome=true`。旧 runner 手工路径也会拒绝执行；root 主动解除保护或运行旧备份仍可绕过管理边界，工具不声称防住这种行为。

## 5. 回退与中断

```bash
python3 -B bin/vps-kit traffic deployment-audit --local
python3 -B bin/vps-kit traffic deployment-rollback --local \
  --operation <最近部署操作UUID> --task <同一任务UUID>
python3 -B bin/vps-kit traffic deployment-audit --local
```

从保留的已验证源码入口执行可覆盖稳定入口尚未安装或已恢复移除的情况。只允许回退**最近一次非 rollback 操作**；逐个核对文件只可能是原字节或计划写入字节，未知手工修改/备份摘要错/主机不符/活动 service/排队任务/额外 drop-in 都停止。回退前先验证旧发行目录全部文件及摘要，并在备份校验后再次检查维护时间窗口；这些前置失败不暂停 timer、不切换 current。存在活动 owner 时，还须保持原部署的配置修订与摘要；后续 P2 配置已经改动或 owner 已退役时阻断，不能借凭据或程序回退覆盖业务修订。旧 unit 归档后尚未建立 mask 的已登记中断点允许原路径暂时不存在。备份未提交时，只有全部正式目标仍与操作前一致才允许撤销部署意图。

恢复范围不包含 control、流量账本、旧 marker、面板数据库或计数。安装失败遗留的发行目录/临时证据保留用于分析，不自动清理、覆盖或删除锁 inode。journal 中断造成孤立记录或未同步 head 时，审计阻断，不能靠重跑 apply/手工删记录修复；需另行基于原证据恢复。

结果 `FILES_RESTORED_AUTOMATION_PAUSED` 表示文件恢复且自动化暂停。回旧 r2 **始终**返回 `r2_resume=NOT_AUTHORIZED`；即使新工具从未发请求也不会自动启用旧 timer。新工具已存在 pending/confirmed、历史处置或新账本时原样保留，不反向改写 r2 格式。程序已恢复不能证明旧自动化安全；须独立评审。

原生程序版本回退仅恢复上一份兼容发行包和程序身份，旧账本继续以产生时摘要验证。凭据回退先恢复受保护旧文件，再 GET 核对；旧 Token 若已被面板撤销，返回失败、timer 继续暂停，不能把文件恢复称为认证恢复。回退失败记录阻断后续写入，默认不重复执行。

完整回退后部署日志仍保留，安装后建立的 owner 也不会被删除。全新安装的退出、回 r2 或回退链需要再次接管时属于新的审计决策，不能通过删日志或改 UUID“重新安装”。本版本不提供多级自动回退、直接覆盖历史或数据库恢复功能。

## 6. 验收边界

`deployment-audit` 不调用 API，只读结构和已完成安装的文件/退役状态；凭据仅在本地做摘要核对，不输出内容或其摘要。失败阶段明确报告。`check` 做 GET 条件核对；执行账本见 `audit`。这些都不独立证明自然触发、systemd 日志、商家账期或代理业务验收，仍须分层核对。

P3 不扩大 P2 的最多一次请求保证，不掩盖 pending、整机快照回卷、无服务端幂等键或面板编辑竞态限制。Windows 的目录同步、权限、双锁、symlink、systemd 与真实 3X-UI 写副作用尚须 L2/L3 验证。Debian 13/12、Ubuntu 24.04 × 3.7.0/3.10.2 六组合未验收；批准方案的完整 P3 退出门槛尚未通过。
