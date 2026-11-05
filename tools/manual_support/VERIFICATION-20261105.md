# a6 配套 r1 本地交付自检记录

日期：2026-11-05。仓库检查基线：`7431bb0cb833e268c6fff3c4657a08c4200d3a5e`。本次仅本地修改与验证，未提交、推送、发布或操作 VPS。原 a6 发布 ZIP 与程序源码不变；新配套版本为 `release-support-r1`，不是新的程序版本。

## 最终制品

| 项目 | 最终值 |
|---|---|
| 配套归档 | `vps-kit-0.5.0a6-release-support-r1.tar.gz`，462773 bytes |
| 外层 SHA256 | `16e5d46230fadbdcc5f110344d6c03b4b9012e3c6b0982fec77a984c76e6857a` |
| SHA256SUMS 自身 SHA256 | `0df19ea8a94451fdcf624b3ee3b0475590bfe7e0a76d2d48d8da9fa9dde6f7ee` |
| 程序 ZIP SHA256 | `f426ecf4cc605947f48989d0185ae1985223b53eb8a64757cf3b28f285209bcf`，与原件相同 |
| tar 成员 | 一个目录0700、八个普通文件0600；root:root，uid/gid均0，无链接 |
| 旧 a6 外层 SHA256 | `d93298ac7c990003a15980abbb26c14e11c8dfcc2d0294952d1a68053e592652`，原包及摘要保留 |

程序 ZIP、发布摘要、helper 和两个模板五文件与旧 a6 配套逐字节相同。新 README/VALIDATION 与 repo payload 逐字节相同。tar 成员内容与交付目录一致，七项清单及清单自身、外层摘要分别核对。用同一输入重建的归档字节一致；不以版本字符串替代摘要。

## 本轮实际运行的检查

| 检查 | 结果 |
|---|---|
| 归档回归 `python -B tools/manual_support/test_build.py` | 9项中8项通过，1项Linux root真实解包测试在Windows明确跳过 |
| 原归档实际 metadata → 真实 `DurableFiles.directory` | 再现 `JOURNAL_DIRECTORY_UNSAFE`；新归档metadata通过。POSIX和目录stat采用模型，不能冒充原生解包 |
| 固定 a6 ZIP 包契约及 manifest | 通过；program_sha256与固定发布ZIP及Basic回传相符 |
| 仓库既有 `python -B tests/run.py test_repository.py` | 8项通过，包含公开包完整性/文档链接、Python3.11语法及只读适配边界 |
| r1 中实际 helper/模板的合成回归 | 5项通过，覆盖Core/Basic/VMRack35/49/18配额与账期、已有候选保护、远程及无效来源拒绝 |
| 两份完整手册命令块 | 100个Bash语法检查、19个PowerShell AST检查、12个Python heredoc、2个Python代码块通过；JSON块也通过 |
| 手册实际 CLI 参数 | 40条命令使用固定a6 ZIP的parser解析通过；未执行部署或重置 |
| 手册及Core/Basic记录引用 | 409处章节引用、57个本地链接通过；新配套与repo说明的本地链接另核对 |
| 差异范围和不变量 | 两份手册仅版本/状态头和第13章变更；13.1原文、13.3配额字段表、其他网络章节、编码及CRLF保持 |
| 历史保护 | 47个旧文件摘要一致，含原a6目录/归档/摘要、a3/a4/a5材料及Core原记录；恢复回原路径的37个文件与旧基线摘要一致 |

新增CI调用：Windows运行打包回归；Linux runner通过root运行真实tar解包与`protected_bytes`测试（含旧目录0777和文件0666拒绝路径）。本次未推送，**没有产生此次修改的CI运行结果**。上述本地结果不等于CI已通过。

## 证据边界与自检修正

Basic已回传a6暂停安装、部署审计、4GET只读检查、resume和enabled/active/waiting；它使用修正权限后的旧附件。Core结果仍属a5；VMRack无对应现场结果。详细Basic阶段与未验收项见 [配套验证记录](payload/VALIDATION.md)；完整脱敏实例记录和两份手册在本地交付目录保留。

**自然账期重置仍未验收。** Basic的write_authorization_and_execution为NOT_TESTED，systemd_journal_and_business_acceptance为NOT_PROVEN，尚无完成期；未取得备份转存或恢复演练结果。此次r1的真实Linux解包/新装也未实测。

自检中修正了配套README仍指向旧目录chmod、而手册已改用独立r1接续的说明不一致；最终归档、手册及本记录摘要均为上表最终值。旧记录当时的“没有Basic结果”保留为历史，不被修改成后验成功。

最终生成物保存在仓库被忽略的 `.tmp/local/a6-support-r1-final-20261105/`；原a6外层归档及摘要副本保存在 `.tmp/local/a6-support-r1-history-20261105/`。先前同轮构建目录仅是修订中间证据，不是最终交付入口。未覆盖任何已发布资产。
