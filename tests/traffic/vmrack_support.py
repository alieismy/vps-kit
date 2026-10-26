"""VMRack L1 替身：私有主机/Token 不进入 fixture；原生 systemd/flock 未执行。"""
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from support import ROOT, config_data
from p3_support import Backend, System, FS, TOKEN_A
from p2_support import Clock, ModelLock
from vps_kit.traffic.config import parse_config, SCHEMA_V2
from vps_kit.traffic.contracts import canonical, digest, require
from vps_kit.traffic.deploy_native import NativeDeploymentBackend
from vps_kit.traffic.journal import Journal
from vps_kit.traffic.package import sha
from vps_kit.traffic.sources import VMRACK, paths
from vps_kit.traffic.units import render, verify_text
from vps_kit.traffic.vmrack_deployment import VMRackDeployment
from vps_kit.traffic.vmrack_legacy import PROFILES

FIXTURES = ROOT/'tests/traffic/fixtures/vmrack'


def config(profile='VMRack-35', first=None, version='3.7.0'):
    d = config_data(version)
    amount, _, _, anchor, _ = PROFILES.get(profile, (1000,0,'','2026-11-16T02:05:00Z',''))
    d['schema_version'] = SCHEMA_V2
    d['schedule'] = {'kind':'fixed-interval','anchor_utc':anchor,'interval_seconds':2592000,
                     'timezone':'Asia/Singapore','first_boundary_utc':first or anchor}
    d['provider']['amount'] = str(amount)
    d['inbound']['quota_bytes'] = amount//2*1024**3
    d['clients'] = d['clients'][:1]
    d['clients'][0]['quota_bytes'] = amount//2*1024**3
    return parse_config(canonical(d))


class RackSystem(System):
    def verify(self,cfg):
        self.hook('verify')
        verify_text(cfg,self.backup(cfg),legacy=VMRACK if self.mode != 'none' else False)

    def install(self,cfg):
        self.calls.append('install')
        self.b.cfg = cfg
        for name,value in render(cfg,legacy=VMRACK if self.mode != 'none' else False).items():
            self.b.fs.write('/etc/systemd/system/'+name,value.encode(),0o644)
        self.timer,self.service = 'paused','inactive'


class RackBackend(Backend):
    def __init__(self,fs,cfg,clock):
        super().__init__(fs,cfg,clock)
        self.source_kind = VMRACK
        self.new_system = RackSystem(self)
        self.execution = 'no-invocation'
        self.lock_ok = True
        self.old_lock = ModelLock()

    def execute_model(self,args,limit=None):
        from subprocess import CompletedProcess
        p = paths(VMRACK)
        require(args[1]=='show' and args[2] in (p['timer'],p['service']),'MODEL_UNEXPECTED_SYSTEM_COMMAND')
        masked = self.fs.image('/etc/systemd/system/'+args[2])['kind']=='link'
        raw = 'LoadState='+('masked' if masked else 'loaded')+'\nActiveState=inactive\nSubState=dead\nMainPID=0\n'
        return CompletedProcess(args,0,raw.encode(),b'')

    def old_system(self,allow_mask=False):
        return {**super().old_system(allow_mask),'execution':self.execution}

    def collect_vmrack(self,cfg,evidence,audit=False):
        with patch('vps_kit.traffic.deploy_native.binding_guard'):
            return NativeDeploymentBackend.collect_vmrack(self,cfg,evidence,audit)

    @contextmanager
    def old_lock_after_pause(self):
        require(self.old_timer=='paused','MODEL_OLD_ACTIVE')
        self.calls.append('old_lock_after_pause')
        with self.old_lock(Path(paths(VMRACK)['lock'])):
            self.lock_identity_check()
            yield

    def lock_identity_check(self):
        require(self.lock_ok,'LOCK_REPLACED')


def harness(root,cfg=None,now='2026-10-23T12:00:00Z'):
    cfg = cfg or config()
    fs,clock = FS(root),Clock(now)
    fs.mkdir('/var/lib/vps-kit/traffic')
    journal = Journal(root/'control',root/'lock',files=fs.files,lock=ModelLock())
    backend = RackBackend(fs,cfg,clock)
    deployment = VMRackDeployment(journal,fs,backend,clock)
    backend.deployment = deployment
    return cfg,fs,clock,journal,backend,deployment


