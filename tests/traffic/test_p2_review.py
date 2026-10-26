"""P2 复查回归：事务边界、错误输入和故障后只读证据。所有外部依赖均为替身。"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from p2_support import ATTESTATIONS, harness, install
from vps_kit.traffic.commands import audit, run
from vps_kit.traffic.contracts import ContractError
from vps_kit.traffic.control import Control, commit
from vps_kit.traffic.execution import execute


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.h = harness(Path(self.temp.name))

    def unresolved(self):
        cfg, clock, journal, panel, system, manager = self.h
        install(self.h)
        clock.set(cfg.data['schedule']['first_boundary_utc'])
        def uncertain(stage, key):
            if stage == 'before_send':
                raise ContractError('SYNTHETIC_UNCERTAIN')
        panel.hook = uncertain
        with self.assertRaises(ContractError):
            execute(journal, lambda c: panel, system, clock)
        clock.set('2027-01-23T12:00:00Z')
        manager.pause()
        panel.hook = lambda *_: None
        control = Control(journal.load())
        return {'resolution_id': '00000000-0000-4000-8000-000000000088',
                'source_ledger_sha256': control.ledger_hashes[1], 'period_id': 1,
                'actor': 'synthetic-reviewer', 'evidence_sha256': 'f' * 64,
                'late_commit_closed': True, 'billing_aligned': True,
                'not_before_boundary_utc': '2027-02-22T20:44:25Z', 'decision': 'future-only'}

    def test_invalid_resolution_keeps_history_available_for_corrected_evidence(self):
        evidence = self.unresolved()
        journal, manager = self.h[2], self.h[5]
        before = journal.load().head
        for value in ({}, {**evidence, 'source_ledger_sha256': '0' * 64},
                      {**evidence, 'late_commit_closed': False},
                      {**evidence, 'not_before_boundary_utc': '2027-02-23T20:44:25Z'}):
            with self.subTest(value=value):
                with self.assertRaises(ContractError):
                    manager.resolve_future(value)
                self.assertEqual(before, journal.load().head)
        manager.resolve_future(evidence)
        self.assertEqual(Control(journal.load()).next_authorization()[0], 2)
        self.assertEqual(self.h[4].timer, 'paused')

    def test_pre_install_validation_failure_retains_prepared_cancel_path(self):
        cfg, clock, journal, panel, system, manager = self.h
        plan = manager.plan(cfg, 'paused', ATTESTATIONS)
        manager.prepare(plan)
        before = journal.load().head
        panel.responses['/panel/api/inbounds/list']['obj'][0]['total'] += 1
        with self.assertRaises(ContractError):
            manager.apply(plan)
        self.assertEqual(before, journal.load().head)
        self.assertEqual(Control(journal.load()).maintenance['stage'], 'prepared')
        manager.cancel(plan)
        self.assertFalse(system.calls or panel.writes)

    def test_apply_activation_cannot_override_a_concurrent_explicit_pause(self):
        cfg, clock, journal, panel, system, manager = self.h
        plan = manager.plan(cfg, 'enabled', ATTESTATIONS)
        manager.prepare(plan)
        original = journal.exclusive
        intervention = []
        @contextmanager
        def competing_operator():
            with original():
                yield
            if not intervention:
                intervention.append(True)
                manager.pause()  # 模拟共享锁释放后，另一操作者立即暂停。
        with patch.object(journal, 'exclusive', competing_operator):
            manager.apply(plan)
        self.assertEqual(system.timer, 'paused')
        self.assertEqual(system.calls[-1], 'pause')
        self.assertFalse(panel.writes)

    def test_slow_final_verify_cannot_enable_inside_boundary_guard(self):
        cfg, clock, journal, panel, system, manager = self.h
        install(self.h, 'paused')
        original = system.verify
        def delayed_verify(config):
            original(config)
            if panel.reads and Control(journal.load()).maintenance['kind'] == 'resume':
                clock.set('2027-01-22T20:43:25Z')
        with patch.object(system, 'verify', delayed_verify):
            with self.assertRaisesRegex(ContractError, 'BOUNDARY'):
                manager.resume()
        self.assertNotIn('resume', system.calls)
        self.assertEqual(system.timer, 'paused')

    def test_resume_read_failure_does_not_commit_skip_or_lock_out_correction(self):
        cfg, clock, journal, panel, system, manager = self.h
        install(self.h, 'paused')
        clock.set('2027-01-23T12:00:00Z')
        before = journal.load().head
        def unavailable(stage, key):
            raise ContractError('SYNTHETIC_GET_FAILED')
        panel.hook = unavailable
        with self.assertRaisesRegex(ContractError, 'GET_FAILED'):
            manager.resume('2027-02-22T20:44:25Z')
        self.assertEqual(before, journal.load().head)
        panel.hook = lambda *_: None
        manager.resume('2027-02-22T20:44:25Z')
        self.assertFalse(panel.writes)

    def test_partial_first_install_is_auditable_without_owner_or_transition(self):
        cfg, clock, journal, panel, system, manager = self.h
        plan = manager.plan(cfg, 'paused', ATTESTATIONS)
        with patch.object(system, 'backup', side_effect=ContractError('SYNTHETIC_BACKUP_FAILED')):
            with self.assertRaises(ContractError):
                manager.prepare(plan)
        before = journal.load().head
        report = audit(Control(journal.load()))
        self.assertEqual(report['maintenance'], 'failed')
        self.assertEqual(report['task_id'], cfg.data['task_id'])
        self.assertIsNone(report['next_authorized_boundary_utc'])
        with patch('vps_kit.traffic.commands.linux_root'), \
             patch('vps_kit.traffic.commands.native_journal', return_value=journal), \
             patch('vps_kit.traffic.commands.NativeSystem') as native, \
             patch('vps_kit.traffic.commands.token_file', side_effect=AssertionError('no credentials')):
            result = run(SimpleNamespace(action='audit', task=cfg.data['task_id']))
            self.assertEqual(result['system']['state'], 'NOT_QUERIED_NO_REGISTERED_CONFIG')
            native.return_value.inspect.assert_not_called()
        self.assertEqual(before, journal.load().head)

    def test_postponed_first_boundary_skips_forward_wakeups_then_runs_once(self):
        from vps_kit.traffic.contracts import utc
        data = self.h[0].data
        data['schedule']['first_boundary_utc'] = '2027-05-22T19:44:25Z'
        h = harness(Path(self.temp.name), data=data)
        cfg, clock, journal, panel, system, manager = h
        install(h)
        before = journal.load().head
        for when in ('2027-02-22T20:44:25Z', '2027-03-22T19:44:25Z', '2027-04-22T19:44:25Z'):
            clock.set(when)
            self.assertEqual(execute(journal, lambda c: panel, system, clock)['result'], 'BEFORE_FIRST_BOUNDARY')
            self.assertEqual(before, journal.load().head)
            self.assertFalse(panel.reads or panel.writes)
        clock.set(data['schedule']['first_boundary_utc'])
        self.assertEqual(execute(journal, lambda c: panel, system, clock)['post_requests'], 3)
        self.assertEqual(Control(journal.load()).next_authorization(), (2, utc('2027-06-22T19:44:25Z', 'TEST')))

    def test_calendar_switch_preserves_old_period_and_delays_first_new_reset(self):
        from vps_kit.traffic.config import parse_config
        from vps_kit.traffic.contracts import canonical
        cfg, clock, journal, panel, system, manager = self.h
        install(self.h)
        clock.set(cfg.data['schedule']['first_boundary_utc'])
        execute(journal, lambda c: panel, system, clock)
        old_hash = Control(journal.load()).ledger_hashes[1]
        clock.set('2027-01-23T12:00:00Z')
        data = cfg.data
        data['revision'] = 2
        data['schedule'].update(day=1, first_boundary_utc='2027-03-01T20:44:25Z')
        new = parse_config(canonical(data))
        plan = manager.plan(new, 'enabled', ATTESTATIONS)
        manager.prepare(plan)
        panel.align(new)
        manager.apply(plan)
        count = len(panel.writes)
        panel.reads.clear()
        before = journal.load().head
        for when in ('2027-02-01T20:44:25Z', '2027-02-22T20:44:25Z'):
            clock.set(when)
            self.assertEqual(execute(journal, lambda c: panel, system, clock)['result'], 'BEFORE_FIRST_BOUNDARY')
            self.assertEqual(before, journal.load().head)
            self.assertFalse(panel.reads)
        clock.set('2027-03-01T20:44:25Z')
        self.assertEqual(execute(journal, lambda c: panel, system, clock)['period_id'], 2)
        self.assertEqual(len(panel.writes) - count, 3)
        self.assertEqual(Control(journal.load()).ledger_hashes[1], old_hash)
        self.assertEqual(execute(journal, lambda c: panel, system, clock)['post_requests'], 0)

    def test_control_rejects_skipping_install_and_reusing_operation_identity(self):
        cfg, clock, journal, panel, system, manager = self.h
        plan = manager.plan(cfg, 'paused', ATTESTATIONS)
        manager.prepare(plan)
        control = Control(journal.load())
        with journal.exclusive():
            with self.assertRaises(ContractError):
                commit(journal, 'maintenance', {**control.maintenance, 'stage': 'complete'}, control.view.head)
        manager.cancel(plan)
        control = Control(journal.load())
        with journal.exclusive():
            with self.assertRaises(ContractError):
                commit(journal, 'maintenance', {**control.maintenance, 'stage': 'preparing'}, control.view.head)

    def test_control_rejects_moving_active_registry_back_to_old_revision(self):
        from vps_kit.traffic.config import parse_config
        from vps_kit.traffic.contracts import canonical
        cfg, clock, journal, panel, system, manager = self.h
        install(self.h, 'paused')
        data = cfg.data
        data['revision'] = 2
        new = parse_config(canonical(data))
        plan = manager.plan(new, 'paused', ATTESTATIONS)
        manager.prepare(plan)
        panel.align(new)
        manager.apply(plan)
        control = Control(journal.load())
        manifest = deepcopy(control.maintenance)
        manifest.update(kind='pause', stage='preparing', operation_id='00000000-0000-4000-8000-000000000099',
                        plan_sha256=None, base_head=control.view.head, backup=None)
        with journal.exclusive():
            control = commit(journal, 'maintenance', manifest, control.view.head)
            with self.assertRaises(ContractError):
                commit(journal, 'registry', {**control.owner, 'active_revision': 1}, control.view.head)
