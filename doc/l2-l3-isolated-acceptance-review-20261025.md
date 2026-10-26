# L2/L3 隔离验收方案 A3 复查记录

日期：2026-10-25（Asia/Singapore）。被评对象为 [A3 方案](l2-l3-isolated-acceptance-plan.md) 的复查前原文，SHA256：`e3d14d0e0ce8e1942ccccb15dd5c28ec331e3ff2fb62c4cc5267a05c0897e6cb`；修订后版本为 **A4**，受测产品仍为 `0.4.0a2 / E1-2`。目标是文档可执行性与判据一致性，授权为范围内直接修文及本地一致性验证；没有执行验收的授权。

控制依据：[已批准总方案](vps-traffic-reset-tool-proposal.md)、[VMRack E0.2](vmrack-fixed-30-day-extension-proposal.md)、[当前扩展契约](vmrack-contract.md)、[产品复查记录](vmrack-review-20261025.md)和当前冻结源码。检查同时覆盖与上游/源码的对齐，以及步骤、判据、异常处置的内在一致性。独立复核分为日历/运行边界和来源迁移/回退两组，主审回到源码核对关键主张；模型意见不作为运行证据。

## 1. 确认问题与整改

| ID / 严重度 | 原位置 / 评审轴 | 问题、证据与影响 | A4 修正及核验 |
|---|---|---|---|
| A4-D1 / Major | §16.3 F30-05；内在一致性、源码对齐 | 把 B+30 的已完成旧期与漏期/pending 混列，并要求完成重入零 POST。`Control.next_authorization()` 在 B 完成后推进到下一 period/B+30；`execute()` 只在已完成期窗口内返回 ALREADY_COMPLETED，下一授权未到时 NOT_DUE，到 B+30 则正常执行。旧文会把合法新期判为重复清零，与 F30-04 冲突。 | 明确漏期/pending 到 B+30 仍阻断；旧期完成的零请求重入在下一授权前检查；若在 B+30 检验重入，先自然完成该新期再检查零额外请求。不以 B 期完成阻止 B+30。按冻结源码分支和纯授权推进算例核对。 |
| A4-D2 / Major | §18.1，关联 §5；可执行性、证据对齐 | “发送/发布/管理写前”未明确相对身份检查的位置。源码为时钟/fault 回调→身份检查→send/replace/command。若在 rename/exec syscall 或 execute 依赖内、API 网关收到请求时才换锁，检查已结束，下一写可能发生，不能判 R1 修复失败。旧文扩大了非原子检查的保证。 | 给出 NativeRuntime、BudgetPanel、Journal/DeploymentFiles、两类 _run 的目标检查前闸门；记录顺序。检查后注入列为非原子竞争边界/未命中；执行闸门不得先耗尽 240/290/300 秒预算，其他保护不能代填 R1 PASS。函数注入与原版 CLI 证据分开。 |
| A4-D3 / Major | §17.4、§18.3、L2-15 及 §2.1；适用范围、源码对齐 | 将配置目录全集守卫泛化到 VMISS/VMRack。`_source_sets()` 只有 VMRack 要求配置目录恰为 config/token、状态顶层普通文件；VMISS 持续守卫验证登记来源文件与状态文件/符号链接集合。合法历史 reset.conf 旁件或未纳入集合的空目录不能统一列为必须拒绝，否则会制造错误 FAIL。L2-15 的来源分支也不应列成 3.10.2 原生必测。 | 分别规定来源的集合判据；VMISS 仍测试登记配置/Token 变化和状态文件增删，VMRack 另测配置目录精确集合与专用 cron。首次来源收集与持续守卫不混用；L2-15 版本适用性单列，3.10.2 做来源拒绝、原生锁由 L2-03/14 覆盖。 |
| A4-D4 / Minor | §16.3 F30-03 及普通日零追加判据；步骤/观测一致性 | F30-03 允许通过 pause/resume 准备受控时间阶段，但未界定 head 比较起点。合法管理操作会追加 maintenance/transition；若拿准备前 head 与普通日醒后比较，会误判业务日志被追加。观察器的 GET 同样不应混入执行器零 API。 | 基线在该阶段准备/恢复完成、timer waiting、service/Job 静止后取得；仅比较单次日醒区间。保留管理事件；API 按 invocation/子进程计数，采集器 GET 单独登记。未改变普通日零业务 API/零执行日志契约。 |

上述分级基于是否会改变验收成败结论或夸大实现保证，不按文字数量分级。整改责任为本轮文档修订者，2026-10-25 已完成；真实实验执行者/设施负责人仍须在 G0 指定。

