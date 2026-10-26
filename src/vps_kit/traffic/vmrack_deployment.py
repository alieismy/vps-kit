"""独立 VMRack 接管事务；实际退役后才签发首期资格。"""
from copy import deepcopy
from datetime import timedelta
import uuid

from .calendar import maintenance_window, stamp
from .config import parse_config
from .contracts import canonical, digest, fields, require, utc
from .control import Control
from .deployment import Deployment, plan_hash, validate_plan
from .deploy_files import ENTRY, CURRENT, TOKEN
from .execution import observe
from .maintenance import safe_system
from .package import CONTRACTS, sha, verify
from .sources import VMRACK, paths
from .units import render
from .vmrack_legacy import (CRON_RETIRED, imported_history, inspect_evidence, old_schedule, qualify)


def validate_qualification(record):
    q = record['qualification']
    require(record['kind'] == 'vmrack-migrate', 'VMRACK_RECORD_KIND')
    if q is None:
        require(record['stage'] in ('prepared','writing','failed'), 'VMRACK_QUALIFICATION_MISSING')
        return
    fields(q, 'cutover_utc minimum_boundary_utc candidate candidate_sha256 confirmed_config state_sha256', 'VMRACK_QUALIFICATION_FIELDS')
    cfg = parse_config(canonical(q['candidate']))
    old = deepcopy(record['plan']['config'])
    old['schedule']['first_boundary_utc'] = cfg.data['schedule']['first_boundary_utc']
    require(old == cfg.data and q['candidate_sha256'] == cfg.fingerprint, 'VMRACK_RECONFIRM_SCOPE')
    require(q['confirmed_config'] is None or q['confirmed_config'] == q['candidate'], 'VMRACK_CONFIRMATION_CHANGED')
    require(utc(cfg.data['schedule']['first_boundary_utc'], 'VMRACK_FIRST') >= utc(q['minimum_boundary_utc'],'VMRACK_MINIMUM')
            > utc(q['cutover_utc'],'VMRACK_CUTOVER'), 'VMRACK_QUALIFICATION_TIME')
    require(q['state_sha256'] == record['plan']['history']['state_sha256'], 'VMRACK_HISTORY_CHANGED')


