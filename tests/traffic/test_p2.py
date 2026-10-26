import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from support import config_data
from p2_support import ATTESTATIONS, harness, install
from vps_kit.traffic.calendar import boundary, expressions, preview, walk
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import ContractError, canonical
from vps_kit.traffic.control import Control
from vps_kit.traffic.execution import execute


class P2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_boundary(self, h):
        cfg, clock, journal, panel, system, manager = h
        clock.set(cfg.data["schedule"]["first_boundary_utc"])
        return execute(journal, lambda c: panel, system, clock)

    def test_both_versions_complete_and_repeat_no_requests_or_changes(self):
        for version in ("3.7.0", "3.10.2"):
            root = self.root / version
            root.mkdir()
            h = harness(root, version)
            install(h)
            r = self.run_boundary(h)
            self.assertEqual(r["post_requests"], 3)
            self.assertEqual(r["get_requests"], 40)
            cfg, clock, journal, panel, system, manager = h
            before = journal.load()
            calls = len(panel.reads), len(panel.writes)
            self.assertEqual(execute(journal, lambda c: panel, system, clock)["result"], "ALREADY_COMPLETED")
            self.assertEqual(journal.load(), before)
            self.assertEqual(calls, (len(panel.reads), len(panel.writes)))

    def test_first_boundary_postponed_has_no_api_or_ledger(self):
        h = harness(self.root)
        install(h)
        cfg, clock, journal, panel, system, manager = h
        clock.set("2026-12-22T20:44:25Z")
        head = journal.load().head
        self.assertEqual(execute(journal, lambda c: panel, system, clock)["result"], "BEFORE_FIRST_BOUNDARY")
        self.assertFalse(panel.reads or panel.writes)
        self.assertEqual(head, journal.load().head)

    def test_write_timeout_blocks_same_and_future_period_without_retry(self):
        h = harness(self.root)
        install(h)
        cfg, clock, journal, panel, system, manager = h
        def failure(stage, key):
            if stage == "after_send":
                raise ContractError("SYNTHETIC_LOST_RESPONSE")
        panel.hook = failure
        with self.assertRaisesRegex(ContractError, "LOST_RESPONSE"):
            self.run_boundary(h)
        self.assertEqual(panel.writes, ["client:11"])
        ledger = Control(journal.load()).ledgers[1]
        self.assertEqual(ledger["steps"][0]["status"], "pending")
        for date in ("2027-01-22T20:44:30Z", "2027-02-22T20:44:25Z"):
            clock.set(date)
            with self.assertRaisesRegex(ContractError, "HISTORY_UNRESOLVED"):
                execute(journal, lambda c: panel, system, clock)
        self.assertEqual(panel.writes, ["client:11"])

    def test_shared_lock_and_preflight_failure_have_zero_posts(self):
        h = harness(self.root)
        install(h)
        cfg, clock, journal, panel, system, manager = h
        clock.set(cfg.data["schedule"]["first_boundary_utc"])
        with journal.lock_factory(journal.lock_path):
            with self.assertRaisesRegex(ContractError, "LOCK_BUSY"):
                execute(journal, lambda c: panel, system, clock)
        panel.responses["/panel/api/clients/traffic/sample-b"]["obj"]["total"] = 1
        head = journal.load().head
        with self.assertRaises(ContractError):
            execute(journal, lambda c: panel, system, clock)
        self.assertFalse(panel.writes)
        self.assertEqual(head, journal.load().head)

    def test_prepare_panel_change_apply_preserves_history(self):
        h = harness(self.root)
        install(h)
        self.run_boundary(h)
        cfg, clock, journal, panel, system, manager = h
        clock.set("2027-01-23T12:00:00Z")
        d = cfg.data
        d["revision"] = 2
        d["inbound"]["quota_bytes"] //= 2
        new = parse_config(canonical(d))
        p = manager.plan(new, "paused", ATTESTATIONS)
        manager.prepare(p)
        old_ledger = Control(journal.load()).ledger_hashes[1]
        panel.align(new)
        manager.apply(p)
        control = Control(journal.load())
        self.assertEqual(control.ledger_hashes[1], old_ledger)
        self.assertEqual(control.config.data["revision"], 2)
        self.assertEqual(system.timer, "paused")

    def test_shortening_schedule_stale_plan_and_recreated_owner_reject(self):
        h = harness(self.root)
        install(h)
        self.run_boundary(h)
        cfg, clock, journal, panel, system, manager = h
        clock.set("2027-01-23T12:00:00Z")
        d = cfg.data
        d["revision"] = 2
        d["schedule"].update(day=1, first_boundary_utc="2027-02-01T20:44:25Z")
        with self.assertRaisesRegex(ContractError, "SHORTENS_PERIOD"):
            manager.plan(parse_config(canonical(d)), "paused", ATTESTATIONS)
        d["schedule"]["first_boundary_utc"] = "2027-03-01T20:44:25Z"
        p = manager.plan(parse_config(canonical(d)), "paused", ATTESTATIONS)
        manager.pause()
        with self.assertRaisesRegex(ContractError, "PLAN_STALE"):
            manager.prepare(p)
        d["task_id"] = "00000000-0000-4000-8000-000000000099"
        with self.assertRaisesRegex(ContractError, "OWNER_BINDING"):
            manager.plan(parse_config(canonical(d)), "paused", ATTESTATIONS)

    def test_pause_failure_reports_actual_state_and_blocks_install(self):
        h = harness(self.root)
        install(h)
        cfg, clock, journal, panel, system, manager = h
        d = cfg.data
        d["revision"] = 2
        p = manager.plan(parse_config(canonical(d)), "paused", ATTESTATIONS)
        def fail(stage):
            if stage == "before_pause":
                raise ContractError("SYNTHETIC_PAUSE_FAILURE")
        system.hook = fail
        with self.assertRaisesRegex(ContractError, "PAUSE_FAILURE"):
            manager.prepare(p)
        manifest = Control(journal.load()).maintenance
        self.assertEqual(manifest["stage"], "failed")
        self.assertEqual(manifest["observed_system"]["timer"], "waiting")
        self.assertEqual(Control(journal.load()).config.data["revision"], 1)

    def test_remove_retains_tombstone_and_history(self):
        h = harness(self.root)
        install(h)
        self.run_boundary(h)
        cfg, clock, journal, panel, system, manager = h
        clock.set("2027-01-23T12:00:00Z")
        manager.remove()
        self.assertEqual(Control(journal.load()).owner["status"], "removed")
        self.assertEqual(len(Control(journal.load()).ledgers), 1)
        with self.assertRaisesRegex(ContractError, "OWNER_NOT_ACTIVE"):
            manager.plan(cfg, "paused", ATTESTATIONS)

    def test_calendar_short_months_leap_year_and_dst(self):
        d = config_data()
        schedule = d["schedule"]
        for day in (1, 22, 28, 29, 30, 31, "last"):
            for policy in (("skip", "clamp-last-day") if type(day) is int and day >= 29 else ("not-applicable",)):
                s = {**schedule, "day": day, "short_month": policy}
                for year in (2027, 2028):
                    result = boundary(s, year, 2)
                    if day in (29, 30, 31) and policy == "skip" and day > (29 if year == 2028 else 28):
                        self.assertIsNone(result)
                    else:
                        self.assertIsNotNone(result)
        for s, year, month in [({**schedule, "day": 14, "time": "02:30:00"}, 2027, 3),
                               ({**schedule, "day": 7, "time": "01:30:00"}, 2027, 11)]:
            with self.assertRaisesRegex(ContractError, "DST"):
                boundary(s, year, month)
        cfg = parse_config(canonical(d))
        self.assertEqual(len(preview(cfg, datetime(2026, 10, 24, tzinfo=timezone.utc))["next_24"]), 24)
