import calendar
import io
import json
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from support import config_data, ROOT
from vps_kit.cli import main
from vps_kit.traffic.calendar import boundary, expressions, stamp, walk
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import ContractError, canonical
from vps_kit.traffic.native import UNIT_DIR, NativeSystem, calendar_check, ensure_runtime_directory, show, systemd_time
from vps_kit.traffic.panel import ReadEndpoint
from vps_kit.traffic.panel_http import ProcessResult
from vps_kit.traffic.panel_write import LocalPanelSession
from vps_kit.traffic.units import render, stem, verify_text


class NativeContractTests(unittest.TestCase):
    def test_runtime_directory_recreation_keeps_existing_lock_inode(self):
        from p2_support import ModelFiles
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lock = root / "run-model" / "execute.lock"
            # 只替代 Linux root/权限与目录同步；实际临时目录和锁文件字节/inode 保留检查。
            with patch("vps_kit.traffic.native.linux_root"), patch("vps_kit.traffic.native.LOCK", lock), \
                 patch("vps_kit.traffic.native.DurableFiles", return_value=ModelFiles(root)), \
                 patch("vps_kit.traffic.native.stat.S_IMODE", return_value=0o700):
                ensure_runtime_directory()
                lock.write_bytes(b"synthetic stable inode")
                inode = lock.stat().st_ino
                ensure_runtime_directory()
                self.assertEqual(lock.read_bytes(), b"synthetic stable inode")
                self.assertEqual(lock.stat().st_ino, inode)

    def test_post_allowlist_token_stdin_and_no_replay_both_versions(self):
        for version in ("3.7.0", "3.10.2"):
            d = config_data(version)
            d["inbound"]["policy"] = "quota-recovery"
            d["clients"][0]["email"] = "a+b@example.test"
            cfg = parse_config(canonical(d))
            calls = []
            def transport(args, **kwargs):
                calls.append((args, kwargs))
                return ProcessResult(0, b'{"success":true}\n200')
            session = LocalPanelSession(cfg, "X" * 48, transport)
            for key in ("client:11", "client:12", "inbound", "enable"):
                session.send(key)
            expected = ("/clients/resetTraffic/a%2Bb%40example.test", "/clients/resetTraffic/sample-b",
                        "/inbounds/7/resetTraffic", "/inbounds/setEnable/7")
            for (args, kw), path in zip(calls, expected):
                self.assertTrue(args[-1].endswith(path))
                self.assertNotIn("X" * 48, repr(args))
                self.assertEqual(kw["stdin"], b"Authorization: Bearer " + b"X" * 48 + b"\n")
                self.assertEqual(args[args.index("--request") + 1], "POST")
                self.assertEqual(args[args.index("--retry") + 1], "0")
                self.assertEqual(args[args.index("--max-redirs") + 1], "0")
                self.assertEqual(args[args.index("--connect-to") + 1], "::127.0.0.1:8443")
                self.assertNotIn("--insecure", args)
                self.assertEqual("--data-binary" in args, path.endswith("setEnable/7"))
            self.assertEqual(calls[-1][0][calls[-1][0].index("--data-binary") + 1], '{"enable":true}')
            with self.assertRaisesRegex(ContractError, "REPLAY"):
                session.send("client:11")
            with self.assertRaisesRegex(ContractError, "NOT_ALLOWED"):
                session.send("resetAllTraffics")
            self.assertEqual(len(calls), 4)

    def test_post_uncertain_response_is_single_attempt_and_read_transport_cannot_write(self):
        cfg = parse_config(canonical(config_data()))
        for response in (ProcessResult(7, b"hidden"), ProcessResult(0, b'{}\n302'),
                         ProcessResult(0, b'{"success":false,"msg":"hidden"}\n200'), ProcessResult(0, b'bad\n200')):
            calls = []
            def transport(*args, **kwargs):
                calls.append(True)
                return response
            session = LocalPanelSession(cfg, "X" * 48, transport)
            with self.assertRaises(ContractError) as error:
                session.send("client:11")
            self.assertNotIn("hidden", str(error.exception))
            with self.assertRaisesRegex(ContractError, "REPLAY"):
                session.send("client:11")
            self.assertEqual(calls, [True])
            self.assertFalse(hasattr(session.reader, "send"))

    def test_calendar_expression_dates_over_gregorian_cycle(self):
        # 独立解释生成表达式的日期部分，枚举 400 年短月规则；不是 systemd 实测。
        for day in (29, 30, 31, "last"):
            for policy in (("skip", "clamp-last-day") if day != "last" else ("not-applicable",)):
                s = {**config_data()["schedule"], "day": day, "short_month": policy, "timezone": "UTC", "time": "12:00:00"}
                s["first_boundary_utc"] = stamp(boundary(s, 2027, 1))
                exprs = [e.split()[0] for e in expressions(s)]
                for year in range(2000, 2400):
                    for month in range(1, 13):
                        last = calendar.monthrange(year, month)[1]
                        actual = []
                        for expr in exprs:
                            if expr == "*-*~01":
                                actual.append(last)
                            elif expr == "*-02~01":
                                if month == 2:
                                    actual.append(last)
                            elif expr.startswith("*-01,03..12-"):
                                if month != 2 and int(expr.rsplit("-", 1)[1]) <= last:
                                    actual.append(int(expr.rsplit("-", 1)[1]))
                            elif expr.startswith("*-*-"):
                                if int(expr.rsplit("-", 1)[1]) <= last:
                                    actual.append(int(expr.rsplit("-", 1)[1]))
                            else:
                                self.fail("unrecognized generated date")
                        expected = boundary(s, year, month)
                        self.assertEqual(actual, [expected.day] if expected else [])

    def test_systemd_calendar_parser_and_mismatch(self):
        cfg = parse_config(canonical(config_data()))
        now = datetime(2026, 10, 24, tzinfo=timezone.utc)
        calls = []
        def transport(args, **kwargs):
            calls.append(args)
            dates = walk(cfg.data["schedule"], now, 24)
            raw = "\n".join(("Next elapse: " if i == 0 else f"Iteration #{i+1}: ") + v.strftime("%a %Y-%m-%d %H:%M:%S UTC") for i, v in enumerate(dates))
            return ProcessResult(0, raw.encode())
        calendar_check(cfg, now, transport)
        self.assertEqual(calls[0][1:3], ["calendar", "--iterations=24"])
        with self.assertRaisesRegex(ContractError, "CALENDAR_OUTPUT"):
            calendar_check(cfg, now, lambda *a, **k: ProcessResult(0, b"not parsed"))

    def test_unit_contract_and_native_timer_only_mutations(self):
        cfg = parse_config(canonical(config_data()))
        name = stem(cfg.data["task_id"])
        units = render(cfg)
        service, timer = units[name + ".service"], units[name + ".timer"]
        for item in ("Type=oneshot", "Restart=no", "RemainAfterExit=no", "TimeoutStartSec=300s", "RuntimeDirectoryPreserve=yes",
                     "LoadCredential=api-token:/etc/vps-kit/traffic/api-token", "execute --local --task"):
            self.assertIn(item, service)
        self.assertNotIn("Requires=", service)
        self.assertNotIn("Wants=", service)
        self.assertIn("Persistent=false", timer)
        self.assertEqual(timer.count("OnCalendar="), 1)
        calls = []
        def transport(args, **kwargs):
            calls.append(args)
            return ProcessResult(0, b"")
        system = NativeSystem(transport)
        system.pause(cfg)
        system.resume(cfg)
        system.reload()
        self.assertEqual(calls, [["/usr/bin/systemctl", "disable", "--now", name + ".timer"],
                                 ["/usr/bin/systemctl", "enable", "--now", name + ".timer"],
                                 ["/usr/bin/systemctl", "daemon-reload"]])
        with self.assertRaisesRegex(ContractError, "UNIT_CONTENT_DRIFT"):
            verify_text(cfg, {**units, name + ".timer": timer.replace("false", "true")})

    def test_unknown_systemd_state_or_bus_error_is_not_absent(self):
        with self.assertRaises(ContractError):
            show("synthetic.timer", lambda *a, **k: ProcessResult(1, b""))
        result = show("synthetic.timer", lambda *a, **k: ProcessResult(1, b"LoadState=not-found\nActiveState=inactive\nSubState=dead\n"))
        self.assertEqual(result["LoadState"], "not-found")
        with self.assertRaisesRegex(ContractError, "TIME_FORMAT"):
            systemd_time("2027-01-22 local")

    def test_native_effective_unit_properties_and_foreign_owner(self):
        cfg = parse_config(canonical(config_data()))
        name = stem(cfg.data["task_id"])
        override = []
        def transport(args, **kwargs):
            if args[1] in ("list-unit-files", "list-units"):
                return ProcessResult(0, b"")
            self.assertEqual(args[1], "show")
            unit = args[2]
            p = {"Id": unit, "LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead",
                 "UnitFileState": "disabled" if unit.endswith(".timer") else "static", "MainPID": "0", "Job": "",
                 "DropInPaths": "unexpected.conf" if override else "", "NeedDaemonReload": "no", "FragmentPath": str(UNIT_DIR / unit),
                 "Type": "oneshot", "Restart": "no", "RemainAfterExit": "no", "TimeoutStartUSec": "5min",
                 "RuntimeDirectoryPreserve": "yes", "Persistent": "no", "AccuracyUSec": "1s", "RandomizedDelayUSec": "0",
                 "Triggers": name + ".service"}
            return ProcessResult(0, "\n".join(k + "=" + v for k, v in p.items()).encode())
        system = NativeSystem(transport)
        with patch.object(system, "backup", return_value=render(cfg)), \
             patch("vps_kit.traffic.native.binding_guard", return_value={}), \
             patch("vps_kit.traffic.native.program_hash", return_value="a" * 64), \
             patch("vps_kit.traffic.native.calendar_check") as parser:
            self.assertEqual(system.inspect(cfg)["timer"], "paused")
            system.verify(cfg)
            parser.assert_called_once()
            override.append(True)
            with self.assertRaisesRegex(ContractError, "EFFECTIVE_OVERRIDE"):
                system.verify(cfg)
        foreign = NativeSystem(lambda *a, **k: ProcessResult(0, b"vmiss-3xui-traffic-reset.timer disabled\n"))
        with self.assertRaisesRegex(ContractError, "P3_REQUIRED"):
            foreign._foreign(cfg)

    def test_offline_cli_and_local_guard_precede_any_file_or_system_access(self):
        fixture = ROOT / "tests/traffic/fixtures/3.7.0/config.json"
        for action in ("calendar", "units"):
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["traffic", action, "--config", str(fixture)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())["script_writes"], 0)
        for args in (["plan", "--config", "hidden", "--save", "hidden", "--attestations", "hidden", "--activation", "paused"],
                     ["prepare", "--plan", "hidden", "--expect-sha256", "a" * 64],
                     ["apply", "--plan", "hidden", "--expect-sha256", "a" * 64],
                     *[[action, "--task", "00000000-0000-4000-8000-000000000001"] for action in ("pause", "resume", "remove", "execute", "audit", "check")]):
            out = io.StringIO()
            with patch("vps_kit.traffic.native.platform.system", return_value="Windows"), \
                 patch("vps_kit.traffic.commands.read_file", side_effect=AssertionError("guard before file read")), redirect_stdout(out):
                code = main(["traffic", *args, "--local"])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(out.getvalue())["code"], "P2_REQUIRES_LINUX_ROOT")
