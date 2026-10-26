# P2 本地验证记录

日期：2026-10-24；当前目标实现 `0.2.0a2 / P2-2`，复查基线为 `0.2.0a1 / P2-1`。验证范围为 L1 本地模拟与故障注入，控制契约见 [P2 契约](p2-contract.md)，修正明细见 [P2 复查记录](p2-review-20261024.md)。状态：P2 复查及 L1 本地复验通过；不代表 VPS 运行验收。

## 1. 执行环境与入口

Windows、Python 3.13.12。全部材料为 SYNTHETIC；没有读取实际 VPS 配置、Token 或数据库。标准测试入口：

```powershell
python -B tests/run.py
```

测试入口禁止网络连接、DNS 解析/监听和非受控 Python 子进程。P2 的 curl POST、systemctl、systemd-analyze 全部使用替身。临时状态文件使用实际文件 fsync 与 os.replace；Windows 的目录同步和锁显式使用模型。没有用模拟原子替换覆盖真实 os.replace 失败，也不宣称 Windows 模型证明 POSIX 持久性。

此外复用 P1 的独立只读传输验证：

```powershell
python -B tests/loopback_transport.py
```

该入口仅临时监听本机回环地址并发送合成 HTTP GET；不发送 POST，不访问 VPS。真实 TLS、IPv6 和面板仍未验证。

## 2. 测试覆盖与证据对应

| 范围 | 主要入口 | 验证内容 |
|---|---|---|
| P1 回归 | 原有 config/panel/state/io/review/repository 测试 | 双版字段、类型、配额/恢复条件、凭据、读取无副作用、fixture 固定摘要 |
| 基本执行 | `test_p2.py` | 两版正常完成/重复唤醒、首次边界、共享锁、全对象预检、改额保存旧账本、22→1 延后、暂停失败、remove 保留历史 |
| 持久化矩阵 | `test_p2_faults.py::test_persist_failure_matrix_blocks_replay` | pending、confirmed、completed × 记录/head × 5 个故障点，共 30 个场景 |
| 写入与恢复故障 | `test_p2_faults.py` | 响应丢失/可能延迟提交、先前目标状态漂移、非目标计数倒退、时间窗/NTP/墙钟/程序漂移、未来处置、漏期显式跳过、半安装/启用失败 |
| 多 Client | `test_p2_faults.py`、`test_p2_maintenance.py` | 1/2/16 Client、独立额度、stop 不限额、hold 保持禁用与计数、两版 Client/Inbound 到额恢复 |
| 日历与适配器 | `test_p2_native.py` | 400 年短月日期规则、有限表达式解析模型、systemd UTC 输出接口、固定 timer-only argv、POST 白名单、stdin Token、单次发送 |
| 维护取消与身份 | `test_p2_maintenance.py` | 恢复旧面板后取消 prepared、保留暂停、取消新建仍保留身份、禁止隐式启用/程序替换、慢 GET 后时钟跳变 |
| 只读审计 | `test_p2_faults.py`、CLI 启动守卫 | 不持有写锁、字节不变、损坏链拒绝、无隐式 pyc/网络/进程；结构有效不等于业务验收 |
| 本次复查回归 | `test_p2_review.py`（10 项） | 安装/启用锁间隙并发暂停、错误处置输入、预检失败保留更正路径、慢 verify 越过安全窗口、首次半安装审计及 CLI、非法控制序列、推迟首次边界、日历切换完整流程 |

五个故障点为 before_file_sync、after_file_sync、before_replace、after_replace、after_directory_sync。未完成 pending 提交时零 POST；confirmed 提交失败时保留已发送请求且不再发下一步；最终完成提交失败时若完整 head 已可读，则当月识别完成、不重发，否则阻断。后者不能粗略写成“每种失败都一定 pending”，也不能把下一合法周期的新请求算作旧周期重复。

初次扩展测试捕获发送前运行守卫返回后程序摘要变化的问题；已在 guard 返回后及读回/最终提交阶段重新核对程序摘要。还补齐了慢 GET 后的维护时钟/窗口复核、取消候选的旧契约检查、原生 pause 不依赖 x-ui active，以及 pending→confirmed 的单步提交约束。

适配器正向测试发现首次创建时空 unit 清单的解析错误，已修正为空行跳过、非空未知任务仍拒绝。增加显式写操作重建 `/run` 目录的模型测试，核对已有锁文件的字节和 inode 不变。resume 对已启用 timer 或不适用的 skip 参数在创建维护意图前拒绝，避免误操作污染正常历史。

## 3. 最终结果

| 门禁 | 实际结果 |
|---|---|
| P1 起点回归 | 52 项通过；开始时保存 37 份文件原字节快照 |
| 原 P2 0.2.0a1 全量基线 | 88 项通过，0 失败，0 跳过；历史测试运行 159.262 秒 |
| 本次 0.2.0a2 最终全量标准库测试 | **98 项通过，0 失败，0 跳过**；测试运行 204.176 秒 |
| 测试期间一致性 | 源码、测试、入口和项目元数据的 SHA256 前后相同 |
| 持久化故障矩阵 | 30 个场景通过，包含在上述测试方法内，不另加到 98 项 |
| 独立回环传输 | `LOOPBACK_TRANSPORT_PASS`；双版适配读取 10 次、总计 12 次本机 GET；不跟随重定向；Token 未进入 argv；VPS 请求 0 |
| Python 3.11 语法、版本与本地文档链接 | 通过；未增加运行依赖或 pyc |
| 原批准方案及旧 VMISS 材料 | 5 份保护文件 SHA256 与本轮开始时一致 |
| 差异、编码、格式及敏感字面量扫描 | 通过；修改文件保留 UTF-8 无 BOM、LF；无新增实际凭据字面量 |

原 P2 基线证据仍保存在忽略目录 `.tmp/local/p2-20261024/`，未覆盖。本次证据位于 `.tmp/local/p2-review-20261024/`：54 份开始快照 `before/` 与 `before-sha256.json`、修正前 `regressions-before.log` / `window-before.log`、首轮定点复验 `regressions-after.log`、最终全量 `tests-release.log`、44 份测试期间文件摘要 `tested-source-hashes.json`、`loopback.log`、`consistency.json`、`review.diff`。这些材料用于本地复现/恢复，不进入 Git 发行材料。最终测试后仅完善文档，代码与已验证摘要再次核对。

保护文件为批准的 v0.1.1 工具方案，以及旧 VMISS 主方案、r2 runner、build_config、manage。没有改动任何 VPS、计数、timer/service；没有执行真实 POST、SSH、部署或 P3 接管/回退。

## 4. 明确保留的未验证项

- L2：Debian 13、Debian 12、Ubuntu 24.04 真实 systemd、权限、flock、目录 fsync、断电/磁盘满、LoadCredential、重启/休眠/时钟校正。
- L3：官方 3X-UI 3.7.0 / 3.10.2 真实 API 写副作用、异步提交、内嵌迁移字段、Xray/代理业务影响与六组合自然账期。
- 未实现且未执行 P3：打包安装、r2 接管、双锁/退役、程序/配置回退及凭据轮换。
- 完整一致的外部快照回卷无法仅凭本机哈希链识别；GET 与 POST 之间没有服务端条件更新/幂等键，无法消除全部人工编辑竞态。
