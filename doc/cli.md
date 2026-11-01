# CLI 参考

当前版本为 `0.5.0a1`，Alpha 预发布。在仓库根目录使用 `python -B bin/vps-kit <工具> <动作>`，或在 Linux 中使用 `python3 -B`。查看具体参数：

```powershell
python -B bin/vps-kit traffic
python -B bin/vps-kit traffic --help
python -B bin/vps-kit traffic validate-config --help
python -B bin/vps-kit reality probe --help
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
| `reality probe --target … --sni …` | 对明确 Target/SNI 配对逐地址检查候选 TLS 基线 | DNS/TCP/TLS 网络探测；不发送 HTTP、不修改配置 |
| `reality probe --from-scanner … [--port …]` | 读取固定格式 CSV 后复验有限候选，保留扫描 IP | 读取指定文件及 TCP/TLS 探测；不启动或下载扫描器 |

`discover` 当前针对**显式配置中已绑定的入口与完整 Client 集合**。不挑选同名 Remark 的第一条，也不将线上发现结果写回配置。P2/P3 的计划接收显式配置；交互式入口/Client 选择和友好向导尚未交付。

上述 `--local` 写接口仅完成本地契约验证，尚未取得真实 Linux/面板集成验收证据。r2 接管通过 `deployment-plan --kind migrate`，轮换凭据通过 `--kind credential`；没有 `run/reset` 通用清零入口。修改业务配置仍使用 revision+1 的 `plan → prepare → apply`。普通 P2 创建拒绝未经 P3 接管的 r2；已完成接管后的所有写管理和执行保留新锁→旧锁顺序，并核对旧入口退役。

选项必须使用完整名称；不接受 `--token`、`--loc` 等隐式缩写。`discover` 使用 `--token-file` 指定的受保护文件；P2 管理 GET 使用固定凭据文件，执行器使用 systemd LoadCredential。Token 不进入 argv、计划或账本。

## REALITY 候选探针

`reality probe` 检查候选在本次运行位置是否满足 TLS 1.3、X25519、ALPN h2 和证书验证基线。它只依赖 Python 标准库；能力基线为 CPython 3.11+ 链接 OpenSSL 3，且支持 TLS 1.3、ALPN、X25519 和可用的证书信任库。官方 CPython 建议使用 3.11.4 或更高版本：[3.11.4 的 OpenSSL 3 路径](https://github.com/python/cpython/blob/v3.11.4/Modules/_ssl.c)增加了本探针所需的 `SSL_CTX_set1_groups` 调用，[3.11.3](https://github.com/python/cpython/blob/v3.11.3/Modules/_ssl.c)仍使用旧路径。发行版可能回移植此能力，以实际能力检查为准，不按补丁版本一律拒绝。环境能力不足仅影响探针，不提高 `traffic` 的 Python 最低版本。支持状态以实际验证组合为准，不表示所有 Python/OS 组合已验收。

以下单目标地址为文档保留示例，不能作为已验证站点使用：

```text
python -B bin/vps-kit reality probe --target 192.0.2.10:443 --sni cover.example
python -B bin/vps-kit reality probe --from-scanner candidates.csv
python -B bin/vps-kit reality probe --from-scanner candidates.csv --port 8443 --timeout 5
```

单目标模式必须同时提供 `--target HOST:PORT` 和具体域名 `--sni NAME`；IPv6 Target 使用 `[ADDRESS]:PORT`。批量模式使用 `--from-scanner FILE`，与单目标参数互斥。`--port` 仅用于 CSV 模式，缺省为 443；RealiTLScanner CSV 不包含端口，因此导入文件本身不能证明原扫描端口。若原扫描使用非 443 端口，必须显式传入对应 `--port`。结果记录端口来源，缺省值标为 `scanner_cli_default`。

两种模式共同接受 `--timeout SECONDS`，默认 5，范围 0.1—30 秒。该值分别约束一次 TCP 连接和 TLS 握手，不是整个目标或整批的总时限。系统 DNS 解析遵循操作系统的等待行为，不能承诺被此参数强制终止。探针不使用 HTTP/SOCKS 代理设置；实际路径仍受操作系统路由和 TUN 影响，不能将本机结果当作另一台 VPS 的证据。

### 候选导入与连接范围

导入格式固定为 [RealiTLScanner v0.2.3](https://github.com/XTLS/RealiTLScanner/blob/e5e114747aa8c6dfa4b394a0782f241bf72bdce6/main.go#L64) 的 11 列 CSV：

```text
IP,ORIGIN,TLS,ALPN,CURVE,CERT_LENGTH,CERT_SIGNATURE,CERT_PUBLICKEY,CERT_DOMAIN,CERT_ISSUER,GEO_CODE
```

最多接收 100 个 CSV 数据行，文件上限为 256 KiB，且须为普通文件；先完整解析输入再开始连接。超过行数或文件大小上限，或者 CSV 头、结构、列数不符合时全批拒绝，不只处理前 100 行。语义异常的条目保留为未完成项，不对该行发起连接；不会回显原始 CSV 或证书内容。

- `IP` 保留为实际探测地址，不能通过候选域名重新解析到其他 IP。
- `ORIGIN` 是具体合法域名时，将其作为待验证 SNI；仅在 `ORIGIN` 为 IP/CIDR 时，才将具体合法的 `CERT_DOMAIN` 作为候选名称。其他异常 `ORIGIN` 拒绝探测该行，不自动猜测替代名称。
- 通配符、缺失或无法确定的名称需要人工补充具体 SNI。不能直接使用 `*.example.com`，也不能删除 `*.` 后假定证书覆盖主域名。
- 上游 TLS、ALPN 和 `feasible` 类结果只作来源信息，本探针重新检查配对，不把导入视为验证通过。

域名 Target 按本次 DNS 解析结果逐地址测试，每个域名最多测试 16 个唯一地址，整批最多进行 256 次连接，串行执行。超出地址或批次预算的部分明确报告 `INCOMPLETE`，不扩大到邻近 IP 或其他网段。IPv4/IPv6 分别记录；某个 IPv6 地址失败不等于整个域名在所有地址族都失败。未覆盖全部地址时不能把已测成功项提升为全域名通过。

### 检查与结果边界

每个地址使用明确端口和 SNI 建立新的 TLS 连接，在同次握手中检查 TLS 1.3、只提供 X25519 的密钥交换条件、h2，以及证书信任链、有效期和 SAN 主机名匹配。主机名验证不回退到证书 CN；不提供关闭证书验证的开关。证书错误只表示本次验证失败，不据此断言网站恶意或所有客户端都无法连接。

JSON 输出保留每个候选/地址的 `BASELINE_PASS`、`BASELINE_FAIL` 或 `INCOMPLETE`，并说明原因；包含测量时间、平台、Python/OpenSSL 版本、实际连接 IP，以及本次 TCP/TLS 耗时。名称、地址与结果绑定，失败和未测部分不会因其他项目成功而被抵消。单次时间不证明稳定性，不输出综合伪装评分。

握手完成前的 EOF、`close_notify` 或连接重置归为 `INCOMPLETE / TLS_CONNECTION_FAILED`，只表示该次握手未完成，不能据此判定目标不支持 TLS 基线；明确的 TLS 协议告警、证书验证失败及 ALPN 不匹配仍按基线失败报告。

HTTP 响应、重定向与实际 REALITY/Xray 连接始终标为 `NOT_TESTED`。ALPN 为 h2 只证明协商结果，不证明已发送 HTTP/2 请求。不检测 X25519MLKEM768、不模拟浏览器/uTLS 指纹，也不支持无 SNI 用法；未满足 h2 或 X25519 基线不能扩大为所有 REALITY 版本、模式均不可用。最终采用前应在实际目标 VPS 和 Xray/客户端版本上确认。

| 退出码 | 含义 |
|---|---|
| `0` | 非空候选的全部结果通过本次基线，且没有未完成项 |
| `1` | 探测结束，但有失败或未完成项 |
| `2` | 输入或环境错误，未形成可采用的本次基线结果 |
| `130` | 用户取消；保留已经完成的项及待处理计数 |

探针不读取面板 Token、不要求 root、不修改 3X-UI/Xray 配置或 systemd，不查询 ASN/GeoIP，不自动安装依赖、运行或下载扫描器。只输出报告，报告中的候选不是自动采用授权。

新增模块属于整包程序身份。已部署的流量工具需要通过 [P3 整包升级机制](p3-contract.md)登记新版本和摘要，不要将探针源码直接覆盖到活动版本目录。开发体验可以从独立源码目录运行。

### 采用前人工复验

1. 在拟采用的 VPS 上，从独立源码目录运行探针；记录系统、Python/OpenSSL 版本和时间。先用域名 Target 取得当地解析结果，再按具体的 `address`、端口和 SNI 阅读每条结果。`BASELINE_PASS` 只对该次配对有效；退出码 `1` 可能只是其中一个 IPv6 地址未完成，不能视为所有地址都失败。必要时用 `--target IP:PORT --sni NAME` 复验选中的配对。
2. 对通过的配对，另行检查 HTTP 版本和跳转。以下为 Linux Bash 示例，保留域名和文档地址必须替换成同一条通过记录的真实 SNI、IPv4 和端口。它使用外部 curl，要求已有 curl 7.73.0+、OpenSSL 后端及 HTTP2 功能；这不是探针自身的运行依赖。

   ```bash
   sni='cover.example'
   ip='192.0.2.10'
   port=443
   env -u SSLKEYLOGFILE curl -q --silent --show-error --head \
     --http2 --tlsv1.3 --tls-max 1.3 --curves X25519 \
     --noproxy '*' --proto '=https' --connect-timeout 5 --max-time 10 \
     --resolve "${sni}:${port}:${ip}" --output /dev/null \
     --write-out '%{json}\n' "https://${sni}:${port}/"
   ```

   `--resolve` 固定连接 IP，URL 主机名同时用于 TLS SNI、证书名称验证和 HTTP Host。检查 curl 退出状态、`remote_ip`、`ssl_verify_result=0` 及实际 `http_version=2`；仅写了 `--http2` 不表示协商成功。不要添加 `-k`。IPv6 需在 `--resolve` 的地址部分加方括号。
3. 记录 `http_code` 和 `redirect_url`；命令不自动跟随跳转。同主机路径跳转可单独检查该路径，Target 始终是 `HOST:PORT`，不含 URL 路径；跨域名跳转应将新域名作为独立候选重新验证。HEAD 的 `403/405` 是应用层观察，不能倒推 TLS 不兼容，必要时另做有界 GET 核验。手工 HTTP 结果不改变原探针报告中的 `http=NOT_TESTED`。
4. 在当地路径上复核候选的延迟和重复连接结果。机构地址、ASN 或 GeoIP 均不能单独证明物理机房城市，CDN/Anycast 的节点还可能随出口和解析变化。最后使用实际 Xray、客户端版本和指纹完成 REALITY 连接验证；短期 TLS/HTTP 成功不证明长期稳定性或抗封锁效果。

2026-11-01 的修复前快照已取得 Windows 本机测试，以及 Debian 13 / CPython 3.13.5 / OpenSSL 3.5.7 的运行与合成 TLS 回环证据；另从一个美国出口对 30 个命名域名做了有限候选研究，并使用外部 curl 检查部分 HTTP 行为。这些是限定环境与地址的测量，不是全部 DNS 地址、其他地区 VPS、实际 REALITY 或生产业务验收，也不作为修复后源码的公网复测记录。

提前关闭分类修复后的完整离线回归在 Windows 下 210 项全部通过。合成 TLS 回环共 15 项：Windows / CPython 3.13.12 / OpenSSL 3.0.18 下 14 项通过、1 项因原生 IPv6 回环 TCP 不可用跳过；上述 Debian 13 环境下 15 项全部通过，包含 FIN、握手前 `close_notify`、RST 及 IPv6。既有 curl 回环的 12 次本机 GET 也通过。Debian 测试由 SSH 标准输入在内存中加载源码，只临时生成合成测试证书并清理，没有部署程序、变更系统信任库或生产配置。

修复后的同一程序源码另于 2026-11-01 从上述美国出口完成 56 个命名域名的有限初筛，共 153 次探针连接尝试；其中 37 个域名至少一个 IPv4 通过。随后对选中的 8 组固定 IPv4/SNI 各作三轮复验，24/24 通过，并用独立 curl 检查同一配对的 HTTP 行为。该出口没有 IPv6 默认路由，相关 IPv6 未完成项不能视为网站不兼容；这些结果也不证明各地区实际路径、全部 DNS 地址、物理机房城市或真实 REALITY 连接。原始本机研究日志不随公开源码和发行包分发，不提供自动采用的候选名单。最终发布提交的 CI 与附件校验以 [发布说明](https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a1) 为准。


## 使用与证据边界

配置与字段规则见 [P1 契约](p1-contract.md)，月日历、控制及执行规则见 [P2 契约](p2-contract.md)，安装、r2 接管和回退见 [P3 契约](p3-contract.md)，固定周期与 VMRack 接管见 [扩展契约](vmrack-contract.md)。历史契约中的阶段号、版本和样例包名称按其原有时间点理解，当前公开准备变化见 [变更记录](../CHANGELOG.md)。

两个面板适配器只承认精确版本。`nodeId` 缺键/null 与其他字段的缺失规则不同；Client 周期字段和 Inbound 周期字段分别核对。`quota-recovery` 仅恢复该对象本人有限额度耗尽且策略允许的状态，入口满额不代表所有 Client 均可恢复。共享总池与商家预算分别核对。

GET 读取是分步快照；`script_writes=0` 不承诺面板没有访问日志。Windows 下共享锁和目录同步测试使用模型，真实 Linux flock、目录 fsync、systemd/cgroup、LoadCredential、TLS 和面板 POST 副作用仍需隔离验收。包摘要用于核对内容，不能单独证明作者身份。
