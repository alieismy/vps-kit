"""P1 复查中发现的反例；只用合成配置、响应和受控进程替身。"""
import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from support import config_data, fixture_data, ledger_data
from vps_kit.cli import main
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import ContractError, MAX_BYTES, json_load
from vps_kit.traffic.discovery import FixtureReader, discover
from vps_kit.traffic.host import read_status
from vps_kit.traffic.panel_http import bounded_process, ProcessResult
from vps_kit.traffic.state import inspect_history


class ReviewRegressions(unittest.TestCase):
    def test_local_node_omitempty_and_remote_origin(self):
        for version in ("3.7.0", "3.10.2"):
            d = config_data(version)
            cfg = parse_config(json.dumps(d))
            fixture = fixture_data(d)
            inbound = fixture["responses"]["/panel/api/inbounds/list"]["obj"][0]
            del inbound["nodeId"]
            # 两版 model.Inbound 的 *int nodeId 使用 omitempty，空值可以不出现在响应里。
            self.assertEqual(discover(cfg, FixtureReader(fixture, cfg), "SYNTHETIC")["result"], "READ_CONTRACT_PASS")
            inbound["originNodeGuid"] = ""
            discover(cfg, FixtureReader(fixture, cfg), "SYNTHETIC")
            for value in ("synthetic-remote-node", None, False, 0):
                inbound["originNodeGuid"] = value
                with self.subTest(version=version, origin=value), self.assertRaisesRegex(ContractError, "API_REMOTE_ORIGIN"):
                    discover(cfg, FixtureReader(fixture, cfg), "SYNTHETIC")
            inbound.pop("originNodeGuid")
            for value in (1, 0, "", False):
                inbound["nodeId"] = value
                with self.subTest(version=version, node=value), self.assertRaisesRegex(ContractError, "API_REMOTE_NODE"):
                    discover(cfg, FixtureReader(fixture, cfg), "SYNTHETIC")

    def test_cli_abbreviations_rejected_before_io(self):
        for option in ("--token", "--loc"):
            args = ["traffic", "discover", "--config", "not-read", "--local", "--token-file", "not-read"]
            args[args.index("--token-file" if option == "--token" else "--local")] = option
            out = io.StringIO()
            with patch("vps_kit.cli.read_file", side_effect=AssertionError("unexpected IO")), redirect_stdout(out):
                self.assertEqual(main(args), 2)
            self.assertEqual(json.loads(out.getvalue())["code"], "CLI_ARGUMENTS")

    def test_null_traffic_enable_is_not_config_enable(self):
        for version in ("3.7.0", "3.10.2"):
            for value in (None, "missing", "true", 1):
                cfg_data = config_data(version)
                cfg = parse_config(json.dumps(cfg_data))
                f = fixture_data(cfg_data)
                traffic = f["responses"]["/panel/api/clients/traffic/sample-a"]["obj"]
                if value == "missing":
                    traffic.pop("enable")
                else:
                    traffic["enable"] = value
                reader = FixtureReader(f, cfg)
                with self.subTest(version=version, value=value), self.assertRaisesRegex(ContractError, "API_TRAFFIC_ENABLE_TYPE"):
                    discover(cfg, reader, "SYNTHETIC")
                self.assertEqual(reader.attempts, 3)

    def test_noncanonical_numeric_hosts_and_empty_userinfo_rejected(self):
        for host in ("2130706433", "0177.0.0.1", "0x7f000001", "127.1", "127.0.0.1.",
                     "@panel.example.invalid", ":@panel.example.invalid"):
            d = config_data()
            d["panel"]["url"] = "https://" + host
            with self.subTest(host=host), self.assertRaises(ContractError):
                parse_config(json.dumps(d))

    def test_history_changed_client_set_still_shares_inbound(self):
        d = config_data()
        first = parse_config(json.dumps(d))
        d["revision"] = 2
        d["clients"].pop()
        second = parse_config(json.dumps(d))
        with self.assertRaisesRegex(ContractError, "HISTORY_DUPLICATE"):
            inspect_history([(json.dumps(ledger_data(first)), first), (json.dumps(ledger_data(second)), second)])

    def test_history_moved_client_still_shares_client_reset(self):
        d = config_data()
        first = parse_config(json.dumps(d))
        for changed_key in (None, "record_id", "uuid"):
            moved = copy.deepcopy(d)
            moved["revision"] = 2
            moved["inbound"]["id"] = 8
            moved["clients"] = [moved["clients"][0]]
            if changed_key == "record_id":
                moved["clients"][0]["record_id"] = 99
            elif changed_key == "uuid":
                moved["clients"][0]["uuid"] = "00000000-0000-4000-8000-000000000099"
            second = parse_config(json.dumps(moved))
            with self.subTest(changed_key=changed_key), self.assertRaisesRegex(ContractError, "HISTORY_DUPLICATE"):
                inspect_history([(json.dumps(ledger_data(first)), first), (json.dumps(ledger_data(second)), second)])

    def test_json_encoding_size_and_nonfinite_numbers(self):
        for raw in (b'\xff\xfe{\x00}\x00', '"' + "中" * (MAX_BYTES // 3 + 1) + '"', '{"extra":1e999}'):
            with self.subTest(kind=type(raw).__name__), self.assertRaises(ContractError):
                json_load(raw)
        json_load("[" * 64 + "0" + "]" * 64)
        with self.assertRaisesRegex(ContractError, "JSON_DEPTH"):
            json_load("[" * 65 + "0" + "]" * 65)
        d = config_data()
        d["inbound"]["remark"] = "\ud800"
        with self.assertRaises(ContractError):
            parse_config(json.dumps(d))

    def test_empty_dependency_output_uses_contract_error(self):
        with patch("vps_kit.traffic.host.platform.system", return_value="Linux"), \
             patch("vps_kit.traffic.host.platform.freedesktop_os_release", return_value={"ID": "debian", "VERSION_ID": "13"}), \
             patch("vps_kit.traffic.host.read_file", return_value=b"systemd\n"):
            with self.assertRaisesRegex(ContractError, "HOST_DEPENDENCY_VERSION"):
                read_status(execute=lambda *a, **kw: ProcessResult(0, b""))

    def test_interruption_reaps_child_before_returning(self):
        class Child:
            def __init__(self):
                self.stdin, self.stdout, self.stderr = io.BytesIO(), io.BytesIO(), io.BytesIO()
                self.killed, self.waits = False, 0
                self.returncode = None

            def wait(self, timeout=None):
                self.waits += 1
                if self.waits == 1:
                    raise KeyboardInterrupt()
                self.returncode = -9
                return self.returncode

            def kill(self):
                self.killed = True

            def poll(self):
                return self.returncode

        child = Child()
        with patch("vps_kit.traffic.panel_http.subprocess.Popen", return_value=child):
            with self.assertRaises(KeyboardInterrupt):
                bounded_process(["synthetic-child"])
        self.assertTrue(child.killed)
        self.assertGreaterEqual(child.waits, 2)
        self.assertTrue(child.stdin.closed)
        self.assertTrue(child.stdout.closed)
        self.assertTrue(child.stderr.closed)
