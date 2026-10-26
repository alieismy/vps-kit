"""自然边界执行状态机。所有系统依赖显式传入；无补跑、无重试、无补偿。"""
from copy import deepcopy
from datetime import timedelta

from .calendar import WINDOW_SECONDS, stamp
from .contracts import ContractError, require, utc
from .control import Control, commit
from .journal import check_lock_identities
from .panel import ReadEndpoint, get_adapter, recovery
from .state import EXECUTION_SCHEMA


def observe(config, panel, expected=None):
    d = config.data
    adapter = get_adapter(d["panel"]["version"])
    embedded, inbound = adapter.inbound(panel.get(ReadEndpoint.INBOUNDS), config,
                                       None if expected is None else expected["inbound"]["enabled"])
    result = {"inbound": {"enabled": inbound["enabled"], "used_bytes": inbound["used_bytes"]}}
    for c in d["clients"]:
        key = "client:" + str(c["record_id"])
        enabled = adapter.client_detail(embedded[c["email"]], panel.get(ReadEndpoint.CLIENT, c["email"]), c, d["inbound"]["id"])
        states = None if expected is None else (expected[key]["enabled"], expected[key]["traffic_enabled"])
        view = adapter.client_traffic(enabled, panel.get(ReadEndpoint.TRAFFIC, c["email"]), c, states)
        result[key] = {"enabled": enabled, "traffic_enabled": view["traffic_enabled"], "used_bytes": view["used_bytes"]}
    return result


class ClockFence:
    def __init__(self, clock, boundary):
        self.clock, self.boundary = clock, boundary
        self.start_wall, self.start_mono, sync = clock.sample()
        require(sync is True, "CLOCK_UNSYNCHRONIZED")
        self.last_mono = self.start_mono

    def check(self, writing=False):
        wall, mono, sync = self.clock.sample()
        require(sync is True and mono >= self.last_mono, "CLOCK_UNSTABLE")
        require(abs((wall - self.start_wall).total_seconds() - (mono - self.start_mono)) <= 2, "CLOCK_JUMP")
        self.last_mono = mono
        require((mono - self.start_mono) < 290, "EXECUTION_TIME_BUDGET")
        if writing:
            require(0 <= (wall - self.boundary).total_seconds() < WINDOW_SECONDS, "OUTSIDE_WRITE_WINDOW")
        return wall


def no_unexpected_decrease(before, after, target=None):
    for key in before:
        if key != target:
            require(after[key]["used_bytes"] >= before[key]["used_bytes"], "COUNTER_EXTERNAL_RESET_OR_ROLLBACK")


class BudgetPanel:
    def __init__(self, panel, reads, writes, fence):
        self.panel, self.max_reads, self.max_writes, self.fence = panel, reads, writes, fence
        self.reads = self.writes = 0

    def get(self, endpoint, email=None):
        self.fence.check()
        require(self.reads < self.max_reads, "EXECUTION_GET_BUDGET")
        self.reads += 1
        return self.panel.get(endpoint, email)

    def send(self, key):
        self.fence.check(writing=True)
        check_lock_identities()
        require(self.writes < self.max_writes, "EXECUTION_POST_BUDGET")
        self.writes += 1
        return self.panel.send(key)


