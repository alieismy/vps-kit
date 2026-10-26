import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from vps_kit.cli import main
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import ContractError, MAX_BYTES, read_file
from vps_kit.traffic.host import live_guard, read_status
from vps_kit.traffic.panel import ReadEndpoint
from vps_kit.traffic.panel_http import LocalPanelReader, ProcessResult, bounded_process
from support import config_data, ROOT


class TransportTests(unittest.TestCase):
    def test_get_only_credentials_stdin_proxy_and_tls_flags(self):
        calls = []
        token = "Synthetic" + "X" * 39

        def execute(args, **kwargs):
            calls.append((args, kwargs))
            return ProcessResult(0, b'{"success":true,"obj":[]}\n200')

        reader = LocalPanelReader(parse_config(json.dumps(config_data())), token, execute)
        reader.get(ReadEndpoint.CLIENT, "a+b@example.test")
        args, options = calls[0]
        self.assertNotIn(token, repr(args))
        self.assertIn(token, options["stdin"].decode())
        self.assertEqual(args[:2], ["/usr/bin/curl", "-q"])
        self.assertEqual(args[args.index("--request") + 1], "GET")
        self.assertEqual(args[args.index("--connect-to") + 1], "::127.0.0.1:8443")
        self.assertEqual(args[args.index("--proxy") + 1], "")
        self.assertEqual(args[args.index("--retry") + 1], "0")
        self.assertEqual(args[args.index("--proto") + 1], "=https")
        for forbidden in ("--location", "--insecure", "--data", "--config"):
            self.assertNotIn(forbidden, args)
        self.assertFalse(hasattr(reader, "post"))
        self.assertTrue(args[-1].endswith("/clients/get/a%2Bb%40example.test"))

    def test_errors_do_not_retry_or_leak(self):
        for response in (ProcessResult(0, b'raw-private\n302'), ProcessResult(7, b'raw-private'),
                         ProcessResult(0, b'x' * (MAX_BYTES + 5))):
            calls = []
            def execute(*args, **kwargs):
                calls.append(args)
                return response
            r = LocalPanelReader(parse_config(json.dumps(config_data())), "X" * 48, execute)
            with self.assertRaises(ContractError) as e:
                r.get(ReadEndpoint.INBOUNDS)
            self.assertNotIn("private", str(e.exception))
            self.assertEqual(len(calls), 1)
            self.assertEqual(r.attempts, 1)

    def test_route_and_request_budget_before_process(self):
        calls = []
        def execute(*args, **kwargs):
            calls.append(args)
            return ProcessResult(0, b'{}\n200')
        r = LocalPanelReader(parse_config(json.dumps(config_data())), "X" * 48, execute)
        with self.assertRaisesRegex(ContractError, "API_ENDPOINT_NOT_ALLOWED"):
            r.get("/panel/api/inbounds/resetAllTraffics")
        self.assertEqual(calls, [])
        for _ in range(5):
            r.get(ReadEndpoint.INBOUNDS)
        with self.assertRaisesRegex(ContractError, "API_CALL_BUDGET"):
            r.get(ReadEndpoint.INBOUNDS)
        self.assertEqual(len(calls), 5)

    def test_process_limits_timeout_and_no_shell(self):
        result = bounded_process([sys.executable, "-B", "-c", "print('local')"])
        self.assertEqual(result.stdout.strip(), b"local")
        with self.assertRaisesRegex(ContractError, "PROCESS_OUTPUT_LIMIT"):
            bounded_process([sys.executable, "-B", "-c", "print('x'*100000)"], limit=1024)
        with self.assertRaisesRegex(ContractError, "PROCESS_TIMEOUT"):
            bounded_process([sys.executable, "-B", "-c", "import time; time.sleep(2)"], timeout=0.1)

    def test_credential_format_and_bounded_file_reads(self):
        for token in ("X" * 47, "X" * 49, "X" * 47 + "\n", "Bearer " + "X" * 48):
            with self.assertRaisesRegex(ContractError, "TOKEN_FORMAT"):
                LocalPanelReader(parse_config(json.dumps(config_data())), token)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample"
            path.write_bytes(b"x" * 65)
            with self.assertRaisesRegex(ContractError, "FILE_SIZE"):
                read_file(path, limit=64)
            self.assertEqual(read_file(path), b"x" * 65)
            if os.name == "nt":
                with self.assertRaisesRegex(ContractError, "PROTECTED_FILE_REQUIRES_POSIX"):
                    read_file(path, protected=True)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ROOT / "tests/traffic/fixtures/3.7.0"

    def cli(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(list(args))
        return code, json.loads(output.getvalue())

    def test_menu_config_and_ledger(self):
        self.assertEqual(self.cli("traffic")[1]["write_actions"], "EXPLICIT_LOCAL_ONLY")
        code, report = self.cli("traffic", "validate-config", "--config", str(self.fixture / "config.json"))
        self.assertEqual(code, 0)
        self.assertEqual(report["result"], "CONFIG_VALID")
        code, report = self.cli("traffic", "inspect-state", "--config", str(self.fixture / "config.json"), "--state", str(self.fixture / "ledger.json"))
        self.assertEqual(code, 0)
        self.assertEqual(report["classification"], "COMPLETED_RECORDED")

    def test_unknown_writes_and_token_arguments_reject_without_io(self):
        with patch("vps_kit.cli.read_file", side_effect=AssertionError("unexpected read")):
            for action in ("apply", "run", "reset", "resume", "migrate", "rollback", "pause", "remove"):
                code, r = self.cli("traffic", action)
                self.assertEqual(code, 2)
                self.assertEqual(r["code"], "CLI_ARGUMENTS")
            code, r = self.cli("traffic", "discover", "--token", "private-value")
            self.assertEqual(code, 2)
            self.assertNotIn("private-value", json.dumps(r))

    def test_fixture_discovery_cannot_call_live_transport(self):
        with patch("vps_kit.cli.LocalPanelReader", side_effect=AssertionError("network forbidden")), \
             patch("vps_kit.cli.live_guard", side_effect=AssertionError("host forbidden")):
            code, r = self.cli("traffic", "discover", "--config", str(self.fixture / "config.json"), "--fixture", str(self.fixture / "panel.json"))
            self.assertEqual(code, 0)
            self.assertEqual(r["evidence_mode"], "SYNTHETIC")
            self.assertEqual(r["http_calls"], 0)

    def test_modes_and_windows_live_rejection(self):
        code, r = self.cli("traffic", "discover", "--config", "absent", "--fixture", "absent", "--token-file", "hidden")
        self.assertEqual(r["code"], "CLI_TOKEN_FILE_MODE")
        if os.name == "nt":
            code, r = self.cli("traffic", "discover", "--config", str(self.fixture / "config.json"), "--local", "--token-file", "absent")
            self.assertEqual(code, 2)
            self.assertEqual(r["code"], "PROTECTED_FILE_REQUIRES_POSIX")

    def test_startup_does_not_write_bytecode_or_change_inputs(self):
        def fingerprints():
            folders = (ROOT / "src", ROOT / "bin", ROOT / "tests")
            return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for folder in folders for p in folder.rglob("*") if p.is_file()}
        before = fingerprints()
        env = dict(os.environ)
        env.pop("PYTHONDONTWRITEBYTECODE", None)
        # 子进程也设置副作用守卫；父进程 audit hook 不会自动继承。
        guard = """import os, sys
def guard(event, args):
    if event.startswith('socket.') or event == 'subprocess.Popen':
        raise AssertionError('offline CLI cannot access network or start child processes')
    if event == 'open' and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
        raise AssertionError('offline CLI cannot write files')
    if event in ('os.mkdir', 'os.remove', 'os.rename', 'os.rmdir', 'os.chmod', 'os.chown', 'os.link', 'os.symlink', 'os.truncate', 'os.utime'):
        raise AssertionError('offline CLI cannot mutate filesystem')
sys.argv = sys.argv[1:]
__file__ = sys.argv[0]
sys.addaudithook(guard)
with open(sys.argv[0], 'rb') as source:
    entry = compile(source.read(), sys.argv[0], 'exec')
exec(entry, globals())
"""
        result = subprocess.run([sys.executable, "-c", guard, str(ROOT / "bin/vps-kit"), "traffic", "discover",
                                 "--config", str(self.fixture / "config.json"), "--fixture", str(self.fixture / "panel.json")],
                                env=env, capture_output=True, check=False, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["result"], "READ_CONTRACT_PASS")
        self.assertEqual(before, fingerprints())
        self.assertEqual(list((ROOT / "src").rglob("*.pyc")), [])

    def test_host_status_fixed_readonly_command_surface(self):
        commands = []
        def execute(args, **kwargs):
            commands.append(args)
            if args[1] == "--version":
                value = b"systemd 252 (252.1)\n" if "systemctl" in args[0] else b"curl 7.88.1 (x86_64)\n"
            else:
                value = b"yes\n" if "timedatectl" in args[0] else b"LoadState=not-found\nActiveState=inactive\nSubState=dead\n"
            return ProcessResult(1 if args[1] == "show" and "systemctl" in args[0] else 0, value)
        with patch("vps_kit.traffic.host.platform.system", return_value="Linux"), \
             patch("vps_kit.traffic.host.platform.freedesktop_os_release", return_value={"ID": "debian", "VERSION_ID": "13"}), \
             patch("vps_kit.traffic.host.read_file", return_value=b"systemd\n"):
            report = read_status(parse_config(json.dumps(config_data())), execute)
        # E0.2 新增已知 VMRack timer/service；仍逐条限定为只读查询。
        self.assertEqual(len(commands), 10)
        task = config_data()['task_id']
        self.assertEqual([args[2] for args in commands if args[0] == '/usr/bin/systemctl' and args[1] == 'show'],
                         ['x-ui.service', 'vmiss-3xui-traffic-reset.timer', 'vmiss-3xui-traffic-reset.service',
                          'vmrack-3xui-traffic-reset.timer', 'vmrack-3xui-traffic-reset.service',
                          'vps-kit-traffic-' + task + '.timer', 'vps-kit-traffic-' + task + '.service'])
        self.assertTrue(all(args[1] in ("show", "--version") for args in commands))
        self.assertEqual(report["api_requests"], 0)
        self.assertEqual(report["history_blocking"], "NOT_ASSESSED")


