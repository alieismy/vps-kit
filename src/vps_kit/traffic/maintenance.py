"""原生任务维护。prepare 与 apply 分开，便于暂停/备份后手工改额。"""
from copy import deepcopy
from datetime import timedelta
import uuid

from .calendar import MAINTENANCE_SECONDS, maintenance_window, next_boundary, preview, stamp, switching_boundary
from .config import parse_config
from .contracts import ContractError, canonical, digest, fields, require, utc
from .control import Control, FINISHED, commit
from .execution import observe


def quiet(system):
    require(system["timer"] in ("paused", "absent") and system["service"] in ("inactive", "absent")
            and system["jobs"] is False, "SYSTEM_NOT_QUIESCENT")


def safe_system(system):
    require(system["dropins"] is False and system["foreign_jobs"] is False and system["reload"] is False,
            "SYSTEM_CONFLICT_OR_OVERRIDE")


def admissible(config, snapshot):
    require(snapshot["inbound"]["enabled"] and snapshot["inbound"]["used_bytes"] < config.data["inbound"]["quota_bytes"],
            "INSTALL_INBOUND_NOT_AVAILABLE")
    for c in config.data["clients"]:
        if c["policy"] == "hold":
            continue
        s = snapshot["client:" + str(c["record_id"])]
        require(s["enabled"] and s["traffic_enabled"] and (c["quota_bytes"] == 0 or s["used_bytes"] < c["quota_bytes"]),
                "INSTALL_CLIENT_NOT_AVAILABLE")


def candidate_digest(candidate):
    return digest({k: v for k, v in candidate.items() if k != "sha256"})


