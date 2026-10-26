"""追加式持久记录及稳定互斥。只读 load 不创建目录、锁或文件。"""
import hashlib
import os
import re
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .contracts import ContractError, canonical, fields, integer, json_load, read_file, require
HELD_LOCKS = {}


def check_lock_identities():
    for path, identity in HELD_LOCKS.items():
        try:
            s = path.lstat()
        except FileNotFoundError:
            raise ContractError('LOCK_REPLACED') from None
        require(stat.S_ISREG(s.st_mode) and (s.st_dev, s.st_ino) == identity, 'LOCK_REPLACED')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class DurableFiles:
    """真实 Linux 权限与同步实现；Windows 测试须显式提供目录同步/权限替身。"""
    def read(self, path):
        return read_file(path, protected=True)

    def directory(self, path):
        require(os.name == "posix", "DURABILITY_REQUIRES_POSIX")
        for p in [path, *path.parents]:
            s = p.lstat()
            require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                    "JOURNAL_DIRECTORY_UNSAFE")

    def sync_directory(self, path):
        require(os.name == "posix", "DIRECTORY_SYNC_UNSUPPORTED")
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


@contextmanager
def file_lock(path):
    require(os.name == "posix", "LOCK_REQUIRES_POSIX")
    import fcntl
    path = Path(path)
    DurableFiles().directory(path.parent)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid == s.st_gid == 0 and stat.S_IMODE(s.st_mode) == 0o600,
                "LOCK_FILE_UNSAFE")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ContractError("LOCK_BUSY") from None
        require(path.stat().st_ino == s.st_ino, "LOCK_REPLACED")
        HELD_LOCKS[path] = (s.st_dev, s.st_ino)
        try:
            check_lock_identities()
            yield
            check_lock_identities()
        finally:
            HELD_LOCKS.pop(path, None)
    finally:
        os.close(fd)  # 保留稳定 inode，不 unlink 锁文件。


@dataclass(frozen=True)
class JournalView:
    events: tuple
    head: str | None


class Journal:
    def __init__(self, root, lock_path, files=None, lock=file_lock, fault=None):
        self.root = Path(root)
        self.lock_path = Path(lock_path)
        self.files = files or DurableFiles()
        self.lock_factory = lock
        self.fault = fault or (lambda *_: None)
        self.held = False

    @contextmanager
    def exclusive(self):
        require(not self.held, "LOCK_REENTRANT")
        with self.lock_factory(self.lock_path):
            self.held = True
            try:
                yield
            finally:
                self.held = False

    def load(self):
        if not self.root.exists():
            require(not self.root.is_symlink(), "JOURNAL_SYMLINK")
            return JournalView((), None)
        self.files.directory(self.root)
        require(not list(self.root.glob(".stage-*")), "JOURNAL_PARTIAL_COMMIT")
        require(all(p.name in ("records", "head.json") for p in self.root.iterdir()), "JOURNAL_UNKNOWN_FILE")
        records = self.root / "records"
        require(not records.is_symlink(), "JOURNAL_SYMLINK")
        if records.exists():
            self.files.directory(records)
            names = sorted(p.name for p in records.iterdir())
            require(all(re.fullmatch(r"[0-9]{8}\.json", n) for n in names), "JOURNAL_PARTIAL_COMMIT")
        else:
            names = []
        head_path = self.root / "head.json"
        require(not head_path.is_symlink(), "JOURNAL_SYMLINK")
        if not head_path.exists():
            require(not names, "JOURNAL_ORPHAN_RECORD")
            return JournalView((), None)
        raw_head = self.files.read(head_path)
        head = fields(json_load(raw_head), "sequence sha256", "JOURNAL_HEAD")
        count = integer(head["sequence"], "JOURNAL_SEQUENCE", 1, 99999999)
        require(len(names) == count and names == [f"{i:08d}.json" for i in range(1, count + 1)],
                "JOURNAL_GAP_OR_ORPHAN")
        previous, events = None, []
        for number, name in enumerate(names, 1):
            raw = self.files.read(records / name)
            event = fields(json_load(raw), "schema_version sequence previous_sha256 kind payload", "JOURNAL_EVENT")
            require(event["schema_version"] == "vps-kit.traffic.event/v1" and type(event["sequence"]) is int
                    and event["sequence"] == number and event["previous_sha256"] == previous, "JOURNAL_CHAIN")
            require(event["kind"] in ("maintenance", "revision", "registry", "transition", "ledger", "resolution", "legacy_import", "program", "deployment", 'format', 'vmrack_import', 'deployment_v2'),
                    "JOURNAL_KIND")
            events.append({**event, "sha256": sha(raw)})
            previous = sha(raw)
        require(head["sha256"] == previous, "JOURNAL_HEAD_MISMATCH")
        require(self.files.read(head_path) == raw_head, "JOURNAL_CHANGED_DURING_READ")
        return JournalView(tuple(events), sha(raw_head))

    def _atomic(self, path, raw):
        check_lock_identities()
        self.files.directory(path.parent)
        fd, name = tempfile.mkstemp(prefix=".stage-", dir=path.parent)
        # 故障残留有意保留：下次 load 阻断，而非猜测未提交文件可以删除。
        with os.fdopen(fd, "wb") as stream:
            os.chmod(name, 0o600)
            stream.write(raw)
            stream.flush()
            self.fault(path.name, "before_file_sync")
            os.fsync(stream.fileno())
            self.fault(path.name, "after_file_sync")
        self.fault(path.name, "before_replace")
        check_lock_identities()
        os.replace(name, path)
        self.fault(path.name, "after_replace")
        self.files.sync_directory(path.parent)
        self.fault(path.name, "after_directory_sync")

    def append(self, kind, payload, expected_head):
        require(self.held, "WRITE_REQUIRES_SHARED_LOCK")
        check_lock_identities()
        view = self.load()
        require(view.head == expected_head, "JOURNAL_STALE")
        if not self.root.exists():
            self.files.directory(self.root.parent)
            self.root.mkdir(mode=0o700)
            self.files.sync_directory(self.root.parent)
        records = self.root / "records"
        if not records.exists():
            records.mkdir(mode=0o700)
            self.files.sync_directory(self.root)
        number = len(view.events) + 1
        event = {"schema_version": "vps-kit.traffic.event/v1", "sequence": number,
                 "previous_sha256": view.events[-1]["sha256"] if view.events else None,
                 "kind": kind, "payload": payload}
        raw = (canonical(event) + "\n").encode("utf-8")
        json_load(raw)  # 同一表示边界，超限在任何文件写入前失败。
        destination = records / f"{number:08d}.json"
        require(not destination.exists(), "JOURNAL_RECORD_EXISTS")
        self._atomic(destination, raw)
        self._atomic(self.root / "head.json", (canonical({"sequence": number, "sha256": sha(raw)}) + "\n").encode())
        return self.load()
