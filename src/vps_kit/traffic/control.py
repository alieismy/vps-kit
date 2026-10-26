"""稳定所有权、不可变修订、周期授权及维护记录的重放契约。"""
from copy import deepcopy

from .calendar import next_boundary, stamp
from .config import parse_config, uuid_value
from .contracts import canonical, fields, integer, require, text, utc
from .journal import JournalView, sha
from .state import EXECUTION_SCHEMA, inspect_history, inspect_ledger

FINISHED = ("complete", "cancelled")
STAGES = ("preparing", "paused", "prepared", "installing", "verifying", "complete", "failed", "cancelled")


class Control:
    def __init__(self, view):
        self.view = view
        self.owner = None
        self.configs = {}
        self.transitions = []
        self.ledgers = {}
        self.ledger_hashes = {}
        self.resolutions = {}
        self.maintenance = None
        self.operations = set()
        self.legacy_import = None
        self.formats = []
        for event in view.events:
            self._accept(event)
        inspect_history([(canonical(s), self.configs[s["revision"]]) for s in self.ledgers.values()])

    @property
    def config(self):
        require(self.owner is not None, "OWNER_MISSING")
        return self.configs[self.owner["active_revision"]]

    def ready(self):
        require(self.owner is not None and self.owner["status"] == "active", "OWNER_NOT_ACTIVE")
        require(self.maintenance is None or self.maintenance["stage"] in FINISHED, "MAINTENANCE_INCOMPLETE")
        require(not self.unresolved(), "HISTORY_UNRESOLVED")
        require(self.transitions and self.transitions[-1]["to_revision"] == self.owner["active_revision"],
                "ACTIVE_AUTHORIZATION_MISMATCH")

    def unresolved(self):
        return [p for p, s in self.ledgers.items() if s["completed_at"] is None
                and self.ledger_hashes[p] not in self.resolutions]

    def next_authorization(self):
        require(self.transitions, "SCHEDULE_AUTHORIZATION_MISSING")
        transition = self.transitions[-1]
        period = transition["next_period_id"]
        value = utc(transition["effective_boundary_utc"], "TRANSITION_BOUNDARY")
        cfg = self.configs[transition["to_revision"]]
        while period in self.ledgers:
            state = self.ledgers[period]
            if state["completed_at"] is None:
                break
            value = next_boundary(cfg.data["schedule"], value)
            period += 1
        return period, value

    def _accept(self, event):
        kind, p = event["kind"], event["payload"]
        if kind == 'format':
            fields(p, 'schema operation_id from_contracts to_contracts config_sha256 backup_sha256', 'FORMAT_FIELDS')
            from .package import OLD_CONTRACTS, CONTRACTS
            require(p['schema'] == 'vps-kit.format-transition/v1' and p['from_contracts'] == OLD_CONTRACTS
                    and p['to_contracts'] == CONTRACTS and not self.formats, 'FORMAT_TRANSITION')
            uuid_value(p['operation_id'])
            self.ready()
            require(p['config_sha256'] == self.config.fingerprint and type(p['backup_sha256']) is str
                    and len(p['backup_sha256']) == 64 and all(c in '0123456789abcdef' for c in p['backup_sha256']), 'FORMAT_BINDING')
            self.formats.append(deepcopy(p))
        elif kind in ('legacy_import', 'vmrack_import'):
            fields(p, 'config_sha256 source_sha256 history', 'IMPORT_FIELDS')
            require(self.owner is None and not self.configs and self.legacy_import is None
                    and self.maintenance and self.maintenance['kind'] == 'create'
                    and self.maintenance['stage'] == 'installing', 'IMPORT_STAGE')
            for key in ('config_sha256', 'source_sha256'):
                require(type(p[key]) is str and len(p[key]) == 64 and all(c in '0123456789abcdef' for c in p[key]), 'IMPORT_HASH')
            if kind == 'vmrack_import':
                from .vmrack_legacy import inspect_evidence
                from .sources import VMRACK
                h = fields(p['history'], 'schema source_kind evidence cutover_utc minimum_boundary_utc next_boundary_utc next_period_id state_sha256', 'VMRACK_IMPORT_FIELDS')
                require(h['schema'] == 'vps-kit.vmrack-import/v1' and h['source_kind'] == VMRACK, 'VMRACK_IMPORT_KIND')
                inspect_evidence(h['evidence'])
                require(type(h['next_period_id']) is int and h['next_period_id'] == 1, 'VMRACK_IMPORT_PERIOD')
                require(utc(h['next_boundary_utc'],'IMPORT_BOUNDARY') >= utc(h['minimum_boundary_utc'],'IMPORT_BOUNDARY')
                        > utc(h['cutover_utc'],'IMPORT_BOUNDARY'), 'VMRACK_IMPORT_BOUNDARY')
            else:
                h = fields(p['history'], 'records markers next_boundary_utc next_period_id source_first_boundary_utc state_sha256', 'IMPORT_HISTORY')
                integer(h['next_period_id'], 'IMPORT_PERIOD', 1)
                require(type(h['records']) is list and h['next_period_id'] == len(h['records']) + 1, 'IMPORT_PERIOD')
                due = utc(h['next_boundary_utc'], 'IMPORT_BOUNDARY')
                require(due >= utc(h['source_first_boundary_utc'], 'IMPORT_FIRST'), 'IMPORT_FIRST')
                require(all(utc(r['boundary_utc'], 'IMPORT_OLD_BOUNDARY') < due for r in h['records']), 'IMPORT_REUSED_BOUNDARY')
            self.legacy_import = deepcopy(p)
        elif kind == 'program':
            fields(p, 'operation_id from_sha256 to_sha256', 'PROGRAM_FIELDS')
            uuid_value(p['operation_id'])
            require(self.owner and self.owner['status'] == 'active' and p['from_sha256'] == self.owner['program_sha256'], 'PROGRAM_BINDING')
            require(type(p['to_sha256']) is str and len(p['to_sha256']) == 64 and all(c in '0123456789abcdef' for c in p['to_sha256']), 'PROGRAM_HASH')
            require(self.maintenance and self.maintenance['stage'] in FINISHED, 'PROGRAM_MAINTENANCE_OPEN')
            self.owner = {**self.owner, 'program_sha256': p['to_sha256']}
        elif kind == "maintenance":
            fields(p, "operation_id task_id kind stage plan_sha256 base_head before_system backup observed_system error_code", "MAINTENANCE_FIELDS")
            uuid_value(p["operation_id"])
            uuid_value(p["task_id"])
            require(p["kind"] in ("create", "change", "pause", "resume", "remove", "resolve"), "MAINTENANCE_KIND")
            require(p["stage"] in STAGES, "MAINTENANCE_STAGE")
            require(type(p["before_system"]) is dict and type(p["observed_system"]) is dict, "MAINTENANCE_SYSTEM")
            require(p["error_code"] is None or type(p["error_code"]) is str, "MAINTENANCE_ERROR")
            for key in ("plan_sha256", "base_head"):
                require(p[key] is None or (type(p[key]) is str and len(p[key]) == 64
                        and all(c in "0123456789abcdef" for c in p[key])), "MAINTENANCE_HASH")
            if p["backup"] is not None:
                fields(p["backup"], "content sha256", "MAINTENANCE_BACKUP")
                from .contracts import digest
                require(type(p["backup"]["content"]) is dict and digest(p["backup"]["content"]) == p["backup"]["sha256"], "MAINTENANCE_BACKUP_HASH")
            old = self.maintenance
            if self.owner:
                require(p["task_id"] == self.owner["task_id"], "MAINTENANCE_OWNER")
            if old and old["stage"] not in FINISHED:
                require(p["operation_id"] == old["operation_id"], "MAINTENANCE_ALREADY_OPEN")
                for key in ("task_id", "kind", "plan_sha256", "base_head", "before_system"):
                    require(p[key] == old[key], "MAINTENANCE_BINDING")
                require(old["stage"] != "failed" or p["stage"] == "failed", "FAILED_MAINTENANCE_REQUIRES_REVIEW")
                if p["stage"] != "failed":
                    if p["kind"] in ("create", "change"):
                        allowed = {"preparing": ("paused",), "paused": ("prepared",),
                                   "prepared": ("installing", "cancelled"), "installing": ("verifying",),
                                   "verifying": ("complete",)}
                    elif p["kind"] == "remove":
                        allowed = {"preparing": ("prepared",), "prepared": ("complete",)}
                    else:
                        allowed = {"preparing": ("complete",)}
                    require(p["stage"] in allowed.get(old["stage"], ()), "MAINTENANCE_STAGE_SEQUENCE")
                if old["backup"] is not None:
                    require(p["backup"] == old["backup"], "MAINTENANCE_BACKUP_IMMUTABLE")
            else:
                require(p["stage"] == "preparing" and p["operation_id"] not in self.operations,
                        "MAINTENANCE_NEW_OPERATION")
                if old and not self.owner:
                    require(p["task_id"] == old["task_id"], "MAINTENANCE_RESERVED_OWNER")
                self.operations.add(p["operation_id"])
            if p["stage"] == "complete" and p["kind"] in ("create", "change", "resume", "resolve"):
                require(self.owner and self.transitions and self.owner["active_revision"] == self.transitions[-1]["to_revision"],
                        "MAINTENANCE_AUTHORIZATION_INCOMPLETE")
            self.maintenance = deepcopy(p)
        elif kind == "revision":
            fields(p, "config parent_revision config_sha256", "REVISION_FIELDS")
            cfg = parse_config(canonical(p["config"]))
            d = cfg.data
            rev = d["revision"]
            require(rev not in self.configs and p["config_sha256"] == cfg.fingerprint, "REVISION_IMMUTABLE")
            if self.owner:
                old = self.config.data
                require(rev == old["revision"] + 1 and p["parent_revision"] == old["revision"], "REVISION_SEQUENCE")
                for key in ("task_id", "host_id_sha256", "panel_binding_id"):
                    require(d[key] == old[key], "OWNER_BINDING_CHANGED")
                require(d["inbound"]["id"] == old["inbound"]["id"], "OWNER_INBOUND_CHANGED")
            else:
                require(rev == 1 and p["parent_revision"] is None and not self.configs, "REVISION_INITIAL")
                if self.legacy_import and self.legacy_import['history'].get('source_kind') == 'vmrack-monthly/v1':
                    from .vmrack_legacy import qualify
                    h = self.legacy_import['history']
                    cutover = utc(h['cutover_utc'],'VMRACK_CUTOVER')
                    require(cfg.object_fingerprint == h['evidence']['object_sha256'] and
                            cfg.fingerprint == self.legacy_import['config_sha256'] and
                            utc(h['minimum_boundary_utc'],'VMRACK_MINIMUM') >= qualify(cfg,h['evidence'],cutover,cutover),
                            'VMRACK_IMPORT_QUALIFICATION')
            require(self.maintenance and self.maintenance["stage"] == "installing", "REVISION_OUTSIDE_MAINTENANCE")
            require(self.maintenance["kind"] == ("change" if self.owner else "create")
                    and d["task_id"] == self.maintenance["task_id"], "REVISION_OPERATION_BINDING")
            self.configs[rev] = cfg
        elif kind == "registry":
            fields(p, "task_id host_id_sha256 panel_binding_id inbound_id active_revision status program_sha256", "REGISTRY_FIELDS")
            integer(p["active_revision"], "REGISTRY_REVISION", 1)
            require(type(p["program_sha256"]) is str and len(p["program_sha256"]) == 64
                    and all(c in "0123456789abcdef" for c in p["program_sha256"]), "REGISTRY_PROGRAM")
            require(p["active_revision"] in self.configs and p["status"] in ("active", "removed"), "REGISTRY_REVISION")
            cfg = self.configs[p["active_revision"]].data
            for key in ("task_id", "host_id_sha256", "panel_binding_id"):
                require(p[key] == cfg[key], "REGISTRY_BINDING")
            require(type(p["inbound_id"]) is int and p["inbound_id"] == cfg["inbound"]["id"], "REGISTRY_INBOUND")
            if self.owner:
                for key in ("task_id", "host_id_sha256", "panel_binding_id", "inbound_id", "program_sha256"):
                    require(p[key] == self.owner[key], "OWNER_REPLACEMENT_FORBIDDEN")
                require(self.owner["status"] == "active", "OWNER_REMOVED")
            require(self.maintenance and self.maintenance["stage"] not in FINISHED, "REGISTRY_OUTSIDE_MAINTENANCE")
            if p["status"] == "removed":
                require(self.owner and self.maintenance["kind"] == "remove" and self.maintenance["stage"] == "prepared"
                        and p["active_revision"] == self.owner["active_revision"], "REGISTRY_REMOVE_OPERATION")
            else:
                require(self.maintenance["kind"] in ("create", "change") and self.maintenance["stage"] == "installing"
                        and p["active_revision"] == max(self.configs)
                        and (not self.owner or p["active_revision"] == self.owner["active_revision"] + 1), "REGISTRY_ACTIVATION_SEQUENCE")
            self.owner = deepcopy(p)
        elif kind == "transition":
            fields(p, "operation_id from_revision to_revision previous_period_id next_period_id effective_boundary_utc old_next_boundary_utc reason", "TRANSITION_FIELDS")
            uuid_value(p["operation_id"])
            require(p["to_revision"] in self.configs and self.owner and p["to_revision"] == self.owner["active_revision"], "TRANSITION_REVISION")
            integer(p["next_period_id"], "TRANSITION_PERIOD", 1)
            integer(p["previous_period_id"], "TRANSITION_PERIOD")
            integer(p["to_revision"], "TRANSITION_REVISION", 1)
            if p["from_revision"] is not None:
                integer(p["from_revision"], "TRANSITION_REVISION", 1)
            require(p["reason"] in ("create", "change", "resume-skip", "resolve-future"), "TRANSITION_REASON")
            b = utc(p["effective_boundary_utc"], "TRANSITION_BOUNDARY")
            schedule = self.configs[p["to_revision"]].data["schedule"]
            from .config import schedule_validate
            schedule_validate({**schedule, "first_boundary_utc": stamp(b)})
            require(b >= utc(schedule["first_boundary_utc"], "TRANSITION_FIRST"), "TRANSITION_BEFORE_FIRST")
            if self.transitions:
                old_period, old_boundary = self.next_authorization()
                require(utc(p["old_next_boundary_utc"], "TRANSITION_OLD") == old_boundary and b >= old_boundary,
                        "TRANSITION_SHORTENS_PERIOD")
                resolved_current = (old_period in self.ledgers and self.ledger_hashes[old_period] in self.resolutions)
                require(old_period not in self.ledgers or resolved_current, "TRANSITION_UNRESOLVED_PERIOD")
                if resolved_current:
                    resolution = self.resolutions[self.ledger_hashes[old_period]]
                    require(p["reason"] == "resolve-future" and b >= utc(resolution["not_before_boundary_utc"], "RESOLUTION_TIME"), "TRANSITION_RESOLUTION_BOUNDARY")
                else:
                    require(p["reason"] in ("change", "resume-skip"), "TRANSITION_REASON")
                expected = old_period + 1 if resolved_current else old_period
                require(p["next_period_id"] == expected, "TRANSITION_PERIOD_SEQUENCE")
                require(p["from_revision"] == self.transitions[-1]["to_revision"], "TRANSITION_PARENT")
            else:
                first_period = self.legacy_import['history']['next_period_id'] if self.legacy_import else 1
                require(p["next_period_id"] == first_period and p["from_revision"] is None and p["old_next_boundary_utc"] is None, "TRANSITION_INITIAL")
                require(p["reason"] == "create", "TRANSITION_INITIAL_REASON")
                if self.legacy_import:
                    require(self.config.fingerprint == self.legacy_import['config_sha256']
                            and p['effective_boundary_utc'] == self.legacy_import['history']['next_boundary_utc'], 'IMPORT_AUTHORIZATION')
            require(p["previous_period_id"] == p["next_period_id"] - 1, "TRANSITION_PREVIOUS")
            require(not self.ledgers or b > max(utc(s["boundary_utc"], "STATE_BOUNDARY") for s in self.ledgers.values()),
                    "TRANSITION_REUSES_BOUNDARY")
            require(self.maintenance and self.maintenance["stage"] not in FINISHED, "TRANSITION_OUTSIDE_MAINTENANCE")
            require(p["operation_id"] == self.maintenance["operation_id"], "TRANSITION_OPERATION")
            kind, stage = {"create": ("create", "installing"), "change": ("change", "installing"),
                           "resume-skip": ("resume", "preparing"), "resolve-future": ("resolve", "preparing")}[p["reason"]]
            require((self.maintenance["kind"], self.maintenance["stage"]) == (kind, stage)
                    and not any(t["operation_id"] == p["operation_id"] for t in self.transitions), "TRANSITION_OPERATION_SEQUENCE")
            if kind in ("resume", "resolve"):
                require(p["from_revision"] == p["to_revision"], "TRANSITION_SAME_REVISION_REQUIRED")
            elif kind == "change":
                require(p["to_revision"] == p["from_revision"] + 1, "TRANSITION_REVISION_SEQUENCE")
            self.transitions.append(deepcopy(p))
        elif kind == "ledger":
            require(type(p) is dict and p.get("revision") in self.configs, "LEDGER_REVISION_MISSING")
            require(p.get("schema_version") == EXECUTION_SCHEMA, "CONTROL_REQUIRES_EXECUTION_LEDGER_V2")
            inspect_ledger(canonical(p), self.configs[p["revision"]])
            require(p["program_sha256"] == self.owner["program_sha256"], "LEDGER_PROGRAM_BINDING")
            period = p["period_id"]
            if period not in self.ledgers:
                self.ready()
                expected, due = self.next_authorization()
                require(period == expected and utc(p["boundary_utc"], "STATE_BOUNDARY") == due
                        and p["revision"] == self.owner["active_revision"], "LEDGER_NOT_AUTHORIZED")
                require(p["completed_at"] is None and all(s["status"] in ("not_sent", "not_required") for s in p["steps"]),
                        "LEDGER_INITIAL_MUST_HAVE_NO_INTENT")
            else:
                old = self.ledgers[period]
                require(self.ledger_hashes[period] not in self.resolutions, "RESOLVED_LEDGER_IMMUTABLE")
                require(old["completed_at"] is None, "LEDGER_COMPLETED_IMMUTABLE")
                for key in ("task_id", "revision", "config_sha256", "object_sha256", "period_id", "boundary_utc", "created_at", "initial"):
                    require(p[key] == old[key], "LEDGER_IDENTITY_CHANGED")
                for key in ("invocation_id", "program_sha256"):
                    require(p.get(key) == old.get(key), "LEDGER_EXECUTION_CHANGED")
                changed = 0
                for a, b in zip(old["steps"], p["steps"]):
                    changed += a != b
                    if a["status"] in ("confirmed", "not_required"):
                        require(a == b, "LEDGER_CONFIRMED_IMMUTABLE")
                    elif a["status"] == "pending":
                        require(b["status"] in ("pending", "confirmed") and a["intent_at"] == b["intent_at"]
                                and a.get("before") == b.get("before"), "LEDGER_REPLAY_FORBIDDEN")
                    else:
                        require(a == b or b["status"] == "pending", "LEDGER_INTENT_REQUIRED")
                require((changed == 1 and p["completed_at"] is None) or (changed == 0 and p["completed_at"] is not None),
                        "LEDGER_ONE_TRANSITION_PER_COMMIT")
            self.ledgers[period] = deepcopy(p)
            self.ledger_hashes[period] = event["sha256"]
        elif kind == 'resolution':
            require(self.maintenance and self.maintenance["kind"] == "resolve" and self.maintenance["stage"] == "preparing", "RESOLUTION_OUTSIDE_MAINTENANCE")
            self.validate_resolution(p)
            self.resolutions[p["source_ledger_sha256"]] = deepcopy(p)
        else:
            require(False, 'CONTROL_EVENT_KIND')

    def validate_resolution(self, p):
        """输入预检与日志重放共用；验证不追加记录，也不解除原账本。"""
        fields(p, "resolution_id source_ledger_sha256 period_id actor evidence_sha256 late_commit_closed billing_aligned not_before_boundary_utc decision", "RESOLUTION_FIELDS")
        integer(p["period_id"], "RESOLUTION_PERIOD", 1)
        uuid_value(p["resolution_id"])
        require(p["period_id"] in self.ledgers and p["source_ledger_sha256"] == self.ledger_hashes[p["period_id"]], "RESOLUTION_BINDING")
        require(self.ledgers[p["period_id"]]["completed_at"] is None and p["source_ledger_sha256"] not in self.resolutions, "RESOLUTION_NOT_UNRESOLVED")
        require(not any(r["resolution_id"] == p["resolution_id"] for r in self.resolutions.values()), "RESOLUTION_ID_REUSED")
        require(p["late_commit_closed"] is True and p["billing_aligned"] is True and p["decision"] == "future-only", "RESOLUTION_EVIDENCE")
        text(p["actor"], "RESOLUTION_ACTOR", 100)
        require(type(p["evidence_sha256"]) is str and len(p["evidence_sha256"]) == 64
                and all(c in "0123456789abcdef" for c in p["evidence_sha256"]), "RESOLUTION_EVIDENCE_HASH")
        target = utc(p["not_before_boundary_utc"], "RESOLUTION_TIME")
        require(target > utc(self.ledgers[p["period_id"]]["boundary_utc"], "STATE_BOUNDARY"), "RESOLUTION_NOT_FUTURE")
        from .config import schedule_validate
        schedule_validate({**self.config.data["schedule"], "first_boundary_utc": stamp(target)})
        require(target >= utc(self.config.data["schedule"]["first_boundary_utc"], "RESOLUTION_FIRST"), "RESOLUTION_BEFORE_FIRST")


def commit(journal, kind, payload, expected_head):
    view = journal.load()
    require(view.head == expected_head, "CONTROL_STALE")
    event = {"schema_version": "vps-kit.traffic.event/v1", "sequence": len(view.events) + 1,
             "previous_sha256": view.events[-1]["sha256"] if view.events else None, "kind": kind, "payload": payload}
    raw = (canonical(event) + "\n").encode("utf-8")
    Control(JournalView((*view.events, {**event, "sha256": sha(raw)}), None))
    return Control(journal.append(kind, payload, expected_head))
