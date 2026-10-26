"""Linux/systemd 适配器。固定本机路径；测试必须注入命令替身，不在开发机执行。"""
import hashlib
import os
from pathlib import Path
import platform
import re
import stat
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

from .calendar import expressions, next_boundary, next_wakeup, stamp, walk, wakeups
from .contracts import ContractError, canonical, digest, read_file, require
from .host import binding_guard, command, live_guard
from .journal import DurableFiles, Journal, file_lock, check_lock_identities
from .panel_http import bounded_process
from .units import render, stem, verify_text

STORE = Path("/var/lib/vps-kit/traffic/control")
LOCK = Path("/run/vps-kit-traffic/execute.lock")
TOKEN = Path("/etc/vps-kit/traffic/api-token")
UNIT_DIR = Path("/etc/systemd/system")
ENTRY = Path("/usr/local/bin/vps-kit")
OLD_LOCK = Path('/run/vmiss-traffic-reset/reset.lock')
PROPS = ("Id", "LoadState", "ActiveState", "SubState", "UnitFileState", "MainPID", "Job", "DropInPaths",
         "NeedDaemonReload", "FragmentPath", "NextElapseUSecRealtime", "InvocationID", "Type", "Restart",
         "RemainAfterExit", "TimeoutStartUSec", "Persistent", "AccuracyUSec", "RandomizedDelayUSec",
         "RuntimeDirectoryPreserve", "Triggers")
PROPS += ('ControlGroup', 'ExecMainStartTimestamp', 'ExecMainExitTimestamp', 'NRestarts')


def linux_root():
    require(platform.system() == "Linux" and os.geteuid() == 0, "P2_REQUIRES_LINUX_ROOT")


def protected_bytes(path, modes=(0o644, 0o755), limit=2 * 1024 * 1024):
    path = Path(path).absolute()
    DurableFiles().directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        s = os.fstat(stream.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == s.st_gid == 0 and stat.S_IMODE(s.st_mode) in modes,
                "NATIVE_FILE_PERMISSIONS")
        raw = stream.read(limit + 1)
        require(len(raw) <= limit, "NATIVE_FILE_SIZE")
        return raw


def program_hash():
    package = Path(__file__).absolute().parents[1]
    pointer = Path('/usr/local/lib/vps-kit/current')
    if pointer.exists() or pointer.is_symlink():
        target = os.readlink(pointer)
        require(re.fullmatch(r'releases/0\.[0-9]+\.[0-9]+(?:a[0-9]+)?', target)
                and package == pointer.parent / target / 'src/vps_kit', 'PROGRAM_NOT_ACTIVE_RELEASE')
    content = [("entry", hashlib.sha256(protected_bytes(ENTRY, (0o755,))).hexdigest())]
    for path in sorted(package.rglob("*.py")):
        content.append((path.relative_to(package).as_posix(), hashlib.sha256(protected_bytes(path)).hexdigest()))
    require(len(content) > 1, "PROGRAM_FILES_MISSING")
    return digest(content)


def bootstrap():
    """仅显式 prepare 调用。只建立受控目录，不创建 Token、不改变现有任务。"""
    linux_root()
    files = DurableFiles()
    for path in (Path("/var/lib/vps-kit"), STORE.parent):
        files.directory(path.parent)
        if not path.exists():
            path.mkdir(mode=0o700)
            files.sync_directory(path.parent)
        files.directory(path)
        require(stat.S_IMODE(path.stat().st_mode) == 0o700, "STATE_DIRECTORY_MODE")
    ensure_runtime_directory()


def ensure_runtime_directory():
    """重启后 /run 可能消失；只在显式写操作中重建目录，绝不重建已有锁 inode。"""
    linux_root()
    files = DurableFiles()
    kind = committed_or_detected_source()
    from .sources import paths
    for path in [LOCK.parent] + ([Path(paths(kind)['lock']).parent] if kind else []):
        files.directory(path.parent)
        if not path.exists():
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                pass  # 并发创建后仍核对权限，已有锁 inode 永不删除。
            files.sync_directory(path.parent)
        files.directory(path)
        require(stat.S_IMODE(path.stat().st_mode) == 0o700, "RUNTIME_DIRECTORY_MODE")


