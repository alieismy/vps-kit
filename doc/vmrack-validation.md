# 固定 30 天扩展本地验证记录

版本 `0.4.0a1`，2026-10-24—25。控制输入：[E0.2](vmrack-fixed-30-day-extension-proposal.md)；实现契约：[E1](vmrack-contract.md)。

本文件保留首次实现的历史证据；当前 `0.4.0a2` 的修正、最终回归和包验证见 [E1-2 复查记录](vmrack-review-20261025.md)，下面的 166 项及原包信息不作为 a2 的验证结果。

## 执行边界

仅 Windows 本地 L1 模型、临时文件及故障注入。`tests/run.py` 通过 Python audit hook 拒绝真实网络与非测试 Python 子进程。未创建实验 VM/容器，未访问 VPS，未调用真实清零 API 或现有 systemd。traffic.2 写适配未实现。

## 结果

最终冻结源码执行 `python -B tests/run.py`：**166 项全部通过，耗时 767.780 秒**，无失败、错误或跳过。本次冻结的 54 个源码/测试/元数据文件在回归结束后重新核对，摘要一致。原 134 项与新增 32 项在同一完整回归中运行；开发诊断和中断日志不作为最终通过依据。

文档检查覆盖 21 份 Markdown 的本地链接、代码围栏、契约中的源码族摘要和 10 条命令示例的实际 CLI 参数解析。发行包通过 manifest 逐成员校验、与工作区逐字节核对、Python 3.11 语法检查和独立重复构建摘要比较；具体包摘要及结果见包外 `release-validation.json`。

原 134 项测试全部保留；新增 32 项，共 166 项（各项内部还包含子场景）。原 19 个测试 Python 文件中 18 个逐字节未变；唯一修改位于 `test_io_cli.py::test_host_status_fixed_readonly_command_surface`，按 E0.2 新增 VMRack 两个已知 unit，将命令数从 8 改为 10 并增加完整 unit 清单断言，原只读命令和零 API 断言保留。原六份双版本 JSON fixture 及其 SHA256SUMS 保持，未更改当前 VMISS 主方案、r2 runner/build_config/manage 或旧 `0.3.0a2` 包。

额外执行了两类与一般合成包不同的核对：

- 在离线进程中加载保留的 `0.3.0a2` 原包源码，确认配置 v2 返回 `CONFIG_SCHEMA`、新增包能力返回 `PACKAGE_CONTRACTS`、新增控制事件返回 `CONTROL_EVENT_KIND`。完整新包也会因旧版不认识新增 fixture 路径提前返回 `PACKAGE_PATH`；能力检查另外独立验证，未用提前报错冒充能力检查。
- 将真实旧 ZIP 作为安装/升级文件输入，在临时目录和系统替身中验证新版本升级、原月历/授权/事件保留，以及不兼容降级仅恢复文件、自动化暂停和格式记录保留。旧程序没有在 Linux 中运行，此项仍属于文件及状态机模型。

开发中修正了来源类型在目录消失后的锁选择、旧 service RuntimeDirectory 的首次取锁时序、真实 cutover 后首期再确认、只读 cron 清单与凭据权限的区别、以及新增事件的重放白名单。收尾另补齐 24 次唤醒预览和只读审计的 owner/维护阻断，避免将单纯时间窗口描述为完整运行资格。接管证据还须与候选商家预算的总字节数一致，拒绝空证明文件；预算缺失、套餐混用和 GB/GiB 不等字节均在 API 前阻断。

## 本地复现与证据

在仓库根目录执行离线全量回归：

```powershell
python -B tests/run.py
```

本次本地证据保存在 `.tmp/local/vmrack-implementation-20261024/`（Git 忽略，保留供复查）：`frozen-source-hashes-final.json`、`full-regression-final.log`、`delivery-static-validation.json`、`old-package-compatibility.json`、`actual-package-upgrade.json`、`calendar-preview.json`。开发诊断的旧日志不会替代最终日志。

新包名称为 `vps-kit-0.4.0a1.zip`；最终 ZIP 摘要、成员核对和可重复构建结果记录在包外 `release-validation.json` 及同名 `.sha256` 文件，避免将包自身摘要写进包内文档形成循环。旧包摘要仍为 `1b58972a06ca7052774957bb188df091c73ae7a867e3302ef69e563c3e5b4972`。ZIP 与 manifest 摘要证明本地内容一致，不能代替作者签名或生产验收。

## 验证映射

| E0.2 用例 | 本地入口与证据 |
|---|---|
| V01/V02 | `test_vmrack.FixedCalendarTests`：配置 v1/v2、三套餐、24 个间隔、远期 first、负序号、溢出、短月/DST 原回归 |
| V03 | `test_vmrack_faults`：日唤醒与 24+24 业务点的原生命令替身、锚点本地输入、来源锁选择；日历预览分别输出 24 次日唤醒和 24 个业务边界 |
| V04/V05 | 原 P2 30 个落盘故障点及各步故障；固定周期新 pending 持久化矩阵、POST 前后失败、时间/NTP/程序/退役漂移、漏期阻断 |
| V06 | 原 P2 修改和稳定所有权；固定周期展示时区不改变授权、后续 revision 拒绝旧回退；非活动 owner 和未完成维护的审计资格为 BLOCKED |
| V07 | 双版本 16 Client 到额恢复、父池/个人限额分离、hold 和不限额 stop；仍不保证慢 API 可在窗口内完成 |
| V08/V09 | `test_vmrack.VMRackDeploymentTests`：35/49 逐对象 M1、M2 真实延迟再确认/取消、未知来源拒绝、cron 及持续退役漂移、部分安装回退 |
| V10 | `test_vmrack_upgrade`：能力格式转换、原月历原历史保留、pending 升级阻断、不兼容文件退出及有新账本后降级拒绝 |
| V11 | 原 134 项完整保留，不删除失败用例；旧包和冻结基线用于独立兼容核对 |
| V12 | 完整 traffic.2 后缀和旧来源/面板版本不符拒绝；无法仅凭版本字符串识别刻意伪装构建的边界保留 |

## 未达到的层级

Linux 文件权限、目录 fsync、真实 flock/inode 生命周期、systemd 日历与 cgroup、LoadCredential、真实面板 POST 顺序/并发/晚提交、代理业务和商家换期均未验收。Debian 13/12、Ubuntu 24.04 × 官方 3.7.0/3.10.2 的 L2/L3 仍为 NOT_RUN。旧模板正向迁移只覆盖已审核 3.7.0 来源；18 刀未知旧脚本及 traffic.2 正向写入不在支持范围内。
