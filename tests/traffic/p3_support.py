"""P3 L1：临时文件真实读写；权限、符号链接、目录 fsync、flock、systemd 全部显式建模。"""
import copy
import io
import json
from pathlib import Path
from unittest.mock import patch
import zipfile
from datetime import datetime, timedelta

from support import ROOT, config_data
from p2_support import ATTESTATIONS, Clock, ModelFiles, ModelLock, Panel
from vps_kit import __version__
from vps_kit.traffic.calendar import boundary, stamp
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import canonical, digest, json_load, require
from vps_kit.traffic.control import Control
from vps_kit.traffic.deploy_files import DeploymentFiles, CURRENT, ENTRY, LIB, TOKEN
from vps_kit.traffic.deploy_native import NativeDeploymentBackend, proposed_images
from vps_kit.traffic.deployment import Deployment
from vps_kit.traffic.journal import Journal
from vps_kit.traffic.legacy import CONFIG, RUNNER, STATE, TIMER, SERVICE, TOKEN as OLD_TOKEN, R2_HASH, V212_HASH, schedule
from vps_kit.traffic.package import build, sha, program_digest
from vps_kit.traffic.units import render, verify_text

TOKEN_A = 'A' * 48  # 仅合成固定值，不是任何实际凭据。
TOKEN_B = 'B' * 48


class Files(ModelFiles):
    def read_mode(self, path, modes):
        return self.read(path)  # 权限由模型元数据检查；不把 Windows chmod 称为 Linux 权限验证。


class FS(DeploymentFiles):
    def __init__(self, root):
        super().__init__(root, files=Files(root))
        self.modes = {}

    def read(self, logical, modes=(0o600,)):
        require(self.modes[logical] in modes, 'MODEL_FILE_PERMISSIONS')
        return super().read(logical, modes)

    def write(self, logical, raw, mode=0o600):
        self.modes[logical] = mode
        return super().write(logical, raw, mode)

    def image(self, logical):
        p = self.path(logical)
        if not p.exists():
            return {'kind': 'absent'}
        raw = p.read_bytes()
        if raw.startswith(b'MODEL_LINK:'):
            return {'kind': 'link', 'target': raw[11:].decode()}
        return {'kind': 'file', 'sha256': sha(raw), 'mode': self.modes[logical]}

    def link(self, logical, target):
        # 在无 symlink 特权的 Windows 上显式用普通文件建模；生产实现仍为 os.symlink。
        self.write(logical, b'MODEL_LINK:' + target.encode(), 0o644)


class System:
    invocation_id = '1' * 32

    def __init__(self, backend):
        self.b = backend
        self.timer = self.service = 'absent'
        self.mode = 'none'
        self.calls = []
        self.hook = lambda *_: None

    @property
    def program_sha256(self):
        image = self.b.fs.image(CURRENT)
        if image['kind'] == 'absent':
            return digest({'bootstrap': 'vps-kit-P3'})
        manifest = json_load(self.b.fs.read(LIB + '/' + image['target'] + '/manifest.json', (0o644,)))
        return manifest['program_sha256']

    def backup(self, cfg):
        return {name: self.b.fs.read('/etc/systemd/system/' + name, (0o644,)).decode()
                for name in render(cfg) if self.b.fs.image('/etc/systemd/system/' + name)['kind'] == 'file'}

    def inspect(self, cfg):
        if self.mode == 'retired':
            self.b.legacy_check(cfg, self.mode)
        return {'timer': self.timer, 'service': self.service, 'jobs': False, 'dropins': False,
                'foreign_jobs': False, 'reload': False, 'program_sha256': self.program_sha256,
                'units_sha256': digest(self.backup(cfg)),
                'next_utc': stamp(__import__('vps_kit.traffic.calendar', fromlist=['next_boundary']).next_boundary(cfg.data['schedule'], self.b.clock.wall)) if self.timer == 'waiting' else None}

    def verify(self, cfg):
        self.hook('verify')
        verify_text(cfg, self.backup(cfg), legacy=self.mode != 'none')

    def install(self, cfg):
        self.calls.append('install')
        for name, value in render(cfg, legacy=self.mode != 'none').items():
            self.b.fs.write('/etc/systemd/system/' + name, value.encode(), 0o644)
        self.timer, self.service = 'paused', 'inactive'

    def reload(self):
        self.b.reload()

    def pause(self, cfg):
        self.calls.append('pause')
        self.timer = 'paused'

    def resume(self, cfg):
        self.calls.append('resume')
        self.timer = 'waiting'

    def guard(self, cfg, control):
        self.b.deployment.guard(cfg)
        self.verify(cfg)


