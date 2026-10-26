"""P3 Linux 接口。固定本机路径、GET 读取与 timer 操作；没有面板写入或 service 启动接口。"""
import re
from contextlib import contextmanager
from pathlib import Path

from .contracts import canonical, digest, json_load, read_file, require
from .control import Control
from .deploy_files import CURRENT, ENTRY, LIB, TOKEN, DeploymentFiles
from .deployment import Deployment
from .host import binding_guard, command, live_guard
from .journal import check_lock_identities
from .legacy import CONFIG, RUNNER, STATE, TIMER, SERVICE, TOKEN as OLD_TOKEN, RETIRED
from .maintenance import quiet, safe_system
from .native import NativeClock, NativeSystem, PROPS, show
from .package import LAUNCHER, VERSION, sha
from .panel_http import LocalPanelReader
from .units import render, stem
from .sources import VMISS, VMRACK, paths, kind_of, plan_kind


def proposed_images(cfg, plan, migrated):
    source = paths(plan_kind(plan))
    source_kind = plan.get('source', {}).get('kind') if plan.get('source') else None
    result = {ENTRY: {'kind': 'file', 'sha256': sha(LAUNCHER), 'mode': 0o755},
              CURRENT: {'kind': 'link', 'target': 'releases/' + plan['release']['version']},
              TOKEN: {'kind': 'file', 'sha256': plan['credential_sha256'], 'mode': 0o600}}
    result.update({'/etc/systemd/system/' + name: {'kind': 'file', 'sha256': sha(value.encode()), 'mode': 0o644}
                   for name, value in render(cfg, legacy=source_kind or migrated).items()})
    result.update({'/etc/systemd/system/' + name: {'kind': 'link', 'target': '/dev/null'} for name in (source['timer'], source['service'])})
    result[source['runner']] = {'kind': 'file', 'sha256': sha(source['retired']), 'mode': 0o700}
    if plan['kind'] == 'vmrack-migrate':
        from .vmrack_legacy import CRON_RETIRED
        result.update({p: {'kind':'file','sha256':sha(CRON_RETIRED),'mode':0o644} for p in plan['source']['evidence']['retire_cron']})
    return result


class InstallationSystem(NativeSystem):
    """安装进程可从已校验离线包运行；已部署程序摘要取实际版本目录。"""
    def __init__(self, backend, mode):
        super().__init__(backend.execute, (lambda cfg: backend.legacy_check(cfg, mode)) if mode != 'none' else None,
                         legacy_kind=backend.source_kind)
        self.backend = backend

    @property
    def program_sha256(self):
        image = self.backend.fs.image(CURRENT)
        if image['kind'] == 'absent':
            return digest({'bootstrap': 'vps-kit-P3'})
        require(image['kind'] == 'link', 'DEPLOY_CURRENT_TYPE')
        version = image['target'].removeprefix('releases/')
        require(VERSION.fullmatch(version), 'DEPLOY_CURRENT_VERSION')
        manifest = json_load(self.backend.fs.read(LIB + '/releases/' + version + '/manifest.json', (0o644,)))
        self.backend.fs.verify_release(manifest)
        require(self.backend.fs.read(ENTRY, (0o755,)) == LAUNCHER, 'DEPLOY_ENTRY_DRIFT')
        return manifest['program_sha256']


