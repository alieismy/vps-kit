import copy
import json
import unittest

from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import ContractError, canonical
from vps_kit.traffic.discovery import FixtureReader, discover
from vps_kit.traffic.panel import ReadEndpoint, get_adapter, recovery
from support import config_data, fixture_data, ROOT


class PanelTests(unittest.TestCase):
    def run_fixture(self, d, fixture):
        cfg = parse_config(json.dumps(d))
        reader = FixtureReader(fixture, cfg)
        return discover(cfg, reader, "SYNTHETIC"), reader

    def test_both_version_fixtures_fixed_and_five_gets(self):
        for version in ("3.7.0", "3.10.2"):
            d = config_data(version)
            fixture = fixture_data(d)
            path = ROOT / "tests" / "traffic" / "fixtures" / version
            self.assertEqual(json.loads((path / "config.json").read_text(encoding="utf-8")), d)
            self.assertEqual(json.loads((path / "panel.json").read_text(encoding="utf-8")), fixture)
            report, reader = self.run_fixture(d, fixture)
            self.assertEqual(report["read_calls"], 5)
            self.assertEqual(report["http_calls"], 0)
            self.assertEqual(len(set(reader.paths)), 5)
            for hidden in ("panel.example.invalid", "00000000-0000-4000", "sample-a"):
                self.assertNotIn(hidden, json.dumps(report))

    def test_weekday_missing_rejected_only_for_new_adapter(self):
        d = config_data("3.10.2")
        f = fixture_data(d)
        i = f["responses"]["/panel/api/inbounds/list"]["obj"][0]
        del i["settings"]["clients"][0]["resetWeekday"]
        with self.assertRaisesRegex(ContractError, "EMBEDDED_WEEKDAY_MIGRATION_UNVERIFIED"):
            self.run_fixture(d, f)
        old = config_data()
        self.run_fixture(old, fixture_data(old))

    def test_missing_vs_null_bool_numeric_string_and_active_periods(self):
        for version in ("3.7.0", "3.10.2"):
            for field, value in (("resetDay", None), ("resetDay", False), ("resetDay", "0"),
                                 ("resetDay", 1), ("trafficReset", None), ("trafficReset", "monthly")):
                d = config_data(version)
                f = fixture_data(d)
                f["responses"]["/panel/api/inbounds/list"]["obj"][0]["settings"]["clients"][0][field] = value
                with self.subTest(version=version, field=field, value=value), self.assertRaises(ContractError):
                    self.run_fixture(d, f)
        for route in ("get", "traffic"):
            d = config_data()
            f = fixture_data(d)
            target = f["responses"][f"/panel/api/clients/{route}/sample-a"]["obj"]
            if route == "get":
                target = target["client"]
            del target["resetDay"]
            with self.assertRaises(ContractError):
                self.run_fixture(d, f)

    def test_recovery_uses_own_quota_and_both_enable_states(self):
        for config_enable in (False, True):
            for traffic_enable in (False, True):
                for exhausted in (False, True):
                    for policy in ("stop", "quota-recovery", "hold"):
                        valid = ((policy == "stop" and config_enable and traffic_enable)
                                 or (policy == "hold" and not config_enable and not traffic_enable)
                                 or (policy == "quota-recovery" and ((config_enable and traffic_enable) or exhausted)))
                        args = (policy, 100, 100 if exhausted else 50, config_enable, traffic_enable)
                        with self.subTest(args=args):
                            if valid:
                                recovery(*args)
                            else:
                                with self.assertRaises(ContractError):
                                    recovery(*args)

    def test_inbound_exhaustion_does_not_enable_other_client(self):
        d = config_data()
        d["inbound"]["policy"] = "quota-recovery"
        f = fixture_data(d)
        i = f["responses"]["/panel/api/inbounds/list"]["obj"][0]
        i.update(enable=False, up=d["inbound"]["quota_bytes"], down=0)
        t = f["responses"]["/panel/api/clients/traffic/sample-b"]["obj"]
        t["enable"] = False
        with self.assertRaisesRegex(ContractError, "POLICY_DISABLED_BELOW_QUOTA"):
            self.run_fixture(d, f)
        t.update(up=d["clients"][1]["quota_bytes"], down=0)
        result, _ = self.run_fixture(d, f)
        self.assertEqual(result["clients"][1]["policy_result"], "quota-recovery-eligible")

    def test_hold_and_enable_drift(self):
        d = config_data()
        d["clients"][1]["policy"] = "hold"
        f = fixture_data(d)
        with self.assertRaisesRegex(ContractError, "POLICY_HOLD_ENABLED"):
            self.run_fixture(d, f)
        f["responses"]["/panel/api/inbounds/list"]["obj"][0]["settings"]["clients"][1]["enable"] = False
        f["responses"]["/panel/api/clients/get/sample-b"]["obj"]["client"]["enable"] = False
        f["responses"]["/panel/api/clients/traffic/sample-b"]["obj"]["enable"] = False
        self.run_fixture(d, f)
        f["responses"]["/panel/api/clients/get/sample-b"]["obj"]["client"]["enable"] = True
        with self.assertRaisesRegex(ContractError, "API_CONFIG_ENABLE_MISMATCH"):
            self.run_fixture(d, f)

    def test_binding_set_node_association_and_quota_failures(self):
        mutations = [
            lambda f: f["responses"]["/panel/api/inbounds/list"]["obj"][0].update(nodeId=8),
            lambda f: f["responses"]["/panel/api/inbounds/list"]["obj"][0].update(total=500 * 1024**3),
            lambda f: f["responses"]["/panel/api/inbounds/list"]["obj"][0]["settings"]["clients"].pop(),
            lambda f: f["responses"]["/panel/api/clients/get/sample-a"]["obj"].update(inboundIds=[7, 8]),
            lambda f: f["responses"]["/panel/api/clients/get/sample-a"]["obj"].update(inboundIds=[True]),
            lambda f: f["responses"]["/panel/api/clients/get/sample-a"]["obj"]["client"].update(uuid="wrong"),
            lambda f: f["responses"]["/panel/api/clients/traffic/sample-a"]["obj"].update(total=0),
            lambda f: f["responses"]["/panel/api/clients/traffic/sample-a"]["obj"].update(uuid=None),
            lambda f: f["responses"]["/panel/api/clients/traffic/sample-a"]["obj"].update(up=True),
            lambda f: f["responses"]["/panel/api/clients/traffic/sample-a"]["obj"].update(down=-1)]
        for mutation in mutations:
            d = config_data()
            f = fixture_data(d)
            mutation(f)
            with self.subTest(mutation=mutation), self.assertRaises(ContractError):
                self.run_fixture(d, f)

    def test_settings_string_and_sixteen_clients_budget(self):
        d = config_data()
        d["clients"] = [{**d["clients"][0], "record_id": n, "email": f"c{n}",
                         "uuid": f"00000000-0000-4000-8000-{n:012d}"} for n in range(1, 17)]
        f = fixture_data(d)
        i = f["responses"]["/panel/api/inbounds/list"]["obj"][0]
        i["settings"] = canonical(i["settings"])
        report, _ = self.run_fixture(d, f)
        self.assertEqual(report["read_calls"], 33)

    def test_failed_response_stops_without_retry(self):
        d = config_data()
        cfg = parse_config(json.dumps(d))
        f = fixture_data(d)
        f["responses"]["/panel/api/inbounds/list"]["success"] = False
        reader = FixtureReader(f, cfg)
        with self.assertRaisesRegex(ContractError, "API_ENVELOPE"):
            discover(cfg, reader, "SYNTHETIC")
        self.assertEqual(reader.attempts, 1)

    def test_route_allowlist_and_side_effect_metadata(self):
        self.assertEqual(ReadEndpoint.CLIENT.path("a+b@example.test"), "/panel/api/clients/get/a%2Bb%40example.test")
        for evil in ("../resetAll", "a/b", "a%2Fb", "a?b", "a\nb"):
            with self.assertRaises(ContractError):
                ReadEndpoint.CLIENT.path(evil)
        self.assertEqual(get_adapter("3.7.0").future_client_reset_order, "enable-then-reset")
        self.assertEqual(get_adapter("3.10.2").future_client_reset_order, "reset-then-enable")

    def test_detail_failure_stops_before_traffic_get(self):
        d = config_data()
        f = fixture_data(d)
        f["responses"]["/panel/api/clients/get/sample-a"]["obj"]["inboundIds"] = [7, 8]
        cfg = parse_config(json.dumps(d))
        reader = FixtureReader(f, cfg)
        with self.assertRaisesRegex(ContractError, "API_CLIENT_ASSOCIATION"):
            discover(cfg, reader, "SYNTHETIC")
        self.assertEqual(reader.attempts, 2)
