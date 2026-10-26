from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from p2_support import ATTESTATIONS, harness, install
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import ContractError, canonical
from vps_kit.traffic.control import Control
from vps_kit.traffic.execution import execute


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.h = harness(Path(self.temp.name))

    def test_cancel_requires_original_panel_and_retains_paused_old_revision(self):
        h = self.h
        install(h)
        cfg, clock, journal, panel, system, manager = h
        data = cfg.data
        data["revision"] = 2
        data["inbound"]["quota_bytes"] //= 2
        new = parse_config(canonical(data))
        plan = manager.plan(new, "paused", ATTESTATIONS)
        manager.prepare(plan)
        panel.align(new)
        before = journal.load().head
        with self.assertRaises(ContractError):
            manager.cancel(plan)
        self.assertEqual(journal.load().head, before)
        panel.align(cfg)  # 模拟操作者恢复配额；管理器没有修改面板。
        manager.cancel(plan)
        self.assertEqual(Control(journal.load()).config.data["revision"], 1)
        self.assertEqual(system.timer, "paused")
        self.assertFalse(panel.writes)
        manager.resume()
        self.assertEqual(system.timer, "waiting")

    def test_resume_invalid_state_or_skip_does_not_poison_history(self):
        cfg, clock, journal, panel, system, manager = self.h
        install(self.h)
        before = journal.load().head
        with self.assertRaisesRegex(ContractError, "SYSTEM_NOT_QUIESCENT"):
            manager.resume()
        self.assertEqual(before, journal.load().head)
        manager.pause()
        before = journal.load().head
        with self.assertRaisesRegex(ContractError, "UNNECESSARY_SKIP"):
            manager.resume("2027-02-22T20:44:25Z")
        self.assertEqual(before, journal.load().head)
        manager.resume()
        self.assertEqual(system.timer, "waiting")
        self.assertFalse(panel.writes)

    def test_cancel_new_task_reserves_identity_and_can_plan_same_task(self):
        cfg, clock, journal, panel, system, manager = self.h
        plan = manager.plan(cfg, "paused", ATTESTATIONS)
        manager.prepare(plan)
        manager.cancel(plan)
        self.assertEqual(manager.plan(cfg, "paused", ATTESTATIONS)["kind"], "create")
        changed = cfg.data
        changed["task_id"] = "00000000-0000-4000-8000-000000000099"
        with self.assertRaisesRegex(ContractError, "OWNER_CREATE_NOT_EMPTY"):
            manager.plan(parse_config(canonical(changed)), "paused", ATTESTATIONS)

    def test_paused_plan_cannot_implicitly_enable_or_replace_program(self):
        cfg, clock, journal, panel, system, manager = self.h
        install(self.h, "paused")
        data = cfg.data
        data["revision"] = 2
        new = parse_config(canonical(data))
        with self.assertRaisesRegex(ContractError, "PRESERVE_PAUSED"):
            manager.plan(new, "enabled", ATTESTATIONS)
        system.program_sha256 = "b" * 64
        with self.assertRaisesRegex(ContractError, "PROGRAM_CHANGED"):
            manager.plan(new, "paused", ATTESTATIONS)
        self.assertFalse(panel.writes)

    def test_clock_jump_after_reads_blocks_install_without_program_changes(self):
        cfg, clock, journal, panel, system, manager = self.h
        plan = manager.plan(cfg, "paused", ATTESTATIONS)
        manager.prepare(plan)
        def jump(stage, key):
            if stage == "get":
                clock.wall += timedelta(seconds=10)
        panel.hook = jump
        with self.assertRaisesRegex(ContractError, "CLOCK_JUMP"):
            manager.apply(plan)
        self.assertNotIn("install", system.calls)
        self.assertFalse(panel.writes)
        self.assertEqual(Control(journal.load()).maintenance["stage"], "prepared")

    def test_single_client_and_label_quota_revision_no_extra_current_reset(self):
        cfg, clock, journal, panel, system, manager = self.h
        # 首任务的 Client 集合直接使用独立合成配置，无已部署实例。
        data = cfg.data
        data["clients"] = data["clients"][:1]
        from p2_support import harness
        h = harness(Path(self.temp.name), data=data)
        cfg, clock, journal, panel, system, manager = h
        install(h)
        clock.set(cfg.data["schedule"]["first_boundary_utc"])
        self.assertEqual(execute(journal, lambda c: panel, system, clock)["post_requests"], 2)
        clock.set("2027-01-23T12:00:00Z")
        data["revision"] = 2
        data["inbound"]["remark"] = "SYNTHETIC-new-label"
        data["clients"][0]["email"] = "new-label"
        data["clients"][0]["quota_bytes"] //= 2
        new = parse_config(canonical(data))
        plan = manager.plan(new, "paused", ATTESTATIONS)
        old_hash = Control(journal.load()).ledger_hashes[1]
        manager.prepare(plan)
        panel.align(new)
        manager.apply(plan)
        self.assertEqual(Control(journal.load()).ledger_hashes[1], old_hash)
        self.assertEqual(execute(journal, lambda c: panel, system, clock)["result"], "NOT_DUE")
        self.assertEqual(len(panel.writes), 2)
