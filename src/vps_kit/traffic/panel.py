"""两个 tag 的显式读契约。不存在 POST、setEnable 或重置入口。"""
from dataclasses import dataclass
from enum import Enum
from urllib.parse import quote

from .config import EMAIL, VERSIONS
from .contracts import boolean, integer, json_load, require


class ReadEndpoint(Enum):
    INBOUNDS = "inbounds"
    CLIENT = "client"
    TRAFFIC = "traffic"

    def path(self, email=None):
        if self is ReadEndpoint.INBOUNDS:
            require(email is None, "API_ROUTE_ARGUMENT")
            return "/panel/api/inbounds/list"
        require(type(email) is str and EMAIL.fullmatch(email), "API_EMAIL")
        branch = "get" if self is ReadEndpoint.CLIENT else "traffic"
        return "/panel/api/clients/" + branch + "/" + quote(email, safe="")


def unwrap(raw):
    d = json_load(raw)
    require(type(d) is dict and d.get("success") is True and "obj" in d, "API_ENVELOPE")
    return d["obj"]


def used(d):
    require(type(d) is dict, "API_OBJECT")
    amount = integer(d.get("up"), "API_UP") + integer(d.get("down"), "API_DOWN")
    return integer(amount, "API_COUNTER_SUM_OVERFLOW")


def recovery(policy, quota, amount, enabled, traffic_enabled):
    boolean(enabled, "API_ENABLE_TYPE")
    boolean(traffic_enabled, "API_TRAFFIC_ENABLE_TYPE")
    if policy == "hold":
        require(not enabled and not traffic_enabled, "POLICY_HOLD_ENABLED")
        return "hold"
    if policy == "stop":
        require(enabled and traffic_enabled, "POLICY_STOP_DISABLED")
        return "enabled"
    require(policy == "quota-recovery" and quota > 0, "POLICY_RECOVERY_QUOTA")
    require((enabled and traffic_enabled) or amount >= quota, "POLICY_DISABLED_BELOW_QUOTA")
    return "enabled" if enabled and traffic_enabled else "quota-recovery-eligible"