关键源码依据为 [control.py](../src/vps_kit/traffic/control.py) 的 next_authorization、[execution.py](../src/vps_kit/traffic/execution.py) 的执行资格及 BudgetPanel.send、[journal.py](../src/vps_kit/traffic/journal.py) 的原子提交、[deploy_files.py](../src/vps_kit/traffic/deploy_files.py) 的发布、[native.py](../src/vps_kit/traffic/native.py) 的运行/系统检查、[deploy_native.py](../src/vps_kit/traffic/deploy_native.py) 的来源集合与回退守卫，以及 [maintenance.py](../src/vps_kit/traffic/maintenance.py) 的 pause/resume 记录。全部为已接受 0.4.0a2 源码，只读核对，未改实现。

## 2. 其余重点范围的结论

- **六组合**：保留 Debian 13/12、Ubuntu 24.04 × 官方 3.7.0/3.10.2；月日历和原生固定周期均必测。r2 Basic/Core、VMRack 35/49 的正向来源接管仅限 3.7.0，3.10.2 为各自失配拒绝；未知 18 刀旧源与 traffic.2 不获写入资格。
- **固定周期/VMISS**：2592000 秒网格、锚点前/边界/远期 first、24 个日醒与24个业务点、跨月/年/闰日/同月双边界分别核对。VMISS Toronto 月日历、Basic/Core 参数、原 first 与导入后 next 区别保留；每日 LAST 不代替业务 invocation。
- **M1/M2**：逐对象旧窗口与网格、旧重试/延迟关闭证据、预计与实际 cutover、等待再确认的无运行授权、同 operation/hash/原包继续、failed 不自动续装与取消文件退出均与契约一致。没有把 marker 或等待 30 天改写为成功证明。
- **双锁/持续退役**：来源决定固定旧路径和新→旧锁顺序；旧目录删除不取消来源。stub、实际 mask、登记 cron、源文件/备份、进程/cgroup/Job、持有锁身份的检查按各来源适用，未声称能防任意 root 副本或检查后原子竞争。
- **R2/R5 可执行性**：R2 是独立只读 NativeSystem.verify 驱动，跨日区间无可执行授权，真实系统读取、正常单次重采样与第三采样负面分开；没有把自然执行 service 的 300 秒套给只读跨日等待。R5 使用古老锚点作现代 now 只读预览，不把 guest/NTP/证书拨至公元 1 年。
- **R3/R4 回退**：未知目标/来源在暂停前后恢复前检查；初始文件退出仍需原证明，运行期可依赖受保护备份。partial 保留旧月日维护窗，完整 retired 免旧错误月日窗；最近操作、配置/owner/格式资格、未决保留及恢复旧 runner 后不再具有 stub 拒绝能力均明确。

未发现这些范围需要改变已批准产品行为的依据。G0 设施缺失、注入未命中、资产不可得或观察能力不足仍必须 BLOCKED，不能用本次文档通过抵扣。

## 3. 本地一致性验证与制品

本地一致性验证通过：六组合、8 个环境步骤、15 个 L2/19 个 L3 组、6 个固定日醒子场景和 R1～R5 追踪完整；20 条命令只作参数解析，36 个本地链接（含引用锚点）有效，10 项纯计算核对通过。新增授权推进算例明确区分旧期完成、下一授权及未决未推进，只是内存投影计算，不生成完整账本、不运行 execute。

当前开发包仍为 90 个有效载荷文件；其 55 个冻结代码/测试/入口/元数据输入均未改变，两个旧包也保留。与封存包的受控有效载荷比较，仅方案因授权更新；本记录为另一个明确的包外新增文档。未重建任何 ZIP，未改源代码/测试。方案与本记录的最终文件摘要及详细检查结果见同目录 document-validation.json，避免将文档自身摘要嵌回正文形成循环。

本轮不执行 L2/L3、实验设施、真实 API/systemd、完整产品回归或公网访问。原始 A3、旧报告及差异保留，静态/纯计算结果不表示环境就绪或运行验收通过。

复查前 A3、旧验证报告、当前基线散列、差异和本轮验证报告保存在 `.tmp/local/l2l3-plan-a3-review-20261025/`。原 `.tmp/local/l2l3-plan-a3-20261025/` 与 A2 历史材料保留，不覆盖。方案及本记录均为包外文件；封存 0.4.0a2 ZIP 内 A2 不重打，最终执行采用用户确认的 A4 及其独立摘要。

## 4. 评审结论与执行边界

文档复查结论：**通过（限 A4 文档的范围、判据与本地一致性）**。4 处确认问题已修正并通过针对性复核，没有遗留 Blocker/Critical 或未关闭的本轮文档问题。运行验收仍 NOT_RUN，实际虚拟化平台、执行者/白名单、制品、时间域和故障设施须按 G0 登记，用户确认后才可进入隔离批次。

本轮没有创建实验环境、连接或修改现有 VPS、执行真实清零或调整 timer/service；未读取生产 Token/数据库，没有 commit/push、发布或重建开发包。traffic.2 写适配继续延期。