def execute(journal, panel_factory, runtime, clock):
    """仅调用一次每步 send；返回值不把模型结果升级为真实面板验收。"""
    with journal.exclusive():
        control = Control(journal.load())
        control.ready()
        config = control.config
        cfg = config.data
        now, _, synced = clock.sample()
        require(synced is True, "CLOCK_UNSYNCHRONIZED")
        require(runtime.program_sha256 == control.owner['program_sha256'], 'EXECUTION_PROGRAM_CHANGED')
        runtime.guard(config, control)  # 普通日唤醒也须核对退役来源；守卫不得调用面板 API。
        first = utc(cfg["schedule"]["first_boundary_utc"], "FIRST_BOUNDARY")
        if now < first:
            return {"result": "BEFORE_FIRST_BOUNDARY", "get_requests": 0, "post_requests": 0}
        for state in control.ledgers.values():
            if state["completed_at"] is not None and 0 <= (now - utc(state["boundary_utc"], "STATE_BOUNDARY")).total_seconds() < WINDOW_SECONDS:
                return {"result": "ALREADY_COMPLETED", "get_requests": 0, "post_requests": 0}
        period, due = control.next_authorization()
        if now < due:
            return {"result": "NOT_DUE", "get_requests": 0, "post_requests": 0}
        require(now < due + timedelta(seconds=WINDOW_SECONDS), "MISSED_BOUNDARY_REVIEW_REQUIRED")
        require(runtime.program_sha256 == control.owner["program_sha256"], "EXECUTION_PROGRAM_CHANGED")
        runtime.guard(config, control)
        fence = ClockFence(clock, due)
        max_writes = sum(c["policy"] != "hold" for c in cfg["clients"]) + 2
        panel = BudgetPanel(panel_factory(config), (1 + 2 * len(cfg["clients"])) * (2 + 2 * max_writes), max_writes, fence)
        initial = observe(config, panel)
        steps = []
        for c in cfg["clients"]:
            steps.append({"object": "client:" + str(c["record_id"]), "status": "not_required" if c["policy"] == "hold" else "not_sent",
                          "reason": "hold" if c["policy"] == "hold" else None, "intent_at": None,
                          "confirmed_at": None, "before": None, "after": None})
        steps.append({"object": "inbound", "status": "not_sent", "reason": None, "intent_at": None,
                      "confirmed_at": None, "before": None, "after": None})
        steps.append({"object": "enable", "status": "not_required" if initial["inbound"]["enabled"] else "not_sent",
                      "reason": "already_enabled" if initial["inbound"]["enabled"] else None, "intent_at": None,
                      "confirmed_at": None, "before": None, "after": None})
        state = {"schema_version": EXECUTION_SCHEMA, "task_id": cfg["task_id"], "revision": cfg["revision"],
                 "config_sha256": config.fingerprint, "object_sha256": config.object_fingerprint,
                 "period_id": period, "boundary_utc": stamp(due), "created_at": stamp(fence.check(writing=True)),
                 "invocation_id": runtime.invocation_id, "program_sha256": runtime.program_sha256,
                 "initial": initial, "steps": steps, "completed_at": None, "final": None}
        control = commit(journal, "ledger", state, control.view.head)
        expected, previous = deepcopy(initial), initial

        def guard():
            fence.check(writing=True)
            require(journal.load().head == control.view.head, "EXECUTION_CONTROL_CHANGED")
            require(runtime.program_sha256 == control.owner["program_sha256"], "EXECUTION_PROGRAM_CHANGED")
            runtime.guard(config, control)
            require(runtime.program_sha256 == control.owner["program_sha256"], "EXECUTION_PROGRAM_CHANGED")

        for step in state["steps"]:
            if step["status"] == "not_required":
                continue
            guard()
            before = observe(config, panel, expected)
            no_unexpected_decrease(previous, before)
            key = step["object"]
            target = "inbound" if key == "enable" else key
            if key.startswith("client:"):
                c = next(c for c in cfg["clients"] if key == "client:" + str(c["record_id"]))
                v = before[key]
                recovery(c["policy"], c["quota_bytes"], v["used_bytes"], v["enabled"], v["traffic_enabled"])
            elif key == "inbound":
                v = before[key]
                recovery(cfg["inbound"]["policy"], cfg["inbound"]["quota_bytes"], v["used_bytes"], v["enabled"], v["enabled"])
            step.update(status="pending", intent_at=stamp(fence.check(writing=True)), before=before[target])
            control = commit(journal, "ledger", state, control.view.head)
            guard()  # 同步后到真正发送前仍重新检查；失败保留 pending，绝不重放。
            panel.send(key)
            after_expected = deepcopy(expected)
            if key.startswith("client:"):
                after_expected[key].update(enabled=True, traffic_enabled=True)
            elif key == "enable":
                after_expected["inbound"]["enabled"] = True
            after = observe(config, panel, after_expected)
            no_unexpected_decrease(before, after, target if key != "enable" else None)
            if key != "enable":
                require(before[target]["used_bytes"] == 0 or after[target]["used_bytes"] < before[target]["used_bytes"],
                        "POSTCHECK_COUNTER_NOT_REDUCED")
            require(journal.load().head == control.view.head, "EXECUTION_CONTROL_CHANGED")
            runtime.guard(config, control)
            require(runtime.program_sha256 == control.owner["program_sha256"], "EXECUTION_PROGRAM_CHANGED")
            step.update(status="confirmed", confirmed_at=stamp(fence.check()), after=after[target])
            control = commit(journal, "ledger", state, control.view.head)
            expected, previous = after_expected, after
        final = observe(config, panel, expected)
        no_unexpected_decrease(previous, final)
        require(journal.load().head == control.view.head, "EXECUTION_CONTROL_CHANGED")
        runtime.guard(config, control)
        require(runtime.program_sha256 == control.owner["program_sha256"], "EXECUTION_PROGRAM_CHANGED")
        state.update(completed_at=stamp(fence.check()), final=final)
        commit(journal, "ledger", state, control.view.head)
        return {"result": "COMPLETED", "period_id": period, "boundary_utc": stamp(due),
                "get_requests": panel.reads, "post_requests": panel.writes,
                "automatic_retry": False, "business_acceptance": "MANUAL_REVIEW_REQUIRED"}
