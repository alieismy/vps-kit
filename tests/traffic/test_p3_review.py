"""P3 复查反例：真实临时文件 + 合成面板；不访问网络或宿主 systemd。"""
import copy
import getpass
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from p2_support import ATTESTATIONS, ModelFiles
from p3_support import TOKEN_A, TOKEN_B, harness, package, legacy_config, target, old_state, provenance, seed_legacy
from vps_kit import __version__
from vps_kit.cli import main
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import ContractError, canonical, digest
from vps_kit.traffic.control import Control
from vps_kit.traffic.deploy_files import CURRENT, LIB, TOKEN, DeploymentFiles
from vps_kit.traffic.legacy import R2_HASH, V212_HASH, history
from vps_kit.traffic.maintenance import Maintenance
from vps_kit.traffic.execution import execute
from vps_kit.traffic.package import sha, verify, inspect_manifest
from vps_kit.traffic.native import candidate_path, save_plan


class ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packages = tempfile.TemporaryDirectory()
        cls.raw = package(Path(cls.packages.name))
        cls.upgrade = package(Path(cls.packages.name), '0.3.1a1')

    @classmethod
    def tearDownClass(cls):
        cls.packages.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def installed(self):
        h = harness(self.root)
        cfg, fs, clock, journal, b, d = h
        p = d.plan('install', cfg, self.raw, sha(self.raw), TOKEN_A, 'enabled', ATTESTATIONS)
        d.apply(p, self.raw, TOKEN_A)
        return h

    def test_rollback_rejects_damaged_old_release_before_any_mutation(self):
        cfg, fs, clock, journal, b, d = self.installed()
        p = d.plan('upgrade', cfg, self.upgrade, sha(self.upgrade), None, 'enabled', ATTESTATIONS)
        d.apply(p, self.upgrade)
        fs.write(LIB + '/releases/' + __version__ + '/src/vps_kit/cli.py', b'raise SystemExit(99)\n', 0o644)
        before = (fs.image(CURRENT), journal.load().head, d.log.read()[0].head, list(b.new_system.calls))
        with self.assertRaisesRegex(ContractError, 'RELEASE_FILE_DRIFT'):
            d.rollback(p['operation_id'], cfg.data['task_id'])
        self.assertEqual(before, (fs.image(CURRENT), journal.load().head, d.log.read()[0].head, b.new_system.calls))
        self.assertEqual(b.new_system.timer, 'waiting')

    def test_rollback_after_p2_config_change_stops_before_pause(self):
        cfg, fs, clock, journal, b, d = self.installed()
        p = d.plan('credential', cfg, None, None, TOKEN_B, 'enabled', ATTESTATIONS)
        d.apply(p, credential=TOKEN_B)
        data = cfg.data
        data['revision'] = 2
        data['clients'][0]['quota_bytes'] //= 2
        changed = parse_config(canonical(data))
        m = Maintenance(journal, b.new_system, lambda c: b.panel, clock)
        plan = m.plan(changed, 'enabled', ATTESTATIONS)
        m.prepare(plan)
        b.panel.align(changed)
        m.apply(plan)
        before = (fs.image(TOKEN), journal.load().head, d.log.read()[0].head, list(b.new_system.calls))
        with self.assertRaisesRegex(ContractError, 'ROLLBACK_CONFIG_CHANGED'):
            d.rollback(p['operation_id'], cfg.data['task_id'])
        self.assertEqual(before, (fs.image(TOKEN), journal.load().head, d.log.read()[0].head, b.new_system.calls))
        self.assertEqual(Control(journal.load()).config.fingerprint, changed.fingerprint)

    def test_mixed_history_rejects_conflicting_original_identity_and_deleted_marker(self):
        old, v1 = legacy_config(), legacy_config(schema=1)
        cfg = target(old)
        now = harness(self.root, cfg)[2].wall
        states = {'v2/2026-12.json': old_state(v1, cfg, '2026-12'),
                  'client-reset.done': b'2026-11\n', 'inbound-reset.done': b'2026-11\n'}
        manifest = provenance(old, v1, cfg, states)
        prior = {'config': canonical(v1).encode(), 'manifest': canonical(manifest).encode()}
        self.assertEqual(history(canonical(old).encode(), states, prior, cfg, now)['next_period_id'], 2)
        for change in ('inbound', 'client', 'cycle', 'marker'):
            with self.subTest(change=change):
                m, observed = copy.deepcopy(manifest), dict(states)
                if change == 'inbound': m['original_inbound_id'] += 1
                elif change == 'client': m['original_client_binding'] = '0' * 64
                elif change == 'cycle': m['old_cycle'] = '2026-11'
                else: observed = {n: b for n, b in observed.items() if n.endswith('.json')}
                with self.assertRaises(ContractError):
                    history(canonical(old).encode(), observed,
                            {**prior, 'manifest': canonical(m).encode()}, cfg, now)

    def test_hidden_input_never_falls_back_to_echo(self):
        output = io.StringIO()
        def fallback(*args, **kwargs):
            return getpass.fallback_getpass('synthetic prompt', stream=io.StringIO())
        with patch('vps_kit.traffic.deploy_commands.linux_root'), patch('sys.stdin.isatty', return_value=True), \
             patch('vps_kit.traffic.deploy_commands.getpass.getpass', fallback), \
             patch('getpass._raw_input', side_effect=AssertionError('VISIBLE_INPUT_MUST_NOT_BE_READ')) as visible, \
             patch('sys.stdout', output):
            code = main(['traffic', 'credential-input', '--local', '--save', str(self.root / 'candidate')])
        self.assertEqual(code, 2)
        self.assertIn('TOKEN_HIDDEN_INPUT_UNAVAILABLE', output.getvalue())
        visible.assert_not_called()
        self.assertFalse((self.root / 'candidate').exists())

    def test_basic_core_schema1_and_schema2_history_import_and_next_period_only(self):
        for instance in ('Basic', 'Core'):
            with self.subTest(instance=instance), tempfile.TemporaryDirectory() as tmp:
                old, v1 = legacy_config(instance), legacy_config(instance, schema=1)
                cfg, fs, clock, journal, b, d = harness(Path(tmp), target(old))
                clock.set('2027-01-23T12:00:00Z')
                prior_states = {'v2/2026-12.json': old_state(v1, cfg, '2026-12'),
                                'client-reset.done': b'2026-11\n', 'inbound-reset.done': b'2026-11\n'}
                states = {**prior_states, 'v2/2027-01.json': old_state(old, cfg, '2027-01')}
                seed_legacy(fs, old, states)
                b.prior = {'config': '/root/prior/config', 'manifest': '/root/prior/manifest.json'}
                fs.write(b.prior['config'], canonical(v1).encode())
                fs.write(b.prior['manifest'], canonical(provenance(old, v1, cfg, prior_states)).encode())
                p = d.plan('migrate', cfg, self.raw, sha(self.raw), None, 'enabled', ATTESTATIONS)
                d.apply(p, self.raw)
                control = Control(journal.load())
                self.assertEqual([v['schema'] for v in control.legacy_import['history']['records']], [1, 2])
                self.assertEqual(control.next_authorization()[0], 3)
                self.assertFalse(control.ledgers)
                self.assertFalse(b.panel.writes)
                clock.set(p['next_boundary_utc'])
                execute(journal, lambda c: b.panel, b.new_system, clock)
                writes = list(b.panel.writes)
                execute(journal, lambda c: b.panel, b.new_system, clock)
                self.assertEqual(b.panel.writes, writes)
                self.assertEqual(set(Control(journal.load()).ledgers), {3})
                for name, raw in states.items():
                    from vps_kit.traffic.legacy import STATE
                    self.assertEqual(fs.read(STATE + '/' + name), raw)

    def test_prior_nonobject_state_is_a_sanitized_contract_failure(self):
        old, v1 = legacy_config(), legacy_config(schema=1)
        cfg = target(old)
        states = {'v2/2026-12.json': b'[]'}
        prior = {'config': canonical(v1).encode(), 'manifest': canonical(provenance(old, v1, cfg, states)).encode()}
        with self.assertRaisesRegex(ContractError, 'LEGACY_PRIOR_STATE_SCHEMA'):
            history(canonical(old).encode(), states, prior, cfg, harness(self.root, cfg)[2].wall)

    def test_candidate_writes_cannot_target_managed_files_even_when_absent(self):
        for path in (TOKEN, '/etc/vps-kit/traffic/../candidate.json',
                     '/run/vmiss-traffic-reset/reset.lock', '/etc/systemd/system/example.timer'):
            with self.subTest(path=path):
                with self.assertRaisesRegex(ContractError, 'CANDIDATE_MANAGED_PATH'):
                    save_plan(path, {})
                with patch('vps_kit.traffic.deploy_commands.linux_root'), \
                     patch('vps_kit.traffic.deploy_commands.getpass.getpass') as prompt, \
                     patch('sys.stdout', new_callable=io.StringIO) as output:
                    code = main(['traffic', 'credential-input', '--local', '--save', path])
                self.assertEqual(code, 2)
                self.assertIn('CANDIDATE_MANAGED_PATH', output.getvalue())
                prompt.assert_not_called()
        self.assertEqual(candidate_path(self.root / 'candidate'), self.root / 'candidate')

    def test_package_rejects_path_collisions_and_missing_cli_before_install(self):
        manifest, _ = verify(self.raw, sha(self.raw))
        manifest['files']['doc/p3-contract.md/child.md'] = {'sha256': sha(b''), 'size': 0, 'mode': 0o644}
        with self.assertRaisesRegex(ContractError, 'PACKAGE_PATH_COLLISION'):
            inspect_manifest(manifest)
        manifest, _ = verify(self.raw, sha(self.raw))
        del manifest['files']['src/vps_kit/cli.py']
        with self.assertRaisesRegex(ContractError, 'PACKAGE_REQUIRED'):
            inspect_manifest(manifest)

    def test_file_images_check_parent_even_for_absent_and_controlled_links(self):
        fs = DeploymentFiles(self.root, files=ModelFiles(self.root))
        fs.mkdir('/usr/local/lib/vps-kit')
        for linked in (False, True):
            with self.subTest(linked=linked), \
                 patch.object(fs.files, 'directory', side_effect=ContractError('JOURNAL_DIRECTORY_UNSAFE')), \
                 patch('pathlib.Path.is_symlink', return_value=linked), \
                 patch('vps_kit.traffic.deploy_files.os.readlink', return_value='releases/' + __version__) as readlink:
                with self.assertRaisesRegex(ContractError, 'JOURNAL_DIRECTORY_UNSAFE'):
                    fs.image(CURRENT)
                readlink.assert_not_called()

    def test_rollback_rechecks_window_after_slow_backup_verification(self):
        cfg, fs, clock, journal, b, d = self.installed()
        p = d.plan('credential', cfg, None, None, TOKEN_B, 'enabled', ATTESTATIONS)
        d.apply(p, credential=TOKEN_B)
        backup = d.log.read()[2]['backup'][TOKEN]['backup']
        original = fs.read
        def slow_read(path, modes=(0o600,)):
            value = original(path, modes)
            if path == backup:
                clock.set('2027-01-22T20:40:00Z')
            return value
        before = (fs.image(TOKEN), d.log.read()[0].head, list(b.new_system.calls))
        with patch.object(fs, 'read', slow_read), self.assertRaisesRegex(ContractError, 'MAINTENANCE_NEAR_BOUNDARY'):
            d.rollback(p['operation_id'], cfg.data['task_id'])
        self.assertEqual(before, (fs.image(TOKEN), d.log.read()[0].head, b.new_system.calls))
        self.assertEqual(b.new_system.timer, 'waiting')
