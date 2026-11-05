# vps-kit 0.5.0a6 手册配套附件 r1

日期：2026-11-05。状态：本地修订附件，尚未发布；程序仍为已发布的 Alpha `0.5.0a6`。r1 只修复配套 tar.gz 的权限并更新说明，未重建程序 ZIP。此次本地修订没有操作 VPS。

## 固定来源与八文件清单

[固定 Release](https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a6) 的来源提交为 `7431bb0cb833e268c6fff3c4657a08c4200d3a5e`。命名程序 ZIP 和发布摘要沿用已核验的下载原字节，不使用 latest 或 Source code 归档。

| 文件 | 用途 |
|---|---|
| `vps-kit-0.5.0a6.zip` | 发布程序原件，461145 bytes、111 个载荷文件另加 manifest |
| `vps-kit-0.5.0a6.zip.sha256` | 发布摘要原件 |
| `prepare-config.py` | 匹配 a6 的首次配置采集附件，只 GET，不安装、清零或启用 |
| `vmiss-monthly.template.json` | VMISS 月日历模板，默认 Core；Basic 按本机配额和时间填写 |
| `vmrack-fixed.template.json` | VMRack 固定 30 天模板，默认 35 刀；其他实例按本机事实填写 |
| `README.md` | 本说明 |
| `VALIDATION.md` | 修复、核验及现场证据边界 |
| `SHA256SUMS` | 其余七文件的摘要 |

程序 ZIP SHA256：`f426ecf4cc605947f48989d0185ae1985223b53eb8a64757cf3b28f285209bcf`。helper 与两份模板沿用原 a6 字节，配额、网络和账期未变。固定摘要证明完整性，不等于作者数字签名，`author_authentication=NOT_PROVEN` 保持原义。外层及清单自身摘要见随附手册，不能用同版本字符串替代摘要。

## 权限与首次安装入口

旧配套归档继承 Windows metadata，目录 `0777`、文件 `0666`，Basic 生成首次安装计划时触发 `JOURNAL_DIRECTORY_UNSAFE`。只改目录仍会留下 ZIP 文件权限问题；不能放宽程序 guard 或递归修改系统目录来绕过。

r1 使用独立目录 `vps-kit-0.5.0a6-release-support-r1`。tar 头固定 root:root、目录 `0700`、八个普通文件 `0600`，不含链接。操作者按手册第 13.2 节在 root 会话先校验外层摘要，再以 `umask 077` 和 `--no-same-owner --no-same-permissions` 解包，核对目录、成员、所有权及模式后校验 SHA256SUMS，最后才运行 helper/程序。工作目录已存在时检查进度，不覆盖或删除。

完全新装按手册第 13.2—13.4 节完成配置采集、暂停安装、审计、受控备份与单独启用。原 a6 配套已解包、候选/Token/确认文件已保存而尚未安装者，按手册的接续分支保留这些文件：独立解包并核验 r1，沿用已核验的 a6 kit，改用 r1 中字节相同的程序 ZIP，保存新文件名的暂停计划；不重复创建凭据或任务，不覆盖旧计划。

已受管安装的 Basic a6 和 Core a5 不因附件 r1 重装或重复 resume。程序升级/回退需要独立的整包事务、当前配置和完整历史审计，不能以首次安装覆盖 `current`，不能删除账本。程序版本与 ZIP 未变，r1 不构成 runtime 升级。

## 历史与验收边界

旧 a6 tar.gz（462254 bytes）SHA256：`d93298ac7c990003a15980abbb26c14e11c8dfcc2d0294952d1a68053e592652`；旧清单 SHA256：`5c3afb07bd4391f70bf6d8c10d272d28a0bf0c9c966cb4aa6c1d7ac6949de152`。原包、摘要及原说明作为历史保留，不重写历史结果。a3/a4/a5 旧材料同样保留；回退 a4/a3 会恢复相应首次安装/来源缺陷，a2 不支持 3.11.0 配置。

Basic 已由用户回传 a6 暂停安装、审计、只读检查、启用及下一触发核对结果，使用的是手动修正权限后的旧附件，不能据此宣称 r1 已在 Linux 上实测。Core 现场记录仍属 a5，VMRack 无对应现场结果。**自然账期重置、实际写入授权/执行、计数读回及业务账本完成仍未验收。**详见 [验证记录](VALIDATION.md)。
