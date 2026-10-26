"""P2 CLI 组合层。可变系统接口只通过显式 --local 操作到达。"""
from datetime import datetime, timezone
import time

from .calendar import preview, stamp, WINDOW_SECONDS
from .config import parse_config, uuid_value
from .contracts import ContractError, json_load, read_file, require
from .control import Control
from .execution import execute, observe
from .host import binding_guard, live_guard
from .maintenance import Maintenance, safe_system
from .native import (TOKEN, NativeClock, NativeRuntime, NativeSystem, bootstrap, linux_root,
                     ensure_runtime_directory, native_journal, save_plan)
from .panel_http import LocalPanelReader, token_file
from .panel_write import LocalPanelSession
from .units import render

ACTIONS = ("anchor", "calendar", "units", "plan", "prepare", "apply", "cancel", "check", "audit", "pause", "resume", "remove", "resolve", "execute")


def add_arguments(actions):
    for name in ACTIONS:
        p = actions.add_parser(name, help="P2 " + name)
        if name == 'anchor':
            p.add_argument('--local-time',required=True)
            p.add_argument('--timezone',required=True)
            continue
        if name in ("calendar", "units", "plan"):
            p.add_argument("--config", required=True)
        if name not in ("calendar", "units"):
            p.add_argument("--local", action="store_true", required=True)
        if name == "plan":
            p.add_argument("--activation", choices=("paused", "enabled"), required=True)
            p.add_argument("--attestations", required=True)
            p.add_argument("--save", required=True)
        elif name in ("prepare", "apply", "cancel"):
            p.add_argument("--plan", required=True)
            p.add_argument("--expect-sha256", required=True)
        elif name not in ("calendar", "units"):
            p.add_argument("--task", required=True)
        if name == "resume":
            p.add_argument("--skip-to")
        elif name == "resolve":
            p.add_argument("--evidence", required=True)


def audit(control):
    """只读结构审计；不以文件声明冒充 journal 或业务验收。"""
    require(control.maintenance is not None, "AUDIT_HISTORY_EMPTY")
    period, due = control.next_authorization() if control.transitions else (None, None)
    now = datetime.now(timezone.utc)
    calendar = preview(control.config, now) if control.owner else {}
    reason = None
    try:
        control.ready()
    except ContractError as error:
        reason = error.code
    if reason is None and due and (now-due).total_seconds() >= WINDOW_SECONDS:
        reason = 'MISSED_BOUNDARY_REVIEW_REQUIRED'
    eligibility = ('BLOCKED' if reason else 'IN_WINDOW' if due and now >= due else 'FUTURE')
    completed = max((p for p,s in control.ledgers.items() if s['completed_at']), default=None)
    return {"result": "CONTROL_CONTRACT_VALID", "task_id": control.maintenance["task_id"],
            'schedule_kind': calendar.get('schedule_kind'),
            'next_wakeup_utc': calendar.get('next_wakeup_utc'),
            'next_business_boundary_utc': calendar.get('next_business_boundary_utc'),
            'authorized_boundary_utc': stamp(due) if due else None,
            'first_allowed_utc': calendar.get('first_allowed_utc'), 'eligibility': eligibility,
            'eligibility_reason': reason, 'eligibility_scope': 'CONTROL_AND_TIME_ONLY',
            'last_completed_period': completed,
            'last_completed_boundary_utc': control.ledgers[completed]['boundary_utc'] if completed is not None else None,
            "revision": control.owner["active_revision"] if control.owner else None,
            "owner_status": control.owner["status"] if control.owner else "NOT_REGISTERED",
            "control_head_sha256": control.view.head, "events": len(control.view.events),
            "maintenance": control.maintenance["stage"], "maintenance_kind": control.maintenance["kind"],
            "maintenance_error_code": control.maintenance["error_code"], "unresolved_periods": control.unresolved(),
            "next_period_id": period, "next_authorized_boundary_utc": stamp(due) if due else None,
            "periods": [{"period_id": p, "revision": s["revision"], "boundary_utc": s["boundary_utc"],
                         "completed_at": s["completed_at"], "steps": [{"object": a["object"], "status": a["status"]} for a in s["steps"]],
                         "ledger_sha256": control.ledger_hashes[p]} for p, s in sorted(control.ledgers.items())],
            "script_writes": 0, "post_requests": 0, "systemd_journal_and_business_acceptance": "NOT_PROVEN"}


