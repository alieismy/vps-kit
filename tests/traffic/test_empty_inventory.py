"""systemd 空匹配的现场返回码回放；真实 Native 检查与 CLI 计划，系统命令为替身。"""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from p2_support import ATTESTATIONS
from p3_support import TOKEN_A, harness, package
from support import config_data
from vps_kit import __version__
from vps_kit.cli import main
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import ContractError, canonical
from vps_kit.traffic.deploy_native import InstallationSystem
from vps_kit.traffic.native import NativeSystem
from vps_kit.traffic.package import sha
from vps_kit.traffic.panel_http import ProcessResult
from vps_kit.traffic.units import render


class InventoryTransport:
    def __init__(self):
        self.calls = []
        self.unit_files = b'apt-daily.timer enabled enabled\nx-ui.service enabled enabled\n'
        self.loaded = b''
        self.returncodes = {'list-unit-files': 0, 'list-units': 0}

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        assert args[0] == '/usr/bin/systemctl'
        if args[1] == 'list-unit-files':
            # 现场 systemd 257：带这组模式且无匹配时 rc=1、stdout 空。
            if any('*' in arg for arg in args):
                return ProcessResult(1, b'')
            return ProcessResult(self.returncodes[args[1]], self.unit_files)
        if args[1] == 'list-units':
            return ProcessResult(self.returncodes[args[1]], self.loaded)
        assert args[1] == 'show'
        return ProcessResult(1, b'LoadState=not-found\nActiveState=inactive\nSubState=dead\nMainPID=0\n')


