# 开发与贡献

欢迎提交可复现的问题、文档修正和有界的实现改进。`vps-kit` 目前为 Alpha；修改应保留版本适配、对象绑定、历史连续性与写请求边界。项目功能和限制见 [README](README.md)，接口见 [CLI 参考](doc/cli.md)。

## 本地开发环境

需要 Python 3.11+。源码位于 `src/`，测试使用标准库 `unittest`，无需安装项目或测试框架。运行期 Python 代码仅使用标准库；日期计算另需 IANA 时区数据。

Linux 通常通过系统 `tzdata` 提供时区数据。Windows 开发环境需要预先提供可供 `zoneinfo` 读取的 IANA 数据，例如开发环境中的 `tzdata` 包。依赖准备由开发者明确执行，工具与测试不会自动安装。不要以 UTC 样例替代有意覆盖 Toronto/DST 的用例来绕开缺失数据。

在仓库根目录运行完整离线回归：

```powershell
python -B tests/run.py
```

也可使用同一入口执行某个测试模块：

```powershell
python -B tests/run.py test_config.py
```

`tests/run.py` 拒绝测试进程的真实网络连接和监听，仅允许受控 Python 子进程。面板 POST、systemd 和目标系统操作使用替身；文件持久化测试使用临时文件。部分平台行为采用模型，不能据此声称真实 Linux 集成通过。完整回归可能需要十余分钟，请保留失败输出并先定位原因。

需要验证 curl 传输时，单独运行：

```powershell
python -B tests/loopback_transport.py
```

该测试要求已有 curl，临时监听 `127.0.0.1` 随机端口，发出 12 次本机合成 HTTP GET，结束后关闭监听。缺少 curl 返回 `NOT_RUN`。通过标识为 `LOOPBACK_TRANSPORT_PASS`，不表示真实 TLS、IPv6、面板或 systemd 集成已经验收。

Linux 可将上述 `python` 换成 `python3`。源码入口及测试入口会关闭 pyc 写入；任意绕过这些入口的导入方式不享有该保证。

## 提交问题或改动

普通问题请给出版本、环境、错误代码、预期行为及最小脱敏复现。安全问题按 [SECURITY.md](SECURITY.md) 处理。

改动前阅读相关契约与现有测试。配置/API/状态格式变化需要同步其契约及有实质价值的回归检查；文档修正使用与风险相称的验证。完整命令记录集中在 `doc/cli.md`，历史工作记录保持其版本与证据范围。

[JSON fixture](tests/traffic/fixtures/README.md) 及其 SHA256 是固定输入，常规测试不会重新生成。兼容测试中的旧脚本材料是获授权的脱敏来源，不执行 Bash；修改这些字节可能改变来源识别契约，需要一并评估摘要与旧包兼容性。

保留用户或其他贡献者的未提交工作。公共提交与发行包只包含明确的公开材料；本机报告、原始凭据、恢复副本和私人证据应留在忽略目录。不要用宽泛暂存混入这些内容，也不要覆盖固定版本的旧包来制造新的验证结果。

## 验证与许可

仓库 CI 配置位于 `.github/workflows/ci.yml`，使用 Ubuntu 24.04 / Python 3.11 与 Windows 2025 / Python 3.13 两个 hosted runner 组合，复用上述完整回归和回环测试入口。Windows CI 显式准备 `tzdata`；仓库权限为只读，不需要 VPS 凭据。该工作流文件不随离线 ZIP 分发。

CI 结果只证明对应测试环境，不代替 Debian 12/13、Ubuntu 24.04 与真实双版本面板验收。以实际 workflow 运行结果为准，添加配置不表示已经在 GitHub 运行。

提交贡献表示你有权按本项目 [MIT License](LICENSE) 提供相应内容。保留必要的第三方来源与许可证信息；独立 API 适配依据见 [源码证据](doc/p1-source-evidence.json)。
