"""P3 固定路径文件事务。生产固定根目录；测试显式注入临时根与权限/目录同步模型。"""
from contextlib import nullcontext
import os
import re
from pathlib import Path, PurePosixPath
import stat
import tempfile

from .contracts import canonical, digest, fields, require
from .journal import DurableFiles, Journal, check_lock_identities
from .package import LAUNCHER, inspect_manifest, sha, verify

LIB = '/usr/local/lib/vps-kit'
ENTRY = '/usr/local/bin/vps-kit'
TOKEN = '/etc/vps-kit/traffic/api-token'
DEPLOY = '/var/lib/vps-kit/traffic/deployment'
BACKUPS = '/var/lib/vps-kit/traffic/backups'
CURRENT = LIB + '/current'


class LocalFiles(DurableFiles):
    def read_mode(self, path, modes):
        from .native import protected_bytes
        return protected_bytes(path, modes, limit=16 * 1024 * 1024)


class DeploymentFiles:
    def __init__(self, root=Path('/'), files=None, fault=None):
        self.root, self.files = Path(root), files or LocalFiles()
        self.fault = fault or (lambda *args: None)

    def path(self, logical):
        p = PurePosixPath(logical)
        require(p.is_absolute() and '..' not in p.parts and '\\' not in logical and str(p) == logical, 'DEPLOY_PATH')
        return self.root.joinpath(*p.parts[1:])

    def mkdir(self, logical, mode=0o700):
        check_lock_identities()
        path = self.path(logical)
        missing = []
        while not path.exists():
            require(not path.is_symlink(), 'DEPLOY_DIRECTORY_SYMLINK')
            missing.append(path)
            path = path.parent
        self.files.directory(path)
        for p in reversed(missing):
            check_lock_identities()
            p.mkdir(mode=mode)
            self.files.sync_directory(p.parent)
        self.files.directory(self.path(logical))

    def read(self, logical, modes=(0o600,)):
        return self.files.read_mode(self.path(logical), modes)

    def image(self, logical):
        p = self.path(logical)
        # link/absent 分支同样不能跳过祖先目录检查；不存在的尾部不需要创建。
        parent = p.parent
        while not parent.exists():
            require(not parent.is_symlink(), 'DEPLOY_DIRECTORY_SYMLINK')
            parent = parent.parent
        self.files.directory(parent)
        if p.is_symlink():
            target = os.readlink(p)
            require((logical == CURRENT and re.fullmatch(r'releases/0\.[0-9]+\.[0-9]+(?:a[0-9]+)?', target)) or
                    (re.fullmatch(r'/etc/systemd/system/vm(?:iss|rack)-3xui-traffic-reset\.(?:timer|service)', logical) and target == '/dev/null'), 'DEPLOY_UNEXPECTED_LINK')
            return {'kind': 'link', 'target': target}
        if not p.exists():
            return {'kind': 'absent'}
        raw = self.read(logical, (0o600, 0o644, 0o700, 0o755))
        return {'kind': 'file', 'sha256': sha(raw), 'mode': stat.S_IMODE(p.stat().st_mode)}

    def write(self, logical, raw, mode=0o600):
        path = self.path(logical)
        self.mkdir(str(PurePosixPath(logical).parent))
        require(not path.is_symlink(), 'DEPLOY_WRITE_SYMLINK')
        self.fault(logical, 'before_write')
        check_lock_identities()
        fd, temp = tempfile.mkstemp(prefix='.vps-kit-stage-', dir=path.parent)
        with os.fdopen(fd, 'wb') as stream:
            os.chmod(temp, mode)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        self.fault(logical, 'before_replace')
        check_lock_identities()
        os.replace(temp, path)
        self.fault(logical, 'after_replace')
        self.files.sync_directory(path.parent)
        self.fault(logical, 'after_sync')

    def link(self, logical, target):
        check_lock_identities()
        require((logical == CURRENT and re.fullmatch(r'releases/0\.[0-9]+\.[0-9]+(?:a[0-9]+)?', target)) or
                (re.fullmatch(r'/etc/systemd/system/vm(?:iss|rack)-3xui-traffic-reset\.(?:timer|service)', logical) and target == '/dev/null'), 'DEPLOY_LINK_TARGET')
        path = self.path(logical)
        self.files.directory(path.parent)
        temp = path.parent / ('.vps-kit-link-' + path.name)
        require(not temp.exists() and not temp.is_symlink(), 'DEPLOY_LINK_PARTIAL')
        os.symlink(target, temp)
        self.fault(logical, 'before_replace')
        check_lock_identities()
        os.replace(temp, path)
        self.files.sync_directory(path.parent)
        self.fault(logical, 'after_sync')

    def remove(self, logical, expected):
        require(self.image(logical) == expected, 'DEPLOY_REMOVE_DRIFT')
        if expected['kind'] != 'absent':
            check_lock_identities()
            self.path(logical).unlink()
            self.files.sync_directory(self.path(logical).parent)
        self.fault(logical, 'after_remove')

    def backup(self, operation, images):
        result = {}
        for logical, image in images.items():
            require(self.image(logical) == image, 'DEPLOY_BACKUP_DRIFT')
            if image['kind'] == 'file':
                backup = BACKUPS + '/' + operation + '/' + digest(logical)
                require(self.image(backup)['kind'] == 'absent', 'DEPLOY_BACKUP_EXISTS')
                self.write(backup, self.read(logical, (0o600, 0o644, 0o700, 0o755)))
                require(sha(self.read(backup)) == image['sha256'], 'DEPLOY_BACKUP_HASH')
                result[logical] = {**image, 'backup': backup}
            else:
                result[logical] = image
        return result

    def restore(self, logical, original, allowed):
        actual = self.image(logical)
        require(actual in allowed, 'ROLLBACK_UNKNOWN_FILE_CHANGE')
        if original['kind'] == 'file':
            raw = self.read(original['backup'])
            require(sha(raw) == original['sha256'], 'ROLLBACK_BACKUP_HASH')
            if actual['kind'] == 'link':
                self.remove(logical, actual)
            self.write(logical, raw, original['mode'])
        elif original['kind'] == 'link':
            self.link(logical, original['target'])
        else:
            self.remove(logical, actual)

    def install_release(self, raw, expected):
        manifest, files = verify(raw, expected)
        folder = LIB + '/releases/' + manifest['version']
        if self.path(folder).exists() or self.path(folder).is_symlink():
            self.verify_release(manifest)
            return manifest  # 只复用逐字节一致的完整版本，绝不覆盖半安装或同名不同内容。
        self.mkdir(folder, 0o755)
        for name, data in files.items():
            self.write(folder + '/' + name, data, manifest['files'][name]['mode'])
        self.write(folder + '/manifest.json', (canonical(manifest) + '\n').encode(), 0o644)
        self.verify_release(manifest)
        return manifest

    def verify_release(self, manifest):
        inspect_manifest(manifest)
        folder = LIB + '/releases/' + manifest['version']
        self.files.directory(self.path(folder))
        require(self.read(folder + '/manifest.json', (0o644,)) == (canonical(manifest) + '\n').encode(), 'RELEASE_MANIFEST_DRIFT')
        paths = [p for p in self.path(folder).rglob('*') if p.is_file() or p.is_symlink()]
        require({p.relative_to(self.path(folder)).as_posix() for p in paths} == set(manifest['files']) | {'manifest.json'}, 'RELEASE_FILE_SET')
        for name, spec in manifest['files'].items():
            require(sha(self.read(folder + '/' + name, (spec['mode'],))) == spec['sha256'], 'RELEASE_FILE_DRIFT')

    def activate(self, manifest):
        self.verify_release(manifest)
        self.write(ENTRY, LAUNCHER, 0o755)
        self.link(CURRENT, 'releases/' + manifest['version'])