class Backend(NativeDeploymentBackend):
    def __init__(self, fs, cfg, clock):
        super().__init__(fs, execute=self.execute_model)
        self.cfg, self.clock, self.panel = cfg, clock, Panel(cfg)
        self.new_system = System(self)
        self.old_timer = 'waiting'
        self.old_service = 'inactive'
        self.revoked = set()
        self.calls = []
        self.hook = lambda *_: None

    def execute_model(self, args, limit=None):
        from subprocess import CompletedProcess
        require(args[1] == 'show', 'MODEL_UNEXPECTED_SYSTEM_COMMAND')
        name = args[2]
        require(name in (TIMER, SERVICE), 'MODEL_UNIT')
        masked = self.fs.image('/etc/systemd/system/' + name)['kind'] == 'link'
        p = {'LoadState': 'masked' if masked else 'loaded', 'ActiveState': 'inactive', 'SubState': 'dead', 'MainPID': '0',
             'Job': '', 'DropInPaths': '', 'NeedDaemonReload': 'no'}
        return CompletedProcess(args, 0, ''.join(k+'='+v+'\n' for k,v in p.items()).encode(), b'')

    def reader(self, cfg, token):
        require(token not in self.revoked, 'SYNTHETIC_TOKEN_REVOKED')
        self.hook('reader')
        return self.panel

    def system(self, cfg, mode):
        self.new_system.mode = mode
        return self.new_system

    def old_system(self, allow_mask=False):
        require(self.old_service == 'inactive', 'LEGACY_SERVICE_BUSY')
        return {'timer': self.old_timer, 'service': self.old_service, 'jobs': False}

    def collect_r2(self, cfg):
        with patch('vps_kit.traffic.deploy_native.binding_guard'):
            value = super().collect_r2(cfg)
        # 模拟已识别 r2 二进制；仅测试适配器的受信替身，不拿伪脚本冒充真实摘要。
        value['runner_sha256'] = R2_HASH
        return value

    def pause_old(self, cfg):
        self.calls.append('pause_old')
        self.old_timer = 'paused'

    def reload(self):
        self.calls.append('reload')
        self.hook('reload')
        names = render(self.cfg)
        present = any(self.fs.image('/etc/systemd/system/' + n)['kind'] == 'file' for n in names)
        self.new_system.service = 'inactive' if present else 'absent'
        self.new_system.timer = 'paused' if present else 'absent'

    def quiescent(self, cfg, old=False, migrated=False, partial=False):
        require(self.new_system.timer in ('paused', 'absent') and self.new_system.service in ('inactive', 'absent'), 'MODEL_ACTIVE')
        if migrated:
            require(self.old_timer == 'paused' and self.old_service == 'inactive', 'MODEL_OLD_ACTIVE')

    def pause_for_rollback(self, cfg, migrated):
        self.new_system.pause(cfg)
        if migrated:
            self.pause_old(cfg)


