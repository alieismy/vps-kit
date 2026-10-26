import copy
from contextlib import contextmanager
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from p2_support import ATTESTATIONS
from p3_support import (TOKEN_A, TOKEN_B, harness, package, legacy_config, target, seed_legacy, old_state, provenance)
from support import config_data
from vps_kit.cli import main
from vps_kit import __version__
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import ContractError, canonical, digest, json_load
from vps_kit.traffic.control import Control
from vps_kit.traffic.deploy_files import CURRENT, ENTRY, TOKEN
from vps_kit.traffic.execution import execute
from vps_kit.traffic.legacy import CONFIG, STATE, RUNNER, R2_HASH, V212_HASH, SERVICE, TIMER, RETIRED, history
from vps_kit.traffic.native import ordered_locks, OLD_LOCK
from vps_kit.traffic.package import sha, verify


class DeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packages = tempfile.TemporaryDirectory()
        cls.raw = package(Path(cls.packages.name))
        cls.new_raw = package(Path(cls.packages.name), '0.3.1a1')

    @classmethod
    def tearDownClass(cls):
        cls.packages.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def plan(self, h, kind='install', token=TOKEN_A, raw=None, activation='paused'):
        cfg, fs, clock, journal, b, d = h
        raw = self.raw if raw is None and kind != 'credential' else raw
        return d.plan(kind, cfg, raw, sha(raw) if raw else None, token, activation, ATTESTATIONS)

    def install(self, h, activation='paused'):
        p = self.plan(h, activation=activation)
        h[-1].apply(p, self.raw, TOKEN_A)
        return p

    def test_install_both_versions_and_no_write_api(self):
        for v in ('3.7.0', '3.10.2'):
            with self.subTest(version=v), tempfile.TemporaryDirectory() as tmp:
                h = harness(Path(tmp), parse_config(canonical(config_data(v))))
                cfg, fs, clock, journal, b, d = h
                p = self.plan(h)
                self.assertFalse(journal.root.exists())
                self.assertFalse(d.log.journal.root.exists())
                result = d.apply(p, self.raw, TOKEN_A)
                self.assertEqual(result['result'], 'DEPLOYMENT_COMPLETE')
                self.assertEqual(b.new_system.timer, 'paused')
                self.assertEqual(Control(journal.load()).next_authorization()[0], 1)
                self.assertIsNone(d.guard(cfg))
                self.assertFalse(b.panel.writes)
                with self.assertRaises(ContractError):
                    d.apply(p, self.raw, TOKEN_A)
                self.assertNotIn(TOKEN_A, canonical(p))

    def test_install_enabled_is_timer_only_and_preserves_first_boundary(self):
        h = harness(self.root)
        self.install(h, 'enabled')
        cfg, fs, clock, journal, b, d = h
        self.assertEqual(b.new_system.timer, 'waiting')
        self.assertEqual(b.new_system.calls, ['install', 'resume'])
        self.assertEqual(Control(journal.load()).next_authorization()[1].isoformat(), '2027-01-22T20:44:25+00:00')
        self.assertFalse(b.panel.writes)

    def test_upgrade_and_rollback_preserve_completed_ledger(self):
        h = harness(self.root)
        self.install(h, 'enabled')
        cfg, fs, clock, journal, b, d = h
        clock.set('2027-01-22T20:44:26Z')
        execute(journal, lambda cfg: b.panel, b.new_system, clock)
        old_ledgers = copy.deepcopy(Control(journal.load()).ledgers)
        clock.set('2027-01-23T12:00:00Z')
        p = self.plan(h, 'upgrade', token=None, raw=self.new_raw)
        d.apply(p, self.new_raw)
        self.assertEqual(d.log.read()[2]['active_release']['version'], '0.3.1a1')
        d.rollback(p['operation_id'], cfg.data['task_id'])
        self.assertEqual(d.log.read()[2]['active_release']['version'], __version__)
        self.assertEqual(Control(journal.load()).ledgers, old_ledgers)
        self.assertEqual(b.new_system.timer, 'paused')
        d.guard(cfg)
        p = self.plan(h, 'upgrade', token=None, raw=self.new_raw)
        d.apply(p, self.new_raw)
        self.assertEqual(Control(journal.load()).ledgers, old_ledgers)

    def test_credential_rotation_and_revoked_rollback_stay_paused(self):
        h = harness(self.root)
        self.install(h, 'enabled')
        cfg, fs, clock, journal, b, d = h
        original = journal.load().head
        p = self.plan(h, 'credential', token=TOKEN_B)
        d.apply(p, credential=TOKEN_B)
        self.assertEqual(fs.read(TOKEN), (TOKEN_B+'\n').encode())
        self.assertEqual(journal.load().head, original)
        b.revoked.add(TOKEN_A)
        with self.assertRaisesRegex(ContractError, 'SYNTHETIC_TOKEN_REVOKED'):
            d.rollback(p['operation_id'], cfg.data['task_id'])
        self.assertEqual(b.new_system.timer, 'paused')
        self.assertEqual(d.log.read()[2]['stage'], 'failed')
        with self.assertRaisesRegex(ContractError, 'DEPLOY_INCOMPLETE'):
            d.guard(cfg)
        self.assertFalse(b.panel.writes)

    def test_bad_credential_preflight_never_pauses_or_writes(self):
        h = harness(self.root)
        self.install(h, 'enabled')
        cfg, fs, clock, journal, b, d = h
        b.revoked.add(TOKEN_B)
        heads = journal.load().head, d.log.read()[0].head
        with self.assertRaises(ContractError):
            self.plan(h, 'credential', token=TOKEN_B)
        self.assertEqual(heads, (journal.load().head, d.log.read()[0].head))
        self.assertEqual(b.new_system.timer, 'waiting')
        self.assertEqual(fs.read(TOKEN), (TOKEN_A+'\n').encode())

    def test_basic_core_migration_before_first_and_retirement_guard(self):
        for instance in ('Basic', 'Core'):
            with self.subTest(instance=instance), tempfile.TemporaryDirectory() as tmp:
                old = legacy_config(instance)
                h = harness(Path(tmp), target(old))
                cfg, fs, clock, journal, b, d = h
                seed_legacy(fs, old)
                p = self.plan(h, 'migrate', token=None)
                original = {n:fs.image(n) for n in p['source_images']}
                d.apply(p, self.raw)
                self.assertEqual(fs.read(RUNNER, (0o700,)), RETIRED)
                self.assertEqual(fs.image('/etc/systemd/system/'+TIMER), {'kind':'link','target':'/dev/null'})
                self.assertEqual(b.old_timer, 'paused')
                self.assertEqual(Control(journal.load()).config.data['schedule'], cfg.data['schedule'])
                self.assertEqual(Control(journal.load()).legacy_import['history']['records'], [])
                d.guard(cfg)
                fs.write(RUNNER, b'ALTERED', 0o700)
                with self.assertRaisesRegex(ContractError, 'LEGACY_RUNNER_NOT_RETIRED'):
                    d.guard(cfg)
                fs.write(RUNNER, RETIRED, 0o700)
                result = d.rollback(p['operation_id'], cfg.data['task_id'])
                self.assertEqual(result['r2_resume'], 'NOT_AUTHORIZED')
                self.assertEqual({n:fs.image(n) for n in original}, original)
                self.assertEqual(b.old_timer, 'paused')
                self.assertFalse(b.panel.writes)

    def test_migration_after_r2_completed_and_new_intent_rollback(self):
        old = legacy_config()
        h = harness(self.root, target(old))
        cfg, fs, clock, journal, b, d = h
        clock.set('2027-01-23T12:00:00Z')
        raw_state = old_state(old, cfg, '2027-01')
        seed_legacy(fs, old, {'v2/2027-01.json':raw_state})
        p = self.plan(h, 'migrate', token=None)
        d.apply(p, self.raw)
        self.assertEqual(Control(journal.load()).next_authorization()[0], 2)
        self.assertEqual(p['next_boundary_utc'], '2027-02-22T20:44:25Z')
        clock.set('2027-02-22T20:44:26Z')
        b.panel.hook = lambda event, key: (_ for _ in ()).throw(OSError('synthetic after send')) if event == 'after_send' else None
        with self.assertRaises(OSError):
            execute(journal, lambda cfg:b.panel, b.new_system, clock)
        c = Control(journal.load())
        self.assertTrue(c.unresolved())
        ledger_head = journal.load().head
        clock.set('2027-02-23T12:00:00Z')
        result = d.rollback(p['operation_id'], cfg.data['task_id'])
        self.assertTrue(result['new_intents_present'])
        self.assertEqual(journal.load().head, ledger_head)
        self.assertEqual(fs.read(STATE+'/v2/2027-01.json'), raw_state)
        self.assertEqual(b.old_timer, 'paused')

    def test_v1_prior_evidence_required_and_preserved(self):
        old, v1 = legacy_config(), legacy_config(schema=1)
        cfg = target(old)
        s = old_state(v1, cfg, '2026-12')
        states = {'v2/2026-12.json': s, 'client-reset.done':b'2026-11\n', 'inbound-reset.done':b'2026-11\n'}
        now = harness(self.root, cfg)[2].wall
        with self.assertRaisesRegex(ContractError, 'LEGACY_SOURCE_CONFIG_MISSING'):
            history(canonical(old).encode(), states, None, cfg, now)
        original_cfg = canonical(v1).encode()
        manifest = provenance(old, v1, cfg, states)
        prior = {'config':original_cfg,'manifest':canonical(manifest).encode()}
        imported = history(canonical(old).encode(), states, prior, cfg, now)
        self.assertEqual(imported['next_period_id'], 2)
        self.assertEqual(imported['records'][0]['sha256'], sha(s))
        manifest['state_before']['v2/2026-12.json'] = '0'*64
        with self.assertRaises(ContractError):
            history(canonical(old).encode(), states, {**prior,'manifest':canonical(manifest).encode()}, cfg, now)

    def test_mixed_profile_quota_boundary_and_partial_history_rejected(self):
        old = legacy_config()
        cfg = target(old)
        h = harness(self.root, cfg)
        now = h[2].wall
        for change in ('profile', 'quota', 'calendar', 'first', 'email'):
            bad = copy.deepcopy(old)
            if change == 'profile': bad['instance'] = 'Core'
            elif change == 'quota': bad['inbound_quota_bytes'] += 1
            elif change == 'calendar': bad['inbound_port'] = 8443
            elif change == 'first': bad['first_cycle'] = '2027-02'
            else: bad['clients'][0]['email'] = 'Core'
            with self.subTest(change=change), self.assertRaises(ContractError):
                history(canonical(bad).encode(), {}, None, cfg, now)
        h[2].set('2027-01-23T12:00:00Z')
        with self.assertRaisesRegex(ContractError, 'LEGACY_DUE_HISTORY_MISSING'):
            history(canonical(old).encode(), {}, None, cfg, h[2].wall)
        state = json_load(old_state(old,cfg,'2027-01'))
        state['steps']['client:Basic']['status'] = 'pending'
        with self.assertRaisesRegex(ContractError, 'LEGACY_UNRESOLVED'):
            history(canonical(old).encode(), {'v2/2027-01.json':canonical(state).encode()}, None, cfg, h[2].wall)

    def test_failure_after_mutation_blocks_replay_and_restores_files(self):
        cases = [(TOKEN,'after_replace'), (ENTRY,'after_replace'), (CURRENT,'after_sync')]
        for path, point in cases:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as tmp:
                h = harness(Path(tmp))
                cfg, fs, clock, journal, b, d = h
                p = self.plan(h)
                def fault(pth, where):
                    if (pth,where) == (path,point): raise OSError('synthetic crash')
                fs.fault = fault
                with self.assertRaises(OSError): d.apply(p,self.raw,TOKEN_A)
                fs.fault = lambda *_:None
                self.assertEqual(d.log.read()[2]['stage'], 'failed')
                with self.assertRaises(ContractError): d.guard(cfg)
                with self.assertRaises(ContractError): d.apply(p,self.raw,TOKEN_A)
                d.rollback(p['operation_id'],cfg.data['task_id'])
                self.assertTrue(all(fs.image(n)['kind']=='absent' for n in p['before']))
                self.assertFalse(b.panel.writes)

    def test_retirement_mask_midpoint_can_rollback(self):
        old = legacy_config()
        h = harness(self.root, target(old))
        cfg, fs, clock, journal, b, d = h
        seed_legacy(fs,old)
        p = self.plan(h,'migrate',token=None)
        def fault(path, point):
            if path == '/etc/systemd/system/'+SERVICE and point == 'after_remove':
                raise OSError('synthetic interrupted mask')
        fs.fault = fault
        with self.assertRaises(OSError): d.apply(p,self.raw)
        fs.fault = lambda *_:None
        d.rollback(p['operation_id'],cfg.data['task_id'])
        self.assertEqual(fs.image('/etc/systemd/system/'+SERVICE),p['before']['/etc/systemd/system/'+SERVICE])
        self.assertEqual(b.old_timer,'paused')

    def test_backup_corruption_and_manual_file_edit_block_rollback(self):
        h = harness(self.root)
        self.install(h)
        cfg,fs,clock,journal,b,d = h
        p = self.plan(h,'credential',token=TOKEN_B)
        d.apply(p,credential=TOKEN_B)
        item = d.log.read()[2]['backup'][TOKEN]
        fs.write(item['backup'],b'CORRUPT')
        with self.assertRaisesRegex(ContractError,'ROLLBACK_BACKUP_HASH'):
            d.rollback(p['operation_id'],cfg.data['task_id'])
        fs.write(TOKEN,b'MANUAL_CHANGE')
        with self.assertRaisesRegex(ContractError,'ROLLBACK_UNKNOWN_FILE_CHANGE'):
            d.rollback(p['operation_id'],cfg.data['task_id'])

    def test_dual_lock_order_contention_and_release(self):
        events = []
        @contextmanager
        def lock(path):
            events.append(('take',str(path)))
            try:
                if path == OLD_LOCK: raise ContractError('LOCK_BUSY')
                yield
            finally:
                events.append(('release',str(path)))
        with self.assertRaisesRegex(ContractError,'LOCK_BUSY'):
            with ordered_locks(Path('/new.lock'),True,lock): self.fail('must not enter')
        self.assertEqual(events,[('take',str(Path('/new.lock'))),('take',str(OLD_LOCK)),('release',str(OLD_LOCK)),('release',str(Path('/new.lock')))])

    def test_package_integrity_paths_and_cli_platform_boundary(self):
        manifest,_ = verify(self.raw,sha(self.raw))
        self.assertEqual(manifest['version'], __version__)
        with self.assertRaises(ContractError): verify(self.raw,'0'*64)
        out=io.BytesIO(self.raw)
        with zipfile.ZipFile(out,'a') as z: z.writestr('../escape.py',b'pass')
        with self.assertRaises(ContractError): verify(out.getvalue(),sha(out.getvalue()))
        out=io.StringIO()
        with patch('sys.stdout',out),patch('vps_kit.traffic.native.platform.system',return_value='Windows'):
            code=main(['traffic','deployment-audit','--local'])
        self.assertEqual(code,2)
        self.assertIn('REQUIRES_LINUX_ROOT',out.getvalue())
        self.assertNotIn(TOKEN_A,out.getvalue())

    def test_backup_failure_before_formal_write_can_cancel_without_restore(self):
        old=legacy_config()
        h=harness(self.root,target(old))
        cfg,fs,clock,journal,b,d=h
        seed_legacy(fs,old)
        p=self.plan(h,'migrate',token=None)
        def fault(path,point):
            if '/backups/' in path and point=='after_replace': raise OSError('backup interrupted')
        fs.fault=fault
        with self.assertRaises(OSError): d.apply(p,self.raw)
        fs.fault=lambda *_:None
        self.assertIsNone(d.log.read()[2]['backup'])
        self.assertEqual({n:fs.image(n) for n in p['before']},p['before'])
        d.rollback(p['operation_id'],cfg.data['task_id'])
        self.assertEqual(b.old_timer,'paused')
        self.assertFalse(b.panel.writes)

    def test_release_and_system_fault_injection(self):
        cases=('release_write','reload','verify','new_unit_write','control_commit','deploy_commit')
        for case in cases:
            with self.subTest(case=case),tempfile.TemporaryDirectory() as tmp:
                h=harness(Path(tmp))
                cfg,fs,clock,journal,b,d=h
                p=self.plan(h)
                def fail(*_): raise OSError('synthetic failure')
                if case=='release_write':
                    fs.fault=lambda path,point: fail() if '/releases/' in path and point=='after_replace' else None
                elif case=='new_unit_write':
                    fs.fault=lambda path,point: fail() if path.endswith('.service') and point=='after_sync' else None
                elif case=='reload': b.hook=lambda event: fail() if event=='reload' else None
                elif case=='verify': b.new_system.hook=lambda event: fail() if event=='verify' else None
                elif case=='control_commit': journal.fault=fail
                else: d.log.journal.fault=fail
                with self.assertRaises(OSError): d.apply(p,self.raw,TOKEN_A)
                fs.fault=lambda *_:None
                b.hook=b.new_system.hook=lambda *_:None
                journal.fault=d.log.journal.fault=lambda *_:None
                with self.assertRaises(ContractError): d.guard(cfg)
                self.assertFalse(b.panel.writes)
                self.assertNotEqual(b.new_system.timer,'waiting')

    def test_full_v1_provenance_backup_readable_without_original_root_proof(self):
        old,v1=legacy_config(),legacy_config(schema=1)
        h=harness(self.root,target(old))
        cfg,fs,clock,journal,b,d=h
        state=old_state(v1,cfg,'2026-12')
        seed_legacy(fs,old,{'v2/2026-12.json':state})
        original=canonical(v1).encode()
        manifest = provenance(old, v1, cfg, {'v2/2026-12.json': state})
        paths={'config':'/root/prior/config.json','manifest':'/root/prior/manifest.json'}
        fs.write(paths['config'],original)
        fs.write(paths['manifest'],canonical(manifest).encode())
        b.prior=paths
        p=self.plan(h,'migrate',token=None)
        d.apply(p,self.raw)
        original_read=fs.read
        def read(path,modes=(0o600,)):
            if path.startswith('/root/'): raise PermissionError('MODEL ProtectHome=true')
            return original_read(path,modes)
        with patch.object(fs,'read',read): d.guard(cfg)
        self.assertEqual(Control(journal.load()).next_authorization()[0],2)

    def test_new_legacy_state_or_unknown_runner_blocks(self):
        old=legacy_config()
        h=harness(self.root,target(old))
        cfg,fs,clock,journal,b,d=h
        seed_legacy(fs,old)
        collect=b.collect_r2
        def unknown(cfg): return {**collect(cfg),'runner_sha256':'0'*64}
        with patch.object(b,'collect_r2',unknown),self.assertRaisesRegex(ContractError,'MIGRATION_UNKNOWN_RUNNER'):
            self.plan(h,'migrate',token=None)
        p=self.plan(h,'migrate',token=None)
        d.apply(p,self.raw)
        fs.write(STATE+'/client-reset.done',b'2026-12')
        with self.assertRaisesRegex(ContractError,'LEGACY_STATE_SET_CHANGED'): d.guard(cfg)

    def test_window_drift_and_paused_activation_are_rejected(self):
        h=harness(self.root)
        cfg,fs,clock,journal,b,d=h
        p=self.plan(h)
        clock.set('2027-01-22T20:40:00Z')
        with self.assertRaisesRegex(ContractError,'MAINTENANCE_NEAR_BOUNDARY'): d.apply(p,self.raw,TOKEN_A)
        self.assertFalse(d.log.journal.root.exists())
        clock.set('2027-01-20T12:00:00Z')
        self.install(h)
        with self.assertRaisesRegex(ContractError,'DEPLOY_PRESERVE_PAUSED'):
            self.plan(h,'upgrade',token=None,raw=self.new_raw,activation='enabled')

    def test_migrated_multi_client_recovery_and_hold_at_natural_boundary(self):
        old=legacy_config()
        old['clients'].append({'email':'held','quota_bytes':10*1024**3,'disabled_policy':'hold'})
        data=target(legacy_config()).data
        data['clients'].append({'record_id':12,'uuid':'00000000-0000-4000-8000-000000000012',
                                'email':'held','quota_bytes':10*1024**3,'policy':'hold'})
        cfg=parse_config(canonical(data))
        h=harness(self.root,cfg)
        cfg,fs,clock,journal,b,d=h
        seed_legacy(fs,old)
        inbound=b.panel.responses['/panel/api/inbounds/list']['obj'][0]
        inbound.update(enable=False,up=cfg.data['inbound']['quota_bytes'],down=0)
        for c in cfg.data['clients']:
            e=next(e for e in inbound['settings']['clients'] if e['email']==c['email'])
            e['enable']=False
            b.panel.responses['/panel/api/clients/get/'+c['email']]['obj']['client']['enable']=False
            b.panel.responses['/panel/api/clients/traffic/'+c['email']]['obj'].update(enable=False,up=c['quota_bytes'],down=0)
        held=copy.deepcopy(b.panel.responses['/panel/api/clients/traffic/held'])
        p=self.plan(h,'migrate',token=None)
        d.apply(p,self.raw)
        self.assertFalse(b.panel.writes)
        clock.set('2027-01-22T20:44:26Z')
        execute(journal,lambda cfg:b.panel,b.new_system,clock)
        self.assertEqual(b.panel.writes,['client:11','inbound','enable'])
        self.assertEqual(b.panel.responses['/panel/api/clients/traffic/held'],held)
        count=len(b.panel.writes)
        execute(journal,lambda cfg:b.panel,b.new_system,clock)
        self.assertEqual(len(b.panel.writes),count)

    def test_deployment_audit_does_not_write_or_call_api(self):
        h=harness(self.root)
        self.install(h)
        cfg,fs,clock,journal,b,d=h
        snapshot=lambda: {p.relative_to(self.root).as_posix():sha(p.read_bytes()) for p in self.root.rglob('*') if p.is_file()}
        before=snapshot()
        reads=len(b.panel.reads)
        with patch('vps_kit.traffic.deploy_commands.linux_root'),patch('vps_kit.traffic.deploy_commands.native_journal',return_value=journal),patch('vps_kit.traffic.deploy_commands.context',return_value=d),patch('sys.stdout',new_callable=io.StringIO) as output:
            self.assertEqual(main(['traffic','deployment-audit','--local']),0)
        self.assertEqual(json.loads(output.getvalue())['script_writes'],0)
        self.assertEqual(snapshot(),before)
        self.assertEqual(len(b.panel.reads),reads)
        self.assertFalse(b.panel.writes)

    def test_malformed_package_and_hidden_input_fail_without_traceback(self):
        path=self.root/'invalid.zip'
        path.write_bytes(b'not a zip')
        with patch('sys.stdout',new_callable=io.StringIO) as output:
            code=main(['traffic','package-verify','--package',str(path),'--expect-sha256',sha(path.read_bytes())])
        self.assertEqual(code,2)
        self.assertEqual(json.loads(output.getvalue())['code'],'PACKAGE_FORMAT')
        with patch('vps_kit.traffic.deploy_commands.linux_root'),patch('sys.stdin.isatty',return_value=False),patch('sys.stdout',new_callable=io.StringIO) as output:
            code=main(['traffic','credential-input','--local','--save',str(self.root/'not-written')])
        self.assertEqual(code,2)
        self.assertFalse((self.root/'not-written').exists())

    def test_migrated_upgrade_and_credential_reuse_original_retirement_evidence(self):
        old=legacy_config()
        h=harness(self.root,target(old))
        cfg,fs,clock,journal,b,d=h
        seed_legacy(fs,old)
        p=self.plan(h,'migrate',token=None)
        d.apply(p,self.raw)
        migration=copy.deepcopy(d.log.read()[2]['migration'])
        b.migration=None  # 模拟下一 CLI 进程，不能依赖上次内存中的迁移对象。
        p=self.plan(h,'upgrade',token=None,raw=self.new_raw)
        d.apply(p,self.new_raw)
        self.assertEqual(d.log.read()[2]['migration'],migration)
        p=self.plan(h,'credential',token=TOKEN_B)
        d.apply(p,credential=TOKEN_B)
        self.assertEqual(d.log.read()[2]['migration'],migration)
        self.assertEqual(fs.read(TOKEN),(TOKEN_B+'\n').encode())
        self.assertEqual(fs.read(RUNNER,(0o700,)),RETIRED)
        d.guard(cfg)

    def test_native_rollback_commands_are_timer_only_and_reject_foreign_fragment(self):
        from subprocess import CompletedProcess
        from vps_kit.traffic.deploy_native import NativeDeploymentBackend
        cfg,fs,*_=harness(self.root)
        calls=[]
        foreign=False
        def process(args,limit=None):
            calls.append(args)
            if args[1]=='show':
                name=args[2]
                data={'LoadState':'loaded','ActiveState':'inactive','SubState':'dead','MainPID':'0',
                      'Job':'','DropInPaths':'','FragmentPath':('/run/foreign/' if foreign else '/etc/systemd/system/')+name}
                output=''.join(k+'='+v+'\n' for k,v in data.items()).encode()
            else:
                self.assertEqual(args[1:3],['disable','--now'])
                self.assertTrue(args[-1].endswith('.timer'))
                output=b''
            return CompletedProcess(args,0,output,b'')
        b=NativeDeploymentBackend(fs,execute=process)
        with patch('vps_kit.traffic.deploy_native.binding_guard'):
            b.pause_for_rollback(cfg,False)
            self.assertEqual(sum(c[1]=='disable' for c in calls),1)
            calls.clear()
            foreign=True
            with self.assertRaisesRegex(ContractError,'ROLLBACK_UNIT_ORIGIN'): b.pause_for_rollback(cfg,False)
            self.assertTrue(all(c[1]=='show' for c in calls))