def ensure_source_runtime(kind):
    from .sources import paths
    linux_root()
    path = Path(paths(kind)['lock']).parent
    files = DurableFiles()
    files.directory(path.parent)
    if not path.exists():
        path.mkdir(mode=0o700)
        files.sync_directory(path.parent)
    files.directory(path)
    require(stat.S_IMODE(path.stat().st_mode) == 0o700, 'RUNTIME_DIRECTORY_MODE')


def committed_or_detected_source():
    """已有部署记录为权威；删掉来源目录不能取消旧锁。只读且不创建记录。"""
    from .deploy_files import DeploymentFiles, DeploymentLog
    from .sources import VMISS, VMRACK, kind_of, paths
    _, _, latest = DeploymentLog(DeploymentFiles(), Journal(STORE, LOCK)).read()
    if latest:
        if latest['migration']:
            return kind_of(latest['migration'])
        if latest['plan']['kind'] in ('migrate', 'vmrack-migrate'):
            return VMRACK if latest['plan']['kind'] == 'vmrack-migrate' else VMISS
    found = []
    for kind in (VMISS, VMRACK):
        p = paths(kind)
        if any(x.exists() or x.is_symlink() for x in (Path(p['config']).parent, Path(p['state']), Path(p['runner']))):
            found.append(kind)
    require(len(found) <= 1, 'MULTIPLE_LEGACY_SOURCES')
    return found[0] if found else None


def legacy_present():
    return any(p.exists() or p.is_symlink() for p in (Path('/etc/vmiss-traffic-reset'), Path('/var/lib/vmiss-traffic-reset'),
                                                    Path('/usr/local/sbin/vmiss-3xui-traffic-reset')))


@contextmanager
def ordered_locks(path, legacy, lock=file_lock):
    """全局固定顺序；第二把锁失败时释放第一把，不等待、不换 inode。"""
    with lock(path):
        if legacy:
            from .sources import paths, VMISS
            with lock(Path(paths(VMISS if legacy is True else legacy)['lock'])):
                yield
        else:
            yield


def native_journal(deployment_guard=True, migration_setup=False):
    @contextmanager
    def locked(path):
        with ordered_locks(path, None if migration_setup else committed_or_detected_source()):
            if deployment_guard:
                from .deploy_native import guard_existing
                guard_existing(journal)
            yield
    journal = Journal(STORE, LOCK, lock=locked)
    return journal


def candidate_path(path):
    """候选只写管理区之外，不能绕开锁/部署事务创建活动凭据或 unit。"""
    path = Path(os.path.abspath(path))
    roots = ('/usr/local/lib/vps-kit', '/usr/local/bin/vps-kit', '/etc/vps-kit', '/var/lib/vps-kit',
             '/run/vps-kit-traffic', '/run/vmiss-traffic-reset', '/etc/vmiss-traffic-reset',
             '/var/lib/vmiss-traffic-reset', '/usr/local/sbin/vmiss-3xui-traffic-reset', '/etc/systemd/system')
    roots += ('/run/vmrack-traffic-reset', '/etc/vmrack-traffic-reset', '/var/lib/vmrack-traffic-reset', '/usr/local/sbin/vmrack-3xui-traffic-reset')
    require(not any(path == Path(os.path.abspath(root)) or path.is_relative_to(Path(os.path.abspath(root))) for root in roots), 'CANDIDATE_MANAGED_PATH')
    return path


def save_plan(path, value):
    """只写用户明确选择的新候选文件，不覆盖已确认计划。"""
    path = candidate_path(path)
    DurableFiles().directory(path.parent)
    require(not path.exists() and not path.is_symlink(), "PLAN_DESTINATION_EXISTS")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write((canonical(value) + "\n").encode("utf-8"))
        stream.flush()
        os.fsync(stream.fileno())
    DurableFiles().sync_directory(path.parent)