def package(root, version=None):
    version = version or __version__
    path = root / ('package-' + version + '.zip')
    build(ROOT, path)
    raw = path.read_bytes()
    if version == __version__:
        return raw
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        files = {n: z.read(n) for n in z.namelist()}
    for key in ('pyproject.toml', 'src/vps_kit/__init__.py'):
        files[key] = files[key].replace(__version__.encode(), version.encode())
    manifest = json_load(files.pop('manifest.json'))
    manifest['version'] = version
    manifest['program_sha256'] = program_digest(files)
    manifest['files'] = {n: {'sha256': sha(b), 'size': len(b), 'mode': 0o755 if n == 'bin/vps-kit' else 0o644} for n,b in files.items()}
    files['manifest.json'] = canonical(manifest).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as z:
        for n,b in files.items():
            z.writestr(n,b)
    return output.getvalue()


def harness(root, cfg=None):
    cfg = cfg or parse_config(canonical(config_data()))
    fs, clock = FS(root), Clock()
    fs.mkdir('/var/lib/vps-kit/traffic')  # 显式模拟 native bootstrap 的受控目录。
    journal = Journal(root/'control', root/'lock', files=fs.files, lock=ModelLock())
    backend = Backend(fs, cfg, clock)
    deployment = Deployment(journal, fs, backend, clock)
    backend.deployment = deployment
    return cfg, fs, clock, journal, backend, deployment