def seed(h,profile='VMRack-35',mode='M1',expected_cutover=None,cron=False):
    cfg,fs,clock,journal,b,d = h
    p = paths(VMRACK)
    runner = (FIXTURES/'runner-35.sh').read_bytes()
    timer = (FIXTURES/'legacy.timer').read_bytes()
    if profile=='VMRack-49':
        runner = runner.replace(b'644245094400',b'1073741824000').replace(b'day 10 09:50:00-09:54:59',b'day 21 10:35:00-10:39:59')
        for m in range(5):
            runner = runner.replace(f'10 09:5{m}'.encode(),f'21 10:3{5+m}'.encode())
        timer = timer.replace(b'*-*-10 09:50:00',b'*-*-21 10:35:00')
    from urllib.parse import urlsplit
    url = urlsplit(cfg.data['panel']['url'])
    resolve = f'{url.hostname}:{url.port or 443}:127.0.0.1'
    runner = runner.replace(b'panel.example.invalid:8443:127.0.0.1',resolve.encode())
    old = {'PANEL_BASE_URL':cfg.data['panel']['url'],'PANEL_RESOLVE':resolve,
           'CLIENT_EMAIL':cfg.data['clients'][0]['email'],'INBOUND_REMARK':cfg.data['inbound']['remark'],
           'EXPECTED_TOTAL_BYTES':str(PROFILES[profile][0]*1024**3)}
    fs.write(p['config'],''.join(k+"='"+v+"'\n" for k,v in old.items()).encode())
    fs.write(p['runner'],runner,0o700)
    fs.write(p['token'],(TOKEN_A+'\n').encode())
    fs.write('/etc/systemd/system/'+p['timer'],timer,0o644)
    fs.write('/etc/systemd/system/'+p['service'],(FIXTURES/'legacy.service').read_bytes(),0o644)
    fs.mkdir(p['state'])
    fs.write(p['state']+'/client-reset.done',b'2026-10\n')
    fs.write(p['state']+'/inbound-reset.done',b'2026-10\n')
    proof = '/root/migration-evidence/observations.json'
    fs.write(proof,b'SYNTHETIC independent evidence; not actual provider or API evidence')
    targets = [p[k] for k in ('runner','config','token')]+['/etc/systemd/system/'+p[k] for k in ('timer','service')]+[p['state']+'/'+n for n in ('client-reset.done','inbound-reset.done')]
    crons=[]
    if cron:
        crons=['/etc/cron.d/vmrack-reset']
        fs.write(crons[0],('50 9 10 * * root '+p['runner']+' --run\n').encode(),0o644)
        targets+=crons
    from datetime import timedelta
    from vps_kit.traffic.contracts import utc
    from vps_kit.traffic.calendar import stamp
    previous=utc(cfg.data['schedule']['anchor_utc'],'ANCHOR')-timedelta(days=30)
    evidence={'schema':'vps-kit.vmrack-evidence/v1','profile':profile,'candidate_sha256':cfg.fingerprint,
              'object_sha256':cfg.object_fingerprint,'source_images_sha256':digest({n:fs.image(n) for n in targets}),
              'provider_anchor_utc':cfg.data['schedule']['anchor_utc'],'provider_amount_gib':PROFILES[profile][0],
              'mode':mode,'actor':'synthetic-reviewer','evidence_files':{proof:sha(fs.read(proof))},'retire_cron':crons,
              'inventory_reviewed':True,'no_other_entries':True,'official_build_confirmed':True,'late_effects_closed':True,
              'processes_closed_at':stamp(clock.wall),'server_closed_at':stamp(clock.wall),
              'expected_cutover_utc':expected_cutover or stamp(clock.wall),'quarantine_accepted':mode=='M2',
              'no_resets_after_actions':True if mode=='M1' else None,
              'last_boundary_utc':stamp(previous) if mode=='M1' else None,'actions':[]}
    if mode=='M1':
        for obj in ['inbound']+['client:'+str(c['record_id']) for c in cfg.data['clients']]:
            evidence['actions'].append({'object':obj,'first_possible_at':stamp(previous+timedelta(seconds=1)),
                                       'last_possible_at':stamp(previous+timedelta(seconds=2)), 'evidence_sha256':sha(fs.read(proof))})
    return evidence
