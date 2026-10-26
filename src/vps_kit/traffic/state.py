"""纯账本解析：P1 历史 v1 与 P2 执行 v2；本模块不持久化或发送请求。"""
from .config import schedule_validate
from .contracts import boolean, fields, integer, json_load, require, utc
import re
from .panel import recovery

SCHEMA = "vps-kit.traffic.ledger/v1"
EXECUTION_SCHEMA = "vps-kit.traffic.ledger/v2"


def snapshot(value, client=False):
    fields(value, "used_bytes enabled traffic_enabled" if client else "used_bytes enabled", "STATE_SNAPSHOT_FIELDS")
    integer(value["used_bytes"], "STATE_COUNTER")
    boolean(value["enabled"], "STATE_ENABLE")
    if client:
        boolean(value["traffic_enabled"], "STATE_TRAFFIC_ENABLE")


def inspect_ledger(raw, config):
    state = json_load(raw)
    require(type(state) is dict, "STATE_FIELDS")
    execution = state.get("schema_version") == EXECUTION_SCHEMA
    fields(state, "schema_version task_id revision config_sha256 object_sha256 period_id boundary_utc created_at initial steps completed_at final" +
           (" invocation_id program_sha256" if execution else ""),
           "STATE_FIELDS")
    cfg = config.data
    require(state["schema_version"] in (SCHEMA, EXECUTION_SCHEMA), "STATE_SCHEMA")
    if execution:
        require(type(state["invocation_id"]) is str and re.fullmatch(r"[0-9a-f]{32}", state["invocation_id"]), "STATE_INVOCATION")
        require(type(state["program_sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", state["program_sha256"]), "STATE_PROGRAM")
    require(state["task_id"] == cfg["task_id"] and type(state["revision"]) is int
            and state["revision"] == cfg["revision"] and state["config_sha256"] == config.fingerprint
            and state["object_sha256"] == config.object_fingerprint, "STATE_BINDING")
    integer(state["period_id"], "STATE_PERIOD_ID", 1)
    boundary = utc(state["boundary_utc"], "STATE_BOUNDARY")
    schedule_validate({**cfg["schedule"], "first_boundary_utc": state["boundary_utc"]})
    created = utc(state["created_at"], "STATE_CREATED")
    require(boundary >= utc(cfg["schedule"]["first_boundary_utc"], "STATE_FIRST_BOUNDARY")
            and created >= boundary, "STATE_TIME_ORDER")
    clients = cfg["clients"]
    keys = ["client:" + str(c["record_id"]) for c in clients]
    initial = fields(state["initial"], " ".join([*keys, "inbound"]), "STATE_INITIAL_FIELDS")
    for k in keys:
        snapshot(initial[k], client=True)
    snapshot(initial["inbound"])
    for key, client in zip(keys, clients):
        s = initial[key]
        recovery(client["policy"], client["quota_bytes"], s["used_bytes"], s["enabled"], s["traffic_enabled"])
    s = initial["inbound"]
    recovery(cfg["inbound"]["policy"], cfg["inbound"]["quota_bytes"], s["used_bytes"], s["enabled"], s["enabled"])
    expected = [*keys, "inbound", "enable"]
    require(type(state["steps"]) is list and len(state["steps"]) == len(expected), "STATE_STEPS")
    before = created
    stopped = False
    any_intent = False
    all_done = True
    for key, step in zip(expected, state["steps"]):
        fields(step, "object status reason intent_at confirmed_at after" + (" before" if execution else ""), "STATE_STEP_FIELDS")
        require(step["object"] == key, "STATE_STEP_ORDER")
        status = step["status"]
        require(status in ("not_sent", "pending", "confirmed", "not_required"), "STATE_STATUS")
        client = clients[keys.index(key)] if key in keys else None
        reason = "hold" if client and client["policy"] == "hold" else "already_enabled" if key == "enable" and initial["inbound"]["enabled"] else None
        if status == "not_required":
            require(reason is not None and step["reason"] == reason, "STATE_SKIP_REASON")
            require(step["intent_at"] is None and step["confirmed_at"] is None and step["after"] is None, "STATE_SKIP_EVIDENCE")
            require(not execution or step["before"] is None, "STATE_SKIP_EVIDENCE")
            if reason == "hold":
                require(not initial[key]["enabled"] and not initial[key]["traffic_enabled"], "STATE_HOLD_EVIDENCE")
            continue
        require(reason is None and step["reason"] is None, "STATE_UNEXPECTED_ACTION")
        if status == "not_sent":
            require(step["intent_at"] is None and step["confirmed_at"] is None and step["after"] is None, "STATE_NOT_SENT_EVIDENCE")
            require(not execution or step["before"] is None, "STATE_NOT_SENT_EVIDENCE")
            stopped = True
            all_done = False
            continue
        require(not stopped, "STATE_ACTION_AFTER_UNRESOLVED")
        intent = utc(step["intent_at"], "STATE_INTENT_TIME")
        require(intent >= before, "STATE_TIME_ORDER")
        require(0 <= (intent - boundary).total_seconds() < 240, "STATE_INTENT_OUTSIDE_WINDOW")
        any_intent = True
        if execution:
            snapshot(step["before"], client=client is not None)
            target = key if key != "enable" else "inbound"
            require(step["before"]["enabled"] == initial[target]["enabled"], "STATE_BEFORE_ENABLE_DRIFT")
            if key != "enable":
                require(step["before"]["used_bytes"] >= initial[target]["used_bytes"], "STATE_BEFORE_COUNTER_ROLLBACK")
                if client:
                    require(step["before"]["traffic_enabled"] == initial[key]["traffic_enabled"], "STATE_BEFORE_TRAFFIC_DRIFT")
        if status == "pending":
            require(step["confirmed_at"] is None and step["after"] is None, "STATE_PENDING_EVIDENCE")
            stopped = True
            all_done = False
            continue
        confirmed = utc(step["confirmed_at"], "STATE_CONFIRMED_TIME")
        require(confirmed >= intent, "STATE_TIME_ORDER")
        snapshot(step["after"], client=client is not None)
        if execution and key != "enable":
            require(step["before"]["used_bytes"] == 0 or step["after"]["used_bytes"] < step["before"]["used_bytes"],
                    "STATE_COUNTER_NOT_REDUCED")
        if execution and key == "enable":
            require(not step["before"]["enabled"], "STATE_ENABLE_BEFORE")
        if client:
            require(step["after"]["enabled"] and step["after"]["traffic_enabled"], "STATE_CLIENT_NOT_RECOVERED")
        elif key == "enable":
            require(step["after"]["enabled"], "STATE_INBOUND_NOT_RECOVERED")
        else:
            require(step["after"]["enabled"] == initial["inbound"]["enabled"], "STATE_INBOUND_PHASE")
        before = confirmed
    if state["completed_at"] is not None:
        require(all_done and utc(state["completed_at"], "STATE_COMPLETED_TIME") >= before, "STATE_INCOMPLETE_FINAL")
        final = fields(state["final"], " ".join([*keys, "inbound"]), "STATE_FINAL_FIELDS")
        snapshot(final["inbound"])
        require(final["inbound"]["enabled"], "STATE_FINAL_INBOUND")
        for key, client in zip(keys, clients):
            snapshot(final[key], client=True)
            required = client["policy"] != "hold"
            require(final[key]["enabled"] is required and final[key]["traffic_enabled"] is required, "STATE_FINAL_CLIENT")
        if execution:
            for key in [*keys, "inbound"]:
                target_step = next(s for s in state["steps"] if s["object"] == key)
                previous = target_step["after"] if target_step["status"] == "confirmed" else initial[key]
                require(final[key]["used_bytes"] >= previous["used_bytes"], "STATE_FINAL_COUNTER_ROLLBACK")
        classification = "COMPLETED_RECORDED"
    else:
        require(state["final"] is None, "STATE_FINAL_WITHOUT_COMPLETION")
        classification = "REVIEW_REQUIRED" if any_intent else "NOT_EXECUTED_RECORDED"
    return {"classification": classification, "period_id": state["period_id"],
            "boundary_utc": state["boundary_utc"], "object_sha256": state["object_sha256"],
            "any_intent": any_intent, "automatic_replay": "FORBIDDEN",
            "evidence_authenticity": "NOT_PROVEN", "schedule_authorization": "NOT_ASSESSED"}


def inspect_history(entries):
    """每项为 (原始账本, 产生它的配置)。跨修订的旧记录不得用新摘要解释。"""
    entries = list(entries)
    results = [inspect_ledger(raw, cfg) for raw, cfg in entries]
    periods, boundaries = set(), set()
    for r, (_, config) in zip(results, entries):
        cfg = config.data
        scope = (cfg["host_id_sha256"], cfg["panel_binding_id"])
        # 整体对象摘要会随 Client 集合变更，不能拿它当逐目标防重复键。
        targets = [("inbound", cfg["inbound"]["id"])]
        for c in cfg["clients"]:
            if c["policy"] != "hold":
                targets.extend([("client-record", c["record_id"]), ("client-uuid", c["uuid"])])
        for target in targets:
            period = (scope, target, r["period_id"])
            boundary = (scope, target, utc(r["boundary_utc"], "STATE_BOUNDARY"))
            require(period not in periods and boundary not in boundaries, "HISTORY_DUPLICATE_BOUNDARY_OR_PERIOD")
            periods.add(period)
            boundaries.add(boundary)
    return {"count": len(results), "unresolved": any(r["classification"] == "REVIEW_REQUIRED" for r in results),
            "continuity": "NOT_PROVEN", "automatic_replay": "FORBIDDEN"}
