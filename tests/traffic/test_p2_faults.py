"""L1 反例与故障注入：所有面板/服务/目录同步均为替身，不发送网络请求。"""
import copy
from datetime import timedelta
from pathlib import Path
import shutil
import tempfile
import unittest

from p2_support import ATTESTATIONS, harness, install
from support import config_data
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import ContractError, canonical
from vps_kit.traffic.control import Control
from vps_kit.traffic.execution import execute
from vps_kit.traffic.commands import audit


def disable_client(panel, client, used):
    inbound = panel.responses["/panel/api/inbounds/list"]["obj"][0]
    next(e for e in inbound["settings"]["clients"] if e["email"] == client["email"])["enable"] = False
    panel.responses["/panel/api/clients/get/" + client["email"]]["obj"]["client"]["enable"] = False
    panel.responses["/panel/api/clients/traffic/" + client["email"]]["obj"].update(enable=False, up=used, down=0)


class FaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def due(self, h):
        cfg, clock, journal, panel, system, _ = h
        clock.set(cfg.data["schedule"]["first_boundary_utc"])
        return execute(journal, lambda c: panel, system, clock)

    def test_persist_failure_matrix_blocks_replay(self):
        base = self.root / "base"
        base.mkdir()
        template = harness(base)
        install(template)
        count = len(template[2].load().events)
        # 3 次写的账本序列：初始 + 3*(pending/confirmed) + completed。
        cases = 0
        for offset, expected_posts in ((2, 0), (3, 1), (8, 3)):
            for target in ("record", "head"):
                for point in ("before_file_sync", "after_file_sync", "before_replace", "after_replace", "after_directory_sync"):
                    cases += 1
                    with self.subTest(offset=offset, target=target, point=point):
                        root = self.root / str(cases)
                        root.mkdir()
                        h = harness(root)
                        shutil.copytree(base / "state", root / "state")
                        cfg, clock, journal, panel, system, _ = h
                        system.units = copy.deepcopy(template[4].units)
                        system.timer, system.service = "waiting", "inactive"
                        number = count + offset
                        triggered = []
                        def fail(name, stage):
                            record = f"{number:08d}.json"
                            selected = (name == record if target == "record" else
                                        name == "head.json" and (journal.root / "records" / record).exists())
                            if not triggered and selected and stage == point:
                                triggered.append(True)
                                raise OSError("synthetic persistence fault")
                        journal.fault = fail
                        with self.assertRaises(OSError):
                            self.due(h)
                        self.assertTrue(triggered)
                        self.assertEqual(len(panel.writes), expected_posts)
                        journal.fault = lambda *_: None
                        for date in ("2027-01-22T20:44:30Z", "2027-02-22T20:44:25Z"):
                            clock.set(date)
                            try:
                                result = execute(journal, lambda c: panel, system, clock)
                                # completed 的 head 已提交时，本账期自然返回完成；后续账期允许正常执行。
                                # 该分支只在同一账期测试；下一账期另见下方处理。
                                if date.startswith("2027-01"):
                                    self.assertEqual(result["result"], "ALREADY_COMPLETED")
                            except ContractError:
                                pass
                            if date.startswith("2027-01"):
                                self.assertEqual(len(panel.writes), expected_posts)
                        # 完整提交 completed 后的新账期允许新 3 次；任何未完整提交只允许原请求数。
                        fully_committed = offset == 8 and target == "head" and point in ("after_replace", "after_directory_sync")
                        self.assertEqual(len(panel.writes), expected_posts + (3 if fully_committed else 0))
        self.assertEqual(cases, 30)

    def test_recovery_and_hold_both_versions(self):
        for version in ("3.7.0", "3.10.2"):
            root = self.root / version
            root.mkdir()
            d = config_data(version)
            d["inbound"]["policy"] = "quota-recovery"
            d["clients"][0]["policy"] = "hold"
            h = harness(root, data=d)
            cfg, clock, journal, panel, system, manager = h
            disable_client(panel, d["clients"][0], 1200)
            install(h)
            disable_client(panel, d["clients"][1], d["clients"][1]["quota_bytes"])
            inbound = panel.responses["/panel/api/inbounds/list"]["obj"][0]
            inbound.update(enable=False, up=d["inbound"]["quota_bytes"], down=0)
            self.due(h)
            self.assertEqual(panel.writes, ["client:12", "inbound", "enable"])
            control = Control(journal.load())
            self.assertEqual(control.ledgers[1]["final"]["client:11"]["used_bytes"], 1200)
            self.assertFalse(control.ledgers[1]["final"]["client:11"]["enabled"])
            reset = next(s for s in control.ledgers[1]["steps"] if s["object"] == "inbound")
            self.assertFalse(reset["after"]["enabled"])
            self.assertTrue(control.ledgers[1]["final"]["inbound"]["enabled"])

    def test_no_inbound_shortcut_for_unexhausted_disabled_client(self):
        d = config_data()
        d["inbound"]["policy"] = "quota-recovery"
        h = harness(self.root, data=d)
        install(h)
        disable_client(h[3], d["clients"][1], 1000)
        h[3].responses["/panel/api/inbounds/list"]["obj"][0].update(enable=False, up=d["inbound"]["quota_bytes"], down=0)
        head = h[2].load().head
        with self.assertRaises(ContractError):
            self.due(h)
        self.assertFalse(h[3].writes)
        self.assertEqual(head, h[2].load().head)

    def test_sixteen_independent_clients_and_unlimited_stop(self):
        d = config_data("3.10.2")
        d["clients"] = [{"record_id": i, "uuid": f"00000000-0000-4000-8000-{i:012d}", "email": "client-" + str(i),
                         "quota_bytes": i * 1024**3 if i != 1 else 0, "policy": "stop"} for i in range(1, 17)]
        h = harness(self.root, data=d)
        install(h)
        result = self.due(h)
        self.assertEqual(h[3].writes, ["client:" + str(i) for i in range(1, 17)] + ["inbound"])
        self.assertEqual(result["get_requests"], 33 * (2 + 2 * 17))

    def test_processed_client_drift_and_unrelated_counter_rollback_stop_next_write(self):
        for fault in ("enabled", "counter"):
            root = self.root / fault
            root.mkdir()
            h = harness(root)
            install(h)
            def change(stage, key):
                if stage == "after_send" and key == "client:11":
                    if fault == "enabled":
                        disable_client(h[3], h[0].data["clients"][0], 0)
                    else:
                        h[3].responses["/panel/api/clients/traffic/sample-b"]["obj"].update(up=0, down=0)
            h[3].hook = change
            with self.assertRaises(ContractError):
                self.due(h)
            self.assertEqual(h[3].writes, ["client:11"])
            self.assertEqual(Control(h[2].load()).unresolved(), [1])

    def test_time_window_and_clock_jump_after_pending_send_zero_posts(self):
        for change in ("window", "clock", "ntp", "program"):
            root = self.root / change
            root.mkdir()
            h = harness(root)
            install(h)
            cfg, clock, journal, panel, system, manager = h
            def hook(stage):
                state = Control(journal.load()).ledgers.get(1)
                if state and state["steps"][0]["status"] == "pending":
                    if change == "window":
                        clock.set("2027-01-22T20:48:25Z")
                    elif change == "clock":
                        clock.wall += timedelta(seconds=10)
                    elif change == "ntp":
                        clock.synced = False
                    else:
                        system.program_sha256 = "b" * 64
            system.hook = hook
            with self.assertRaises(ContractError):
                self.due(h)
            self.assertFalse(panel.writes)
            self.assertEqual(Control(journal.load()).unresolved(), [1])

    def test_late_response_resolution_does_not_rewrite_or_replay_old_period(self):
        h = harness(self.root)
        install(h)
        cfg, clock, journal, panel, system, manager = h
        def lost(stage, key):
            if stage == "before_send":
                raise ContractError("SYNTHETIC_REQUEST_MAY_BE_QUEUED")
        panel.hook = lost
        with self.assertRaises(ContractError):
            self.due(h)
        old = Control(journal.load())
        clock.set("2027-01-23T12:00:00Z")
        manager.pause()
        evidence = {"resolution_id": "00000000-0000-4000-8000-000000000088", "source_ledger_sha256": old.ledger_hashes[1],
                    "period_id": 1, "actor": "synthetic-reviewer", "evidence_sha256": "f" * 64,
                    "late_commit_closed": True, "billing_aligned": True, "not_before_boundary_utc": "2027-02-22T20:44:25Z", "decision": "future-only"}
        manager.resolve_future(evidence)
        new = Control(journal.load())
        self.assertEqual(old.ledgers[1], new.ledgers[1])
        self.assertEqual(old.ledger_hashes[1], new.ledger_hashes[1])
        self.assertFalse(new.unresolved())
        self.assertEqual(new.next_authorization()[0], 2)
        panel.hook = lambda *_: None
        manager.resume()
        before = len(panel.writes)
        clock.set("2027-02-22T20:44:25Z")
        execute(journal, lambda c: panel, system, clock)
        self.assertEqual(len(panel.writes) - before, 3)
        self.assertIsNone(Control(journal.load()).ledgers[1]["completed_at"])

    def test_missed_month_explicit_skip_has_no_reset_and_period_remains_monotonic(self):
        h = harness(self.root)
        install(h)
        cfg, clock, journal, panel, system, manager = h
        clock.set("2027-01-23T12:00:00Z")
        with self.assertRaisesRegex(ContractError, "MISSED_BOUNDARY"):
            execute(journal, lambda c: panel, system, clock)
        manager.pause()
        manager.resume("2027-02-22T20:44:25Z")
        self.assertFalse(panel.writes)
        self.assertEqual(Control(journal.load()).next_authorization()[0], 1)

    def test_half_install_and_resume_failure_keep_actual_state(self):
        for failure in ("installed_file", "reload", "after_resume"):
            root = self.root / failure
            root.mkdir()
            h = harness(root)
            cfg, clock, journal, panel, system, manager = h
            p = manager.plan(cfg, "enabled", ATTESTATIONS)
            manager.prepare(p)
            def fault(stage):
                if stage == failure:
                    raise ContractError("SYNTHETIC_SYSTEM_FAILURE")
            system.hook = fault
            with self.assertRaises(ContractError):
                manager.apply(p)
            control = Control(journal.load())
            self.assertEqual(control.maintenance["stage"], "failed")
            self.assertEqual(control.maintenance["observed_system"]["timer"], system.timer)
            self.assertFalse(panel.writes)
            with self.assertRaises(ContractError):
                self.due(h)

    def test_audit_is_readonly_and_corrupt_chain_is_not_success(self):
        h = harness(self.root)
        install(h)
        self.due(h)
        journal = h[2]
        before = {str(p): p.read_bytes() for p in journal.root.rglob("*") if p.is_file()}
        report = audit(Control(journal.load()))
        self.assertEqual(report["result"], "CONTROL_CONTRACT_VALID")
        self.assertEqual(report["script_writes"], 0)
        self.assertEqual(before, {str(p): p.read_bytes() for p in journal.root.rglob("*") if p.is_file()})
        (journal.root / "records/00000001.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ContractError):
            audit(Control(journal.load()))

    def test_unknown_store_content_cannot_be_treated_as_empty_owner(self):
        h = harness(self.root)
        h[2].root.mkdir()
        (h[2].root / "unexpected-backup.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "JOURNAL_UNKNOWN_FILE"):
            h[5].plan(h[0], "paused", ATTESTATIONS)
        self.assertFalse(h[3].reads or h[3].writes or h[4].calls)
