"""E1-2 反例回归：临时文件及注入系统替身，绝不调用真实 systemd/API。"""
from contextlib import contextmanager
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from p2_support import ATTESTATIONS, ModelFiles, ModelLock, harness as execution_harness, install
from p3_support import TOKEN_B, Files, package, harness as monthly_harness, legacy_config, target, seed_legacy
from vmrack_support import config, harness, seed
from vps_kit.cli import main
from vps_kit.traffic.calendar import preview, wakeups
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import ContractError, canonical, utc
from vps_kit.traffic.control import Control
from vps_kit.traffic.deploy_files import DeploymentFiles
from vps_kit.traffic.deploy_native import NativeDeploymentBackend
from vps_kit.traffic.execution import execute
from vps_kit.traffic.journal import HELD_LOCKS, Journal, check_lock_identities
from vps_kit.traffic.native import NativeRuntime, NativeSystem, UNIT_DIR, calendar_check
from vps_kit.traffic.package import sha
from vps_kit.traffic.panel_http import ProcessResult
from vps_kit.traffic.sources import VMISS, VMRACK, paths
from vps_kit.traffic.units import render, stem


@contextmanager
def held_file(root):
    """建模已持有 fd 的身份；只真实验证文件替换，不声称测试了 flock。"""
    path = root / 'held.lock'
    path.write_bytes(b'original')
    original = path.stat()
    with patch.dict(HELD_LOCKS, {path: (original.st_dev, original.st_ino)}, clear=True):
        def replace():
            new = root / 'replaced.lock'
            new.write_bytes(b'replacement')
            new.replace(path)
            assert path.stat().st_ino != original.st_ino
        yield path, replace


class LockReviewTests(unittest.TestCase):
    def test_native_runtime_rechecks_lock_after_slow_guard_all_sources(self):
        for kind in (None, VMISS, VMRACK):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                cfg = config()
                runtime = object.__new__(NativeRuntime)
                runtime.task = cfg.data['task_id']; runtime.invocation_id = 'a' * 32
                runtime.execute = lambda *_: None
                with held_file(Path(tmp)) as (_, replace), \
                     patch('vps_kit.traffic.native.live_guard'), \
                     patch.object(runtime, '_foreign'), patch.object(runtime, 'verify'), \
                     patch('vps_kit.traffic.native.program_hash', return_value='b' * 64), \
                     patch('vps_kit.traffic.native.show', return_value={
                         'InvocationID': 'a' * 32, 'MainPID': str(os.getpid()), 'ActiveState': 'active'}):
                    runtime.legacy_kind = kind or VMISS
                    runtime.legacy_check = (lambda _: None) if kind else None
                    runtime.guard(cfg, SimpleNamespace(owner={'program_sha256': 'b' * 64}))
                    runtime.verify.side_effect = lambda _: replace()
                    with self.assertRaisesRegex(ContractError, 'LOCK_REPLACED'):
                        runtime.guard(cfg, SimpleNamespace(owner={'program_sha256': 'b' * 64}))

    def test_pending_then_lock_replaced_or_deleted_stops_next_post(self):
        for mutation in ('replace', 'delete'):
            for which in ('new', 'old'):
                with self.subTest(mutation=mutation, which=which), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    cfg, clock, journal, panel, system, manager = execution_harness(root)
                    install((cfg, clock, journal, panel, system, manager))
                    clock.set(cfg.data['schedule']['first_boundary_utc'])
                    with held_file(root) as (lock, replace):
                        # 两把已持有锁都在同一检查集合；不依赖来源的 special-case。
                        other = root / (which + '.lock'); other.write_bytes(b'second')
                        stat = other.stat(); HELD_LOCKS[other] = (stat.st_dev, stat.st_ino)
                        injected = []
                        def fault(stage):
                            state = Control(journal.load()).ledgers.get(1)
                            if state and state['steps'][0]['status'] == 'pending' and not injected:
                                injected.append(True)
                                if which == 'old':
                                    if mutation == 'delete': other.unlink()
                                    else:
                                        replacement = root / 'other.tmp'; replacement.write_bytes(b'new'); replacement.replace(other)
                                elif mutation == 'delete': lock.unlink()
                                else: replace()
                        system.hook = fault
                        with self.assertRaisesRegex(ContractError, 'LOCK_REPLACED'):
                            execute(journal, lambda _: panel, system, clock)
                        self.assertTrue(injected)
                        self.assertFalse(panel.writes)
                        self.assertEqual(Control(journal.load()).unresolved(), [1])
                    # 身份错误解除后 pending 仍不可重放。
                    system.hook = lambda *_: None
                    with self.assertRaisesRegex(ContractError, 'HISTORY_UNRESOLVED'):
                        execute(journal, lambda _: panel, system, clock)
                    self.assertFalse(panel.writes)

    def test_deleted_identity_blocks_commands_and_file_mutations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fs = DeploymentFiles(root, files=Files(root))
            fs.write('/kept', b'old')
            calls = []
            backend = NativeDeploymentBackend(fs, execute=lambda *a, **k: calls.append(a))
            system = NativeSystem(execute=lambda *a, **k: calls.append(a))
            journal = Journal(root/'journal', root/'model.lock', files=ModelFiles(root), lock=ModelLock())
            expected = fs.image('/kept')
            with held_file(root) as (lock, _):
                lock.unlink()
                for action in (check_lock_identities, lambda: system.pause(config()), backend.reload,
                               lambda: fs.write('/kept', b'changed'), lambda: fs.remove('/kept', expected),
                               lambda: fs.mkdir('/absent'), lambda: fs.link('/usr/local/lib/vps-kit/current', 'releases/0.4.0a2')):
                    with self.subTest(action=action), self.assertRaisesRegex(ContractError, 'LOCK_REPLACED'): action()
                with journal.exclusive(), self.assertRaisesRegex(ContractError, 'LOCK_REPLACED'):
                    journal.append('ledger', {}, None)
            self.assertFalse(calls)
            self.assertEqual((root/'kept').read_bytes(), b'old')
            self.assertFalse((root/'journal').exists() or (root/'absent').exists())

    def test_lock_replacement_before_atomic_publish_keeps_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fs = DeploymentFiles(root, files=ModelFiles(root)); fs.write('/kept', b'old')
            with held_file(root) as (_, replace):
                fs.fault = lambda path, stage: replace() if stage == 'before_replace' else None
                with self.assertRaisesRegex(ContractError, 'LOCK_REPLACED'): fs.write('/kept', b'changed')
            self.assertEqual((root/'kept').read_bytes(), b'old')
            journal = Journal(root/'journal', root/'model.lock', files=ModelFiles(root), lock=ModelLock())
            with held_file(root) as (_, replace):
                journal.fault = lambda name, stage: replace() if stage == 'before_replace' else None
                with journal.exclusive(), self.assertRaisesRegex(ContractError, 'LOCK_REPLACED'):
                    journal.append('ledger', {}, None)
            self.assertFalse((journal.root/'head.json').exists())
            with self.assertRaises(ContractError): journal.load()