class DeploymentLog:
    """与执行账本分离；写入必须已经持有同一外部双锁。"""
    def __init__(self, fs, control):
        self.fs, self.control = fs, control
        self.journal = Journal(fs.path(DEPLOY), control.lock_path, files=fs.files, lock=lambda _: nullcontext())

    def read(self):
        view = self.journal.load()
        return self._validate(view)

    def _validate(self, view):
        operations, latest = {}, None
        for event in view.events:
            require(event['kind'] in ('deployment','deployment_v2'), 'DEPLOY_EVENT_KIND')
            extended = event['kind'] == 'deployment_v2'
            p = fields(event['payload'], 'operation_id kind stage plan backup after active_release migration error' + (' qualification' if extended else ''), 'DEPLOY_EVENT')
            from .config import uuid_value
            from .deployment import validate_plan
            validate_plan(p['plan'])
            uuid_value(p['operation_id'])
            require(p['kind'] in ('install', 'migrate', 'upgrade', 'credential', 'rollback','vmrack-migrate') and p['stage'] in
                    ('prepared', 'writing', 'complete', 'failed','retired','waiting-candidate-reconfirmation','reconfirmed'), 'DEPLOY_STAGE')
            require(extended == ('qualification' in p) and (extended or p['kind'] != 'vmrack-migrate'), 'DEPLOY_EVENT_VERSION')
            if extended:
                from .vmrack_deployment import validate_qualification
                validate_qualification(p)
            require(p['kind'] == 'rollback' or (p['operation_id'] == p['plan']['operation_id'] and p['kind'] == p['plan']['kind']), 'DEPLOY_OPERATION_BINDING')
            if p['active_release'] is not None:
                inspect_manifest(p['active_release'])
            require(p['error'] is None or (type(p['error']) is str and re.fullmatch('[A-Z0-9_]+', p['error'])), 'DEPLOY_ERROR_CODE')
            if p['backup'] is not None:
                require(type(p['backup']) is dict and set(p['backup']) <= set(p['plan']['before']) | set(p['plan']['source_images']), 'DEPLOY_BACKUP_PATHS')
                for path, image in p['backup'].items():
                    require(type(image) is dict and image.get('kind') in ('file', 'link', 'absent'), 'DEPLOY_BACKUP_IMAGE')
                    expected = {**p['plan']['source_images'], **p['plan']['before']}[path]
                    require({k:v for k,v in image.items() if k != 'backup'} == expected, 'DEPLOY_BACKUP_BINDING')
                    if image['kind'] == 'file':
                        require(image.get('backup') == BACKUPS + '/' + p['plan']['operation_id'] + '/' + digest(path), 'DEPLOY_BACKUP_PATH')
            old = operations.get(p['operation_id'])
            if old:
                require(latest['operation_id'] == p['operation_id'] and old['stage'] not in ('complete', 'failed'), 'DEPLOY_OPERATION_CLOSED')
                transitions = {'prepared':('writing','failed'), 'writing':('complete','failed','retired'),
                               'retired':('complete','failed','waiting-candidate-reconfirmation'),
                               'waiting-candidate-reconfirmation':('reconfirmed',), 'reconfirmed':('complete','failed')}
                require(p['stage'] in transitions[old['stage']], 'DEPLOY_SEQUENCE')
                require(p['plan'] == old['plan'] and p['kind'] == old['kind'], 'DEPLOY_PLAN_IMMUTABLE')
                if old['backup'] is not None:
                    require(p['backup'] == old['backup'], 'DEPLOY_BACKUP_IMMUTABLE')
                if extended and old.get('qualification'):
                    for key in ('cutover_utc','minimum_boundary_utc','candidate','candidate_sha256','state_sha256'):
                        require(p['qualification'][key] == old['qualification'][key], 'VMRACK_QUALIFICATION_IMMUTABLE')
            else:
                require(p['stage'] == 'prepared' and (latest is None or latest['stage'] == 'complete' or p['kind'] == 'rollback'), 'DEPLOY_INCOMPLETE')
            operations[p['operation_id']] = p
            latest = p
        return view, operations, latest

    def append(self, value):
        require(self.control.held, 'DEPLOY_SHARED_LOCK_REQUIRED')
        view, _, _ = self.read()
        from .journal import JournalView
        kind = 'deployment_v2' if 'qualification' in value else 'deployment'
        self._validate(JournalView((*view.events, {'kind': kind, 'payload': value}), view.head))
        with self.journal.exclusive():
            self.journal.append(kind, value, view.head)
        return self.read()[2]
