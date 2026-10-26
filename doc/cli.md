# CLI 参考

当前公开整理版本为 `0.4.0a3`。在仓库根目录使用 `python -B bin/vps-kit traffic <动作>`，或在 Linux 中使用 `python3 -B`。查看具体参数：

```powershell
python -B bin/vps-kit traffic
python -B bin/vps-kit traffic --help
python -B bin/vps-kit traffic validate-config --help
```

以下是已实现接口及其副作用边界。`--local` 接口的 Linux/systemd/真实面板集成仍未验收；表格是接口契约说明，不能替代针对目标主机的部署授权。

| 接口 | 当前能力 | 副作用边界 |
|---|---|---|
| `traffic` | 列出当前动作 | 只输出 |
| `traffic validate-config --config …` | 严格配置校验，输出脱敏摘要 | 读取指定文件，不证明现场一致或权限合规 |
| `traffic inspect-state --config … --state …` | 按产生时配置核对一份原生账本 | 不读取线上计数，不修复，不重试 |
| `traffic discover --config … --fixture …` | 双版本合成快照核对 | 无 HTTP、无系统命令 |
| `traffic discover --config … --local --token-file …` | 本机环境守卫后执行固定 GET | 已实现但未在真实 Linux/面板运行；真实 Linux/面板集成尚未验收 |
| `traffic status [--config …]` | 本机平台和已知 unit 的只读状态 | 无 Token/API；Windows 明确报告非目标平台 |
| `traffic calendar / units --config …` | 下一唤醒、未来 24 个业务边界、首次允许边界 / 生成 unit | 离线输出，无系统命令或写入 |
| `traffic anchor --local-time … --timezone …` | 无歧义的整秒本地锚点转 UTC | 离线计算，无写入或网络；不授予首期执行资格 |
| `traffic plan --local … --save …` | 新建或 revision+1 候选、脱敏差异与摘要 | 只读系统，显式保存候选；不暂停、不清零 |
| `traffic prepare --local …` | 锁内复核、暂停目标 timer、备份、prepared | 本机维护记录与 timer；API 仅 GET |
| `traffic apply --local …` | 改额后全量核对、提交修订和日历、安装 unit | 不改面板、不清零；是否恢复 timer 绑定已确认候选 |
| `traffic cancel --local …` | 取消尚未安装的 prepared 候选 | 必须核对旧契约，保留暂停；不回滚计数 |
| `traffic check / audit --local --task …` | 当前只读条件核查 / 持久历史结构审计 | 不持有写锁、不创建文件，check 仅 GET；audit 不调用 API |
| `traffic pause / resume / remove --local --task …` | 原生 timer 生命周期 | 显式管理目标 timer，保留历史；不启动重置 service |
| `traffic resolve --local --task … --evidence …` | 追加人工未来处置记录 | 不改旧账本、不补发旧请求，保持 timer 暂停 |
| `traffic execute --local --task …` | 自然边界执行器入口 | 仅匹配本任务的 systemd InvocationID/MainPID；不是手动清零入口 |
| `traffic package-build / package-verify …` | 固定清单离线 ZIP 构建 / 摘要校验 | build 仅写指定新包；verify 只读；不下载或执行包内代码 |
| `traffic deployment-plan --local --kind … --save …` | install/migrate/upgrade/credential 候选 | GET、系统和文件检查后写新计划，不暂停；Token 仅保存摘要 |
| `traffic deployment-apply --local …` | 确认摘要后在锁内应用候选 | 先暂停、备份，再改文件；是否启用新 timer 绑定计划；零重置 POST |
| `traffic deployment-audit --local` | 独立部署日志及已完成安装完整性 | 只读，无 API、写锁或文件写入；失败阶段也可报告 |
| `traffic deployment-rollback --local …` | 按最近操作恢复已登记文件 | 保留所有流量历史，两侧 timer 暂停；不自动恢复 r2 调度 |
| `traffic credential-input --local --save …` | TTY 隐藏输入，保存新候选凭据 | 不覆盖已有文件，不替换活动凭据、不请求 API |
| `traffic vmrack-source-audit --local --config … --vmrack-evidence …` | 已知模板、来源文件集合和绑定的只读核对 | 不执行旧脚本、零 API；输出来源汇总摘要，隐藏 Token 和原文 |
| `traffic deployment-plan --kind vmrack-migrate …` | M1/M2 专用接管候选 | 必须 `--activation paused`，需来源及历史证据 |
| `traffic deployment-reconfirm --local --operation … --expect-sha256 … --package …` | 同一事务确认实际 cutover 导致的新首期 | 仅接受等待再确认阶段；安装后仍暂停，不重放 failed |

`discover` 当前针对**显式配置中已绑定的入口与完整 Client 集合**。不挑选同名 Remark 的第一条，也不将线上发现结果写回配置。P2/P3 的计划接收显式配置；交互式入口/Client 选择和友好向导尚未交付。

上述 `--local` 写接口仅完成本地契约验证，尚未取得真实 Linux/面板集成验收证据。r2 接管通过 `deployment-plan --kind migrate`，轮换凭据通过 `--kind credential`；没有 `run/reset` 通用清零入口。修改业务配置仍使用 revision+1 的 `plan → prepare → apply`。普通 P2 创建拒绝未经 P3 接管的 r2；已完成接管后的所有写管理和执行保留新锁→旧锁顺序，并核对旧入口退役。

选项必须使用完整名称；不接受 `--token`、`--loc` 等隐式缩写。`discover` 使用 `--token-file` 指定的受保护文件；P2 管理 GET 使用固定凭据文件，执行器使用 systemd LoadCredential。Token 不进入 argv、计划或账本。


## 使用与证据边界

配置与字段规则见 [P1 契约](p1-contract.md)，月日历、控制及执行规则见 [P2 契约](p2-contract.md)，安装、r2 接管和回退见 [P3 契约](p3-contract.md)，固定周期与 VMRack 接管见 [扩展契约](vmrack-contract.md)。历史契约中的阶段号、版本和样例包名称按其原有时间点理解，当前公开准备变化见 [变更记录](../CHANGELOG.md)。

两个面板适配器只承认精确版本。`nodeId` 缺键/null 与其他字段的缺失规则不同；Client 周期字段和 Inbound 周期字段分别核对。`quota-recovery` 仅恢复该对象本人有限额度耗尽且策略允许的状态，入口满额不代表所有 Client 均可恢复。共享总池与商家预算分别核对。

GET 读取是分步快照；`script_writes=0` 不承诺面板没有访问日志。Windows 下共享锁和目录同步测试使用模型，真实 Linux flock、目录 fsync、systemd/cgroup、LoadCredential、TLS 和面板 POST 副作用仍需隔离验收。包摘要用于核对内容，不能单独证明作者身份。