class DailyResampleReviewTests(unittest.TestCase):
    def run_check(self, timer_change=None, service_change=None, bytes_change=False, second_cross=False):
        cfg = config(); name = stem(cfg.data['task_id'])
        timer = {'LoadState':'loaded', 'FragmentPath':str(UNIT_DIR/(name+'.timer')), 'DropInPaths':'',
                 'NeedDaemonReload':'no', 'Persistent':'no', 'AccuracyUSec':'1s', 'RandomizedDelayUSec':'0',
                 'Triggers':name+'.service', 'ActiveState':'active', 'SubState':'waiting', 'UnitFileState':'enabled',
                 'Job':'', 'NextElapseUSecRealtime':'Mon 2026-11-09 01:50:00 UTC'}
        service = {'LoadState':'loaded', 'FragmentPath':str(UNIT_DIR/(name+'.service')), 'DropInPaths':'',
                   'NeedDaemonReload':'no', 'Type':'oneshot', 'Restart':'no', 'RemainAfterExit':'no',
                   'TimeoutStartUSec':'5min', 'RuntimeDirectoryPreserve':'yes', 'Job':''}
        later_timer = {**timer, 'NextElapseUSecRealtime':'Tue 2026-11-10 01:50:00 UTC', **(timer_change or {})}
        later_service = {**service, **(service_change or {})}
        values = iter([utc(t,'TIME') for t in ('2026-11-09T01:49:59Z', '2026-11-09T01:50:01Z',
                      '2026-11-10T01:50:01Z' if second_cross else '2026-11-09T01:50:02Z')])
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None): return next(values)
        unit_bytes = render(cfg)
        changed_bytes = {**unit_bytes, name+'.timer':unit_bytes[name+'.timer'].replace('Persistent=false','Persistent=true')} if bytes_change else unit_bytes
        system = NativeSystem()
        with patch.object(system,'backup',side_effect=[unit_bytes,changed_bytes]), \
             patch('vps_kit.traffic.native.show',side_effect=[timer,service,later_timer,later_service]) as show, \
             patch('vps_kit.traffic.native.datetime',Clock), patch('vps_kit.traffic.native.calendar_check'):
            system.verify(cfg)
            self.assertEqual(show.call_count, 4)

    def test_clean_daily_crossing_has_exactly_one_resample(self): self.run_check()

    def test_resampled_timer_and_service_contract_drift_rejected(self):
        for field, value in {'LoadState':'not-found', 'FragmentPath':'/foreign', 'DropInPaths':'override.conf',
                             'NeedDaemonReload':'yes', 'Persistent':'yes', 'AccuracyUSec':'1min',
                             'RandomizedDelayUSec':'3s', 'Triggers':'other.service', 'UnitFileState':'disabled',
                             'Job':'9', 'ActiveState':'inactive', 'SubState':'running'}.items():
            with self.subTest(timer=field), self.assertRaises(ContractError): self.run_check(timer_change={field:value})
        for field, value in {'LoadState':'not-found', 'FragmentPath':'/foreign', 'DropInPaths':'override.conf',
                             'NeedDaemonReload':'yes', 'Type':'simple', 'Restart':'on-failure',
                             'RemainAfterExit':'yes', 'TimeoutStartUSec':'10min',
                             'RuntimeDirectoryPreserve':'no', 'Job':'9'}.items():
            with self.subTest(service=field), self.assertRaises(ContractError): self.run_check(service_change={field:value})

    def test_second_day_or_unit_bytes_change_rejected(self):
        with self.assertRaisesRegex(ContractError, 'UNIT_CONTENT_DRIFT'): self.run_check(bytes_change=True)
        with self.assertRaisesRegex(ContractError, 'DAILY_WAKE_CHANGED_AGAIN'): self.run_check(second_cross=True)

    def test_earliest_anchor_has_preview_cli_and_calendar_without_underflow(self):
        data = config().data
        data['schedule'].update(anchor_utc='0001-01-01T00:00:00Z', first_boundary_utc='0001-01-01T00:00:00Z', timezone='UTC')
        cfg = parse_config(canonical(data)); now = utc('2026-10-25T00:00:00Z','TIME')
        self.assertEqual(len(preview(cfg, now)['next_24']),24)
        calls = []
        def process(args, **kwargs):
            calls.append(args)
            count = int(next(x for x in args if x.startswith('--iterations=')).split('=')[1])
            base = datetime.strptime(next(x for x in args if x.startswith('--base-time=')).split('=',1)[1],'%Y-%m-%d %H:%M:%S UTC').replace(tzinfo=timezone.utc)
            dates = wakeups(cfg.data['schedule'],base,count)
            text = '\n'.join(('Next elapse: ' if n == 0 else f'Iteration #{n+1}: ')+v.strftime('%a %Y-%m-%d %H:%M:%S UTC') for n,v in enumerate(dates))
            return ProcessResult(0,text.encode())
        calendar_check(cfg,now,process); self.assertEqual(len(calls),25)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'candidate.json'; path.write_text(canonical(data),encoding='utf-8')
            from contextlib import redirect_stdout
            output = io.StringIO()
            with redirect_stdout(output): result = main(['traffic','calendar','--config',str(path)])
            self.assertEqual(result,0); self.assertEqual(json.loads(output.getvalue())['script_writes'],0)


class RollbackReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pkg = tempfile.TemporaryDirectory(); cls.raw = package(Path(cls.pkg.name))
        cls.upgrade = package(Path(cls.pkg.name), '0.4.1a1')

    @classmethod
    def tearDownClass(cls): cls.pkg.cleanup()

    def migrated(self, root, partial=False):
        h = harness(root); cfg,fs,clock,j,b,d = h
        e = seed(h,cron=True); p = d.plan_vmrack(cfg,self.raw,sha(self.raw),e,ATTESTATIONS)
        if partial:
            def fault(path,stage):
                if path == '/etc/systemd/system/'+paths(VMRACK)['service'] and stage == 'after_remove':
                    raise OSError('synthetic interruption before runner/cron retirement')
            fs.fault = fault
            with self.assertRaises(OSError): d.apply_vmrack(p,self.raw)
            fs.fault = lambda *_: None
        else: d.apply_vmrack(p,self.raw)
        return h,e,p

    def snapshot(self,h,p):
        cfg,fs,clock,j,b,d = h
        return ({n:fs.image(n) for n in p['before']}, j.load().head, d.log.read()[0].head,
                list(b.calls), list(b.new_system.calls))

    def test_initial_migration_source_drift_rejected_before_pause_or_restore(self):
        for change in ('config','marker','proof','new-state','new-config'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
                h,e,p = self.migrated(Path(tmp)); cfg,fs,clock,j,b,d = h; src=paths(VMRACK)
                path = {'config':src['config'], 'marker':src['state']+'/client-reset.done',
                        'proof':next(iter(e['evidence_files'])), 'new-state':src['state']+'/unknown',
                        'new-config':str(Path(src['config']).parent).replace('\\','/')+'/unknown'}[change]
                fs.write(path,b'changed')
                before = self.snapshot(h,p)
                with self.assertRaises(ContractError): d.rollback(p['operation_id'],cfg.data['task_id'])
                self.assertEqual(self.snapshot(h,p),before); self.assertFalse(b.panel.writes)

    def test_upgrade_and_credential_rollback_require_retired_source(self):
        for kind in ('upgrade','credential'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as tmp:
                h,e,p = self.migrated(Path(tmp)); cfg,fs,clock,j,b,d = h
                raw = self.upgrade if kind == 'upgrade' else None
                token = TOKEN_B if kind == 'credential' else None
                p = d.plan(kind,cfg,raw,sha(raw) if raw else None,token,'paused',ATTESTATIONS)
                d.apply(p,raw,token)
                fs.write(paths(VMRACK)['runner'],b'changed',0o700)
                before = self.snapshot(h,p)
                with self.assertRaisesRegex(ContractError,'LEGACY_RUNNER_NOT_RETIRED'): d.rollback(p['operation_id'],cfg.data['task_id'])
                self.assertEqual(self.snapshot(h,p),before); self.assertFalse(b.panel.writes)

    def test_source_rechecked_after_pause_before_formal_restore(self):
        for target in ('config','runner','cron','unit','entry'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                h,e,p = self.migrated(Path(tmp)); cfg,fs,clock,j,b,d = h
                source = paths(VMRACK)
                changed = {'config':source['config'], 'runner':source['runner'], 'cron':e['retire_cron'][0],
                           'unit':'/etc/systemd/system/'+source['service'], 'entry':'/usr/local/bin/vps-kit'}[target]
                before = self.snapshot(h,p)[:3]; pause = b.pause_for_rollback
                observed = []
                def drift(*args):
                    pause(*args); fs.write(changed,b'changed after pause')
                    observed.append(self.snapshot(h,p)[:3])
                code = 'ROLLBACK_OLD_SOURCE_DRIFT' if target == 'config' else 'ROLLBACK_UNKNOWN_FILE_CHANGE'
                with patch.object(b,'pause_for_rollback',side_effect=drift), self.assertRaisesRegex(ContractError,code):
                    d.rollback(p['operation_id'],cfg.data['task_id'])
                self.assertEqual(self.snapshot(h,p)[:3],observed[0])
                self.assertEqual(self.snapshot(h,p)[1:3],before[1:3]); self.assertFalse(b.panel.writes)

    def test_partial_old_boundary_refuses_but_fully_retired_allows(self):
        for partial in (True,False):
            with self.subTest(partial=partial),tempfile.TemporaryDirectory() as tmp:
                h,e,p = self.migrated(Path(tmp),partial); cfg,fs,clock,j,b,d = h
                clock.set('2026-11-10T01:50:00Z')
                if partial:
                    before = self.snapshot(h,p)
                    with self.assertRaisesRegex(ContractError,'MAINTENANCE_NEAR_BOUNDARY'): d.rollback(p['operation_id'],cfg.data['task_id'])
                    self.assertEqual(self.snapshot(h,p),before)
                else:
                    result = d.rollback(p['operation_id'],cfg.data['task_id'])
                    self.assertEqual(result['legacy_resume'],'NOT_AUTHORIZED')
                    self.assertEqual({n:fs.image(n) for n in p['before']},p['before'])
                self.assertFalse(b.panel.writes); self.assertEqual(b.old_timer,'paused')

    def test_runtime_uses_proof_backup_but_file_exit_requires_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            h,e,p = self.migrated(Path(tmp)); cfg,fs,clock,j,b,d = h
            fs.path(next(iter(e['evidence_files']))).unlink()
            d.guard(cfg)
            before = self.snapshot(h,p)
            with self.assertRaisesRegex(ContractError,'ROLLBACK_OLD_SOURCE_DRIFT'): d.rollback(p['operation_id'],cfg.data['task_id'])
            self.assertEqual(self.snapshot(h,p),before); self.assertFalse(b.panel.writes)

    def test_vmiss_source_state_drift_also_stops_file_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = legacy_config(); cfg = target(old)
            h = monthly_harness(Path(tmp),cfg); cfg,fs,clock,j,b,d = h
            seed_legacy(fs,old)
            p = d.plan('migrate',cfg,self.raw,sha(self.raw),None,'paused',ATTESTATIONS); d.apply(p,self.raw)
            fs.write(paths(VMISS)['state']+'/unknown',b'changed')
            before = self.snapshot(h,p)
            with self.assertRaisesRegex(ContractError,'LEGACY_STATE_SET_CHANGED'): d.rollback(p['operation_id'],cfg.data['task_id'])
            self.assertEqual(self.snapshot(h,p),before); self.assertFalse(b.panel.writes)