class HostGuardTests(unittest.TestCase):
    def guard(self, config, version="3.7.0", active=True, platform_match=True):
        with patch("vps_kit.traffic.host.platform.system", return_value="Linux"), \
             patch("vps_kit.traffic.host.os.geteuid", return_value=0, create=True), \
             patch("vps_kit.traffic.host.read_status", return_value={"target_platform_match": platform_match,
                       "units": [{"properties": {"ActiveState": "active" if active else "inactive"}}]}), \
             patch("vps_kit.traffic.host.read_file", return_value=b"1" * 32 + b"\n"), \
             patch("vps_kit.traffic.host.command", return_value=version), \
             patch("vps_kit.traffic.host.Path.is_file", return_value=True):
            return live_guard(config)

    def test_binding_versions_and_inactive_service(self):
        d = config_data()
        with self.assertRaisesRegex(ContractError, "HOST_BINDING_MISMATCH"):
            self.guard(parse_config(json.dumps(d)))
        d["host_id_sha256"] = hashlib.sha256(b"1" * 32).hexdigest()
        for version in ("3.7.0", "3.10.2"):
            d["panel"]["version"] = version
            cfg = parse_config(json.dumps(d))
            self.assertEqual(self.guard(cfg, "v" + version)["panel_version"], version)
            with self.assertRaisesRegex(ContractError, "HOST_PANEL_VERSION_MISMATCH"):
                self.guard(cfg, version + "+custom")
            with self.assertRaisesRegex(ContractError, "HOST_PANEL_INACTIVE"):
                self.guard(cfg, version, active=False)
            with self.assertRaisesRegex(ContractError, "HOST_OS_UNSUPPORTED"):
                self.guard(cfg, version, platform_match=False)
