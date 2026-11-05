# 合成数据与旧脚本兼容 fixture

`vmrack/` 是已获权利人授权按本项目 [MIT 许可证](../../../LICENSE)公开的旧月脚本测试材料：域名替换为 `.invalid`，不含 Token，测试只读取字节、不执行 Bash。35 刀 runner、service/timer 及 49 刀派生规则对应 [E1 来源契约](../../../doc/vmrack-contract.md) 的固定摘要。它们保留迁移识别所需的历史模板参数，不是随机生成的脚本，也不代表任何 VPS 当前安装；原六份 JSON 及其摘要保持不变，3.11.0 三份 JSON 的摘要追加到 SHA256SUMS。

本目录的 `3.7.0/`、`3.10.2/` 以及 [doc/fixtures/3.11.0](../../../doc/fixtures/3.11.0) 各包含 config、panel、ledger 三份 JSON。3.11.0 数据放在 a2 已允许的 `doc/` 包路径下，使回退后的 a2 能继续校验保留历史中的 a3 manifest；源码与发行包路径一致，不再生成 `examples/3.11.0.json`，直接使用该目录的 config.json 作为示例。SHA256SUMS 中的路径相对于本目录，三份新数据仅移动位置、原字节与摘要不变。

- 全部为合成数据，不是面板抓包或生产配置；`.invalid` 域名、零前缀 UUID 和零主机摘要仅供测试。
- panel 按精确 GET 路径索引，固定两个 Client，共 5 次逻辑读取。没有 Token 字段。
- 每版 embedded 故意省略 resetDay/trafficReset，3.10.2/3.11.0 保留必填 resetWeekday；独立 detail/traffic 不省略必需字段。
- 固定 panel 样例保留显式 `nodeId=null`；三版模型 omitempty 省略该键的常规输出，以及旧版远程 originNodeGuid 反例，由 [复查回归测试](../test_review_regressions.py) 单独覆盖。3.11.0 读取时注入本机 GUID、status 独立核验、远程及异常来源拒绝由 [来源回归](../test_origin_guid.py) 覆盖；固定 JSON 保留原字节。
- ledger 是合成“已完成记录”，不表示任何请求真的发送过；最终计数为正，以防把“永远为零”写成验收条件。
- [SHA256SUMS](SHA256SUMS) 固定样例原字节。测试同时核对原字节哈希和合成生成源语义；更新契约时应一起审核，不自动接受新哈希。

样例生成源是 [support.py](../support.py)，只供维护 fixture；常规测试和 CLI 不会重新生成或修改输入。P1 只做只读输出，用户不得将这些身份或边界照搬到实际部署。
