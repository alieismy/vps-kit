"""P2 L1 替身：真实临时文件，模型目录同步/锁/面板/systemd；不代表 Linux 集成。"""
import copy
import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from support import config_data, fixture_data
from vps_kit.traffic.calendar import next_boundary, stamp
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import ContractError, canonical, read_file, require
from vps_kit.traffic.journal import Journal
from vps_kit.traffic.maintenance import Maintenance
from vps_kit.traffic.units import render, verify_text

ATTESTATIONS = {"admin_scope": True, "local_accounting": True, "other_reset_jobs_absent": True}


class ModelFiles:
    def __init__(self, root):
        self.root, self.syncs = root, []

    def directory(self, path):
        require(path.resolve().is_relative_to(self.root.resolve()) and path.is_dir() and not path.is_symlink(), "MODEL_DIRECTORY")

    def read(self, path):
        require(path.resolve().is_relative_to(self.root.resolve()), "MODEL_PATH_ESCAPE")
        return read_file(path)

    def sync_directory(self, path):
        self.directory(path)
        self.syncs.append(str(path))  # Windows 的目录 fsync 不在此伪装成功，明确是模型。


class ModelLock:
    def __init__(self):
        self.mutex = threading.Lock()

    @contextmanager
    def __call__(self, path):
        require(self.mutex.acquire(blocking=False), "LOCK_BUSY")
        try:
            yield
        finally:
            self.mutex.release()


class Clock:
    def __init__(self, now="2027-01-20T12:00:00Z"):
        self.wall = datetime.fromisoformat(now.replace("Z", "+00:00"))
        self.mono, self.synced = 1.0, True

    def sample(self):
        return self.wall, self.mono, self.synced

    def set(self, text):
        target = datetime.fromisoformat(text.replace("Z", "+00:00"))
        self.mono += (target - self.wall).total_seconds()
        self.wall = target


class Panel:
    def __init__(self, cfg):
        self.align(cfg)
        self.reads, self.writes, self.effects = [], [], []
        self.hook = lambda *_: None

    def align(self, cfg):
        self.cfg = cfg
        self.responses = fixture_data(cfg.data)["responses"]

    def get(self, endpoint, email=None):
        path = endpoint.path(email)
        self.reads.append(path)
        self.hook("get", path)
        return canonical(self.responses[path])

    def send(self, key):
        self.writes.append(key)
        self.hook("before_send", key)
        i = self.responses["/panel/api/inbounds/list"]["obj"][0]
        if key.startswith("client:"):
            c = next(c for c in self.cfg.data["clients"] if key == "client:" + str(c["record_id"]))
            e = next(e for e in i["settings"]["clients"] if e["email"] == c["email"])
            detail = self.responses["/panel/api/clients/get/" + c["email"]]["obj"]["client"]
            traffic = self.responses["/panel/api/clients/traffic/" + c["email"]]["obj"]
            order = ("enable", "reset") if self.cfg.data["panel"]["version"] == "3.7.0" else ("reset", "enable")
            for effect in order:
                if effect == "reset":
                    traffic.update(up=0, down=0)
                else:
                    e["enable"] = detail["enable"] = traffic["enable"] = True
                self.effects.append((key, effect))
        elif key == "inbound":
            i.update(up=0, down=0)
        elif key == "enable":
            i["enable"] = True
        else:
            raise AssertionError("unexpected write")
        self.hook("after_send", key)


class System:
    program_sha256 = "a" * 64
    invocation_id = "1" * 32

    def __init__(self, clock):
        self.clock = clock
        self.units = {}
        self.timer, self.service = "absent", "absent"
        self.jobs = self.dropins = self.foreign_jobs = self.reload_needed = False
        self.calls = []
        self.hook = lambda *_: None

    def inspect(self, cfg):
        from vps_kit.traffic.contracts import digest
        return {"timer": self.timer, "service": self.service, "jobs": self.jobs, "dropins": self.dropins,
                "foreign_jobs": self.foreign_jobs, "reload": self.reload_needed,
                "program_sha256": self.program_sha256, "units_sha256": digest(self.units),
                "next_utc": stamp(next_boundary(cfg.data["schedule"], self.clock.wall)) if self.timer == "waiting" else None}

    def backup(self, cfg):
        return copy.deepcopy(self.units)

    def install(self, cfg):
        self.calls.append("install")
        self.hook("before_install")
        for name, text in render(cfg).items():
            self.units[name] = text
            self.hook("installed_file")
        self.timer, self.service = "paused", "inactive"

    def reload(self):
        self.calls.append("reload")
        self.hook("reload")

    def verify(self, cfg):
        verify_text(cfg, self.units)
        require(not self.dropins and not self.reload_needed, "UNIT_OVERRIDE")

    def pause(self, cfg):
        self.calls.append("pause")
        self.hook("before_pause")
        self.timer = "paused"

    def resume(self, cfg):
        self.calls.append("resume")
        self.timer = "waiting"
        self.hook("after_resume")

    def remove(self, cfg):
        self.calls.append("remove")
        self.units = {}
        self.timer = self.service = "absent"

    def guard(self, cfg, control):
        self.verify(cfg)
        require(not self.foreign_jobs and not self.dropins, "SYSTEM_CONFLICT")
        require(control.owner["status"] == "active", "OWNER_NOT_ACTIVE")
        self.hook("execution_guard")


def harness(root, version="3.7.0", data=None):
    config = parse_config(json.dumps(data or config_data(version)))
    clock, lock = Clock(), ModelLock()
    journal = Journal(root / "state", root / "lock", files=ModelFiles(root), lock=lock)
    panel, system = Panel(config), System(clock)
    manager = Maintenance(journal, system, lambda cfg: panel, clock)
    return config, clock, journal, panel, system, manager


def install(h, activation="enabled"):
    cfg, clock, journal, panel, system, manager = h
    p = manager.plan(cfg, activation, ATTESTATIONS)
    manager.prepare(p)
    manager.apply(p)
    panel.reads.clear()
    return p