class Maintenance:
    def __init__(self, journal, system, panel_factory, clock):
        self.journal, self.system, self.panel_factory, self.clock = journal, system, panel_factory, clock
        self.clock_start = None

    def _clock_begin(self):
        self.clock_start = self.clock.sample()[:2]

    def now(self):
        wall, mono, synced = self.clock.sample()
        require(synced is True, "CLOCK_UNSYNCHRONIZED")
        if self.clock_start:
            old_wall, old_mono = self.clock_start
            require(mono >= old_mono and abs((wall - old_wall).total_seconds() - (mono - old_mono)) <= 2,
                    "MAINTENANCE_CLOCK_JUMP")
        return wall

    def plan(self, config, activation, attestations):
        self._clock_begin()
        fields(attestations, "admin_scope local_accounting other_reset_jobs_absent", "PLAN_ATTESTATIONS")
        require(all(v is True for v in attestations.values()), "PLAN_MANUAL_CONFIRMATIONS_REQUIRED")
        require(activation in ("paused", "enabled"), "PLAN_ACTIVATION")
        control = Control(self.journal.load())
        require(control.maintenance is None or control.maintenance["stage"] in FINISHED, "MAINTENANCE_INCOMPLETE")
        now = self.now()
        old = control.config if control.owner else None
        info = self.system.inspect(old or config)
        safe_system(info)
        require(info["service"] in ("inactive", "absent") and not info["jobs"], "SERVICE_OR_JOB_ACTIVE")
        if old:
            control.ready()
            require(info["program_sha256"] == control.owner["program_sha256"], "PROGRAM_CHANGED_P3_UPGRADE_REQUIRED")
            a, b = old.data, config.data
            for key in ("task_id", "host_id_sha256", "panel_binding_id"):
                require(a[key] == b[key], "OWNER_BINDING_CHANGED")
            require(a["inbound"]["id"] == b["inbound"]["id"] and b["revision"] == a["revision"] + 1, "PLAN_REVISION_OR_OWNER")
            period, old_next = control.next_authorization()
            require(old_next > now, "OLD_DUE_HISTORY_MISSING")
            effective = switching_boundary(old, config, now, old_next)
            require(info["timer"] != "paused" or activation == "paused", "PLAN_PRESERVE_PAUSED")
            maintenance_window([a["schedule"], b["schedule"]], now)
            self.system.verify(old)
        else:
            reusable = (control.maintenance and control.maintenance["stage"] == "cancelled"
                        and control.maintenance["task_id"] == config.data["task_id"]
                        and all(e["kind"] == "maintenance" for e in control.view.events))
            require((not control.view.events or reusable) and config.data["revision"] == 1, "OWNER_CREATE_NOT_EMPTY")
            require(info["timer"] == info["service"] == "absent", "OWNER_CONFLICT")
            period, old_next = 1, None
            effective = utc(config.data["schedule"]["first_boundary_utc"], "PLAN_FIRST")
            require(effective > now + timedelta(seconds=MAINTENANCE_SECONDS), "PLAN_NOT_FUTURE")
            maintenance_window([config.data["schedule"]], now)
        calendar = preview(config, now)
        changes = {"old": old.summary() if old else None, "new": config.summary(),
                   "object_binding_changed": bool(old and old.object_fingerprint != config.object_fingerprint),
                   "panel_configuration_changed": bool(old and old.data["panel"] != config.data["panel"]),
                   "panel_api_may_restart_xray": True,
                   "quota_change_method": "MANUAL_PANEL_AFTER_PREPARE", "automatic_reset_during_maintenance": False}
        candidate = {"schema_version": "vps-kit.traffic.plan/v1", "operation_id": str(uuid.uuid4()),
                     "kind": "change" if old else "create", "config": config.data, "base_head": control.view.head,
                     "before_system": info, "activation": activation, "attestations": attestations,
                     "next_period_id": period, "old_next_boundary_utc": stamp(old_next) if old_next else None,
                     "effective_boundary_utc": stamp(effective), "created_at": stamp(now), "calendar": calendar, "changes": changes}
        return {**candidate, "sha256": candidate_digest(candidate)}

    def _candidate(self, p):
        fields(p, "schema_version operation_id kind config base_head before_system activation attestations next_period_id old_next_boundary_utc effective_boundary_utc created_at calendar changes sha256", "PLAN_FIELDS")
        require(p["schema_version"] == "vps-kit.traffic.plan/v1" and p["sha256"] == candidate_digest(p), "PLAN_HASH")
        return parse_config(canonical(p["config"]))

    def _save(self, control, manifest, stage, error=None):
        manifest = deepcopy(manifest)
        manifest["stage"], manifest["error_code"] = stage, error
        return commit(self.journal, "maintenance", manifest, control.view.head)

    def _failure(self, manifest, config, error):
        # 尽力保存实际状态；这里不重试系统命令，不恢复 timer，也不覆盖原异常。
        try:
            manifest = deepcopy(manifest)
            try:
                manifest["observed_system"] = self.system.inspect(config)
            except (ContractError, OSError):
                manifest["observed_system"] = {"state": "UNKNOWN"}
            control = Control(self.journal.load())
            self._save(control, manifest, "failed", error.code if isinstance(error, ContractError) else "LOCAL_IO_FAILURE")
        except (ContractError, OSError):
            pass

    def prepare(self, candidate):
        config = self._candidate(candidate)
        with self.journal.exclusive():
            control = Control(self.journal.load())
            require(control.view.head == candidate["base_head"], "PLAN_STALE")
            # 重做所有动态门槛，但保持已确认的操作身份和计划内容。
            fresh = self.plan(config, candidate["activation"], candidate["attestations"])
            for key in ("kind", "before_system", "next_period_id", "old_next_boundary_utc", "effective_boundary_utc"):
                require(candidate[key] == fresh[key], "PLAN_STALE")
            old = control.config if control.owner else None
            if old:
                observe(old, self.panel_factory(old))
            maintenance_window(([old.data["schedule"]] if old else []) + [config.data["schedule"]], self.now())
            manifest = {"operation_id": candidate["operation_id"], "task_id": config.data["task_id"],
                        "kind": candidate["kind"], "stage": "preparing", "plan_sha256": candidate["sha256"],
                        "base_head": candidate["base_head"], "before_system": candidate["before_system"],
                        "backup": None, "observed_system": candidate["before_system"], "error_code": None}
            control = self._save(control, manifest, "preparing")
            try:
                if old:
                    self.system.pause(old)
                manifest["observed_system"] = self.system.inspect(old or config)
                quiet(manifest["observed_system"])
                control = self._save(control, manifest, "paused")
                backup = {"head": candidate["base_head"], "config": old.data if old else None,
                          "units": self.system.backup(old or config)}
                manifest["backup"] = {"content": backup, "sha256": digest(backup)}
                self._save(control, manifest, "prepared")
                return {"result": "PREPARED", "timer": manifest["observed_system"]["timer"], "post_requests": 0,
                        "next_action": "ALIGN_PANEL_THEN_APPLY", "backup_sha256": digest(backup)}
            except (ContractError, OSError) as error:
                self._failure(manifest, old or config, error)
                raise

    def apply(self, candidate):
        self._clock_begin()
        config = self._candidate(candidate)
        with self.journal.exclusive():
            control = Control(self.journal.load())
            manifest = deepcopy(control.maintenance)
            require(manifest and manifest["stage"] == "prepared" and manifest["plan_sha256"] == candidate["sha256"], "APPLY_NOT_PREPARED")
            old = control.config if control.owner else None
            installing = False
            try:
                info = self.system.inspect(old or config)
                quiet(info)
                safe_system(info)
                require(info["program_sha256"] == candidate["before_system"]["program_sha256"], "PROGRAM_CHANGED")
                backup = manifest["backup"]
                require(backup and digest(backup["content"]) == backup["sha256"], "BACKUP_CHANGED")
                require(self.system.backup(old or config) == backup["content"]["units"], "UNITS_CHANGED_SINCE_BACKUP")
                schedules = [config.data["schedule"]] + ([old.data["schedule"]] if old else [])
                maintenance_window(schedules, self.now())
                require(utc(candidate["effective_boundary_utc"], "PLAN_BOUNDARY") > self.now() + timedelta(seconds=MAINTENANCE_SECONDS), "PLAN_EXPIRED")
                admissible(config, observe(config, self.panel_factory(config)))
                maintenance_window(schedules, self.now())
                require(utc(candidate["effective_boundary_utc"], "PLAN_BOUNDARY") > self.now() + timedelta(seconds=MAINTENANCE_SECONDS), "PLAN_EXPIRED")
                require(self.system.inspect(old or config) == info, "APPLY_SYSTEM_CHANGED")
                installing = True  # 从此处起，提交结果不确定也必须保留维护阻断。
                control = self._save(control, manifest, "installing")
                control = commit(self.journal, "revision", {"config": config.data,
                                 "parent_revision": old.data["revision"] if old else None,
                                 "config_sha256": config.fingerprint}, control.view.head)
                d = config.data
                owner = {k: d[k] for k in ("task_id", "host_id_sha256", "panel_binding_id")}
                owner.update(inbound_id=d["inbound"]["id"], active_revision=d["revision"], status="active",
                             program_sha256=candidate["before_system"]["program_sha256"])
                control = commit(self.journal, "registry", owner, control.view.head)
                transition = {"operation_id": candidate["operation_id"], "from_revision": old.data["revision"] if old else None,
                              "to_revision": d["revision"], "previous_period_id": candidate["next_period_id"] - 1,
                              "next_period_id": candidate["next_period_id"], "effective_boundary_utc": candidate["effective_boundary_utc"],
                              "old_next_boundary_utc": candidate["old_next_boundary_utc"], "reason": candidate["kind"]}
                control = commit(self.journal, "transition", transition, control.view.head)
                maintenance_window(schedules, self.now())
                require(self.system.program_sha256 == control.owner["program_sha256"], "PROGRAM_CHANGED")
                self.system.install(config)
                self.system.reload()
                self.system.verify(config)
                manifest["observed_system"] = self.system.inspect(config)
                quiet(manifest["observed_system"])
                control = self._save(control, manifest, "verifying")
                admissible(config, observe(config, self.panel_factory(config)))
                self._save(control, manifest, "complete")
            except (ContractError, OSError) as error:
                if installing:
                    self._failure(manifest, config, error)
                raise
            # 同一确认的安装和恢复共用一次锁；释放后不能再覆盖另一操作者的暂停。
            if candidate["activation"] == "enabled":
                return self._resume_locked()
            return {"result": "INSTALLED_PAUSED", "post_requests": 0}

    def cancel(self, candidate):
        """只取消尚未安装的 prepared 计划；旧面板契约须先由操作者恢复。"""
        self._clock_begin()
        config = self._candidate(candidate)
        with self.journal.exclusive():
            control = Control(self.journal.load())
            manifest = deepcopy(control.maintenance)
            require(manifest and manifest["stage"] == "prepared" and manifest["plan_sha256"] == candidate["sha256"], "CANCEL_NOT_PREPARED")
            old = control.config if control.owner else None
            info = self.system.inspect(old or config)
            quiet(info)
            safe_system(info)
            require(info["program_sha256"] == candidate["before_system"]["program_sha256"], "PROGRAM_CHANGED")
            backup = manifest["backup"]
            require(backup and digest(backup["content"]) == backup["sha256"]
                    and self.system.backup(old or config) == backup["content"]["units"], "CANCEL_BACKUP_OR_UNITS_CHANGED")
            if old:
                require(old.data == backup["content"]["config"], "CANCEL_CONFIG_CHANGED")
                self.system.verify(old)
                observe(old, self.panel_factory(old))
            manifest["observed_system"] = info
            self._save(control, manifest, "cancelled")
            return {"result": "PREPARED_CHANGE_CANCELLED", "timer": info["timer"], "post_requests": 0,
                    "resume": "SEPARATE_EXPLICIT_ACTION", "panel_modified": False}

    def _simple(self, kind, action, require_ready=True, preflight=None):
        with self.journal.exclusive():
            return self._simple_locked(kind, action, require_ready, preflight)

    def _simple_locked(self, kind, action, require_ready=True, preflight=None):
        require(self.journal.held, "WRITE_REQUIRES_SHARED_LOCK")
        self._clock_begin()
        control = Control(self.journal.load())
        if require_ready:
            control.ready()
        require(control.owner is not None and control.owner["status"] == "active", "OWNER_NOT_ACTIVE")
        require(control.maintenance is None or control.maintenance["stage"] in FINISHED, "MAINTENANCE_INCOMPLETE")
        cfg = control.config
        info = self.system.inspect(cfg)
        safe_system(info)
        if kind != "pause":
            maintenance_window([cfg.data["schedule"]], self.now())
            require(info["program_sha256"] == control.owner["program_sha256"], "PROGRAM_CHANGED")
        prepared = preflight(control, cfg) if preflight else None
        manifest = {"operation_id": str(uuid.uuid4()), "task_id": cfg.data["task_id"], "kind": kind,
                    "stage": "preparing", "plan_sha256": None, "base_head": control.view.head,
                    "before_system": info, "backup": None, "observed_system": info, "error_code": None}
        control = self._save(control, manifest, "preparing")
        try:
            control = action(control, cfg, manifest, prepared)
            manifest["observed_system"] = self.system.inspect(cfg)
            self._save(control, manifest, "complete")
            return {"result": kind.upper() + "_COMPLETE", "timer": manifest["observed_system"]["timer"], "post_requests": 0}
        except (ContractError, OSError) as error:
            self._failure(manifest, cfg, error)
            raise

    def pause(self):
        def action(control, cfg, manifest, prepared):
            self.system.pause(cfg)
            quiet(self.system.inspect(cfg))
            return control
        return self._simple("pause", action, require_ready=False)

    def resume(self, skip_to=None):
        with self.journal.exclusive():
            return self._resume_locked(skip_to)

    def _resume_locked(self, skip_to=None):
        def preflight(control, cfg):
            # 已启用/漏期参数错误在创建维护意图前拒绝，不能因此锁死正常任务。
            quiet(self.system.inspect(cfg))
            self.system.verify(cfg)
            period, original_due = control.next_authorization()
            due = original_due
            now = self.now()
            if due <= now:
                require(skip_to is not None, "MISSED_REQUIRES_EXPLICIT_SKIP")
                target = utc(skip_to, "RESUME_SKIP_BOUNDARY")
                require(target == next_boundary(cfg.data["schedule"], now) and target > due, "RESUME_SKIP_BOUNDARY")
                due = target
            else:
                require(skip_to is None, "UNNECESSARY_SKIP")
            require(due > now + timedelta(seconds=MAINTENANCE_SECONDS), "RESUME_NEAR_BOUNDARY")
            observe(cfg, self.panel_factory(cfg))
            maintenance_window([cfg.data["schedule"]], self.now())
            require(due > self.now() + timedelta(seconds=MAINTENANCE_SECONDS), "RESUME_NEAR_BOUNDARY")
            return period, original_due, due

        def action(control, cfg, manifest, prepared):
            period, original_due, due = prepared
            if due != original_due:
                control = commit(self.journal, "transition", {"operation_id": manifest["operation_id"],
                                 "from_revision": cfg.data["revision"], "to_revision": cfg.data["revision"],
                                 "previous_period_id": period - 1, "next_period_id": period,
                                 "effective_boundary_utc": stamp(due), "old_next_boundary_utc": stamp(original_due),
                                 "reason": "resume-skip"}, control.view.head)
            quiet(self.system.inspect(cfg))
            require(self.system.program_sha256 == control.owner["program_sha256"], "PROGRAM_CHANGED")
            self.system.verify(cfg)
            # 所有耗时检查之后、enable 命令之前，重新验证墙钟/单调钟和安全距离。
            maintenance_window([cfg.data["schedule"]], self.now())
            require(due > self.now() + timedelta(seconds=MAINTENANCE_SECONDS), "RESUME_NEAR_BOUNDARY")
            self.system.resume(cfg)
            info = self.system.inspect(cfg)
            require(info["timer"] == "waiting" and info["service"] == "inactive" and not info["jobs"], "RESUME_NOT_CONFIRMED")
            self.system.verify(cfg)
            maintenance_window([cfg.data["schedule"]], self.now())
            require(due > self.now() + timedelta(seconds=MAINTENANCE_SECONDS), "RESUME_NEAR_BOUNDARY")
            return control
        return self._simple_locked("resume", action, preflight=preflight)

    def remove(self):
        def action(control, cfg, manifest, prepared):
            self.system.pause(cfg)
            quiet(self.system.inspect(cfg))
            self.system.verify(cfg)
            manifest["backup"] = {"content": self.system.backup(cfg)}
            manifest["backup"]["sha256"] = digest(manifest["backup"]["content"])
            control = self._save(control, manifest, "prepared")
            self.system.remove(cfg)
            self.system.reload()
            info = self.system.inspect(cfg)
            require(info["timer"] == info["service"] == "absent", "REMOVE_NOT_CONFIRMED")
            return commit(self.journal, "registry", {**control.owner, "status": "removed"}, control.view.head)
        return self._simple("remove", action, require_ready=False)

    def resolve_future(self, evidence):
        def preflight(control, cfg):
            quiet(self.system.inspect(cfg))
            require(len(control.unresolved()) == 1, "RESOLVE_SINGLE_UNRESOLVED_REQUIRED")
            control.validate_resolution(evidence)
            period = control.unresolved()[0]
            require(evidence["period_id"] == period, "RESOLUTION_PERIOD")
            target = utc(evidence["not_before_boundary_utc"], "RESOLUTION_TIME")
            _, due = control.next_authorization()
            require(target >= next_boundary(cfg.data["schedule"], self.now()) and target > due, "RESOLUTION_NOT_FUTURE")
            return period, target, due

        def action(control, cfg, manifest, prepared):
            period, target, due = prepared
            maintenance_window([cfg.data["schedule"]], self.now())
            require(target > self.now() + timedelta(seconds=MAINTENANCE_SECONDS), "RESOLUTION_NOT_FUTURE")
            control = commit(self.journal, "resolution", evidence, control.view.head)
            return commit(self.journal, "transition", {"operation_id": manifest["operation_id"],
                          "from_revision": cfg.data["revision"], "to_revision": cfg.data["revision"],
                          "previous_period_id": period, "next_period_id": period + 1,
                          "effective_boundary_utc": stamp(target), "old_next_boundary_utc": stamp(due),
                          "reason": "resolve-future"}, control.view.head)
        return self._simple("resolve", action, require_ready=False, preflight=preflight)
