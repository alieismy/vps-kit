import copy
import json
import unittest

from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import ContractError
from vps_kit.traffic.state import inspect_history, inspect_ledger
from support import config_data, ledger_data, ROOT


class StateTests(unittest.TestCase):
    def setUp(self):
        self.config = parse_config(json.dumps(config_data()))
        self.ledger = ledger_data(self.config)

    def inspect(self, value):
        return inspect_ledger(json.dumps(value), self.config)

    def test_completed_record_not_execution_proof(self):
        report = self.inspect(self.ledger)
        self.assertEqual(report["classification"], "COMPLETED_RECORDED")
        self.assertEqual(report["automatic_replay"], "FORBIDDEN")
        self.assertEqual(report["evidence_authenticity"], "NOT_PROVEN")
        for version in ("3.7.0", "3.10.2"):
            cfg = parse_config(json.dumps(config_data(version)))
            saved = json.loads((ROOT / "tests/traffic/fixtures" / version / "ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(saved, ledger_data(cfg))
            inspect_ledger(json.dumps(saved), cfg)

    def test_pending_and_partial_do_not_allow_continuation(self):
        for failed_index in range(3):
            d = copy.deepcopy(self.ledger)
            d.update(completed_at=None, final=None)
            for index in range(failed_index, 3):
                step = d["steps"][index]
                step.update(status="pending" if index == failed_index else "not_sent",
                            confirmed_at=None, after=None)
                if index > failed_index:
                    step["intent_at"] = None
            report = self.inspect(d)
            self.assertEqual(report["classification"], "REVIEW_REQUIRED")
        d = copy.deepcopy(self.ledger)
        d.update(completed_at=None, final=None)
        self.assertEqual(self.inspect(d)["classification"], "REVIEW_REQUIRED")  # 最终落盘缺失。

    def test_after_pending_final_or_later_step_rejected(self):
        d = self.ledger
        d["steps"][0].update(status="pending", confirmed_at=None, after=None)
        with self.assertRaises(ContractError):
            self.inspect(d)
        d.update(completed_at=None, final=None)
        with self.assertRaisesRegex(ContractError, "ACTION_AFTER_UNRESOLVED"):
            self.inspect(d)

    def test_bindings_types_time_and_skip_evidence(self):
        mutations = [lambda d: d.update(revision=True), lambda d: d.update(config_sha256="0" * 64),
                     lambda d: d.update(schema_version=2), lambda d: d.update(extra=True),
                     lambda d: d["steps"][0].update(reason="already_enabled", status="not_required", intent_at=None, confirmed_at=None, after=None),
                     lambda d: d["steps"][0].update(confirmed_at="2027-01-22T20:44:24Z"),
                     lambda d: d.update(completed_at="2027-01-22T20:44:26Z"),
                     lambda d: d["final"]["client:11"].update(enabled=False),
                     lambda d: d["initial"]["client:11"].update(used_bytes=True),
                     lambda d: d.update(boundary_utc="2027-01-23T20:44:25Z")]
        for mutation in mutations:
            d = copy.deepcopy(self.ledger)
            mutation(d)
            with self.subTest(mutation=mutation), self.assertRaises(ContractError):
                self.inspect(d)

    def test_hold_skips_require_disabled_evidence(self):
        cfg = config_data()
        cfg["clients"][1]["policy"] = "hold"
        config = parse_config(json.dumps(cfg))
        d = ledger_data(config)
        d["initial"]["client:12"].update(enabled=False, traffic_enabled=False)
        d["final"]["client:12"].update(enabled=False, traffic_enabled=False)
        d["steps"][1].update(status="not_required", reason="hold", intent_at=None, confirmed_at=None, after=None)
        inspect_ledger(json.dumps(d), config)
        d["steps"][1]["reason"] = "timeout"
        with self.assertRaises(ContractError):
            inspect_ledger(json.dumps(d), config)

    def test_history_duplicate_and_config_revision_isolation(self):
        raw = json.dumps(self.ledger)
        with self.assertRaisesRegex(ContractError, "HISTORY_DUPLICATE"):
            inspect_history([(raw, self.config), (raw, self.config)])
        changed = config_data()
        changed["revision"] = 2
        changed["inbound"]["remark"] = "renamed"
        new = parse_config(json.dumps(changed))
        with self.assertRaisesRegex(ContractError, "STATE_BINDING"):
            inspect_ledger(raw, new)
        future = ledger_data(new)
        future.update(period_id=2, boundary_utc="2027-02-22T20:44:25Z", created_at="2027-02-22T20:44:25Z", completed_at=None, final=None)
        for step in future["steps"]:
            if step["status"] != "not_required":
                step.update(status="not_sent", intent_at=None, confirmed_at=None, after=None)
        result = inspect_history([(raw, self.config), (json.dumps(future), new)])
        self.assertFalse(result["unresolved"])
        self.assertEqual(result["continuity"], "NOT_PROVEN")
        incomplete = copy.deepcopy(self.ledger)
        incomplete.update(completed_at=None, final=None)
        result = inspect_history([(json.dumps(incomplete), self.config), (json.dumps(future), new)])
        self.assertTrue(result["unresolved"])

    def test_same_utc_with_different_text_cannot_be_new_period(self):
        d = copy.deepcopy(self.ledger)
        d.update(period_id=2, boundary_utc="2027-01-22T20:44:25.000Z")
        with self.assertRaisesRegex(ContractError, "HISTORY_DUPLICATE"):
            inspect_history([(json.dumps(self.ledger), self.config), (json.dumps(d), self.config)])

    def test_disabled_inbound_recovery_has_separate_enable_step(self):
        cfg = config_data()
        cfg["inbound"]["policy"] = "quota-recovery"
        config = parse_config(json.dumps(cfg))
        d = ledger_data(config)
        d["initial"]["inbound"].update(enabled=False, used_bytes=cfg["inbound"]["quota_bytes"])
        d["steps"][2]["after"]["enabled"] = False
        d["steps"][3].update(status="confirmed", reason=None, intent_at="2027-01-22T20:44:27Z",
                              confirmed_at="2027-01-22T20:44:28Z", after={"used_bytes": 1, "enabled": True})
        inspect_ledger(json.dumps(d), config)
        d["steps"][2]["after"]["enabled"] = True
        with self.assertRaisesRegex(ContractError, "STATE_INBOUND_PHASE"):
            inspect_ledger(json.dumps(d), config)

    def test_intent_at_end_of_window_is_invalid(self):
        d = self.ledger
        d["steps"][0]["intent_at"] = "2027-01-22T20:48:25Z"
        with self.assertRaisesRegex(ContractError, "STATE_INTENT_OUTSIDE_WINDOW"):
            self.inspect(d)
