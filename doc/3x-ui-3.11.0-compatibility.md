# 3X-UI v3.11.0 适配说明

日期：2026-11-04。对应 `vps-kit 0.5.0a3` Alpha 预发布。本次升级的是工具的面板适配目标，不是执行 VPS 上的面板安装或数据库升级。保留官方 `3.7.0`、`3.10.2` 兼容性，新增官方精确 `3.11.0`；未知版本及定制后缀继续拒绝。

## 上游核对与实现判断

核对对象为官方 [v3.11.0 发布](https://github.com/MHSanaei/3x-ui/releases/tag/v3.11.0) 和该 tag 的源码；固定文件及 Git blob SHA 追加到 [证据索引](p1-source-evidence.json)，旧版本来源保留。以下是源码证据，不是运行验收结果。

| 核对点 | 3.11.0 结果与本工具处理 | 上游依据 |
|---|---|---|
| GET 路由及对象绑定 | 沿用入口 list、Client get/traffic；详情仍提供 `client` 和 `inboundIds`。仍要求完整客户端集合和单入口关联 | [Client controller](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/controller/client.go)、[Inbound controller](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/controller/inbound.go) |
| 周期字段 | `resetWeekday` 仍存在且 0 表示关闭；embedded/detail/traffic 均要求显式整数 0，不放宽缺键迁移规则 | [模型](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/database/model/model.go)、[traffic 模型](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/xray/client_traffic.go) |
| 本地入口 | `nodeId`、`originNodeGuid` 的 omitempty 定义保留；缺键分别按 null/空字符串接受，存在远程关联仍拒绝 | [模型](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/database/model/model.go) |
| 逐 Client 重置 | `ResetTrafficByEmail` 先重置再尝试启用，启用失败可能只记日志。因此沿用 `reset-then-enable` 模型，仍必须 GET 读回验证，不能仅凭 success 认定恢复 | [Client service](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/service/client_traffic.go) |
| 逐入口重置 | 指定入口的 `ResetInboundTraffic` 清 up/down，controller 标记需要 Xray 重启；不是全 Client 重置，也不代替入口 setEnable | [Inbound service](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/service/inbound_traffic.go)、[controller](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/controller/inbound.go) |
| 其他重置修复 | 上游新增/修正的 periodic、resetAll 恢复行为不等于本工具逐对象接口变化。本工具继续关闭内置周期，禁止 bulk/resetAll，保留 hold 策略 | [周期任务](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/job/periodic_traffic_reset_job.go)、[Client service](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/service/client_traffic.go) |
| 请求与凭据 | Bearer Token 仍为 48 位；完整所需路由仍使用 admin scope。新增 node-admin 不代表获得本工具所需全部权限 | [Token service](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/service/panel/api_token.go)、[API controller](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/controller/api.go) |
| 不确定写入 | 服务端存在串行写队列；客户端超时不证明服务端取消。写前落盘、单次发送、失败阻断的规则继续适用 | [traffic writer](https://github.com/MHSanaei/3x-ui/blob/v3.11.0/internal/web/service/traffic_writer.go) |

因此新增独立版本登记，复用既有字段校验和执行状态机；未添加新 API、批量清零、自动改额或重试路径。本机版本守卫、包内示例和三版固定 fixture 同步更新；包成员继续使用 a2 可识别的路径。`host` 的版本字符串检查不证明安装包来源，现场仍需核对官方构建。

## 升级与兼容边界

- 工具版本为 `0.5.0a3`，[Alpha 预发布](https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a3)；已发布 `0.5.0a2` 附件不包含这些变更。已有受管工具使用正常整包升级事务，不覆盖当前目录。
- 修复后的 a3 将三份新合成 JSON 放在 `doc/fixtures/3.11.0/`，其中 config.json 也作为配置示例；不生成 a2 不认识的 `tests/traffic/fixtures/3.11.0/*.json` 或 `examples/3.11.0.json` 包成员。部署日志保留新包 manifest，回退后的真实 a2 仍可校验这些历史；没有放宽旧校验器、删改历史或修改已发布 a2 包。
- 已验证的工具回退场景为：在面板 3.10.2 合成配置保持不变的模型中，a2 → 修复后的 a3 → a2，保留已完成账本，旧版历史读取、部署守卫和部署审计通过，timer 保持暂停。工具回退要求配置仍被目标旧版识别；该结果不赋予 a2 对面板 3.11.0 的支持，也不代表恢复自动化或真实 Linux 验收。修复前的未发布 a3 构建不在此结论内；若已产生相应部署历史，不能通过覆盖同版本包或删日志套用本修复，需独立审计。
- `--kind upgrade` 要求当前活动配置不变，不能借程序升级同时改面板版本。面板版本变化需要维护窗口、恢复准备和配置修订；本次没有完成真实 `3.10.2 → 3.11.0` 数据库迁移验收，不提供绕过旧配置版本守卫的自动升级承诺。
- VMISS r2 和 VMRack 35/49 旧来源正向接管仍限官方 `3.7.0`。`3.10.2`、`3.11.0` 原生月日历/固定周期支持不扩大旧来源范围。
- `L1.VPS.1C1G.Base` 对应 VMRack-18 的原生固定 30 天需求，见 [契约补充](vmrack-contract.md#第三台-vps-需求补充2026-11-04)；不新增未经审核的旧脚本 profile。

## 验证范围与待办

回归覆盖三版配置与字段、主机版本守卫替身、执行防重复、失败阻断、POST 白名单、原生安装、固定周期和旧来源拒绝；固定输入新增 3.11.0 config/panel/ledger，旧六份 fixture 保持原字节。执行入口见 [CONTRIBUTING](../CONTRIBUTING.md)。

真实工具版本回退由 [独立回归入口](../tests/release_rollback.py) 验证：使用 SHA256 固定的原版 a2 发布包，在独立进程加载其完整程序源码，避免“当前源码只改版本号”的替身掩盖兼容性问题。系统和面板仍沿用本地模型；完整离线回归与该入口分别报告。

现有 [L2/L3 计划](l2-l3-isolated-acceptance-plan.md) 的六组合和历史结果保留。本次新增待验收组合为 Debian 13、Debian 12、Ubuntu 24.04 LTS × 官方 `3.11.0`，均为 **NOT_RUN**。每个组合需覆盖原生月日历和固定周期、真实 API 读写及恢复副作用、systemd/持久化/锁、旧来源失配拒绝；另需独立副本观察 `3.10.2 → 3.11.0` 数据库迁移及 embedded 周期字段。合成回归或本机 HTTP 回环不能代替这些结果。
