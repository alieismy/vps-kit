# CLI 参考

当前版本为 `0.5.0a3`，Alpha 预发布，精确适配官方 `3.7.0`、`3.10.2`、`3.11.0`。本页安装指南固定为该版本；新版本范围见 [适配说明](3x-ui-3.11.0-compatibility.md)。在仓库根目录使用 `python -B bin/vps-kit <工具> <动作>`，或在 Linux 中使用 `python3 -B`。查看具体参数：

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
| `traffic discover --config … --fixture …` | 三版本合成快照核对 | 无 HTTP、无系统命令 |
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

按下文 [VPS 安装与七地区人工复验](#vps-安装与七地区人工复验)执行：独立目录安装 → 当地 DNS 逐地址探测 → 固定 IP/SNI 重复连接 → 独立 HTTP 检查 → 实际 Xray/客户端验收。每一步分别记录状态，不能用 TLS 通过替代后两步。安装/复验示例集中在该节，接口含义以上文为准。

2026-11-01 的修复前快照已取得 Windows 本机测试，以及 Debian 13 / CPython 3.13.5 / OpenSSL 3.5.7 的运行与合成 TLS 回环证据；另从一个美国出口对 30 个命名域名做了有限候选研究，并使用外部 curl 检查部分 HTTP 行为。这些是限定环境与地址的测量，不是全部 DNS 地址、其他地区 VPS、实际 REALITY 或生产业务验收，也不作为修复后源码的公网复测记录。

提前关闭分类修复后的完整离线回归在 Windows 下 210 项全部通过。合成 TLS 回环共 15 项：Windows / CPython 3.13.12 / OpenSSL 3.0.18 下 14 项通过、1 项因原生 IPv6 回环 TCP 不可用跳过；上述 Debian 13 环境下 15 项全部通过，包含 FIN、握手前 `close_notify`、RST 及 IPv6。既有 curl 回环的 12 次本机 GET 也通过。Debian 测试由 SSH 标准输入在内存中加载源码，只临时生成合成测试证书并清理，没有部署程序、变更系统信任库或生产配置。

修复后的同一程序源码另于 2026-11-01 从上述美国出口完成 56 个命名域名的有限初筛，共 153 次探针连接尝试；其中 37 个域名至少一个 IPv4 通过。随后对选中的 8 组固定 IPv4/SNI 各作三轮复验，24/24 通过，并用独立 curl 检查同一配对的 HTTP 行为。该出口没有 IPv6 默认路由，相关 IPv6 未完成项不能视为网站不兼容；这些结果也不证明各地区实际路径、全部 DNS 地址、物理机房城市或真实 REALITY 连接。原始本机研究日志不随公开源码和发行包分发；下文只整理供人工复验的条件候选，不自动采用。最终发布提交的 CI 与附件校验以 [发布说明](https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a1) 为准。

## VPS 安装与七地区人工复验

本节面向自行维护 VPS 的操作者，初稿整理于 **2026-11-01**，安装命令于 **2026-11-04** 同步为 `v0.5.0a3`。历史 `v0.5.0a1` ZIP 保持原字节，不包含本节后补内容；本节没有改变 CLI、P3 契约或生产验收状态。命令使用下列明确版本，不自动追随未来版本。

只检测 SNI 时，建议安装到当前用户的独立目录 `$HOME/vps-kit-probe/0.5.0a3`。探针不需要 root、面板 Token、pip 安装或 systemd 服务，不会修改现役 `traffic`、3X-UI 或 Xray；命令示例会下载文件、保存本地报告，并在明确探测时发起网络连接。若由 root 执行，默认目录为 `/root/vps-kit-probe/0.5.0a3`。

| 现场情况 | 本节采用的操作 |
|---|---|
| 首次使用，只检测 SNI | 独立目录安装后直接运行 |
| 旧版也是解压运行 | 保留旧目录，另装 `0.5.0a3`，改用新入口；回退时改回旧入口 |
| 已有受管 `traffic`，此次只检测 SNI | 并行使用独立目录，不改变现役版本 |
| 需要升级受管的整套工具 | 执行下文 P3 整包升级；禁止直接覆盖现役源码或手工切换 `current` |

以下命令使用 **Linux Bash**，各代码块应按步骤运行；标明人工输入的步骤需要先阅读前一步报告。依赖安装示例适用于 Debian 12/13、Ubuntu 24.04，不能据此认为这些 OS 的真实 `traffic` 集成已经验收。

### 1. 环境准备与独立安装

先查看系统、UTC 时间、Python 实际链接的 OpenSSL 和 curl 功能。探针运行要求见前文；curl 只用于下载和独立 HTTP 复验，不是 Python 探针的运行依赖。

```bash
cat /etc/os-release
date -u
python3 -c 'import sys, ssl; print(sys.version); print(ssl.OPENSSL_VERSION); print("TLS1.3:", ssl.HAS_TLSv1_3, "ALPN:", ssl.HAS_ALPN)'
curl --version
```

缺少依赖时，在 Debian/Ubuntu 以 root 执行以下命令；普通用户需由管理员准备。它不执行整机发行版升级，也不保证发行版提供的 Python/OpenSSL 组合必然满足探针能力检查。

```bash
apt-get update && apt-get install -y python3 ca-certificates curl
```

打开 [v0.5.0a3 发布页](https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a3)，复制正文中的 **该版本 ZIP SHA256**，在下面提示时粘贴；不要使用历史版本摘要。命令下载两个命名附件，不用自动生成的 `Source code` 归档代替。摘要用于核对内容，不是数字签名。

下面整段在子 shell 中执行，任一步失败即停止；已存在的版本目录不覆盖。校验文件采用 CRLF，检查前只规范化其换行，不改 ZIP。

```bash
(
set -eu
umask 077

base="$HOME/vps-kit-probe"
version='0.5.0a3'
url='https://github.com/alieismy/vps-kit/releases/download/v0.5.0a3'
dest="$base/$version"

if [ -e "$dest" ] || [ -L "$dest" ]; then
  printf '目录已存在，请先核对：%s\n' "$dest"
  exit 1
fi

read -r -p '粘贴 v0.5.0a3 发布页的 ZIP SHA256：' sha
if [[ ! "$sha" =~ ^[0-9a-f]{64}$ ]]; then
  printf '需要发布页提供的 64 位小写十六进制摘要。\n' >&2
  exit 1
fi

mkdir -p "$base/downloads/$version"
cd "$base/downloads/$version"
curl -q -fL "$url/vps-kit-$version.zip" -o "vps-kit-$version.zip"
curl -q -fL "$url/vps-kit-$version.zip.sha256" -o "vps-kit-$version.zip.sha256"

printf '%s  %s\n' "$sha" "vps-kit-$version.zip" | sha256sum -c -
tr -d '\r' < "vps-kit-$version.zip.sha256" | sha256sum -c -

stage=$(mktemp -d "$base/.unpack-$version.XXXXXX")
python3 -B -m zipfile -e "vps-kit-$version.zip" "$stage"
python3 -B "$stage/bin/vps-kit" traffic package-verify \
  --package "$PWD/vps-kit-$version.zip" --expect-sha256 "$sha"
python3 -B "$stage/bin/vps-kit" --version

mv -T -- "$stage" "$dest"
printf '入口：%s/bin/vps-kit\n' "$dest"
)
```

预期两个摘要检查均为 `OK`，包校验返回 `PACKAGE_VALID`、`version=0.5.0a3`、`files=109`，版本命令输出 `0.5.0a3`。`traffic package-verify` 是只读包校验，不调用面板或 systemd。中途失败时保留下载文件和 `.unpack-*` 目录排查，不把它们视为安装成功，也不要覆盖现役目录重试。

设置本次终端入口；新开 SSH 会话后需重新设置该变量：

```bash
kit="$HOME/vps-kit-probe/0.5.0a3/bin/vps-kit"
python3 -B "$kit" --version
python3 -B "$kit" reality probe --help
```

仅使用独立探针时，安装和升级到这里完成。不要为此执行第 7 节的受管部署事务。

### 2. 七地区候选与证据状态

以下为**历史观测，不是当前可用性承诺**。2026-11-01 07:26–07:32 UTC，从**同一个美国出口**使用 Debian 13 / CPython 3.13.5 / OpenSSL 3.5.7 检查候选。八组固定 IPv4/SNI 各三轮通过；独立 HTTP 观察为固定同一 IP/SNI 的 **HEAD**，不是产品自身发送的 HTTP 请求。

当时使用修复后的开发包，包 SHA256 为 `5f451425be09d41238b6a1318cc988e022ee30b6f3c3a5d87eb65e8c664c1c2d`；发布核对已确认其程序源码与正式 `v0.5.0a1` 相同，包整体摘要因发布文档变化而不同。这个历史摘要不能用于校验正式下载附件。维护者保留原始测量与源码核对记录，公开文档只提供结果摘要，不包含测试 VPS 地址、SSH 信息或私有日志链接；外部读者应按本节独立复验。

域名 Target 统一为该行 **SNI 加 `:443`**。固定 Target 只用于复现旧测量，采用前先使用当地 DNS，再从新报告中选择实际地址。不得跨行混用 IP 和 SNI。

| 地区关联 | SNI | 历史固定 Target | 美国出口历史 TLS / HTTP HEAD | 当地路径状态与选择限制 |
|---|---|---|---|---|
| 洛杉矶 | `www.ipam.ucla.edu` | `128.97.46.8:443` | TLS 3/3；根路径 HTTP/2 200 | **待验证**；UCLA 自身网络，优先复验；物理机房城市未独立确认 |
| 圣何塞 | `www.thegoldenstateacademy.com` | `199.34.228.151:443` | TLS 3/3；根路径 HTTP/2 200 | **待验证**；Weebly 托管，学校有 San Jose 教学点，不能据此定位机房 |
| 哥伦布，俄亥俄州 | `www.osc.edu` | `23.185.0.4:443` | TLS 3/3；根路径 HTTP/2 200 | **待验证／条件备选**；Fastly CDN；坚持避开已识别 CDN 时不选此项 |
| 新加坡 | `mirror.sg.gs` | `163.47.176.15:443` | TLS 3/3；根路径 HTTP/2 200 | **待验证**；新加坡镜像候选，AS58867 名称含 Anycast，不保证各出口同一机房 |
| 东京 | `www.nii.ac.jp` | `153.120.132.42:443` | TLS 3/3；根路径 HTTP/2 200 | **待验证**；Sakura 托管，机构位于东京，机房城市未证实 |
| 东京备选 | `www.s.u-tokyo.ac.jp` | `202.230.235.116:443` | TLS 3/3；根路径 HTTP/2 301；同主机 `/ja/` 为 HTTP/2 200 | **待验证**；IDC Frontier 托管；同主机路径跳转需明确接受 |
| 法兰克福 | `www.uni-frankfurt.de` | `141.2.37.198:443` | TLS 3/3；根路径 HTTP/2 308；同主机 `/en` 为 HTTP/2 200 | **待验证**；大学自身网络；同主机路径跳转需明确接受 |
| 伦敦 | `www.lse.ac.uk` | `185.18.139.150:443` | TLS 3/3；根路径 HTTP/2 200 | **待验证**；Zengenti 托管，不能据 LSE 地址确认伦敦机房 |

八组配对的实际 REALITY 连接均为 **未验证**。历史出口没有 IPv6 默认路由；IPv6 未完成项不能证明目标不支持 IPv6。机构地址、ASN（自治系统编号）和 GeoIP 均不能单独证明服务器城市；本表网络归属也是历史查询结果，不是探针的输出。

地域/网络核对入口：[IPAM 联系页](https://www.ipam.ucla.edu/contact-us/)、[Golden State 官网](https://www.thegoldenstateacademy.com/)、[OSC 官网](https://www.osc.edu/)、[Arch 官方 sg.gs 镜像条目](https://archlinux.org/mirrors/sg.gs/)、[NII 地址](https://www.nii.ac.jp/en/about/access/)、[东大理学部地址](https://www.s.u-tokyo.ac.jp/ja/access.html)、[法兰克福大学](https://www.uni-frankfurt.de/en)、[LSE 联系页](https://www.lse.ac.uk/lse-information/contact-us)。可按测量 IP 查询 [RIPEstat network-info](https://stat.ripe.net/docs/data-api/api-endpoints/network-info)，但当前网页或 API 结果不重写历史观测，也不替代当地测试。

此前候选的反证也应保留：

- `registrar.osu.edu` 与 `www.asc.ohio-state.edu` 在上述修复后初筛中为 `BASELINE_FAIL / TLS_HANDSHAKE_FAILED`，不列为通过候选。`www.asc.ohio-state.ed` 少末尾 `u`，不是正确域名。当前没有已验证通过且避开已识别 CDN 的哥伦布候选。
- 同日较早的独立复验中，`events.comp.nus.edu.sg`、`www.ms.u-tokyo.ac.jp`、`two.wide.ad.jp`、`www.frankfurt-university.de` 选择 HTTP/1.1，`www.imperial.ac.uk` 未协商 ALPN，均未满足 h2 基线；`www.mrcctu.ucl.ac.uk` 的 TLS 1.3 握手失败。这些是当次观测，不是永久拒绝名单。
- 厂商黑名单属于额外选站政策，不能等同于 TLS 技术失败。探针不检测 CDN/ASN；云 ASN 也不能单独区分 CDN 与独立云主机。

### 3. 当地 DNS 探测与固定配对复验

在拟采用的每台 VPS 上运行。以下以新加坡为例，其他地区只替换 `sni` 为表中同一行的域名；东京两个候选分别运行。报告保存到每次新建的目录，默认不覆盖旧记录。

```bash
kit="$HOME/vps-kit-probe/0.5.0a3/bin/vps-kit"
sni='mirror.sg.gs'
umask 077
mkdir -p "$HOME/sni-retest"
run=$(mktemp -d "$HOME/sni-retest/${sni}.XXXXXX")

date -u '+%Y-%m-%dT%H:%M:%SZ' > "$run/time.txt"
cat /etc/os-release > "$run/os-release.txt"
rc=0
python3 -B "$kit" reality probe \
  --target "${sni}:443" --sni "$sni" --timeout 5 \
  > "$run/domain.json" || rc=$?
printf '%s\n' "$rc" > "$run/domain.exit"
cat "$run/domain.json"
printf '探针退出码=%s；记录目录=%s\n' "$rc" "$run"
```

先阅读 `results` 中各地址的 `address`、`port`、`sni`、`status` 和 `code`。**没有通过项时，不进入固定配对和 HTTP 检查；先按第 4 节排查。** 退出码 `1` 不等于全域名失败，例如它可能只由某个不可达的 IPv6 产生。探针不使用 HTTP/SOCKS 代理环境变量，但系统路由/TUN 仍会影响路径。

选定报告中一个 `BASELINE_PASS` 的地址后，在同一 Bash 会话输入该地址；IPv6 输入不带方括号。不要直接把历史 IP 当成当地当前解析结果。

```bash
read -r -p '输入 domain.json 中已通过的 address：' ip
target="${ip}:443"
case "$ip" in
  *:*) target="[${ip}]:443" ;;
esac

for n in 1 2 3; do
  rc=0
  python3 -B "$kit" reality probe \
    --target "$target" --sni "$sni" --timeout 5 \
    > "$run/fixed-$n.json" || rc=$?
  printf '%s\n' "$rc" > "$run/fixed-$n.exit"
  printf '第 %s 轮退出码=%s\n' "$n" "$rc"
  if [ "$rc" -ge 2 ]; then
    break
  fi
  if [ "$n" -lt 3 ]; then
    sleep 2
  fi
done
cat "$run"/fixed-*.json
```

这是操作者显式选择的三次独立测量，探针本身不自动重试。退出码为 `2`、`130` 或其他异常值时停止循环并排查；退出码 `1` 的记录完整保留，不被后一次通过覆盖。三轮通过只说明短期重复连接表现；采用前宜在另一时段再次测量，比较 `tcp_ms`、`tls_ms` 与失败情况，不设跨地区通用的低延迟阈值。

### 4. 结果判读与停止条件

退出码及状态定义见前文 [检查与结果边界](#检查与结果边界)。顶层 `result=PROBE_COMPLETE` 只说明流程完成；必须逐条检查 `results`，不能以它判定通过。

| 状态／原因 | 本次可以得出的结论 | 下一步 |
|---|---|---|
| `BASELINE_PASS / BASELINE_MATCH` | 同一地址、端口、SNI 的新握手满足既定基线 | 核对 `TLSv1.3`、`h2`、`certificate_verified=true`、`X25519_ONLY_FULL_HANDSHAKE`，再做独立 HTTP 检查 |
| `BASELINE_FAIL / ALPN_NOT_H2` | 本次未协商到 h2 | 不满足本仓库基线；不泛化为所有 REALITY 模式不可用 |
| `BASELINE_FAIL / TLS_HANDSHAKE_FAILED` | 严格条件下发生明确 TLS 错误 | 暂不采用该配对，必要时独立诊断协议、密钥交换或目标策略 |
| `BASELINE_FAIL / CERTIFICATE_VERIFY_FAILED` | 本次证书验证失败 | 检查系统时间、CA 信任库、SNI；不通过关闭验证绕过 |
| `INCOMPLETE / TLS_CONNECTION_FAILED` | 提前关闭、EOF 或重置，没有完整握手证据 | 保留未完成状态，检查路径并另时复验 |
| `INCOMPLETE / TCP_TIMEOUT`、`TLS_TIMEOUT`、`TCP_CONNECT_FAILED` | 本次路径连接或握手未完成 | 检查地址族、路由、可达性，不直接否定网站 TLS 能力 |
| `INCOMPLETE / DNS_LOOKUP_FAILED` | 当前系统解析失败 | 检查拼写和 DNS |
| `PROBE_RUNTIME_UNSUPPORTED`、`PROBE_TLS_CAPABILITY_UNAVAILABLE` 等环境错误 | 运行环境未满足要求 | 停止评价目标，先修复运行环境；不重试一批网站来绕开相同错误 |

`--timeout 5` 分别约束一次 TCP 和一次 TLS 阶段，不是整域名总时限，也不强制终止系统 DNS 等待。每域名最多 16 个地址；未覆盖部分为 `INCOMPLETE`。IPv4 成功、IPv6 未完成只能写成该 IPv4 配对通过，不能写成整域名通过。

### 5. 固定同一配对检查 HTTP

沿用第 3 节的 `sni`、`ip`、`run`。先运行 `curl --version`，确认 curl 7.73.0+、OpenSSL 后端和 `HTTP2` 功能；缺失时记录 HTTP 复验环境不满足，不将其算作站点失败。以下先发 HEAD，固定连接 IP，保留域名用于 TLS SNI、证书名称验证和 HTTP Host。

```bash
resolve_ip="$ip"
case "$ip" in
  *:*) resolve_ip="[$ip]" ;;
esac
rc=0
env -u SSLKEYLOGFILE curl -q --silent --show-error --head \
  --http2 --tlsv1.3 --tls-max 1.3 --curves X25519 \
  --noproxy '*' --proto '=https' --connect-timeout 5 --max-time 10 \
  --resolve "${sni}:443:${resolve_ip}" --output /dev/null \
  --write-out '%{json}\n' "https://${sni}/" \
  > "$run/http-head.json" || rc=$?
printf '%s\n' "$rc" > "$run/http-head.exit"
cat "$run/http-head.json"
printf 'curl 退出码=%s\n' "$rc"
```

`-q` 避免默认 `.curlrc` 干扰，`--noproxy '*'` 排除 curl 的代理配置，但不绕过系统路由/TUN。不要添加 `-k` 跳过证书验证，也不要添加 `-L` 自动跟随跳转。参数语义见 [curl 官方手册](https://curl.se/docs/manpage.html)。

| 字段 | 判读 |
|---|---|
| curl 退出码 | 应为 `0`；非零先检查错误，不能只凭 HTTP 状态码判断 |
| `remote_ip` | 应为选定的 IP；IPv6 文本压缩形式不同需按地址值比较 |
| `ssl_verify_result` | 应为 `0` |
| `http_version` | 应为 `2`；仅提供 `--http2` 不证明已使用 HTTP/2 |
| `http_code` | `200` 最直接；3xx、403、405 结合应用行为判断 |
| `redirect_url` | 无跳转通常为空；存在时检查是否跨域名或降级到 HTTP |

东大理学部历史上跳到同主机 `/ja/`，法兰克福大学跳到同主机 `/en`。若当地结果仍如此，可将 URL 改为该 HTTPS 路径单独检查，并使用新的报告文件名；**Target 始终是 `HOST:PORT`，不含 `/ja/`、`/en` 或 URL scheme**。同主机路径跳转是需明确接受的权衡，不等于“完全无跳转”。跨域名跳转时，将新主机作为独立候选重新测试，不自动继承原配对的结论。

HEAD 返回 `403/405` 是应用层观察，不能倒推 TLS 不兼容。必要时去掉 `--head` 做 GET，保留 10 秒总时限，并可加 `--max-filesize 1048576`，输出另存 `http-get.json`/`.exit`；遇到大小限制错误只表示响应未完整取得。较旧 curl 对未知长度响应的大小限制能力不同，不能承诺严格字节上限。不要下载镜像站的大文件作为选站检查。

手工 HTTP 结果独立记录，原探针 JSON 中的 `http=NOT_TESTED` 保持不变；HEAD 通过也不等于已经验证完整 GET 内容。

### 6. 实际 REALITY 验收与记录

当地 TLS 和 HTTP 满足选择标准后，再使用实际 Xray/客户端版本和指纹验收。服务端 Target 采用刚验证的 `域名:443` 或 `IP:443`，服务端允许的 SNI 包含同一个具体域名，客户端 SNI 与之相同。具体字段以现场版本为准，本节不提供覆盖现有节点的通用配置。

若采用域名 Target，应按实际 Xray 的 DNS/路由路径复核，并接受 DNS 地址后续变化；固定 IP 也可能因站点迁移失效。确认实际 REALITY 连接和客户端请求成功、服务端无对应握手错误、客户端没有意外回退到直连，再单独记录验收结果。短期 TLS/HTTP 成功不能证明长期稳定性、浏览器指纹等价、混合后量子密钥交换支持或抗封锁效果。

建议每台 VPS 使用下列记录格式；分享时移除 VPS 自身地址、账号、客户端身份和凭据，不上传完整生产配置。

```text
地区 / 测试时间（UTC）：
OS / Python / OpenSSL / curl：
工具版本 / 发布包 SHA256：
SNI / 当地 DNS 地址集合：
本次固定 Target / 实际连接 IP：
固定配对三轮状态、原因 / TCP 与 TLS 耗时：
HTTP 方法 / HTTP 版本 / 状态码 / 跳转：
CDN、同主机跳转等已接受权衡：
Xray / 客户端版本与指纹：
实际 REALITY：通过 / 失败 / 未测
证据文件 / 下次复验时间：
```

完成某台 VPS 的复验只更新该地区、该主机路径和该配对的记录，不把历史表中其他“待验证”行批量提升为通过。

### 7. 已有受管 traffic 的整包升级

**只在已由 P3 管理且确实需要升级整套工具时执行。** 普通解压目录、其他流量脚本或尚未接管的旧部署不适用 `--kind upgrade`。真实 Linux/systemd、面板及业务集成仍未验收；以下是已实现接口的操作步骤，不是生产升级已验证的声明。约束、异常及回退范围以 [P3 契约](p3-contract.md)为准。

前置条件：以 root 操作；新包和独立源码在 root 独占写入目录；已保存当前部署/配置及恢复材料；NTP 同步；维护窗口距业务周期边界至少 900 秒；没有活动重置 service、排队任务或未知调度。准备与当前活动修订完全一致的配置 JSON，不得用包内合成示例代替。升级不同时改配置、配额或 Token。

本节的 `/root/current-traffic.json`、`/root/upgrade-attestations.json` 是**操作者事先准备的现场文件名示例**，不是工具自动导出的文件。它们须为 root 所有、权限 `0600`，父目录不可由其他用户写入；ZIP 须为 `0600` 或 `0644`。人工确认文件仅在逐项确认 admin 权限、本地计量和没有其他清零源后写入：

```json
{"admin_scope":true,"local_accounting":true,"other_reset_jobs_absent":true}
```

下面 `<…>` 均为必须替换的现场值；各阶段成功并审阅结果后，才进入下一阶段。首先在 root 会话设置路径并只读检查：

```bash
kit='/root/vps-kit-probe/0.5.0a3/bin/vps-kit'
pkg='/root/vps-kit-probe/downloads/0.5.0a3/vps-kit-0.5.0a3.zip'
python3 -B "$kit" traffic deployment-audit --local
/usr/local/bin/vps-kit traffic check --local --task '<实际任务UUID>'
```

`deployment-audit` 应显示已有完整部署，`check` 无阻断；否则停止升级并核查。计划保存路径必须尚不存在。下面默认选择升级后暂停：

```bash
python3 -B "$kit" traffic deployment-plan \
  --local --kind upgrade \
  --config /root/current-traffic.json \
  --package "$pkg" \
  --package-sha256 '<v0.5.0a3发布页的ZIP-SHA256>' \
  --activation paused \
  --attestations /root/upgrade-attestations.json \
  --save /root/upgrade-0.5.0a3-plan.json
```

预期 `DEPLOYMENT_PLAN_SAVED`、`version=0.5.0a3`、`timer_changed=false`。保存 `operation_id` 和输出的 `sha256`，核对任务、程序版本、首个/下一合法边界及启用选择。计划不暂停 timer；apply 会暂停并按 `paused` 保持暂停。原本已启用且明确希望升级后恢复调度时，可在**生成计划时**选择 `enabled`；原本暂停只能选择 `paused`。

使用计划输出的 **`sha256` 字段**确认，而不是对 JSON 文件执行 `sha256sum` 的结果。升级 plan/apply 均不传 `--credential-file`：

```bash
python3 -B "$kit" traffic deployment-apply \
  --local --plan /root/upgrade-0.5.0a3-plan.json \
  --expect-sha256 '<计划输出的sha256>' \
  --package "$pkg"
```

只有 apply 成功后，才从稳定入口核对：

```bash
/usr/local/bin/vps-kit --version
/usr/local/bin/vps-kit traffic deployment-audit --local
/usr/local/bin/vps-kit traffic check --local --task '<实际任务UUID>'
```

预期版本 `0.5.0a3`、部署 `stage=complete`、任务检查无阻断，并核对 timer 是否符合计划。文件安装完成不能单独证明 timer 已恢复。若计划选择暂停且操作者确认恢复条件已经满足，再显式恢复；不要将本命令与 apply 连成无条件执行脚本：

```bash
/usr/local/bin/vps-kit traffic resume --local --task '<实际任务UUID>'
```

apply 失败时保留现场，先从独立入口运行 `deployment-audit`；不要直接重复 apply、删除日志或手工改 `current`。确需回退时，使用审计确认的最近部署操作 UUID：

```bash
python3 -B "$kit" traffic deployment-rollback \
  --local --operation '<最近部署操作UUID>' --task '<实际任务UUID>'
python3 -B "$kit" traffic deployment-audit --local
```

成功回退表示文件恢复，流量历史和账本保留，自动化保持暂停。回退只适用于契约允许的最近操作；未知文件改动、备份异常或日志阻断需要按 P3 契约排查，不能靠换 UUID 或删记录绕过。保留独立源码入口、旧发行目录和备份，直至部署与恢复状态核对完成。

### 本节命令核验范围

2026-11-01，本节初稿针对 `v0.5.0a1` 命令完成以下检查，证据层级分别记录。`v0.5.0a3` 已同步版本、安装目录和摘要输入方式；最终发布提交的命令核验、CI 和附件校验见 [本版本发布说明](https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a3)，不得将初稿检查自动视为新附件验证。

| 检查 | 结果与边界 |
|---|---|
| Bash 语法 | 13 个代码块通过 `bash -n`；不代表 apt、systemd 或现场文件已经执行 |
| CLI 参数 | 16 条工具命令模板通过当前参数解析器；现场 UUID、配置、权限、计划摘要及服务状态仍由运行时核验 |
| 下载、解压与入口 | Windows / Git Bash / CPython 的隔离目录中实际下载两个公开附件，核对摘要、解压、`package-verify` 和 `--version` 均通过；只替换安装基目录，并以现有 CPython 提供 `python3` 命令 |
| 安装失败边界 | 已有版本目录被拒绝且原文件不变；错误摘要在解压前停止 |
| 历史配对 | 八组域名/IP、每组三轮通过数与保存的原始记录一致；三个探针模块源码摘要与测量时一致 |
| 仓库文档与包链接 | 既有 8 项仓库检查通过，包括临时测试包内链接完整性；未更新公开附件 |
| 当地路径与实际升级 | 本次未探测公共候选、未执行 VPS 安装或 P3 事务；七地区当地路径、实际 REALITY 和真实 Linux/systemd 升级仍未验证 |


## 使用与证据边界

配置与字段规则见 [P1 契约](p1-contract.md)，月日历、控制及执行规则见 [P2 契约](p2-contract.md)，安装、r2 接管和回退见 [P3 契约](p3-contract.md)，固定周期与 VMRack 接管见 [扩展契约](vmrack-contract.md)。历史契约中的阶段号、版本和样例包名称按其原有时间点理解，当前公开准备变化见 [变更记录](../CHANGELOG.md)。

三个面板适配器只承认精确版本。`nodeId` 缺键/null 与其他字段的缺失规则不同；Client 周期字段和 Inbound 周期字段分别核对。`quota-recovery` 仅恢复该对象本人有限额度耗尽且策略允许的状态，入口满额不代表所有 Client 均可恢复。共享总池与商家预算分别核对。

GET 读取是分步快照；`script_writes=0` 不承诺面板没有访问日志。Windows 下共享锁和目录同步测试使用模型，真实 Linux flock、目录 fsync、systemd/cgroup、LoadCredential、TLS 和面板 POST 副作用仍需隔离验收。包摘要用于核对内容，不能单独证明作者身份。