class NativeDeploymentBackend:
    def __init__(self, fs, execute=None, prior=None):
        from .panel_http import bounded_process
        self.fs, self.execute, self.prior = fs, execute or bounded_process, prior
        self.migration = None
        self.source_kind = VMISS

    def select_source(self, kind):
        paths(kind)
        self.source_kind = kind

    @staticmethod
    def validate_token(token):
        require(type(token) is str and re.fullmatch(r'[A-Za-z0-9]{48}', token), 'TOKEN_FORMAT')

    def token(self, logical):
        value = self.fs.read(logical).decode('ascii').removesuffix('\n')
        self.validate_token(value)
        return value

    def reader(self, cfg, token):
        live_guard(cfg, self.execute)
        return LocalPanelReader(cfg, token)

    def system(self, cfg, mode):
        return InstallationSystem(self, mode)

    def old_system(self, allow_mask=False):
        source = paths(self.source_kind)
        result = {}
        for name, role in ((source['timer'], 'timer'), (source['service'], 'service')):
            p = show(name, self.execute)
            if self.source_kind == VMRACK and role == 'service':
                group = p.get('ControlGroup', '')
                if group:
                    require(group.startswith('/') and '..' not in group.split('/') and re.fullmatch(r'[A-Za-z0-9_./@:-]+', group), 'LEGACY_CGROUP_PATH')
                    cgroup = Path('/sys/fs/cgroup' + group + '/cgroup.events')
                    require(cgroup.is_file() and 'populated 0' in cgroup.read_text().splitlines(), 'LEGACY_CGROUP_BUSY')
                result['execution'] = {k:p.get(k, '') for k in ('InvocationID','ExecMainStartTimestamp','ExecMainExitTimestamp','NRestarts')}
            require(not p.get('DropInPaths') and p.get('NeedDaemonReload', 'no') == 'no'
                    and p.get('Job', '') in ('', '0'), 'LEGACY_OVERRIDE_OR_JOB')
            if allow_mask and p['LoadState'] == 'masked':
                require(p['ActiveState'] == 'inactive' and p.get('MainPID', '0') == '0', 'LEGACY_MASK_ACTIVE')
                result[role] = 'paused' if role == 'timer' else 'inactive'
                continue
            require(p['LoadState'] == 'loaded' and p.get('FragmentPath') == '/etc/systemd/system/' + name,
                    'LEGACY_EFFECTIVE_PATH')
            if role == 'timer':
                triple = p['ActiveState'], p['SubState'], p.get('UnitFileState')
                require(triple in (('active', 'waiting', 'enabled'), ('inactive', 'dead', 'disabled')), 'LEGACY_TIMER_STATE')
                result[role] = 'waiting' if triple[0] == 'active' else 'paused'
                require(p.get('Persistent') == 'no' and p.get('Triggers') == source['service'], 'LEGACY_TIMER_EFFECTIVE')
            else:
                require(p['ActiveState'] in ('inactive', 'failed') and p.get('MainPID') == '0'
                        and p.get('Restart') == ('on-failure' if self.source_kind == VMRACK else 'no')
                        and p.get('Type') == 'oneshot', 'LEGACY_SERVICE_BUSY')
                result[role] = 'inactive'
        return {**result, 'jobs': False}

    def inventory(self, registered, retired=False):
        """有界扫描已知 cron/命名入口；任意 root 自建副本仍依赖人工来源清单。"""
        roots = ('/etc/crontab', '/etc/cron.d', '/etc/cron.hourly', '/etc/cron.daily',
                 '/etc/cron.weekly', '/etc/cron.monthly', '/var/spool/cron/crontabs')
        for root in roots:
            base = self.fs.path(root)
            entries = [base] if base.is_file() else list(base.iterdir()) if base.is_dir() else []
            require(len(entries) <= 256, 'VMRACK_INVENTORY_LIMIT')
            for item in entries:
                if item.is_dir():
                    require(False, 'VMRACK_NESTED_CRON_REVIEW')
                logical = '/' + item.relative_to(self.fs.root).as_posix()
                # cron spool 可合法属于 crontab 组；这里只读不可信清单，不套用凭据权限。
                # 获准退役的专用文件仍另外经过严格的 fs.read/image 校验。
                require(not any(x.is_symlink() for x in (item,*item.parents)), 'VMRACK_CRON_SYMLINK')
                content = read_file(item,limit=2*1024*1024).decode('utf-8')
                active = '\n'.join(x for x in content.splitlines() if x.strip() and not x.lstrip().startswith('#'))
                if re.search(r'resetTraffic|traffic[-_ ]*reset|reset[-_ ]*traffic', active, re.I):
                    require(not retired and logical in registered, 'VMRACK_OTHER_CRON_ENTRY')
        other = paths(VMISS)
        require(all(self.fs.image(p)['kind'] == 'absent' for p in
                    (other['runner'], other['config'], '/etc/systemd/system/'+other['timer'], '/etc/systemd/system/'+other['service'])),
                'MULTIPLE_LEGACY_SOURCES')

    def collect_vmrack(self, cfg, evidence, audit=False):
        from .vmrack_legacy import inspect_evidence, verify_source
        inspect_evidence(evidence)
        self.select_source(VMRACK)
        binding_guard(cfg, self.execute)
        self.inventory(evidence['retire_cron'])
        p = paths(VMRACK)
        targets = [p['runner'], p['config'], p['token'], '/etc/systemd/system/'+p['timer'], '/etc/systemd/system/'+p['service']]
        require({'/'+n.relative_to(self.fs.root).as_posix() for n in self.fs.path(p['config']).parent.iterdir()} ==
                {p['config'],p['token']}, 'VMRACK_CONFIG_SET_CHANGED')
        states = {}
        self.fs.files.directory(self.fs.path(p['state']))
        for item in self.fs.path(p['state']).iterdir():
            require(item.name in ('client-reset.done','inbound-reset.done') and not item.is_symlink() and item.is_file(), 'VMRACK_STATE_UNKNOWN')
            states[item.name] = self.fs.read(p['state']+'/'+item.name)
            targets.append(p['state']+'/'+item.name)
        cron = {n:self.fs.read(n, (0o644,)) for n in evidence['retire_cron']}
        targets += list(cron)
        for proof, checksum in evidence['evidence_files'].items():
            proof_bytes = self.fs.read(proof)
            require(proof_bytes and sha(proof_bytes) == checksum, 'VMRACK_PROOF_EMPTY_OR_CHANGED')
        # source_images 指真实来源文件；证明材料另按摘要备份，避免包含自身摘要的循环。
        images = {n:self.fs.image(n) for n in targets}
        require(all(x['kind'] == 'file' for x in images.values()), 'VMRACK_SOURCE_MISSING')
        source = {'images':images, 'runner':self.fs.read(p['runner'], (0o700,)), 'config':self.fs.read(p['config']),
                  'timer':self.fs.read('/etc/systemd/system/'+p['timer'], (0o644,)),
                  'service':self.fs.read('/etc/systemd/system/'+p['service'], (0o644,)),
                  'states':states, 'cron':cron, 'system':self.old_system()}
        verify_source(source, cfg, {**evidence,'source_images_sha256':digest(images)} if audit else evidence)
        source['proof_images'] = {n:self.fs.image(n) for n in evidence['evidence_files']}
        return source

    @contextmanager
    def old_lock_after_pause(self):
        """首次接管持有新锁后暂停旧任务，再建目录和取得旧锁。"""
        from .native import ensure_source_runtime
        from .journal import file_lock
        require(self.old_system()['timer'] == 'paused', 'LEGACY_TIMER_ACTIVE')
        ensure_source_runtime(self.source_kind)
        with file_lock(Path(paths(self.source_kind)['lock'])):
            require(self.old_system()['timer'] == 'paused', 'LEGACY_TIMER_ACTIVE')
            self.lock_identity_check()
            yield

    @staticmethod
    def lock_identity_check():
        from .journal import check_lock_identities
        check_lock_identities()

    def collect_r2(self, cfg):
        binding_guard(cfg, self.execute)
        paths = [RUNNER, CONFIG, OLD_TOKEN, '/etc/systemd/system/' + TIMER, '/etc/systemd/system/' + SERVICE]
        states = {}
        base = self.fs.path(STATE)
        self.fs.files.directory(base)
        for item in base.rglob('*'):
            relative = item.relative_to(base).as_posix()
            require(not item.is_symlink(), 'LEGACY_STATE_SYMLINK')
            if item.is_dir():
                require(relative == 'v2', 'LEGACY_STATE_DIRECTORY')
                self.fs.files.directory(item)
                continue
            require(relative in ('client-reset.done', 'inbound-reset.done') or re.fullmatch(r'v2/20\d{2}-\d{2}\.json', relative), 'LEGACY_STATE_UNKNOWN_FILE')
            states[relative] = self.fs.read(STATE + '/' + relative)
            paths.append(STATE + '/' + relative)
        prior = None
        if self.prior:
            require(set(self.prior) == {'config', 'manifest'}, 'LEGACY_PRIOR_INPUT')
            prior = {}
            for key, path in self.prior.items():
                require(type(path) is str and path.startswith('/root/') and not Path(path).is_symlink(), 'LEGACY_PRIOR_PATH')
                prior[key] = self.fs.read(path)
                paths.append(path)
        images = {p: self.fs.image(p) for p in paths}
        require(all(v['kind'] == 'file' for v in images.values()), 'LEGACY_REQUIRED_FILE')
        return {'config': self.fs.read(CONFIG), 'runner_sha256': sha(self.fs.read(RUNNER, (0o700,))),
                'timer': self.fs.read('/etc/systemd/system/' + TIMER, (0o644,)),
                'service': self.fs.read('/etc/systemd/system/' + SERVICE, (0o644,)),
                'states': states, 'prior': prior, 'images': images, 'system': self.old_system()}

    def legacy_check(self, cfg, mode):
        if mode == 'source':
            self.old_system()
        elif mode == 'retired':
            require(self.migration is not None, 'LEGACY_RETIREMENT_EVIDENCE_MISSING')
            self.retired(cfg, self.migration)

    def retired(self, cfg, migration):
        self.select_source(kind_of(migration))
        source = paths(self.source_kind)
        require(self.fs.read(source['runner'], (0o700,)) == source['retired'], 'LEGACY_RUNNER_NOT_RETIRED')
        for name in (source['timer'], source['service']):
            require(self.fs.image('/etc/systemd/system/' + name) == {'kind': 'link', 'target': '/dev/null'}, 'LEGACY_UNIT_NOT_MASKED')
            require(show(name, self.execute)['LoadState'] == 'masked', 'LEGACY_MASK_NOT_EFFECTIVE')
        require(self.old_system(allow_mask=True)['timer'] == 'paused', 'LEGACY_TIMER_ACTIVE')
        changed = {source['runner'], '/etc/systemd/system/' + source['timer'], '/etc/systemd/system/' + source['service']}
        if self.source_kind == VMRACK:
            from .vmrack_legacy import CRON_RETIRED
            for path in migration['retire_cron']:
                require(self.fs.read(path, (0o644,)) == CRON_RETIRED, 'VMRACK_CRON_NOT_RETIRED')
                changed.add(path)
            self.inventory(migration['retire_cron'], retired=True)
            self.lock_identity_check()
        for path, image in migration['source_images'].items():
            if path not in changed and not path.startswith('/root/'):
                require(self.fs.image(path) == image, 'LEGACY_SOURCE_CHANGED')
            backup = migration['source_backup'][path]
            require(sha(self.fs.read(backup['backup'])) == image['sha256'], 'LEGACY_BACKUP_CHANGED')
        self._source_sets(migration['source_images'])
        self.migration = migration

    def _source_sets(self, images):
        source = paths(self.source_kind)
        # 增加、删除旧账本都属于漂移，不能只核对已知文件的 hash。
        state = source['state']
        self.fs.files.directory(self.fs.path(state))
        self.fs.files.directory(self.fs.path(source['config']).parent)
        if self.source_kind == VMRACK:
            parent = self.fs.path(source['config']).parent
            require({'/'+p.relative_to(self.fs.root).as_posix() for p in parent.iterdir()} ==
                    {source['config'],source['token']}, 'VMRACK_CONFIG_SET_CHANGED')
            require(all(p.is_file() and not p.is_symlink() for p in self.fs.path(state).iterdir()), 'VMRACK_STATE_SET_CHANGED')
        expected = {p for p in images if p.startswith(state + '/')}
        actual = {state + '/' + p.relative_to(self.fs.path(state)).as_posix()
                  for p in self.fs.path(state).rglob('*') if p.is_file() or p.is_symlink()}
        require(actual == expected, 'LEGACY_STATE_SET_CHANGED')

    def rollback_source_guard(self, cfg, original):
        """恢复前检查来源；返回仍有执行能力的旧日历，纳入维护窗口。"""
        check_lock_identities()
        plan = original['plan']
        if plan['kind'] not in ('migrate', 'vmrack-migrate'):
            if original['migration']:
                self.retired(cfg, original['migration'])
            return []
        self._source_sets(plan['source_images'])
        # 文件退出需要原证明仍在；运行守卫只依赖受保护的证明备份。
        for path, image in plan['source_images'].items():
            if path not in plan['before']:
                require(self.fs.image(path) == image, 'ROLLBACK_OLD_SOURCE_DRIFT')
        source = paths(self.source_kind)
        retired_images = proposed_images(cfg, plan, True)
        mutable_source = set(plan['source_images']) & set(plan['before'])
        if all(self.fs.image(p) == retired_images[p] for p in mutable_source):
            migration = {'source_images': plan['source_images'],
                         'source_backup': original['backup']}
            if self.source_kind == VMRACK:
                migration.update(source_kind=VMRACK, retire_cron=plan['source']['evidence']['retire_cron'])
            self.retired(cfg, migration)
            return []
        if self.source_kind == VMRACK:
            from .vmrack_legacy import old_schedule
            self.inventory(plan['source']['evidence']['retire_cron'])
            return [old_schedule(plan['source']['evidence']['profile'])]
        from .legacy import schedule
        return [schedule(json_load(self.fs.read(source['config'])))]

    def _run(self, *args):
        check_lock_identities()
        return command(['/usr/bin/systemctl', *args], self.execute)

    def pause_old(self, cfg):
        self.old_system()
        self._run('disable', '--now', paths(self.source_kind)['timer'])
        require(self.old_system()['timer'] == 'paused', 'LEGACY_PAUSE_FAILED')

    def reload(self):
        self._run('daemon-reload')

    def quiescent(self, cfg, old=False, migrated=False, partial=False):
        # 部分安装/回退时只接受确实停止且无排队、无 drop-in 的 unit；不需要完整版本目录。
        if partial:
            for name in render(cfg):
                p = show(name, self.execute)
                require(p['LoadState'] in ('loaded', 'not-found') and p['ActiveState'] in ('inactive', 'failed')
                        and p.get('MainPID', '0') == '0' and p.get('Job', '') in ('', '0') and not p.get('DropInPaths'), 'DEPLOY_NOT_QUIESCENT')
                if name.endswith('.timer'):
                    require(p.get('UnitFileState', '') in ('disabled', ''), 'DEPLOY_TIMER_ENABLED')
        else:
            system = self.system(cfg, 'source' if old else 'retired' if migrated else 'none')
            info = system.inspect(cfg)
            safe_system(info)
            quiet(info)
        if migrated and not partial:
            require(self.old_system(allow_mask=True)['timer'] == 'paused', 'LEGACY_TIMER_ACTIVE')
        elif migrated:
            for name in (paths(self.source_kind)['timer'], paths(self.source_kind)['service']):
                p = show(name, self.execute)
                require(p['ActiveState'] in ('inactive', 'failed') and p.get('MainPID', '0') == '0'
                        and p.get('Job', '') in ('', '0') and not p.get('DropInPaths'), 'LEGACY_ROLLBACK_BUSY')
                if name == paths(self.source_kind)['timer']:
                    require(p.get('UnitFileState', '') in ('disabled', 'masked', ''), 'LEGACY_TIMER_ENABLED')

    def pause_for_rollback(self, cfg, migrated):
        binding_guard(cfg, self.execute)
        # 不启动/停止重置 service。活动 service 或排队任务先阻断，由人工独立核查。
        for name in render(cfg):
            p = show(name, self.execute)
            require(not p.get('DropInPaths') and p.get('Job', '') in ('', '0'), 'ROLLBACK_UNIT_OVERRIDE')
            require(p['LoadState'] in ('loaded', 'not-found') and (p['LoadState'] != 'loaded'
                    or p.get('FragmentPath') == '/etc/systemd/system/' + name), 'ROLLBACK_UNIT_ORIGIN')
            if name.endswith('.service'):
                require(p['ActiveState'] in ('inactive', 'failed') and p.get('MainPID', '0') == '0', 'ROLLBACK_SERVICE_BUSY')
        if show(stem(cfg.data['task_id']) + '.timer', self.execute)['LoadState'] == 'loaded':
            self._run('disable', '--now', stem(cfg.data['task_id']) + '.timer')
        if migrated:
            source = paths(self.source_kind)
            p = show(source['service'], self.execute)
            require(p['ActiveState'] in ('inactive', 'failed') and p.get('MainPID', '0') == '0'
                    and p.get('Job', '') in ('', '0') and not p.get('DropInPaths'), 'LEGACY_ROLLBACK_BUSY')
            require(p['LoadState'] in ('loaded', 'masked', 'not-found') and (p['LoadState'] != 'loaded'
                    or p.get('FragmentPath') == '/etc/systemd/system/' + source['service']), 'ROLLBACK_UNIT_ORIGIN')
            p = show(source['timer'], self.execute)
            require(p.get('Job', '') in ('', '0') and not p.get('DropInPaths'), 'LEGACY_ROLLBACK_BUSY')
            require(p['LoadState'] in ('loaded', 'masked', 'not-found') and (p['LoadState'] != 'loaded'
                    or p.get('FragmentPath') == '/etc/systemd/system/' + source['timer']), 'ROLLBACK_UNIT_ORIGIN')
            if p['LoadState'] == 'loaded':
                self._run('disable', '--now', source['timer'])

    def returned(self, cfg, plan):
        require(self.old_system()['timer'] == 'paused', 'ROLLBACK_OLD_TIMER_ACTIVE')
        self._source_sets(plan['source_images'])
        for path, image in plan['source_images'].items():
            require(self.fs.image(path) == image, 'ROLLBACK_OLD_SOURCE_DRIFT')

    proposed_images = staticmethod(proposed_images)


def context(journal, prior=None):
    fs = DeploymentFiles()
    backend = NativeDeploymentBackend(fs, prior=prior)
    deployment = Deployment(journal, fs, backend, NativeClock())
    latest = deployment.log.read()[2]
    if latest and latest['migration']:
        backend.select_source(kind_of(latest['migration']))
    elif latest and latest['plan']['kind'] == 'vmrack-migrate':
        backend.select_source(VMRACK)
    return deployment


def guard_existing(journal):
    deployment = context(journal)
    _, _, latest = deployment.log.read()
    if latest is None:
        return None
    require(latest['stage'] == 'complete', 'DEPLOY_INCOMPLETE')
    control = Control(journal.load())
    require(control.owner, 'DEPLOY_OWNER_MISSING')
    return deployment.guard(control.config)
