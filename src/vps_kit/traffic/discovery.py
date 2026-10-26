"""1+2N GET 的只读快照，不把快照核对当作调度或写权限验收。"""
from .contracts import canonical, fields, require
from .panel import ReadEndpoint, get_adapter


class FixtureReader:
    def __init__(self, fixture, config):
        fields(fixture, "fixture_kind panel_version responses", "FIXTURE_FIELDS")
        require(fixture["fixture_kind"] == "SYNTHETIC" and fixture["panel_version"] == config.data["panel"]["version"], "FIXTURE_VERSION")
        expected = [ReadEndpoint.INBOUNDS.path()]
        for c in config.data["clients"]:
            expected += [ReadEndpoint.CLIENT.path(c["email"]), ReadEndpoint.TRAFFIC.path(c["email"])]
        require(type(fixture["responses"]) is dict and set(fixture["responses"]) == set(expected), "FIXTURE_ROUTES")
        self.responses = fixture["responses"]
        self.paths = []
        self.attempts = 0

    def get(self, endpoint, email=None):
        require(isinstance(endpoint, ReadEndpoint), "API_ENDPOINT_NOT_ALLOWED")
        path = endpoint.path(email)
        require(path in self.responses and path not in self.paths, "FIXTURE_CALL_BUDGET")
        self.paths.append(path)
        self.attempts += 1
        return canonical(self.responses[path])


def discover(config, reader, evidence_mode):
    require(evidence_mode in ("SYNTHETIC", "LOCAL_LIVE_GET"), "DISCOVERY_MODE")
    d = config.data
    adapter = get_adapter(d["panel"]["version"])
    embedded, inbound = adapter.inbound(reader.get(ReadEndpoint.INBOUNDS), config)
    results = []
    for index, client in enumerate(d["clients"], 1):
        detail = reader.get(ReadEndpoint.CLIENT, client["email"])
        enabled = adapter.client_detail(embedded[client["email"]], detail, client, d["inbound"]["id"])
        traffic = reader.get(ReadEndpoint.TRAFFIC, client["email"])
        result = adapter.client_traffic(enabled, traffic, client)
        results.append({"index": index, **result})
    return {"result": "READ_CONTRACT_PASS", "evidence_mode": evidence_mode,
            "panel_version": adapter.version, "inbound": inbound, "clients": results,
            "read_calls": reader.attempts, "read_budget": 1 + 2 * len(d["clients"]),
            "http_calls": 0 if evidence_mode == "SYNTHETIC" else reader.attempts,
            "methods": ["GET"], "retries": 0, "script_writes": 0,
            "token_admin_scope": "NOT_PROVEN", "topology_provenance": "MANUAL_REVIEW_REQUIRED",
            "timer_readiness": "NOT_ASSESSED", "reset_execution": "NOT_REQUESTED_READONLY"}