def run(args):
    if args.action == 'anchor':
        from .calendar import anchor_from_local
        return {'result':'ANCHOR_CONVERTED','anchor_utc':anchor_from_local(args.local_time,args.timezone),
                'interval_seconds':2592000,'script_writes':0,'authorization':'NOT_GRANTED'}
    if args.action in ("calendar", "units"):
        cfg = parse_config(read_file(args.config))
        return ({"result": "CALENDAR_PREVIEW", **preview(cfg, datetime.now(timezone.utc)), "script_writes": 0}
                if args.action == "calendar" else {"result": "UNITS_RENDERED_ONLY", "units": render(cfg), "script_writes": 0})
    linux_root()  # 在任何状态目录创建、凭据读取和系统命令之前。
    journal, system, clock = native_journal(), NativeSystem(), NativeClock()
    from .deploy_native import context, guard_existing
    migration = guard_existing(journal)
    legacy_check = None
    if migration:
        backend = context(journal).backend
        legacy_check = lambda cfg: backend.retired(cfg, migration)
        from .sources import kind_of
        system = NativeSystem(legacy_check=legacy_check, legacy_kind=kind_of(migration))
    def reader(cfg):
        live_guard(cfg)
        return LocalPanelReader(cfg, token_file(TOKEN))
    manager = Maintenance(journal, system, reader, clock)
    if args.action == "plan":
        cfg = parse_config(read_file(args.config, protected=True))
        p = manager.plan(cfg, args.activation, json_load(read_file(args.attestations, protected=True)))
        save_plan(args.save, p)
        return {"result": "PLAN_SAVED", "sha256": p["sha256"], "kind": p["kind"], "activation": p["activation"],
                "calendar": p["calendar"], "effective_boundary_utc": p["effective_boundary_utc"],
                "changes": p["changes"],
                "timer_changed": False, "post_requests": 0}
    if args.action in ("prepare", "apply", "cancel"):
        p = json_load(read_file(args.plan, protected=True))
        require(type(p) is dict and p.get("sha256") == args.expect_sha256, "PLAN_CONFIRMATION_MISMATCH")
        candidate_config = manager._candidate(p)
        binding_guard(candidate_config)
        if args.action == "prepare":
            bootstrap()
            return manager.prepare(p)
        ensure_runtime_directory()
        return getattr(manager, args.action)(p)
    uuid_value(args.task)
    control = Control(journal.load())
    if args.action == "audit":
        require(control.maintenance and control.maintenance["task_id"] == args.task, "TASK_CONFIRMATION_MISMATCH")
        result = audit(control)
        result["system"] = (system.inspect(control.config) if control.owner else
                            {"state": "NOT_QUERIED_NO_REGISTERED_CONFIG"})
        require(journal.load().head == control.view.head, "READONLY_CONTROL_CHANGED")
        return result
    require(control.owner and control.owner["task_id"] == args.task, "TASK_CONFIRMATION_MISMATCH")
    if args.action == "check":
        control.ready()
        before_wall, before_mono, synced = clock.sample()
        require(synced is True, "CLOCK_UNSYNCHRONIZED")
        require(control.next_authorization()[1] > before_wall, "DUE_OR_MISSED_REQUIRES_AUDIT")
        info = system.inspect(control.config)
        safe_system(info)
        require(info["service"] == "inactive" and not info["jobs"] and info["timer"] in ("paused", "waiting"), "READONLY_SERVICE_OR_TIMER_BUSY")
        system.verify(control.config)
        require(system.program_sha256 == control.owner["program_sha256"], "PROGRAM_CHANGED")
        p = reader(control.config)
        started = time.monotonic()
        observe(control.config, p)
        elapsed = time.monotonic() - started
        after_wall, after_mono, synced = clock.sample()
        require(synced is True and after_mono >= before_mono
                and abs((after_wall - before_wall).total_seconds() - (after_mono - before_mono)) <= 2, "CLOCK_UNSTABLE")
        require(control.next_authorization()[1] > after_wall, "DUE_OR_MISSED_REQUIRES_AUDIT")
        require(system.inspect(control.config) == info, "READONLY_SYSTEM_CHANGED")
        require(journal.load().head == control.view.head, "READONLY_CONTROL_CHANGED")
        return {"result": "READONLY_CHECK_PASS", "system": info, "ntp_synchronized": True,
                "get_requests": p.attempts, "post_requests": 0, "script_writes": 0,
                "readonly_api_seconds": round(elapsed, 3),
                "write_authorization_and_execution": "NOT_TESTED"}
    if args.action == "execute":
        from .sources import kind_of, VMISS
        runtime = NativeRuntime(args.task, legacy_check=legacy_check, legacy_kind=kind_of(migration) or VMISS)
        binding_guard(control.config)
        ensure_runtime_directory()
        return execute(journal, lambda cfg: LocalPanelSession(cfg, runtime.credential()), runtime, clock)
    binding_guard(control.config)
    ensure_runtime_directory()
    if args.action == "resolve":
        return manager.resolve_future(json_load(read_file(args.evidence, protected=True)))
    if args.action == "resume":
        return manager.resume(args.skip_to)
    return getattr(manager, args.action)()
