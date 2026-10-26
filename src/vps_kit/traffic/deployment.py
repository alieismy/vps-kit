"""P3 管理事务：只有 GET；部署状态、程序回退与流量账本分离。"""
from copy import deepcopy
from datetime import timedelta
import uuid

from .calendar import MAINTENANCE_SECONDS, maintenance_window, stamp
from .config import parse_config, uuid_value
from .contracts import canonical, digest, fields, json_load, require, utc
from .control import Control, commit
from .deploy_files import CURRENT, ENTRY, LIB, TOKEN, DeploymentLog
from .execution import observe
from .legacy import CONFIG as OLD_CONFIG, RUNNER as OLD_RUNNER, TOKEN as OLD_TOKEN, TIMER, SERVICE, RETIRED, R2_HASH, history, units
from .maintenance import Maintenance, admissible, quiet, safe_system
from .package import LAUNCHER, inspect_manifest, sha, verify, CONTRACTS
from .sources import paths, kind_of, plan_kind, VMRACK
from .units import render


def plan_hash(plan):
    return digest({k: v for k, v in plan.items() if k != 'sha256'})


def validate_plan(plan):
    extended = type(plan) is dict and plan.get('schema') == 'vps-kit.deployment.plan/v2'
    fields(plan, 'schema operation_id kind config control_head deployment_head activation attestations system old_system before source_images history release old_release package_sha256 credential_sha256 next_boundary_utc next_period_id sha256' + (' source' if extended else ''), 'DEPLOY_PLAN_FIELDS')
    require(plan['schema'] in ('vps-kit.deployment.plan/v1','vps-kit.deployment.plan/v2') and plan_hash(plan) == plan['sha256'], 'DEPLOY_PLAN_HASH')
    uuid_value(plan['operation_id'])
    require(plan['kind'] in ('install', 'migrate', 'upgrade', 'credential', 'vmrack-migrate') and plan['activation'] in ('paused','enabled'), 'DEPLOY_PLAN_KIND')
    cfg = parse_config(canonical(plan['config']))
    inspect_manifest(plan['release'])
    if plan['old_release'] is not None:
        inspect_manifest(plan['old_release'])
    paths = {TOKEN} if plan['kind'] == 'credential' else {ENTRY, CURRENT, TOKEN, *['/etc/systemd/system/'+n for n in render(cfg)]}
    if plan['kind'] == 'migrate':
        paths |= {OLD_RUNNER, '/etc/systemd/system/'+TIMER, '/etc/systemd/system/'+SERVICE}
    if extended:
        require(plan['release']['contracts'] == CONTRACTS, 'DEPLOY_EXTENSION_CAPABILITY')
        if plan['kind'] == 'vmrack-migrate':
            from .sources import paths as source_paths
            from .vmrack_legacy import inspect_evidence
            source = fields(plan['source'], 'kind evidence', 'DEPLOY_SOURCE_FIELDS')
            require(source['kind'] == VMRACK and plan['activation'] == 'paused', 'DEPLOY_SOURCE_KIND')
            inspect_evidence(source['evidence'])
            p = source_paths(VMRACK)
            paths |= {p['runner'], '/etc/systemd/system/'+p['timer'], '/etc/systemd/system/'+p['service'], *source['evidence']['retire_cron']}
        else:
            require(plan['source'] is None or plan['source'] == {'kind': VMRACK}, 'DEPLOY_SOURCE_FIELDS')
    else:
        require(plan['kind'] != 'vmrack-migrate' and cfg.data['schema_version'].endswith('/v1'), 'DEPLOY_EXTENSION_REQUIRED')
    require(type(plan['before']) is dict and set(plan['before']) == paths, 'DEPLOY_PLAN_PATHS')
    return cfg