class EmptyInventoryTests(unittest.TestCase):
    def test_empty_match_native_inventory(self):
        transport = InventoryTransport()
        config = parse_config(canonical(config_data('3.11.0')))
        NativeSystem(transport)._foreign(config)
        self.assertEqual([a[0][1] for a in transport.calls], ['list-unit-files', 'list-units'])
        self.assertNotIn('*', ' '.join(transport.calls[0][0]))
        self.assertIn('--full', transport.calls[0][0])
        self.assertEqual(transport.calls[0][1]['limit'], 256 * 1024)
        self.assertEqual(transport.calls[1][1]['limit'], 32768)

    def test_first_install_cli_plan_uses_real_native_inventory(self):
        self.run_install_plan(InventoryTransport())

    def run_install_plan(self, transport, expected_error=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = parse_config(canonical(config_data('3.11.0')))
            cfg, fs, clock, journal, backend, deployment = harness(root, cfg)
            raw = package(root)
            backend.execute = transport
            system = InstallationSystem(backend, 'none')
            saved = []
            def save(path, plan):
                saved.append(plan)
                Path(path).write_text(canonical(plan), encoding='utf-8')
            def read(path, **kwargs):
                return canonical(ATTESTATIONS if path == 'attestations' else cfg.data)
            destination = root/'install-plan.json'
            with patch('vps_kit.traffic.deploy_commands.linux_root'), \
                 patch('vps_kit.traffic.deploy_commands.native_journal', return_value=journal), \
                 patch('vps_kit.traffic.deploy_commands.context', return_value=deployment), \
                 patch('vps_kit.traffic.deploy_commands.package_bytes', return_value=raw), \
                 patch('vps_kit.traffic.deploy_commands.token_file', return_value=TOKEN_A), \
                 patch('vps_kit.traffic.deploy_commands.read_file', side_effect=read), \
                 patch('vps_kit.traffic.deploy_commands.save_plan', side_effect=save), \
                 patch('vps_kit.traffic.native.binding_guard'), \
                 patch.object(backend, 'system', return_value=system), \
                 patch('vps_kit.traffic.deploy_commands.bootstrap') as boot, \
                 patch('sys.stdout', new_callable=io.StringIO) as output:
                rc = main(['traffic', 'deployment-plan', '--local', '--kind', 'install',
                           '--config', 'candidate', '--credential-file', 'token', '--package', 'package',
                           '--package-sha256', sha(raw), '--activation', 'paused',
                           '--attestations', 'attestations', '--save', str(destination)])
            result = json.loads(output.getvalue())
            boot.assert_not_called()
            self.assertEqual(backend.panel.writes, [])
            self.assertFalse(journal.root.exists())
            self.assertFalse(deployment.log.journal.root.exists())
            if expected_error:
                self.assertEqual(rc, 2)
                self.assertEqual(result['code'], expected_error)
                self.assertEqual(saved, [])
                self.assertFalse(destination.exists())
                self.assertEqual(backend.panel.reads, [])
                return
            self.assertEqual(rc, 0, result)
            self.assertEqual(result['result'], 'DEPLOYMENT_PLAN_SAVED')
            self.assertEqual(result['version'], __version__)
            self.assertEqual(result['activation'], 'paused')
            self.assertEqual(result['post_requests'], 0)
            self.assertFalse(result['timer_changed'])
            self.assertTrue(result['token_hidden'])
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]['config'], cfg.data)
            self.assertEqual(saved[0]['attestations'], ATTESTATIONS)

    def test_installed_conflicts_remain_rejected_including_other_unit_types(self):
        config = parse_config(canonical(config_data('3.11.0')))
        for name in ('vps-kit-traffic-other.timer', 'vps-kit-traffic-other.service',
                     'vps-kit-traffic-other.socket', 'vmiss-3xui-traffic-reset.timer',
                     'vmrack-3xui-traffic-reset.service'):
            transport = InventoryTransport()
            transport.unit_files += (name + ' disabled enabled\n').encode()
            with self.subTest(name=name), self.assertRaisesRegex(ContractError, 'FOREIGN_OR_R2_TASK_P3_REQUIRED'):
                NativeSystem(transport)._foreign(config)
            self.assertEqual(len(transport.calls), 1)

    def test_loaded_only_conflicts_remain_rejected(self):
        config = parse_config(canonical(config_data('3.11.0')))
        transport = InventoryTransport()
        transport.loaded = b'vps-kit-traffic-other.timer loaded inactive dead Synthetic description\n'
        with self.assertRaisesRegex(ContractError, 'FOREIGN_OR_R2_TASK_P3_REQUIRED'):
            NativeSystem(transport)._foreign(config)
        self.assertEqual(len(transport.calls), 2)

    def test_owned_units_and_unrelated_units_are_preserved(self):
        config = parse_config(canonical(config_data('3.11.0')))
        transport = InventoryTransport()
        for name in render(config):
            transport.unit_files += (name + ' disabled enabled\n').encode()
            transport.loaded += (name + ' loaded inactive dead Synthetic\n').encode()
        NativeSystem(transport)._foreign(config)
        self.assertEqual(len(transport.calls), 2)

    def test_query_failures_never_become_empty_success_or_retry(self):
        config = parse_config(canonical(config_data('3.11.0')))
        for verb in ('list-unit-files', 'list-units'):
            for code in (1, 5, -15):
                transport = InventoryTransport()
                transport.returncodes[verb] = code
                transport.unit_files = b''
                with self.subTest(verb=verb, code=code), self.assertRaisesRegex(ContractError, 'HOST_COMMAND_FAILED'):
                    NativeSystem(transport)._foreign(config)
                self.assertEqual(len(transport.calls), 1 if verb == 'list-unit-files' else 2)

    def test_invalid_output_is_not_silently_ignored(self):
        config = parse_config(canonical(config_data('3.11.0')))
        for raw, code in ((b'\xff', 'HOST_COMMAND_ENCODING'),
                          (b'invalid-line\n', 'SYSTEMD_UNIT_FILES_FORMAT'),
                          (b'Failed to connect to bus\n', 'SYSTEMD_UNIT_FILES_FORMAT')):
            transport = InventoryTransport()
            transport.unit_files = raw
            with self.subTest(code=code), self.assertRaisesRegex(ContractError, code):
                NativeSystem(transport)._foreign(config)
            self.assertEqual(len(transport.calls), 1)

    def test_large_inventory_keeps_late_conflict_and_bounded_limit(self):
        config = parse_config(canonical(config_data('3.11.0')))
        transport = InventoryTransport()
        transport.unit_files = b''.join(f'synthetic-{n}.service disabled enabled\n'.encode() for n in range(2000))
        self.assertGreater(len(transport.unit_files), 32768)
        NativeSystem(transport)._foreign(config)
        self.assertEqual(transport.calls[0][1]['limit'], 262144)
        transport.unit_files += b'vmrack-3xui-traffic-reset.timer disabled enabled\n'
        with self.assertRaisesRegex(ContractError, 'FOREIGN_OR_R2_TASK_P3_REQUIRED'):
            NativeSystem(transport)._foreign(config)

    def test_first_install_conflicts_or_query_failure_save_nothing(self):
        for case in ('installed', 'loaded', 'query', 'query-loaded'):
            transport = InventoryTransport()
            if case == 'installed':
                transport.unit_files += b'vmiss-3xui-traffic-reset.timer disabled enabled\n'
            elif case == 'loaded':
                transport.loaded = b'vps-kit-traffic-other.timer loaded active waiting Synthetic\n'
            else:
                transport.returncodes['list-units' if case == 'query-loaded' else 'list-unit-files'] = 1
                transport.unit_files = b''
            with self.subTest(case=case):
                self.run_install_plan(transport, 'HOST_COMMAND_FAILED' if case.startswith('query') else 'FOREIGN_OR_R2_TASK_P3_REQUIRED')

    def test_process_timeout_or_output_limit_propagates_without_retry(self):
        config = parse_config(canonical(config_data('3.11.0')))
        for code in ('PROCESS_TIMEOUT', 'PROCESS_OUTPUT_LIMIT'):
            calls = []
            def transport(args, **kwargs):
                calls.append(args)
                raise ContractError(code)
            with self.subTest(code=code), self.assertRaisesRegex(ContractError, code):
                NativeSystem(transport)._foreign(config)
            self.assertEqual(len(calls), 1)
