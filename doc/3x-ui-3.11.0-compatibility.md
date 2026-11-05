# 3X-UI v3.11.0 适配说明

日期：2026-11-05。当前对应 `vps-kit 0.5.0a6` Alpha 预发布，收录 a4/a5 修复；保留下文本地候选、a3 发布与历史回退记录。本次升级的是工具的面板适配目标，不是执行 VPS 上的面板安装或数据库升级。保留官方 `3.7.0`、`3.10.2` 兼容性，新增官方精确 `3.11.0`；未知版本及定制后缀继续拒绝。

## 上游核对与实现判断

核对对象为官方 [v3.11.0 发布](https://github.com/MHSanaei/3x-ui/releases/tag/v3.11.0) 和该 tag 的源码；固定文件及 Git blob SHA 追加到 [证据索引](p1-source-evidence.json)，旧版本来源保留。以下是源码证据，不是运行验收结果。

| 核对点 | 3.11.0 结果与本工具处理 | 上游依据 |
|---|---|---|
| GET 路由及对象绑定 | 沿用入口 list、Client get/traffic；详情仍提供 `client` 和 `inboundIds`。仍要求完整客户端集合和单入口关联 | [Client controller](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/controller/client.go)、[Inbound controller](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/controller/inbound.go) |
| 周期字段 | `resetWeekday` 仍存在且 0 表示关闭；embedded/detail/traffic 均要求显式整数 0，不放宽缺键迁移规则 | [模型](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/database/model/model.go)、[traffic 模型](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/xray/client_traffic.go) |
| 本地入口 | `nodeId` 只接受缺键/null；`GetInbounds` 为本机入口注入 panelGuid，非空 originNodeGuid 须与独立 status GET 返回的本面板 GUID 一致。缺键/空来源沿用原规则；不把 GUID 比对升级为完整计量拓扑证明 | [列表服务](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/service/inbound.go)、[status 服务](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/service/server.go) |
| 逐 Client 重置 | `ResetTrafficByEmail` 先重置再尝试启用，启用失败可能只记日志。因此沿用 `reset-then-enable` 模型，仍必须 GET 读回验证，不能仅凭 success 认定恢复 | [Client service](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/service/client_traffic.go) |
| 逐入口重置 | 指定入口的 `ResetInboundTraffic` 清 up/down，controller 标记需要 Xray 重启；不是全 Client 重置，也不代替入口 setEnable | [Inbound service](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/service/inbound_traffic.go)、[controller](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/controller/inbound.go) |
| 其他重置修复 | 上游新增/修正的 periodic、resetAll 恢复行为不等于本工具逐对象接口变化。本工具继续关闭内置周期，禁止 bulk/resetAll，保留 hold 策略 | [周期任务](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/job/periodic_traffic_reset_job.go)、[Client service](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/service/client_traffic.go) |
| 请求与凭据 | Bearer Token 仍为 48 位；完整所需路由仍使用 admin scope。新增 node-admin 不代表获得本工具所需全部权限 | [Token service](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/service/panel/api_token.go)、[API controller](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/controller/api.go) |
| 不确定写入 | 服务端存在串行写队列；客户端超时不证明服务端取消。写前落盘、单次发送、失败阻断的规则继续适用 | [traffic writer](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/service/traffic_writer.go) |

a3 新增版本登记并复用执行状态机，但遗漏了 API 返回前的 GUID 注入。a4 修正该判断，并仅为非空来源增加一次 status GET；未新增写 API、批量清零、自动改额或重试路径。本机版本守卫、包内示例和三版固定 fixture 同步更新；包成员继续使用 a2 可识别的路径。`host` 的版本字符串检查不证明安装包来源，现场仍需核对官方构建。

## 本次缺陷与修复边界

实际回传显示 `nodeId` 缺失、`originNodeGuid` 为 36 字符非空字符串；a3 在首次配置生成时拒绝。该现象与上游 `annotateLocalOriginGuid` 一致，但仅凭长度不能证明现场 GUID 归属。a4 将在真实继续时读取 status 独立比对；本轮没有访问 VPS，真实比对与自动重置仍未验证。

3.11.0 的非空来源要求规范小写、非零 UUID；status 的 `obj.panelGuid` 必须有效且相等。非空 nodeId 在 status 请求前拒绝；不同 GUID 报 `API_REMOTE_ORIGIN`，异常来源报 `API_ORIGIN_GUID`，缺失/无效本面板身份报 `API_PANEL_GUID`，读取失败保留原传输错误。错误均不重试，不能手工清字段、绕过检查或从入站复制 GUID 充作独立证明。

3.7.0/3.10.2 行为不变。来源缺键或空字符串保持原规则，不增加 GET；3.11.0 非空来源每次快照增加一次 GET。身份不持久化到配置，不增加配置字段；写前和写后分别重新核对，不跨快照缓存。status 使用面板缓存状态，字段不可用时停止；不声称多 GET 是原子事务，也不以该检查代替人工计量拓扑确认。

本地 a4 不覆盖任何旧包；没有公开下载地址。首次安装失败的用户应将 a4 解压至独立候选目录，并使用匹配 a4 的配置采集附件，沿用已填写的模板和凭据。既有受管部署仍需正常升级事务，不能覆盖活动代码。回退 a3 会重新出现此缺陷，不能把“旧包仍可读取历史”当作“3.11.0 自动化可恢复”。a2 不支持 3.11.0。

## 首次安装任务枚举修正（a5）

在未安装任何 vps-kit/旧重置任务的 Debian 13、systemd 257 环境中，带任务模式的 `list-unit-files` 返回空 stdout 和退出码 1，`list-units` 返回空 stdout 和退出码 0。a4 把前者作为 `HOST_COMMAND_FAILED` 拒绝，安装计划没有到达面板 GET。旧测试把两种空结果都模拟为退出码 0，遗漏了原生安装检查分支。

a5 使用一次不带名称模式的 `systemctl list-unit-files --no-legend --no-pager --full`，仅在命令成功后按原来的三组任务模式本地筛选。所有 unit 类型均保留检查，不局限 timer/service；隐藏在普通系统任务后的未归属任务也必须拒绝。随后仍检查已加载任务，覆盖只加载但未安装的任务。无条件吞掉退出码 1、创建占位 unit 或失败后自动重试均未采用。

全量已安装 unit 清单的 stdout 上限单独设为 256 KiB，其他主机命令仍为 32 KiB；保留进程超时、stderr 上限、输出超限拒绝和锁身份检查。每条非空已安装 unit 行应包含名称、状态及可选 preset，共 2/3 列；无法解析、非法编码、总线/权限等非零退出码仍停止。超限不截断后继续筛选，不能以部分清单证明无冲突。

新回归通过真实 CLI 的 `deployment-plan → Deployment.plan → InstallationSystem.inspect → NativeSystem._foreign` 路径复现并修正，系统命令、主机身份、权限和面板使用显式替身；不把模型成功称为 Debian/systemd 验收。计划成功只保存暂停安装计划，不部署、不创建管理记录、不发送 POST。冲突或查询失败在面板读取前停止，不保存计划。

对于已完成 a4 配置采集的用户，继续复用原 candidate、Token 和 attestations；只在独立目录准备 a5，并以 a5 包生成新的暂停安装计划。不必重建配置或再次创建确认文件。a5 不发布、不覆盖 a4；回退 a4 将恢复空匹配缺陷，回退 a3 还会恢复 GUID 缺陷。真实 VPS 上的 a5 计划与安装尚未验证。

## a6 发布与验证边界

`0.5.0a6` 发布上述两项修复。相对已交付 a5，运行代码仅改变版本标识，发布入口与说明同步更新；不覆盖 a4/a5 本地包或旧版 Release 附件。最终提交、CI、独立旧版回退和附件摘要见 [a6 发布说明](https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a6)。

首次安装计划回归保留真实 `InstallationSystem.inspect/_foreign`，但替换主机身份检查、原生上下文、输入读取和计划保存，使用模型面板，并预建模拟管理父目录。既有安装成功测试调用真实事务逻辑，但使用模型 system 的安装和验证方法。测试通过不证明原生冷启动、`NativeSystem.install`、真实 systemd 生效检查或完整首次安装可用；这些仍须按既有隔离验收计划补齐。失败时保留现场，不因发布而绕过守卫或自动重试。

## 升级与兼容边界

- 当前工具版本为 `0.5.0a6`，[Alpha 预发布](https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a6)。历史 a3 引入 3.11.0 适配，但不包含 a4/a5 修复；a2 不支持 3.11.0。已有受管工具使用正常整包升级事务，不覆盖当前目录。
- 修复后的 a3 将三份新合成 JSON 放在 `doc/fixtures/3.11.0/`，其中 config.json 也作为配置示例；不生成 a2 不认识的 `tests/traffic/fixtures/3.11.0/*.json` 或 `examples/3.11.0.json` 包成员。部署日志保留新包 manifest，回退后的真实 a2 仍可校验这些历史；没有放宽旧校验器、删改历史或修改已发布 a2 包。
- 已验证的工具回退场景为：在面板 3.10.2 合成配置保持不变的模型中，a2 → 修复后的 a3 → a2，保留已完成账本，旧版历史读取、部署守卫和部署审计通过，timer 保持暂停。工具回退要求配置仍被目标旧版识别；该结果不赋予 a2 对面板 3.11.0 的支持，也不代表恢复自动化或真实 Linux 验收。修复前的未发布 a3 构建不在此结论内；若已产生相应部署历史，不能通过覆盖同版本包或删日志套用本修复，需独立审计。
- `--kind upgrade` 要求当前活动配置不变，不能借程序升级同时改面板版本。面板版本变化需要维护窗口、恢复准备和配置修订；本次没有完成真实 `3.10.2 → 3.11.0` 数据库迁移验收，不提供绕过旧配置版本守卫的自动升级承诺。
- VMISS r2 和 VMRack 35/49 旧来源正向接管仍限官方 `3.7.0`。`3.10.2`、`3.11.0` 原生月日历/固定周期支持不扩大旧来源范围。
- `L1.VPS.1C1G.Base` 对应 VMRack-18 的原生固定 30 天需求，见 [契约补充](vmrack-contract.md#第三台-vps-需求补充2026-11-04)；不新增未经审核的旧脚本 profile。

## 验证范围与待办

回归覆盖三版配置与字段、主机版本守卫替身、执行防重复、失败阻断、POST 白名单、原生任务安装事务模型、固定周期和旧来源拒绝；固定输入新增 3.11.0 config/panel/ledger，旧六份 fixture 保持原字节。执行入口见 [CONTRIBUTING](../CONTRIBUTING.md)。

真实工具版本回退由 [独立回归入口](../tests/release_rollback.py) 验证：使用 SHA256 固定的原版 a2 发布包，在独立进程加载其完整程序源码，避免“当前源码只改版本号”的替身掩盖兼容性问题。系统和面板仍沿用本地模型；完整离线回归与该入口分别报告。

现有 [L2/L3 计划](l2-l3-isolated-acceptance-plan.md) 的六组合和历史结果保留。本次新增待验收组合为 Debian 13、Debian 12、Ubuntu 24.04 LTS × 官方 `3.11.0`，均为 **NOT_RUN**。每个组合需覆盖原生月日历和固定周期、真实 API 读写及恢复副作用、systemd/持久化/锁、旧来源失配拒绝；另需独立副本观察 `3.10.2 → 3.11.0` 数据库迁移及 embedded 周期字段。合成回归或本机 HTTP 回环不能代替这些结果。