class Deployment:
    def __init__(self, journal, fs, backend, clock):
        self.journal, self.fs, self.backend, self.clock = journal, fs, backend, clock
        self.log = DeploymentLog(fs, journal)
        self.start = None

    def now(self):
        wall, mono, synced = self.clock.sample()
        require(synced is True, 'DEPLOY_NTP')
        if self.start:
            w, m = self.start
            require(mono >= m and abs((wall-w).total_seconds() - (mono-m)) <= 2, 'DEPLOY_CLOCK_JUMP')
        return wall

    def window(self, cfg, due):
        maintenance_window([cfg.data['schedule']], self.now())
        require(utc(due, 'DEPLOY_BOUNDARY') > self.now() + timedelta(seconds=MAINTENANCE_SECONDS), 'DEPLOY_NOT_FUTURE')

    def plan(self, kind, cfg, package_raw, package_sha, credential, activation, attestations):
        self.start = self.clock.sample()[:2]
        require(kind in ('install', 'migrate', 'upgrade', 'credential'), 'DEPLOY_KIND')
        fields(attestations, 'admin_scope local_accounting other_reset_jobs_absent', 'DEPLOY_ATTESTATIONS')
        require(all(v is True for v in attestations.values()), 'DEPLOY_ATTESTATIONS')
        require(activation in ('paused', 'enabled'), 'DEPLOY_ACTIVATION')
        view, operations, latest = self.log.read()
        require(latest is None or latest['stage'] == 'complete', 'DEPLOY_INCOMPLETE')
        require(not latest or not latest['migration'] or latest['migration']['status'] == 'active', 'R2_RETURNED_REQUIRES_REVIEW')
        control = Control(self.journal.load())
        if latest is not None:
            self.guard(control.config)
        source = self.backend.collect_r2(cfg) if kind == 'migrate' else None
        migrated = bool(source or (latest and latest['migration']))
        system = self.backend.system(cfg, 'source' if source else 'retired' if migrated else 'none')
        info = system.inspect(cfg)
        safe_system(info)
        require(info['service'] in ('inactive', 'absent') and not info['jobs'], 'DEPLOY_SERVICE_BUSY')
        old_release = latest['active_release'] if latest else None
        if kind in ('install', 'migrate'):
            require(not control.view.events and latest is None and cfg.data['revision'] == 1, 'DEPLOY_OWNER_EXISTS')
            require(info['timer'] == info['service'] == 'absent', 'DEPLOY_NEW_UNITS_EXIST')
            require(self.fs.image(ENTRY)['kind'] == self.fs.image(CURRENT)['kind'] == 'absent'
                    and self.fs.image(TOKEN)['kind'] == 'absent', 'DEPLOY_NEW_FILES_EXIST')
        else:
            control.ready()
            require(latest and old_release and cfg.fingerprint == control.config.fingerprint, 'DEPLOY_CURRENT_CONFIG')
            self.guard(cfg)
            system.verify(cfg)
        if source:
            require(source['runner_sha256'] == R2_HASH, 'MIGRATION_UNKNOWN_RUNNER')
            units(json_load(source['config']), source['timer'], source['service'])
            imported = history(source['config'], source['states'], source['prior'], cfg, self.now())
            require(source['system']['service'] == 'inactive' and not source['system']['jobs'], 'MIGRATION_OLD_BUSY')
            due, period = imported['next_boundary_utc'], imported['next_period_id']
        else:
            imported = None
            period, edge = control.next_authorization() if control.owner else (1, utc(cfg.data['schedule']['first_boundary_utc'], 'DEPLOY_FIRST'))
            due = stamp(edge)
        timer_before = source['system']['timer'] if source else info['timer']
        require(timer_before in ('waiting', 'paused', 'absent'), 'DEPLOY_TIMER_STATE')
        require(timer_before != 'paused' or activation == 'paused', 'DEPLOY_PRESERVE_PAUSED')
        self.window(cfg, due)
        manifest = verify(package_raw, package_sha)[0] if kind != 'credential' else old_release
        if kind != 'credential':
            require(manifest['contracts'] == CONTRACTS or (cfg.data['schema_version'].endswith('/v1') and
                    not any(e['kind'] in ('format','vmrack_import') for e in control.view.events)), 'DEPLOY_RELEASE_CONTRACT')
            require(old_release is None or manifest['version'] != old_release['version'], 'DEPLOY_RELEASE_ALREADY_ACTIVE')
            folder = self.fs.path(LIB + '/releases/' + manifest['version'])
            if folder.exists() or folder.is_symlink():
                self.fs.verify_release(manifest)
        token = credential if credential is not None else self.backend.token(OLD_TOKEN if source else TOKEN)
        self.backend.validate_token(token)
        if kind in ('upgrade', 'migrate'):
            require(credential is None, 'CREDENTIAL_CHANGE_SEPARATE')
        if kind in ('install', 'credential'):
            require(credential is not None, 'CREDENTIAL_INPUT_REQUIRED')
        snapshot = observe(cfg, self.backend.reader(cfg, token))
        if kind == 'install':
            admissible(cfg, snapshot)
        self.window(cfg, due)
        require(system.inspect(cfg) == info, 'DEPLOY_SYSTEM_DRIFT')
        mutable = [ENTRY, CURRENT, TOKEN, *['/etc/systemd/system/' + n for n in render(cfg)]] if kind != 'credential' else [TOKEN]
        if source:
            mutable += [OLD_RUNNER, '/etc/systemd/system/' + TIMER, '/etc/systemd/system/' + SERVICE]
        images = {path: self.fs.image(path) for path in mutable}
        source_images = source['images'] if source else latest['migration']['source_images'] if migrated else {}
        p = {'schema': 'vps-kit.deployment.plan/v1', 'operation_id': str(uuid.uuid4()), 'kind': kind,
             'config': cfg.data, 'control_head': control.view.head, 'deployment_head': view.head,
             'activation': activation, 'attestations': attestations, 'system': info,
             'old_system': source['system'] if source else None, 'before': images, 'source_images': source_images,
             'history': imported, 'release': manifest, 'old_release': old_release, 'package_sha256': package_sha,
             'credential_sha256': sha((token + '\n').encode()), 'next_boundary_utc': due, 'next_period_id': period}
        if manifest['contracts'] == CONTRACTS:
            p.update(schema='vps-kit.deployment.plan/v2', source={'kind': VMRACK} if latest and kind_of(latest['migration']) == VMRACK else None)
        return {**p, 'sha256': plan_hash(p)}

    def guard(self, cfg):
        deployment_view, _, latest = self.log.read()
        if latest is None:
            return None  # 未安装 P3 的源码/P2 本地模型路径。
        require(latest['stage'] == 'complete', 'DEPLOY_INCOMPLETE')
        require(latest['plan']['config']['task_id'] == cfg.data['task_id'], 'DEPLOY_TASK_BINDING')
        release = latest['active_release']
        require(release is not None, 'DEPLOY_RELEASE_NOT_ACTIVE')
        self.fs.verify_release(release)
        require(self.fs.image(CURRENT) == {'kind': 'link', 'target': 'releases/' + release['version']}
                and self.fs.read(ENTRY, (0o755,)) == LAUNCHER, 'DEPLOY_ENTRY_DRIFT')
        require(self.fs.image(TOKEN) == latest['after'][TOKEN], 'DEPLOY_CREDENTIAL_DRIFT')
        if latest['migration']:
            require(latest['migration']['status'] == 'active', 'R2_RETURNED_REQUIRES_REVIEW')
            self.backend.retired(cfg, latest['migration'])
        require(self.log.read()[0].head == deployment_view.head, 'DEPLOY_READONLY_CHANGED')
        return latest['migration']

    def _record(self, record, stage, error=None):
        record = deepcopy(record)
        record['stage'], record['error'] = stage, error
        self.log.append(record)
        return record

    def _initial_control(self, cfg, plan, program):
        c = Control(self.journal.load())
        require(not c.view.events, 'DEPLOY_CONTROL_NOT_EMPTY')
        m = {'operation_id': plan['operation_id'], 'task_id': cfg.data['task_id'], 'kind': 'create', 'stage': 'preparing',
             'plan_sha256': plan['sha256'], 'base_head': None, 'before_system': plan['system'], 'backup': None,
             'observed_system': plan['system'], 'error_code': None}
        for stage in ('preparing', 'paused', 'prepared', 'installing'):
            c = commit(self.journal, 'maintenance', {**m, 'stage': stage}, c.view.head)
        if plan['history']:
            c = commit(self.journal, 'vmrack_import' if plan['kind'] == 'vmrack-migrate' else 'legacy_import', {'config_sha256': cfg.fingerprint,
                       'source_sha256': digest(plan['source_images']), 'history': plan['history']}, c.view.head)
        c = commit(self.journal, 'revision', {'config': cfg.data, 'parent_revision': None, 'config_sha256': cfg.fingerprint}, c.view.head)
        owner = {k: cfg.data[k] for k in ('task_id', 'host_id_sha256', 'panel_binding_id')}
        owner.update(inbound_id=cfg.data['inbound']['id'], active_revision=1, status='active', program_sha256=program)
        c = commit(self.journal, 'registry', owner, c.view.head)
        c = commit(self.journal, 'transition', {'operation_id': plan['operation_id'], 'from_revision': None, 'to_revision': 1,
                   'previous_period_id': plan['next_period_id']-1, 'next_period_id': plan['next_period_id'],
                   'effective_boundary_utc': plan['next_boundary_utc'], 'old_next_boundary_utc': None, 'reason': 'create'}, c.view.head)
        for stage in ('verifying', 'complete'):
            c = commit(self.journal, 'maintenance', {**m, 'stage': stage}, c.view.head)

    def apply(self, plan, package_raw=None, credential=None):
        cfg = validate_plan(plan)
        with self.journal.exclusive():
            require(self.journal.load().head == plan['control_head'] and self.log.read()[0].head == plan['deployment_head'], 'DEPLOY_PLAN_STALE')
            fresh = self.plan(plan['kind'], cfg, package_raw, plan['package_sha256'], credential, plan['activation'], plan['attestations'])
            require({k:v for k,v in fresh.items() if k not in ('operation_id','sha256')} == {k:v for k,v in plan.items() if k not in ('operation_id','sha256')}, 'DEPLOY_PLAN_DRIFT')
            _, _, latest = self.log.read()
            migrated = bool(plan['history'] or (latest and latest['migration']))
            system = self.backend.system(cfg, 'source' if plan['history'] else 'retired' if migrated else 'none')
            record = {'operation_id': plan['operation_id'], 'kind': plan['kind'], 'stage': 'prepared', 'plan': plan, 'backup': None,
                      'after': {}, 'active_release': plan['old_release'], 'migration': latest['migration'] if latest else None, 'error': None}
            record = self._record(record, 'prepared')
            try:
                if plan['history']:
                    self.backend.pause_old(cfg)
                elif plan['kind'] in ('upgrade', 'credential'):
                    system.pause(cfg)
                self.backend.quiescent(cfg, old=bool(plan['history']), migrated=migrated)
                self.window(cfg, plan['next_boundary_utc'])
                backup_images = {**(plan['source_images'] if plan['kind'] == 'migrate' else {}), **plan['before']}
                record['backup'] = self.fs.backup(plan['operation_id'], backup_images)
                record = self._record(record, 'writing')
                if plan['kind'] != 'credential':
                    manifest = self.fs.install_release(package_raw, plan['package_sha256'])
                    require(manifest == plan['release'], 'DEPLOY_RELEASE_CHANGED')
                token = credential if credential is not None else self.backend.token(OLD_TOKEN if plan['history'] else TOKEN)
                require(sha((token + '\n').encode()) == plan['credential_sha256'], 'DEPLOY_TOKEN_CHANGED')
                self.backend.validate_token(token)
                self.fs.write(TOKEN, (token + '\n').encode())
                if plan['history']:
                    for name in (TIMER, SERVICE):
                        path = '/etc/systemd/system/' + name
                        self.fs.remove(path, plan['before'][path])
                        self.fs.link(path, '/dev/null')
                    self.fs.write(OLD_RUNNER, RETIRED, 0o700)
                    self.backend.reload()
                    record['migration'] = {'status': 'active', 'source_images': plan['source_images'],
                                           'source_backup': {p: record['backup'][p] for p in plan['source_images']},
                                           'state_sha256': plan['history']['state_sha256'], 'operation_id': plan['operation_id']}
                    self.backend.retired(cfg, record['migration'])
                if plan['kind'] != 'credential':
                    self.fs.activate(plan['release'])
                    system = self.backend.system(cfg, 'retired' if migrated else 'none')
                    system.install(cfg)
                    system.reload()
                    system.verify(cfg)
                self.backend.quiescent(cfg, old=False, migrated=migrated)
                snapshot = observe(cfg, self.backend.reader(cfg, token))
                if plan['kind'] == 'install':
                    admissible(cfg, snapshot)
                self.window(cfg, plan['next_boundary_utc'])
                if plan['kind'] in ('install', 'migrate'):
                    self._initial_control(cfg, plan, plan['release']['program_sha256'])
                elif plan['kind'] == 'upgrade':
                    c = Control(self.journal.load())
                    if plan['release']['contracts'] == CONTRACTS and plan['old_release']['contracts'] != CONTRACTS:
                        c = commit(self.journal, 'format', {'schema':'vps-kit.format-transition/v1', 'operation_id':plan['operation_id'],
                                   'from_contracts':plan['old_release']['contracts'], 'to_contracts':CONTRACTS,
                                   'config_sha256':cfg.fingerprint, 'backup_sha256':digest(record['backup'])}, c.view.head)
                    commit(self.journal, 'program', {'operation_id': plan['operation_id'], 'from_sha256': c.owner['program_sha256'],
                           'to_sha256': plan['release']['program_sha256']}, c.view.head)
                record['active_release'] = plan['release']
                record['after'] = {p: self.fs.image(p) for p in plan['before']}
                if latest:
                    record['after'] = {**latest['after'], **record['after']}
                # 先提交完整安装事实；同一锁内再按候选授权恢复，恢复失败追加独立失败事务。
                record = self._record(record, 'complete')
                if plan['activation'] == 'enabled':
                    manager = Maintenance(self.journal, system, lambda c: self.backend.reader(c, self.backend.token(TOKEN)), self.clock)
                    manager._resume_locked()
                return {'result': 'DEPLOYMENT_COMPLETE', 'kind': plan['kind'], 'operation_id': plan['operation_id'],
                        'version': plan['release']['version'], 'timer': system.inspect(cfg)['timer'], 'post_requests': 0}
            except (Exception,) as error:
                # 不覆盖已经完成的安装事实；resume 自身维护账本保存其失败阶段。
                if record['stage'] != 'complete':
                    try:
                        self._record(record, 'failed', getattr(error, 'code', 'LOCAL_OPERATION_FAILED'))
                    except (Exception,):
                        pass
                raise

    def rollback(self, operation, task):
        """仅恢复本 manifest 所登记的文件；从不恢复 control、旧账本或计数。"""
        with self.journal.exclusive():
            self.start = self.clock.sample()[:2]
            _, operations, latest = self.log.read()
            require(operation in operations and latest['operation_id'] == operation, 'ROLLBACK_LATEST_OPERATION_REQUIRED')
            original = operations[operation]
            require(original['kind'] != 'rollback', 'ROLLBACK_ALREADY_RECORDED')
            plan = original['plan']
            cfg = parse_config(canonical((original.get('qualification') or {}).get('confirmed_config') or plan['config']))
            source = paths(plan_kind(plan))
            legacy_migration = plan['kind'] in ('migrate','vmrack-migrate')
            if legacy_migration:
                self.backend.select_source(plan_kind(plan))
            elif original['migration']:
                self.backend.select_source(kind_of(original['migration']))
            require(cfg.data['task_id'] == task, 'ROLLBACK_TASK')
            control = Control(self.journal.load())
            if control.owner:
                require(control.owner['status'] == 'active', 'ROLLBACK_OWNER_NOT_ACTIVE')
                # 配置回退不是程序/凭据回退。后续 P2 修订必须保留，不能先恢复文件再用旧配置 GET。
                require(control.config.fingerprint == cfg.fingerprint, 'ROLLBACK_CONFIG_CHANGED')
            maintenance_window([cfg.data['schedule']], self.now())
            format_exit = False
            if plan['old_release'] is not None:
                # 必须在暂停和修改 current 之前验证目标代码，避免指针短暂指向损坏版本。
                self.fs.verify_release(plan['old_release'])
                compatible = (plan['old_release']['contracts'] == CONTRACTS or
                        (not any(e['kind'] in ('format','vmrack_import') for e in control.view.events)
                         and all(c.data['schema_version'].endswith('/v1') for c in control.configs.values())))
                new_execution = any(s['program_sha256'] == plan['release']['program_sha256'] for s in control.ledgers.values())
                require(compatible or not new_execution, 'ROLLBACK_FORMAT_INCOMPATIBLE')
                format_exit = not compatible  # 无运行账本时只允许文件退出，保留新格式阻断旧运行。
            migrated = bool(original['migration'] or plan['history'])
            before = {p: self.fs.image(p) for p in plan['before']}
            backup = original['backup']
            # 备份未提交时尚未允许写正式文件。只能在原文件逐字节未变时撤销意图。
            if backup is None:
                require(before == plan['before'], 'ROLLBACK_NO_BACKUP_FILES_CHANGED')
            # 每个目标只允许原字节或该操作预定字节；不覆盖未登记手工变更。
            proposed = self.backend.proposed_images(cfg, plan, migrated)
            allowed = {}
            for path, image in before.items():
                allowed[path] = [plan['before'][path], proposed[path]]
                if original['stage'] != 'complete' and path in ('/etc/systemd/system/' + source['timer'], '/etc/systemd/system/' + source['service']):
                    allowed[path].append({'kind': 'absent'})
                require(image in allowed[path], 'ROLLBACK_UNKNOWN_FILE_CHANGE')
            for image in (backup or {}).values():
                if image['kind'] == 'file':
                    require(sha(self.fs.read(image['backup'])) == image['sha256'], 'ROLLBACK_BACKUP_HASH')
            schedules = [cfg.data['schedule'], *self.backend.rollback_source_guard(cfg, original)]
            maintenance_window(schedules, self.now())
            self.backend.pause_for_rollback(cfg, migrated)
            self.backend.quiescent(cfg, old=False, migrated=migrated, partial=True)
            # 暂停/静止检查本身可能耗时；恢复任一目标前重新验证完整目标集合。
            for path in plan['before']:
                require(self.fs.image(path) in allowed[path], 'ROLLBACK_UNKNOWN_FILE_CHANGE')
            schedules = [cfg.data['schedule'], *self.backend.rollback_source_guard(cfg, original)]
            maintenance_window(schedules, self.now())
            record = {'operation_id': str(uuid.uuid4()), 'kind': 'rollback', 'stage': 'prepared',
                      'plan': plan, 'backup': backup or {}, 'after': {}, 'active_release': None if format_exit else plan['old_release'],
                      'migration': original['migration'], 'error': None}
            record = self._record(record, 'prepared')
            try:
                record = self._record(record, 'writing')
                if backup is not None:
                    for path in plan['before']:
                        self.fs.restore(path, backup[path], allowed[path])
                self.backend.reload()
                if legacy_migration:
                    record['migration'] = {'status': 'returned', 'source_images': plan['source_images'],
                                           'source_backup': {p: backup[p] for p in plan['source_images']} if backup else {},
                                           'state_sha256': plan['history']['state_sha256'], 'operation_id': operation}
                    if plan['kind'] == 'vmrack-migrate':
                        record['migration'].update(source_kind=VMRACK, retire_cron=plan['source']['evidence']['retire_cron'])
                    self.backend.returned(cfg, plan)
                elif plan['kind'] == 'upgrade' and not format_exit:
                    self.fs.verify_release(plan['old_release'])
                    c = Control(self.journal.load())
                    if c.owner['program_sha256'] != plan['old_release']['program_sha256']:
                        commit(self.journal, 'program', {'operation_id': record['operation_id'], 'from_sha256': c.owner['program_sha256'],
                               'to_sha256': plan['old_release']['program_sha256']}, c.view.head)
                self.backend.quiescent(cfg, old=legacy_migration, migrated=migrated, partial=True)
                record['after'] = {p: self.fs.image(p) for p in plan['before']}
                if plan['kind'] in ('upgrade', 'credential') and not format_exit:
                    # 旧 Token 可能已在面板撤销；GET 失败不自动启用、不尝试第二次替换。
                    observe(cfg, self.backend.reader(cfg, self.backend.token(TOKEN)))
                self._record(record, 'complete')
                c = Control(self.journal.load())
                return {'result': 'FILES_RESTORED_AUTOMATION_PAUSED', 'post_requests': 0,
                        'history_preserved': True, 'new_intents_present': any(any(s['status'] in ('pending','confirmed') for s in v['steps']) for v in c.ledgers.values()),
                        'r2_resume': 'NOT_AUTHORIZED' if plan['kind'] == 'migrate' else 'NOT_APPLICABLE',
                        'legacy_resume': 'NOT_AUTHORIZED' if legacy_migration else 'NOT_APPLICABLE',
                        'incompatible_format_file_exit': format_exit,
                        'restored_legacy_entry_bypasses_new_history': legacy_migration}
            except Exception as error:
                try:
                    self._record(record, 'failed', getattr(error, 'code', 'LOCAL_OPERATION_FAILED'))
                except Exception:
                    pass
                raise