def legacy_config(instance='Basic', schema=2):
    amount = 500 if instance == 'Basic' else 1000
    v = {'schema': schema, 'instance': instance, 'panel_url': 'https://panel.example.invalid:8443/example',
         'inbound_listen': '', 'inbound_port': 443, 'first_cycle': '2027-01' if schema == 2 else '2026-12', 'machine_id': '1'*32}
    if schema == 1:
        return {**v, 'disabled_policy': 'stop'}
    return {**v, 'provider': {'amount': amount, 'unit': 'GiB', 'unit_confirmed': True, 'accounting': 'in+out', 'proxy_factor': 2, 'reserve_percent': 0},
            'inbound_quota_bytes': amount//2 * 1024**3, 'inbound_disabled_policy': 'quota-recovery',
            'clients': [{'email': instance, 'quota_bytes': amount//2 * 1024**3, 'disabled_policy': 'quota-recovery'}]}


def provenance(old, v1, cfg, states):
    """合成的完整 r2 安装 manifest 结构；不冒充真实备份或真实 unit 摘要。"""
    timer, service = old_units(v1)
    original = next(c for c in cfg.data['clients'] if c['email'] == old['instance'])
    return {'instance': old['instance'], 'machine_id': old['machine_id'],
            'files': {'runner': V212_HASH, 'config': sha(canonical(v1).encode()),
                      'service': sha(service), 'timer': sha(timer)},
            'new_runner': R2_HASH, 'candidate': sha(canonical(old).encode()),
            'status': 'installed', 'state_before': {n: sha(b) for n, b in states.items()},
            'original_inbound_id': cfg.data['inbound']['id'],
            'original_client_binding': digest([original['record_id'], original['uuid']]),
            'old_cycle': (datetime.strptime(old['first_cycle'], '%Y-%m') - timedelta(days=1)).strftime('%Y-%m')}


def target(old):
    d = config_data()
    d['host_id_sha256'] = sha(old['machine_id'].encode())
    d['inbound'].update(remark='🇺🇸 LA-Reality', quota_bytes=old['inbound_quota_bytes'], policy=old['inbound_disabled_policy'])
    d['clients'] = [{**d['clients'][0], 'email': old['instance'], 'quota_bytes': old['inbound_quota_bytes'], 'policy': 'quota-recovery'}]
    d['schedule'] = schedule(old)
    d['provider']['amount'] = str(old['provider']['amount'])
    return parse_config(canonical(d))


def old_units(old):
    timer = f'''[Unit]\nDescription=SYNTHETIC timer\n[Timer]\nOnCalendar=*-*-22 {schedule(old)['time']} America/Toronto\nAccuracySec=1s\nRandomizedDelaySec=0\nPersistent=false\nUnit={SERVICE}\n[Install]\nWantedBy=timers.target\n'''
    values = {'Type':'oneshot','Restart':'no','TimeoutStartSec':'300s','ExecStart':RUNNER+' --run','LoadCredential':'api-token:'+OLD_TOKEN,
              'User':'root','Group':'root','UMask':'0077','RuntimeDirectory':'vmiss-traffic-reset','RuntimeDirectoryMode':'0700',
              'RuntimeDirectoryPreserve':'yes','StateDirectory':'vmiss-traffic-reset','StateDirectoryMode':'0700',
              'StandardOutput':'journal','StandardError':'journal','NoNewPrivileges':'true','PrivateTmp':'true','ProtectSystem':'strict','ProtectHome':'true'}
    service = '[Unit]\nDescription=SYNTHETIC service\nRequires=x-ui.service\nAfter=x-ui.service time-sync.target\n[Service]\n' + ''.join(k+'='+v+'\n' for k,v in values.items())
    return timer.encode(), service.encode()


def seed_legacy(fs, old, states=None):
    fs.write(RUNNER, b'SYNTHETIC_OLD_RUNNER_NOT_EXECUTABLE', 0o700)
    fs.write(CONFIG, canonical(old).encode())
    fs.write(OLD_TOKEN, (TOKEN_A+'\n').encode())
    timer, service = old_units(old)
    fs.write('/etc/systemd/system/'+TIMER, timer, 0o644)
    fs.write('/etc/systemd/system/'+SERVICE, service, 0o644)
    fs.mkdir(STATE)
    for name, raw in (states or {}).items():
        fs.write(STATE+'/'+name, raw)


def old_state(old, cfg, cycle):
    c = cfg.data['clients'][0]
    bind = digest([c['record_id'], c['uuid']])
    ib = {'id': cfg.data['inbound']['id'], 'enabled': True, 'bytes': 2000, 'embedded_binding': digest({c['email']:c['uuid']})}
    client = {'binding':bind,'record_id':c['record_id'],'enabled':True,'traffic_enabled':True,'bytes':1000}
    edge = boundary(schedule(old), *map(int, cycle.split('-')))
    from datetime import timedelta
    time = lambda n: (edge+timedelta(seconds=n)).isoformat()
    if old['schema'] == 1:
        snap = {'id':ib['id'], 'client_binding':bind, 'inbound_bytes':2000, 'client_bytes':1000,
                'inbound_enabled':True, 'client_enabled':True, 'traffic_enabled':True}
        return canonical({'schema':1,'cycle':cycle,'binding':digest(old),'inbound_id':ib['id'],'client_binding':bind,'recover_inbound':False,
                          'steps':{'client':{'status':'confirmed','sent_at':time(1),'confirmed_at':time(2),'before':snap,'after':{**snap,'client_bytes':0}},
                                   'inbound':{'status':'confirmed','sent_at':time(3),'confirmed_at':time(4),'before':{**snap,'client_bytes':0},'after':{**snap,'inbound_bytes':0,'client_bytes':0}},
                                   'enable':{'status':'not_required'}},'completed_at':time(5),'final':{**snap,'inbound_bytes':0,'client_bytes':0}}).encode()
    snap = {'inbound':ib,'clients':{c['email']:client}}
    return canonical({'schema':2,'instance':old['instance'],'cycle':cycle,'binding':digest(old),'initial':snap,
                      'steps':{'client:'+c['email']:{'status':'confirmed','sent_at':time(1),'confirmed_at':time(2),'before':snap,
                               'after':{'inbound':ib,'clients':{c['email']:{**client,'bytes':0}}}},
                               'inbound':{'status':'confirmed','sent_at':time(3),'confirmed_at':time(4),'before':{'inbound':ib,'clients':{}},
                                          'after':{'inbound':{**ib,'bytes':0},'clients':{}}},'enable':{'status':'not_required'}},
                      'completed_at':time(5),'final':{'inbound':{**ib,'bytes':0},'clients':{c['email']:{**client,'bytes':0}}}}).encode()
