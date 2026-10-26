"""合成 fixture 的生成源；不读取任何实际面板、Token 或旧部署配置。"""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def config_data(version="3.7.0"):
    return {
        "schema_version": "vps-kit.traffic.config/v1",
        "task_id": "00000000-0000-4000-8000-000000000001",
        "revision": 1,
        "host_id_sha256": "0" * 64,
        "panel_binding_id": "00000000-0000-4000-8000-000000000002",
        "panel": {"version": version, "url": "https://panel.example.invalid:8443/example",
                  "connect_address": "127.0.0.1", "allow_loopback_http": False},
        "inbound": {"id": 7, "remark": "SYNTHETIC-Reality", "listen": "", "port": 443,
                    "protocol": "vless", "quota_bytes": 250 * 1024**3, "policy": "stop"},
        "clients": [
            {"record_id": 11, "uuid": "00000000-0000-4000-8000-000000000011", "email": "sample-a", "quota_bytes": 150 * 1024**3, "policy": "stop"},
            {"record_id": 12, "uuid": "00000000-0000-4000-8000-000000000012", "email": "sample-b", "quota_bytes": 100 * 1024**3, "policy": "quota-recovery"}],
        "schedule": {"day": 22, "short_month": "not-applicable", "time": "15:44:25", "timezone": "America/Toronto",
                     "first_boundary_utc": "2027-01-22T20:44:25Z"},
        "provider": {"amount": "500", "unit": "GiB", "model": "proxy-duplex-2", "reserve_percent": 0, "other_reserved_bytes": 0}}


def fixture_data(cfg):
    embedded = []
    responses = {}
    for c in cfg["clients"]:
        periods = {"reset": 0, "resetDay": 0, "expiryTime": 0}
        if cfg["panel"]["version"] == "3.10.2":
            periods["resetWeekday"] = 0
        e = {"id": c["uuid"], "email": c["email"], "totalGB": c["quota_bytes"], "enable": True, **periods}
        # 复现已确认的 embedded 缺字段，不把独立 Client/traffic 一并删键。
        del e["resetDay"]
        embedded.append(e)
        detail = {"client": {"id": c["record_id"], "uuid": c["uuid"], "email": c["email"],
                             "totalGB": c["quota_bytes"], "enable": True, "trafficReset": "never",
                             "trafficResetDay": 1, **periods}, "inboundIds": [cfg["inbound"]["id"]]}
        traffic = {"email": c["email"], "total": c["quota_bytes"], "enable": True, "up": 123, "down": 456,
                   "resetMax": 0, "resetCount": 3, **periods}
        responses["/panel/api/clients/get/" + c["email"]] = {"success": True, "obj": detail}
        responses["/panel/api/clients/traffic/" + c["email"]] = {"success": True, "obj": traffic}
    i = cfg["inbound"]
    inbound = {k: i[k] for k in ("id", "remark", "listen", "port", "protocol")}
    inbound.update(total=i["quota_bytes"], nodeId=None, enable=True, expiryTime=0, trafficReset="never",
                   up=1000, down=2000, settings={"clients": embedded})
    responses["/panel/api/inbounds/list"] = {"success": True, "obj": [inbound]}
    return {"fixture_kind": "SYNTHETIC", "panel_version": cfg["panel"]["version"], "responses": responses}


def ledger_data(config):
    cfg = config.data
    initial = {"inbound": {"used_bytes": 3000, "enabled": True}}
    steps = []
    for c in cfg["clients"]:
        key = "client:" + str(c["record_id"])
        initial[key] = {"used_bytes": 1000, "enabled": True, "traffic_enabled": True}
        steps.append({"object": key, "status": "confirmed", "reason": None,
                      "intent_at": "2027-01-22T20:44:26Z", "confirmed_at": "2027-01-22T20:44:26Z",
                      "after": {"used_bytes": 0, "enabled": True, "traffic_enabled": True}})
    steps.append({"object": "inbound", "status": "confirmed", "reason": None,
                  "intent_at": "2027-01-22T20:44:27Z", "confirmed_at": "2027-01-22T20:44:27Z",
                  "after": {"used_bytes": 0, "enabled": True}})
    steps.append({"object": "enable", "status": "not_required", "reason": "already_enabled",
                  "intent_at": None, "confirmed_at": None, "after": None})
    final = copy.deepcopy(initial)
    for s in final.values():
        s["used_bytes"] = 12  # 读回时已自然增长，不要求永远为零。
    return {"schema_version": "vps-kit.traffic.ledger/v1", "task_id": cfg["task_id"], "revision": cfg["revision"],
            "config_sha256": config.fingerprint, "object_sha256": config.object_fingerprint,
            "period_id": 1, "boundary_utc": cfg["schedule"]["first_boundary_utc"], "created_at": "2027-01-22T20:44:25Z",
            "initial": initial, "steps": steps, "completed_at": "2027-01-22T20:44:28Z", "final": final}


def write_fixtures():
    from vps_kit.traffic.config import parse_config
    for version in ("3.7.0", "3.10.2"):
        directory = ROOT / "tests" / "traffic" / "fixtures" / version
        directory.mkdir(parents=True, exist_ok=True)
        data = config_data(version)
        objects = {"config.json": data, "panel.json": fixture_data(data), "ledger.json": ledger_data(parse_config(json.dumps(data)))}
        for name, value in objects.items():
            (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    write_fixtures()