def show(unit, execute):
    result = execute(["/usr/bin/systemctl", "show", unit, "--no-pager", "--property=" + ",".join(PROPS)], limit=32768)
    try:
        properties = {}
        for line in result.stdout.decode("utf-8").splitlines():
            key, sep, value = line.partition("=")
            require(sep and key in PROPS and key not in properties, "SYSTEMD_SHOW_FORMAT")
            properties[key] = value
    except UnicodeError:
        raise ContractError("SYSTEMD_SHOW_ENCODING") from None
    require(result.returncode == 0 or (result.returncode == 1 and properties.get("LoadState") == "not-found"), "SYSTEMD_SHOW_FAILED")
    require(all(k in properties for k in ("LoadState", "ActiveState", "SubState")), "SYSTEMD_SHOW_INCOMPLETE")
    return properties


def systemd_time(value):
    try:
        return datetime.strptime(value, "%a %Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ContractError("SYSTEMD_TIME_FORMAT") from None


def calendar_check(config, now, execute):
    """以固定 UTC 基准合并多表达式，独立核对未来 24 次 systemd 解析结果。"""
    found = set()
    for expression in expressions(config.data["schedule"]):
        raw = command(["/usr/bin/systemd-analyze", "calendar", "--iterations=24",
                       "--base-time=" + now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"), expression], execute)
        matches = re.findall(r"(?:Next elapse:|Iteration #\d+:)\s*(\w{3} \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)", raw)
        require(len(matches) == 24, "SYSTEMD_CALENDAR_OUTPUT")
        found.update(systemd_time(value) for value in matches)
    schedule = config.data['schedule']
    require(sorted(found)[:24] == list(wakeups(schedule, now, count=24)), "SYSTEMD_CALENDAR_MISMATCH")
    if schedule.get('kind') == 'fixed-interval':
        from .contracts import utc
        first = utc(schedule['first_boundary_utc'], 'CALENDAR_FIRST')
        from_first = walk(schedule, first, 24, inclusive=True) if first > now else walk(schedule, now, 24)
        points = set(walk(schedule, now, 24) + from_first)
        for point in sorted(points):
            raw = command(['/usr/bin/systemd-analyze', 'calendar', '--iterations=1',
                           '--base-time=' + (point-timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S UTC'),
                           expressions(schedule)[0]], execute)
            matches = re.findall(r'Next elapse:\s*(\w{3} \d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)', raw)
            require(len(matches) == 1 and systemd_time(matches[0]) == point, 'SYSTEMD_BUSINESS_BOUNDARY_MISMATCH')


class NativeClock:
    def __init__(self, execute=bounded_process):
        self.execute = execute

    def sample(self):
        sync = command(["/usr/bin/timedatectl", "show", "--property=NTPSynchronized", "--value"], self.execute)
        require(sync in ("yes", "no"), "CLOCK_NTP_OUTPUT")
        return datetime.now(timezone.utc), time.monotonic(), sync == "yes"


class NativeSystem:
    def __init__(self, execute=bounded_process, legacy_check=None, legacy_kind='vmiss-r2/v1'):
        self.execute = execute
        self.legacy_check = legacy_check
        self.legacy_kind = legacy_kind

    @property
    def program_sha256(self):
        return program_hash()

    def _run(self, *args):
        check_lock_identities()
        return command(["/usr/bin/systemctl", *args], self.execute)

    def _foreign(self, config):
        names = set(render(config))
        if self.legacy_check:
            from .sources import paths
            self.legacy_check(config)
            names |= {paths(self.legacy_kind)['timer'], paths(self.legacy_kind)['service']}
        units = self._run("list-unit-files", "--no-legend", "--no-pager", "vps-kit-traffic-*", "vmiss-3xui-traffic-reset.*", 'vmrack-3xui-traffic-reset.*')
        loaded = self._run("list-units", "--all", "--plain", "--no-legend", "--no-pager", "vps-kit-traffic-*", "vmiss-3xui-traffic-reset.*", 'vmrack-3xui-traffic-reset.*')
        for line in (units + "\n" + loaded).splitlines():
            if not line.strip():
                continue
            name = line.split()[0]
            require(name in names, "FOREIGN_OR_R2_TASK_P3_REQUIRED")
        from .sources import VMISS, VMRACK, paths
        for kind in (VMISS, VMRACK):
            for path in (Path(paths(kind)['config']).parent, Path(paths(kind)['state'])):
                require((self.legacy_check and self.legacy_kind == kind) or (not path.exists() and not path.is_symlink()), 'R2_MIGRATION_REQUIRES_P3')

    def backup(self, config):
        result = {}
        for name in render(config):
            path = UNIT_DIR / name
            if path.exists() or path.is_symlink():
                result[name] = protected_bytes(path, (0o644,)).decode("utf-8")
        return result

    def inspect(self, config):
        binding_guard(config, self.execute)
        self._foreign(config)
        name = stem(config.data["task_id"])
        timer, service = (show(name + suffix, self.execute) for suffix in (".timer", ".service"))
        def state(p, is_timer):
            if p["LoadState"] == "not-found":
                return "absent"
            require(p["LoadState"] == "loaded", "SYSTEMD_UNIT_NOT_LOADED")
            if is_timer:
                if (p["ActiveState"], p["SubState"], p.get("UnitFileState")) == ("active", "waiting", "enabled"):
                    return "waiting"
                if p["ActiveState"] == "inactive" and p.get("UnitFileState") == "disabled":
                    return "paused"
            elif p["ActiveState"] in ("inactive", "failed") and p.get("MainPID") == "0":
                return "inactive"  # 失败结果保留在账本；没有运行进程才允许人工核查/解除。
            return "other"
        return {"timer": state(timer, True), "service": state(service, False),
                "jobs": any(p.get("Job", "") not in ("", "0") for p in (timer, service)),
                "dropins": any(bool(p.get("DropInPaths", "")) for p in (timer, service)),
                "foreign_jobs": False, "reload": any(p.get("NeedDaemonReload", "no") != "no" for p in (timer, service)),
                "program_sha256": self.program_sha256, "units_sha256": digest(self.backup(config)),
                "next_utc": stamp(systemd_time(timer["NextElapseUSecRealtime"])) if state(timer, True) == "waiting" else None}

    def verify(self, config):
        verify_text(config, self.backup(config), legacy=self.legacy_kind if self.legacy_check else False)
        name = stem(config.data["task_id"])
        timer, service = (show(name + suffix, self.execute) for suffix in (".timer", ".service"))
        self._effective(config, timer, service)
        now = datetime.now(timezone.utc)
        calendar_check(config, now, self.execute)
        if timer["ActiveState"] == "active" and timer["SubState"] == "waiting":
            schedule = config.data['schedule']
            end = datetime.now(timezone.utc)
            expected = next_wakeup(schedule, now)
            # 跨一次日唤醒仅允许重读；两份 unit 原字节与两侧 effective 契约均重新核对。
            if schedule.get('kind') == 'fixed-interval' and now <= expected <= end and (end-now).total_seconds() < 86400:
                verify_text(config, self.backup(config), legacy=self.legacy_kind if self.legacy_check else False)
                timer, service = (show(name + suffix, self.execute) for suffix in ('.timer', '.service'))
                self._effective(config, timer, service)
                require(timer['ActiveState'] == 'active' and timer['SubState'] == 'waiting'
                        and timer.get('UnitFileState') == 'enabled'
                        and all(p.get('Job', '') in ('', '0') for p in (timer, service)), 'SYSTEMD_DAILY_WAKE_BUSY')
                # 长耗时或再次跨日不会静默取得第二次重采样机会。
                sampled = datetime.now(timezone.utc)
                require(next_wakeup(schedule, sampled) == next_wakeup(schedule, end), 'SYSTEMD_DAILY_WAKE_CHANGED_AGAIN')
                expected = next_wakeup(schedule, end)
            require(systemd_time(timer['NextElapseUSecRealtime']) == expected, 'SYSTEMD_NEXT_BOUNDARY')

    def _effective(self, config, timer, service):
        name = stem(config.data['task_id'])
        for p, suffix in ((timer, ".timer"), (service, ".service")):
            require(p["LoadState"] == "loaded" and p.get("FragmentPath") == str(UNIT_DIR / (name + suffix))
                    and not p.get("DropInPaths") and p.get("NeedDaemonReload") == "no", "SYSTEMD_EFFECTIVE_OVERRIDE")
        require(all(service.get(k) == v for k, v in {"Type": "oneshot", "Restart": "no", "RemainAfterExit": "no",
                    "TimeoutStartUSec": "5min", "RuntimeDirectoryPreserve": "yes"}.items()), "SYSTEMD_SERVICE_CONTRACT")
        require(all(timer.get(k) == v for k, v in {"Persistent": "no", "AccuracyUSec": "1s", "RandomizedDelayUSec": "0",
                    "Triggers": name + ".service"}.items()), "SYSTEMD_TIMER_CONTRACT")

    def install(self, config):
        check_lock_identities()
        files = DurableFiles()
        files.directory(UNIT_DIR)
        require(not list(UNIT_DIR.glob(".vps-kit-stage-*")), "UNIT_PARTIAL_WRITE")
        # 同目录替换与目录同步；任一步失败由维护记录阻止再次 apply。
        for name, value in render(config, legacy=self.legacy_kind if self.legacy_check else False).items():
            path = UNIT_DIR / name
            require(not path.is_symlink(), "UNIT_SYMLINK")
            fd, tmp = tempfile.mkstemp(prefix=".vps-kit-stage-", dir=UNIT_DIR)
            with os.fdopen(fd, "wb") as stream:
                os.chmod(tmp, 0o644)
                stream.write(value.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            check_lock_identities()
            os.replace(tmp, path)
            files.sync_directory(UNIT_DIR)
        command(["/usr/bin/systemd-analyze", "verify", "--man=no", *[str(UNIT_DIR / n) for n in render(config)]], self.execute)

    def reload(self):
        self._run("daemon-reload")

    def pause(self, config):
        self._run("disable", "--now", stem(config.data["task_id"]) + ".timer")

    def resume(self, config):
        self._run("enable", "--now", stem(config.data["task_id"]) + ".timer")

    def remove(self, config):
        for name in render(config):
            check_lock_identities()
            (UNIT_DIR / name).unlink()
            DurableFiles().sync_directory(UNIT_DIR)


class NativeRuntime(NativeSystem):
    def __init__(self, task, execute=bounded_process, legacy_check=None, legacy_kind='vmiss-r2/v1'):
        super().__init__(execute, legacy_check, legacy_kind)
        self.task = task
        self.invocation_id = os.environ.get("INVOCATION_ID", "")
        require(re.fullmatch(r"[0-9a-f]{32}", self.invocation_id), "EXECUTION_REQUIRES_SYSTEMD_INVOCATION")

    def guard(self, config, control):
        check_lock_identities()
        live_guard(config, self.execute)
        self._foreign(config)
        require(self.task == config.data["task_id"] and self.program_sha256 == control.owner["program_sha256"], "EXECUTION_BINDING")
        self.verify(config)
        p = show(stem(self.task) + ".service", self.execute)
        require(p.get("InvocationID") == self.invocation_id and p.get("MainPID") == str(os.getpid())
                and p["ActiveState"] in ("activating", "active"), "EXECUTION_NOT_OWN_SERVICE")
        check_lock_identities()

    def credential(self):
        expected = "/run/credentials/" + stem(self.task) + ".service"
        require(os.environ.get("CREDENTIALS_DIRECTORY") == expected, "CREDENTIAL_DIRECTORY")
        raw = protected_bytes(Path(expected) / "api-token", (0o400,), limit=128)
        try:
            token = raw.decode("ascii").removesuffix("\n")
        except UnicodeError:
            raise ContractError("TOKEN_FORMAT") from None
        require(re.fullmatch(r"[A-Za-z0-9]{48}", token), "TOKEN_FORMAT")
        return token
