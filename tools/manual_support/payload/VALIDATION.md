# a6 配套 r1 修复及验证记录

日期：2026-11-05。本次范围：配套归档、构建入口及回归、两份手册、Basic 用户回传摘要。本次本地修订没有推送、发布或操作 VPS。程序 ZIP、源代码运行逻辑、模板配额、网络及账期均未改变。

## 故障与修复

原 a6 配套 tar.gz 的一个目录为 `0777`、八个文件均为 `0666`，uid/gid 为 0。Basic 的目录诊断确认配套目录0777，而其他已存在父目录均通过检查；首次计划返回 `JOURNAL_DIRECTORY_UNSAFE`、`read_calls=0`。真实 `DurableFiles.directory` 拒绝组/其他用户可写目录；`protected_bytes` 还要求程序 ZIP 为 root:root 的0600或0644普通文件。因此只修目录不足以完成交付修复。

修订构建器显式创建 TarInfo，固定 root:root、目录0700、文件0600、成员集合、顺序与时间，不继承 Windows st_mode。输出独立 r1 归档；原包、校验文件及原验证记录保持历史字节。手册增加安全解包选项和权限检查。目录或文件不符时停止，不放宽运行 guard。

## 本次本地检查与限制

- 从原 a6 归档实际读取 metadata，并投入真实 `DurableFiles.directory` 检查：旧0777拒绝，r1的0700接受；宿主 POSIX/file metadata 使用显式模型，不冒充 Linux 原生运行。
- 标准库回归检查 tar 成员、模式、所有权、无链接、内容、确定性、错误发布输入拒绝及已有输出保护。另保留 Linux root 解包/真实 `protected_bytes` 测试分支；本次 Windows 环境未执行该分支。
- 固定 ZIP 及发布摘要与原件逐字节相同；helper 与两个模板也与原 a6 相同。新旧归档仅封装/说明不同，程序版本都为0.5.0a6。
- ZIP SHA256：`f426ecf4cc605947f48989d0185ae1985223b53eb8a64757cf3b28f285209bcf`；program_sha256：`4ca1ee8c77ac037bbfa63f54566ac585fc274c2314e2da30b3ec6c0135effe6c`。
- 既有 a6 验证记录中的来源12项、首次空清单10项及附件5项回归属于此前检查；本次不把历史计数称为新执行的完整产品矩阵。此前手册静态核验遗漏了外层 tar 权限，本次补上这一缺口。
- 本次本地门禁及摘要结果由构建输出和手册修订自检记录保存；未验证 r1 在真实 Linux 上的解包或重新安装，不为此操作 VPS。

## Basic 用户回传的实际状态

证据来源为用户在本次会话粘贴的命令输出，未独立连接目标主机。Basic 为全新 Debian13、3X-UI3.11.0；其正式对象已独立采集。`provider.amount` 一度误填250引发 `CONFIG_BUDGET_EXCEEDED`，恢复原定500后采集成功；保留套餐500GiB、入站与唯一客户端各250GiB，并未改变方案。

目录/文件权限修正后复用现有配置、Token和确认文件，固定程序0.5.0a6首次安装成功：

| 阶段 | 用户回传证据 |
|---|---|
| 暂停计划/安装 | `DEPLOYMENT_PLAN_SAVED` / `DEPLOYMENT_COMPLETE`，kind=install、activation/timer=paused、POST=0 |
| 部署审计 | stage=complete、error_code=null、operations=1、migration=null；runtime_acceptance=NOT_PROVEN |
| 暂停只读检查 | `READONLY_CHECK_PASS`，4 GET、0 POST、零脚本写入，NTP同步；timer=paused、service=inactive、next_utc=null |
| 单独启用 | `RESUME_COMPLETE`，timer=waiting、POST=0 |
| 启用后只读检查 | `READONLY_CHECK_PASS`，4 GET、0 POST、零脚本写入、NTP同步；无drop-in、冲突任务、待执行作业或reload |
| 控制契约 | `CONTROL_CONTRACT_VALID`，eligibility=FUTURE（仅控制/时间范围）、maintenance=complete/resume、owner_status=active、next_period_id=1；periods与unresolved_periods为空，last_completed_period=null |
| systemd | loaded、active/waiting、enabled、Persistent=no；下一次与工具授权/业务边界一致 |

本记录不公开任务UUID、计划摘要、绑定信息、地址、Token或原始配置。Basic 的月日历与首次边界详见随附 VMISS 手册和本地 Basic 验证记录；其时间不同于 Core，不能混用。Basic 未提供受控备份转存或恢复演练输出。

## 未验收与实例隔离

Basic 的 `write_authorization_and_execution=NOT_TESTED`、`systemd_journal_and_business_acceptance=NOT_PROVEN`；尚无完成的自然账期。**实际账期 POST、计数读回、账本完成及业务验收仍未完成。**启用等待、GET成功、未来资格均不能替代这些结果。

Core 的已知现场结果仍是 a5 暂停安装和启用，不能称为升级a6；Basic 的a6现场证据不能套用到Core或VMRack。VMRack35/49/18无本次a5/a6安装、启用或固定30天业务验收证据。Basic修复旧包权限后成功，也不能当作本次r1归档的Linux解包验收。
