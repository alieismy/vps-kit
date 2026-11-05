# a6 手册配套附件构建

配套修订发布入口：[v0.5.0a6-support-r1](https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a6-support-r1)。该标签标识配套构建源码，程序版本仍是 `0.5.0a6`；不将配套标签当作程序版本或升级目标。最终 CI、下载核验及发布状态以该 Release 为准。

`release-support-r1` 修复 Windows 打包将目录/文件权限保存为 `0777/0666` 的问题。程序版本仍为 `0.5.0a6`，固定发布 ZIP 和摘要文件原字节不变；不修改安装器权限检查，不添加运行依赖。

`build.py` 为每个 tar 成员显式设置 root:root、目录 `0700`、文件 `0600`，固定顺序、时间和 gzip 头；不调用继承宿主 metadata 的 `tar.add`。本地展开目录在 Windows 上的 ACL 不等于 Linux POSIX mode，交付以 tar 头和目标解包检查为准。

## 本地生成

准备固定 Release 的 `vps-kit-0.5.0a6.zip` 和 `.zip.sha256`，在仓库根目录运行；将 `<已下载资产目录>` 替换为实际路径，输出目录必须尚不存在且父目录已存在：

```powershell
python -B tools/manual_support/test_build.py
python -B tools/manual_support/build.py --release-dir '<已下载资产目录>' --output-dir '.tmp/manual-support-r1'
```

构建器固定校验两个公开资产的摘要，拒绝错误输入、非普通文件和已有输出目录，不覆盖原包。输出新 tar.gz、外层摘要和八文件展开目录；新外层/清单摘要打印为 JSON，须同步到使用该归档的手册。失败现场保留供检查，不自动删除；更正原因后使用新输出目录。

模板与 helper 从原 a6 附件逐字节迁入 `payload/`；它们用于操作者首次采集配置，不属于产品 CLI 或命名程序 ZIP。`README.md`、`VALIDATION.md` 是新配套说明，内层 SHA256SUMS 由构建器生成。不能以 GitHub Source code 归档替代命名 ZIP。

helper 原件含 CRLF，`.gitattributes` 对此单文件设置 `-text`，回归检查固定原件 SHA256；不要对其执行换行归一化。提交后应从干净 Git 检出或导出目录重新构建，核对归档摘要，而不只验证当前工作区。

归档内说明与 [本地交付自检记录](VERIFICATION-20261105.md) 保留发布前的日期化证据快照，包含当时的“尚未发布”和 Windows 跳过项。发布阶段的新证据记录在 Release 中；保留这些快照可使已有手册固定的 r1 归档摘要继续有效。

## 回归与证据边界

`test_build.py` 位于工具目录，不混入产品 ZIP 的 `tests/**/*.py`，因此固定产品测试不依赖未分发的打包工具。测试覆盖 archive metadata、实际 `DurableFiles.directory` 对旧 0777 的拒绝、0700 的接受、成员白名单、确定性、错误发布资产及旧输出保护。Linux root 分支还用与手册相同的 tar 参数解包，并调用真实 `protected_bytes`，分别断言目录0777及文件0666被拒绝；Windows 会明确跳过该分支。

本次本地 Windows 核验和 Basic 用户回传的边界见 [配套验证记录](payload/VALIDATION.md)。Basic 是修正原附件权限后安装的 a6，不能当作 r1 归档的 Linux 解包实测。Core 证据属于 a5；VMRack 没有对应现场结果；自然账期重置均未验收。

本次最终摘要、实际检查结果和跳过项见 [2026-11-05 交付自检记录](VERIFICATION-20261105.md)。

旧 a6 tar.gz SHA256：`d93298ac7c990003a15980abbb26c14e11c8dfcc2d0294952d1a68053e592652`。旧归档及其 checksum/说明保留为历史，不能继续作为新装入口。r1 只修复交付封装；已经受管安装的机器无须重装、升级程序或重新 resume。