class VMRackDeployment(Deployment):
    def plan_vmrack(self, cfg, raw, package_sha, evidence, attestations):
        self.start = self.clock.sample()[:2]
        inspect_evidence(evidence)
        self.backend.select_source(VMRACK)
        fields(attestations, 'admin_scope local_accounting other_reset_jobs_absent', 'DEPLOY_ATTESTATIONS')
        require(all(v is True for v in attestations.values()), 'DEPLOY_ATTESTATIONS')
        view, _, latest = self.log.read()
        control = Control(self.journal.load())
        require(latest is None and not control.view.events and cfg.data['revision'] == 1, 'DEPLOY_OWNER_EXISTS')
        source = self.backend.collect_vmrack(cfg, evidence)
        system = self.backend.system(cfg, 'source')
        info = system.inspect(cfg)
        safe_system(info)
        require(info['timer'] == info['service'] == 'absent' and not info['jobs'], 'DEPLOY_NEW_UNITS_EXIST')
        require(source['system']['service'] == 'inactive' and not source['system']['jobs'], 'MIGRATION_OLD_BUSY')
        require(source['system']['timer'] in ('waiting','paused'), 'LEGACY_TIMER_STATE')
        require(all(self.fs.image(p)['kind'] == 'absent' for p in (ENTRY,CURRENT,TOKEN)), 'DEPLOY_NEW_FILES_EXIST')
        # 旧来源仍有执行能力时，旧错误月历的竞争窗口也必须受保护。
        maintenance_window([cfg.data['schedule'], old_schedule(evidence['profile'])], self.now())
        history = imported_history(cfg, source, evidence, utc(evidence['expected_cutover_utc'],'VMRACK_CUTOVER'), self.now())
        manifest = verify(raw, package_sha)[0]
        require(manifest['contracts'] == CONTRACTS, 'DEPLOY_EXTENSION_CAPABILITY')
        token = self.backend.token(paths(VMRACK)['token'])
        snapshot = observe(cfg, self.backend.reader(cfg, token))
        # observe 已核对完整对象与各自策略；不修改配额、不创建 Client。
        require(self.backend.collect_vmrack(cfg, evidence) == source, 'VMRACK_SOURCE_CHANGED')
        require(system.inspect(cfg) == info, 'DEPLOY_SYSTEM_DRIFT')
        p = paths(VMRACK)
        mutable = [ENTRY,CURRENT,TOKEN,*['/etc/systemd/system/'+n for n in render(cfg)],
                   p['runner'],'/etc/systemd/system/'+p['timer'],'/etc/systemd/system/'+p['service'],*evidence['retire_cron']]
        plan = {'schema':'vps-kit.deployment.plan/v2','operation_id':str(uuid.uuid4()),'kind':'vmrack-migrate',
                'config':cfg.data,'control_head':control.view.head,'deployment_head':view.head,'activation':'paused',
                'attestations':attestations,'system':info,'old_system':source['system'],
                'before':{p:self.fs.image(p) for p in mutable},'source_images':{**source['images'],**source['proof_images']},
                'history':history,'release':manifest,'old_release':None,'package_sha256':package_sha,
                'credential_sha256':sha((token+'\n').encode()),'next_boundary_utc':history['next_boundary_utc'],
                'next_period_id':1,'source':{'kind':VMRACK,'evidence':evidence}}
        return {**plan,'sha256':plan_hash(plan)}

    def apply_vmrack(self, plan, raw):
        cfg = validate_plan(plan)
        require(plan['kind'] == 'vmrack-migrate', 'VMRACK_PLAN_KIND')
        with self.journal.exclusive():
            require(self.journal.load().head == plan['control_head'] and self.log.read()[0].head == plan['deployment_head'], 'DEPLOY_PLAN_STALE')
            fresh = self.plan_vmrack(cfg, raw, plan['package_sha256'], plan['source']['evidence'], plan['attestations'])
            require({k:v for k,v in plan.items() if k not in ('operation_id','sha256')} ==
                    {k:v for k,v in fresh.items() if k not in ('operation_id','sha256')}, 'DEPLOY_PLAN_DRIFT')
            record = {'operation_id':plan['operation_id'],'kind':'vmrack-migrate','stage':'prepared','plan':plan,
                      'backup':None,'after':{},'active_release':None,'migration':None,'error':None,'qualification':None}
            record = self._record(record,'prepared')
            try:
                self.backend.pause_old(cfg)
                # 首次旧锁在暂停/完全退出后获取，避免旧 RuntimeDirectory 清理换 inode。
                with self.backend.old_lock_after_pause():
                    current = self.backend.collect_vmrack(cfg, plan['source']['evidence'])
                    require({k:v for k,v in current['system'].items() if k != 'timer'} ==
                            {k:v for k,v in plan['old_system'].items() if k != 'timer'}, 'VMRACK_EXECUTION_DURING_PAUSE')
                    self.backend.quiescent(cfg, old=True, migrated=True)
                    maintenance_window([cfg.data['schedule'],old_schedule(plan['source']['evidence']['profile'])], self.now())
                    record['backup'] = self.fs.backup(plan['operation_id'], {**plan['source_images'],**plan['before']})
                    record = self._record(record,'writing')
                    p = paths(VMRACK)
                    for name in (p['timer'],p['service']):
                        target = '/etc/systemd/system/'+name
                        self.fs.remove(target,plan['before'][target])
                        self.fs.link(target,'/dev/null')
                    self.fs.write(p['runner'],p['retired'],0o700)
                    for path in plan['source']['evidence']['retire_cron']:
                        self.fs.write(path,CRON_RETIRED,0o644)
                    self.backend.reload()
                    record['migration'] = {'status':'active','source_kind':VMRACK,'source_images':plan['source_images'],
                        'source_backup':{n:record['backup'][n] for n in plan['source_images']},
                        'state_sha256':plan['history']['state_sha256'],'operation_id':plan['operation_id'],
                        'retire_cron':plan['source']['evidence']['retire_cron']}
                    self.backend.retired(cfg,record['migration'])
                    e = plan['source']['evidence']
                    now = self.now()
                    cutover = max(now, utc(e['processes_closed_at'],'VMRACK_TIME'), utc(e['server_closed_at'],'VMRACK_TIME'))
                    minimum = qualify(cfg,e,cutover,now)
                    candidate = cfg.data
                    candidate['schedule']['first_boundary_utc'] = stamp(max(minimum,utc(candidate['schedule']['first_boundary_utc'],'VMRACK_FIRST')))
                    candidate = parse_config(canonical(candidate))
                    record['qualification'] = {'cutover_utc':stamp(cutover),'minimum_boundary_utc':stamp(minimum),
                        'candidate':candidate.data,'candidate_sha256':candidate.fingerprint,'confirmed_config':None,
                        'state_sha256':plan['history']['state_sha256']}
                    record = self._record(record,'retired')
                    if candidate.fingerprint != cfg.fingerprint:
                        self._record(record,'waiting-candidate-reconfirmation')
                        return {'result':'WAITING_CANDIDATE_RECONFIRMATION','operation_id':plan['operation_id'],
                                'candidate_sha256':candidate.fingerprint,'first_boundary_utc':candidate.data['schedule']['first_boundary_utc'],
                                'cutover_utc':stamp(cutover),'old_entry':'RETIRED','new_timer':'ABSENT_OR_PAUSED','post_requests':0}
                    return self._finish(record,raw,cfg)
            except Exception as error:
                self._fail_open(record,error)
                raise

    def _fail_open(self, record, error):
        try:
            latest = self.log.read()[2]
            if latest and latest['stage'] not in ('complete','failed','waiting-candidate-reconfirmation'):
                self._record(latest,'failed',getattr(error,'code','LOCAL_OPERATION_FAILED'))
        except Exception:
            pass

    def reconfirm(self, operation, expected, raw):
        with self.journal.exclusive():
            self.start = self.clock.sample()[:2]
            _,_,record = self.log.read()
            require(record and record['operation_id'] == operation and record['stage'] == 'waiting-candidate-reconfirmation', 'VMRACK_RECONFIRM_STAGE')
            q, plan = record['qualification'],record['plan']
            require(q['candidate_sha256'] == expected, 'VMRACK_RECONFIRM_HASH')
            cfg = parse_config(canonical(q['candidate']))
            require(not self.journal.load().events, 'VMRACK_RECONFIRM_CONTROL_EXISTS')
            self.backend.select_source(VMRACK)
            self.backend.retired(cfg,record['migration'])
            self.backend.quiescent(cfg, migrated=True)
            require(verify(raw,plan['package_sha256'])[0] == plan['release'],'DEPLOY_RELEASE_CHANGED')
            self.window(cfg,cfg.data['schedule']['first_boundary_utc'])
            require(utc(cfg.data['schedule']['first_boundary_utc'],'VMRACK_FIRST') >=
                    qualify(cfg,plan['source']['evidence'],utc(q['cutover_utc'],'VMRACK_CUTOVER'),self.now()), 'VMRACK_RECONFIRM_EXPIRED')
            record = deepcopy(record)
            record['qualification']['confirmed_config'] = cfg.data
            record = self._record(record,'reconfirmed')
            try:
                return self._finish(record,raw,cfg)
            except Exception as error:
                self._fail_open(record,error)
                raise

    def _finish(self, record, raw, cfg):
        plan = record['plan']
        self.backend.retired(cfg,record['migration'])
        self.window(cfg,cfg.data['schedule']['first_boundary_utc'])
        manifest = self.fs.install_release(raw,plan['package_sha256'])
        require(manifest == plan['release'], 'DEPLOY_RELEASE_CHANGED')
        token = self.backend.token(paths(VMRACK)['token'])
        require(sha((token+'\n').encode()) == plan['credential_sha256'],'DEPLOY_TOKEN_CHANGED')
        self.fs.write(TOKEN,(token+'\n').encode())
        self.fs.activate(manifest)
        system = self.backend.system(cfg,'retired')
        system.install(cfg)
        system.reload()
        system.verify(cfg)
        self.backend.quiescent(cfg,migrated=True)
        observe(cfg,self.backend.reader(cfg,token))
        self.backend.retired(cfg,record['migration'])
        self.window(cfg,cfg.data['schedule']['first_boundary_utc'])
        q = record['qualification']
        history = {**plan['history'],'cutover_utc':q['cutover_utc'],'minimum_boundary_utc':q['minimum_boundary_utc'],
                   'next_boundary_utc':cfg.data['schedule']['first_boundary_utc']}
        self._initial_control(cfg,{**plan,'history':history,'next_boundary_utc':history['next_boundary_utc']},manifest['program_sha256'])
        record['active_release'] = manifest
        record['after'] = {p:self.fs.image(p) for p in plan['before']}
        self._record(record,'complete')
        return {'result':'DEPLOYMENT_COMPLETE','kind':'vmrack-migrate','operation_id':plan['operation_id'],
                'version':manifest['version'],'timer':system.inspect(cfg)['timer'],'post_requests':0,
                'first_boundary_utc':history['next_boundary_utc'],'source_history':'IMPORTED_NOT_NEW_CONFIRMED_LEDGER'}