@dataclass(frozen=True)
class Adapter:
    version: str
    weekly_required: bool
    future_client_reset_order: str

    def periods(self, obj, representation):
        require(type(obj) is dict, "API_OBJECT")
        for field in ("reset", "resetDay", "expiryTime"):
            if representation == "embedded" and field == "resetDay" and field not in obj:
                continue
            require(type(obj.get(field)) is int and obj[field] == 0, "API_PERIOD_" + representation.upper() + "_" + field)
        if representation != "traffic":
            if not (representation == "embedded" and "trafficReset" not in obj):
                require(obj.get("trafficReset") == "never", "API_INTERNAL_RESET")
        if self.weekly_required or "resetWeekday" in obj:
            if representation == "embedded" and self.weekly_required and "resetWeekday" not in obj:
                require(False, "EMBEDDED_WEEKDAY_MIGRATION_UNVERIFIED")
            require(type(obj.get("resetWeekday")) is int and obj["resetWeekday"] == 0, "API_RESET_WEEKDAY")

    def inbound(self, raw, config, expected_enable=None):
        d = config.data
        wanted = d["inbound"]
        all_inbounds = unwrap(raw)
        require(type(all_inbounds) is list and all(type(i) is dict for i in all_inbounds), "API_INBOUNDS_LIST")
        for i in all_inbounds:
            integer(i.get("id"), "API_INBOUND_ID", 1)
        require(len({i["id"] for i in all_inbounds}) == len(all_inbounds), "API_INBOUND_DUPLICATE")
        matches = [i for i in all_inbounds if i["id"] == wanted["id"]]
        require(len(matches) == 1, "API_BOUND_INBOUND_MISSING")
        i = matches[0]
        for k in ("remark", "listen", "port", "protocol"):
            require(type(i.get(k)) is type(wanted[k]) and i[k] == wanted[k], "API_INBOUND_BINDING")
        # 两个 tag 的 NodeID/OriginNodeGuid 均为 omitempty；缺键是明确的序列化行为。
        # 只排除响应中可见的远程关联，不能单凭这两项证明整套本地计量拓扑。
        require(i.get("nodeId") is None, "API_REMOTE_NODE")
        require(i.get("originNodeGuid", "") == "", "API_REMOTE_ORIGIN")
        require(integer(i.get("total"), "API_INBOUND_QUOTA", 1) == wanted["quota_bytes"], "API_INBOUND_QUOTA_MISMATCH")
        require(type(i.get("expiryTime")) is int and i["expiryTime"] == 0 and i.get("trafficReset") == "never", "API_INBOUND_PERIOD")
        amount = used(i)
        if expected_enable is None:
            condition = recovery(wanted["policy"], wanted["quota_bytes"], amount, i.get("enable"), i.get("enable"))
        else:
            boolean(expected_enable, "PHASE_EXPECTATION")
            boolean(i.get("enable"), "API_ENABLE_TYPE")
            require(i["enable"] is expected_enable, "API_INBOUND_PHASE_DRIFT")
            condition = "phase-matched"
        # 两个 tag 的 API 可能把 JSON 文本列转换成对象；仅接受这两种表示。
        settings = json_load(i["settings"]) if type(i.get("settings")) is str else i.get("settings")
        require(type(settings) is dict and type(settings.get("clients")) is list, "API_SETTINGS")
        embedded = settings["clients"]
        require(len(embedded) == len(d["clients"]) and all(type(c) is dict for c in embedded), "API_CLIENT_SET")
        require(all(type(c.get("email")) is str for c in embedded), "API_CLIENT_SET")
        require({c["email"] for c in embedded} == {c["email"] for c in d["clients"]}, "API_CLIENT_SET")
        indexed = {c["email"]: c for c in embedded}
        for c in d["clients"]:
            e = indexed[c["email"]]
            require(e.get("id") == c["uuid"], "API_EMBEDDED_UUID")
            require(integer(e.get("totalGB"), "API_EMBEDDED_QUOTA") == c["quota_bytes"], "API_EMBEDDED_QUOTA_MISMATCH")
            boolean(e.get("enable"), "API_EMBEDDED_ENABLE")
            self.periods(e, "embedded")
        return indexed, {"enabled": i["enable"], "used_bytes": amount, "policy_result": condition,
                         "other_inbound_count": len(all_inbounds) - 1}

    def client_detail(self, embedded, detail_raw, client, inbound_id):
        detail = unwrap(detail_raw)
        require(type(detail) is dict and type(detail.get("client")) is dict, "API_CLIENT_DETAIL")
        require(type(detail.get("inboundIds")) is list and len(detail["inboundIds"]) == 1
                and type(detail["inboundIds"][0]) is int and detail["inboundIds"][0] == inbound_id, "API_CLIENT_ASSOCIATION")
        record = detail["client"]
        require(integer(record.get("id"), "API_CLIENT_ID", 1) == client["record_id"]
                and record.get("uuid") == client["uuid"] and record.get("email") == client["email"], "API_CLIENT_BINDING")
        require(integer(record.get("totalGB"), "API_CLIENT_QUOTA") == client["quota_bytes"], "API_CLIENT_QUOTA_MISMATCH")
        self.periods(record, "detail")
        boolean(record.get("enable"), "API_CLIENT_ENABLE")
        require(record["enable"] is embedded["enable"], "API_CONFIG_ENABLE_MISMATCH")
        return record["enable"]

    def client_traffic(self, config_enabled, traffic_raw, client, expected_states=None):
        t = unwrap(traffic_raw)
        require(type(t) is dict and t.get("email") == client["email"], "API_TRAFFIC_BINDING")
        if "uuid" in t:
            require(type(t["uuid"]) is str and t["uuid"] in ("", client["uuid"]), "API_TRAFFIC_UUID")
        require(integer(t.get("total"), "API_TRAFFIC_QUOTA") == client["quota_bytes"], "API_TRAFFIC_QUOTA_MISMATCH")
        self.periods(t, "traffic")
        amount = used(t)
        if expected_states is None:
            condition = recovery(client["policy"], client["quota_bytes"], amount, config_enabled, t.get("enable"))
        else:
            boolean(t.get("enable"), "API_TRAFFIC_ENABLE_TYPE")
            require((config_enabled, t["enable"]) == expected_states, "API_CLIENT_PHASE_DRIFT")
            condition = "phase-matched"
        return {"config_enabled": config_enabled, "traffic_enabled": t["enable"],
                "used_bytes": amount, "quota_bytes": client["quota_bytes"], "policy_result": condition}


ADAPTERS = {"3.7.0": Adapter("3.7.0", False, "enable-then-reset"),
            "3.10.2": Adapter("3.10.2", True, "reset-then-enable")}


def get_adapter(version):
    require(type(version) is str and version in VERSIONS, "PANEL_VERSION_UNSUPPORTED")
    return ADAPTERS[version]
