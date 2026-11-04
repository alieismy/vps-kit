"""真实 a2 发布包的离线回退回归；面板、权限和 systemd 使用既有模型。"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests/traffic')]

from p2_support import ATTESTATIONS
from p3_support import TOKEN_A, harness
from support import config_data
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import canonical
from vps_kit.traffic.control import Control
from vps_kit.traffic.execution import execute
from vps_kit.traffic.package import build, sha, verify

# https://github.com/alieismy/vps-kit/releases/tag/v0.5.0a2
# 使用已发布原包；不能用当前源码改版本号生成替代物。
A2_SHA256 = 'b091d9f1ef7db47142efcbd37a81e42865682d5fd1e39e049f5b97e588c6445f'


def offline_only(event, args):
    if event in ('socket.connect', 'socket.bind', 'socket.getaddrinfo',
                 'socket.gethostbyaddr', 'socket.gethostbyname'):
        raise AssertionError('回退回归禁止网络访问')


sys.addaudithook(offline_only)

OLD_READER = r'''
import json, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
payload = json.loads(sys.stdin.read())
sys.path[:0] = [payload['old_src'], payload['test_support']]
def offline_only(event, args):
    if event.startswith('socket.') or event == 'subprocess.Popen':
        raise AssertionError('旧版本读取进程禁止网络和子进程')
sys.addaudithook(offline_only)
import vps_kit
assert vps_kit.__version__ == '0.5.0a2'
assert Path(vps_kit.__file__).resolve().is_relative_to(Path(payload['old_src']).resolve())
from vps_kit.traffic.package import verify, sha, inspect_manifest
from vps_kit.traffic.contracts import ContractError, digest
from vps_kit.traffic.control import Control
from vps_kit.traffic.journal import Journal
from vps_kit.traffic.deployment import Deployment
from p2_support import Clock, ModelLock
from p3_support import FS, Backend
raw = Path(payload['new_package']).read_bytes()
manifest, _ = verify(raw, sha(raw))
# 原缺陷路径仍必须被真实旧版本拒绝，确保没有替换或放宽旧解析器。
bad = dict(manifest, files=dict(manifest['files']))
bad['files']['examples/3.11.0.json'] = next(iter(bad['files'].values()))
try:
    inspect_manifest(bad)
except ContractError as error:
    assert error.code == 'PACKAGE_PATH'
else:
    raise AssertionError('旧版白名单被放宽')
root = Path(payload['model'])
fs = FS(root)
fs.modes = payload['modes']
journal = Journal(root / 'control', root / 'lock', files=fs.files, lock=ModelLock())
control = Control(journal.load())
clock = Clock(payload['now'])
backend = Backend(fs, control.config, clock)
deployment = Deployment(journal, fs, backend, clock)
backend.deployment = deployment
view, operations, latest = deployment.log.read()
assert latest['active_release']['version'] == '0.5.0a2'
assert latest['kind'] == 'rollback' and latest['stage'] == 'complete'
assert len(operations) == 3
assert digest(control.ledgers) == payload['ledgers_sha256']
assert view.head == payload['deployment_head']
assert control.view.head == payload['control_head']
deployment.guard(control.config)
from vps_kit.traffic import deploy_commands
with patch.object(deploy_commands, 'linux_root'), \
     patch.object(deploy_commands, 'native_journal', return_value=journal), \
     patch.object(deploy_commands, 'context', return_value=deployment):
    audit = deploy_commands.run(SimpleNamespace(action='deployment-audit'))
assert audit['result'] == 'DEPLOYMENT_AUDIT' and audit['version'] == '0.5.0a2'
assert audit['script_writes'] == audit['api_requests'] == 0
assert backend.panel.writes == []
print(json.dumps({'old_version': vps_kit.__version__, 'package': 'PASS',
                  'history_and_guard': 'PASS', 'deployment_audit': 'PASS', 'ledger_preserved': True,
                  'original_path_rejected': True}))
'''


def fingerprints(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous-package', type=Path, required=True,
                        help='已独立取得的原版 vps-kit-0.5.0a2.zip；不自动下载')
    args = parser.parse_args()
    old_raw = args.previous_package.read_bytes()
    old_manifest, old_files = verify(old_raw, A2_SHA256)
    assert old_manifest['version'] == '0.5.0a2'
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old_root = root / 'old'
        for name, raw in old_files.items():
            if name.startswith('src/vps_kit/') and name.endswith('.py'):
                path = old_root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
        new_path = root / 'a3.zip'
        build(ROOT, new_path)
        new_raw = new_path.read_bytes()
        model = root / 'model'
        model.mkdir()
        cfg = parse_config(canonical(config_data('3.10.2')))
        cfg, fs, clock, journal, backend, deployment = harness(model, cfg)
        plan = deployment.plan('install', cfg, old_raw, A2_SHA256, TOKEN_A, 'enabled', ATTESTATIONS)
        deployment.apply(plan, old_raw, TOKEN_A)
        clock.set('2027-01-22T20:44:26Z')
        execute(journal, lambda _: backend.panel, backend.new_system, clock)
        ledgers = copy.deepcopy(Control(journal.load()).ledgers)
        assert ledgers
        original_events = journal.load().events
        original_deploy_events = deployment.log.read()[0].events
        posts = list(backend.panel.writes)
        clock.set('2027-01-23T12:00:00Z')
        plan = deployment.plan('upgrade', cfg, new_raw, sha(new_raw), None, 'paused', ATTESTATIONS)
        deployment.apply(plan, new_raw)
        result = deployment.rollback(plan['operation_id'], cfg.data['task_id'])
        assert result['result'] == 'FILES_RESTORED_AUTOMATION_PAUSED'
        assert result['incompatible_format_file_exit'] is False
        assert backend.new_system.timer == 'paused'
        assert backend.panel.writes == posts
        assert Control(journal.load()).ledgers == ledgers
        assert journal.load().events[:len(original_events)] == original_events
        assert deployment.log.read()[0].events[:len(original_deploy_events)] == original_deploy_events
        from vps_kit.traffic.contracts import digest
        payload = {'old_src': str(old_root / 'src'), 'test_support': str(ROOT / 'tests/traffic'),
                   'new_package': str(new_path), 'model': str(model), 'modes': fs.modes,
                   'now': '2027-01-23T12:00:00Z', 'ledgers_sha256': digest(ledgers),
                   'deployment_head': deployment.log.read()[0].head, 'control_head': journal.load().head}
        before = fingerprints(model)
        child = subprocess.run([sys.executable, '-I', '-B', '-c', OLD_READER],
                               input=json.dumps(payload), text=True, capture_output=True, timeout=60)
        if child.returncode:
            raise AssertionError(child.stderr)
        assert fingerprints(model) == before
        print(json.dumps({'result': 'RELEASE_ROLLBACK_PASS', 'previous_sha256': A2_SHA256,
                          'old_reader': json.loads(child.stdout), 'timer': 'paused',
                          'history_readonly': True, 'evidence': 'LOCAL_MODEL_WITH_REAL_A2_CODE',
                          'linux_systemd_panel_integration': 'NOT_TESTED'}))


if __name__ == '__main__':
    main()
