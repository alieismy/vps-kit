import copy
import json
import unittest

from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import ContractError
from support import config_data


class ConfigTests(unittest.TestCase):
    def parse(self, d):
        return parse_config(json.dumps(d))

    def test_both_versions_independent_quotas_and_no_provider_branch(self):
        for version in ("3.7.0", "3.10.2"):
            cfg = self.parse(config_data(version))
            self.assertEqual(cfg.data["panel"]["version"], version)
            self.assertEqual([c["quota_bytes"] for c in cfg.data["clients"]], [150 * 1024**3, 100 * 1024**3])

    def test_unknown_duplicate_types_limits(self):
        mutations = [lambda d: d.update(extra=True), lambda d: d.update(revision=True),
                     lambda d: d["panel"].update(version="3.10.2+custom"),
                     lambda d: d["inbound"].update(quota_bytes=0),
                     lambda d: d["clients"][0].update(quota_bytes=1.5),
                     lambda d: d["clients"][0].update(quota_bytes=2**63),
                     lambda d: d["clients"][0].update(email="a/b"),
                     lambda d: d["clients"][0].update(email="a\nb"),
                     lambda d: d["clients"].append(copy.deepcopy(d["clients"][0])),
                     lambda d: d.update(clients=[]),
                     lambda d: d["clients"][1].update(quota_bytes=0),
                     lambda d: [c.update(policy="hold") for c in d["clients"]]]
        for mutation in mutations:
            d = config_data()
            mutation(d)
            with self.subTest(mutation=mutation), self.assertRaises(ContractError):
                self.parse(d)
        with self.assertRaisesRegex(ContractError, "JSON_DUPLICATE_KEY"):
            parse_config('{"revision":1,"revision":2}')
        with self.assertRaisesRegex(ContractError, "JSON_NONFINITE"):
            parse_config('{"revision":NaN}')

    def test_shared_pool_unlimited_stop_and_client_bound(self):
        d = config_data()
        for c in d["clients"]:
            c["quota_bytes"] = 250 * 1024**3
        self.parse(d)  # 总和大于入口不等于双重消耗。
        d["clients"][0]["quota_bytes"] = 0
        self.parse(d)
        d["clients"] = [{**d["clients"][0], "record_id": n, "email": f"c{n}",
                         "uuid": f"00000000-0000-4000-8000-{n:012d}"} for n in range(1, 18)]
        with self.assertRaisesRegex(ContractError, "CLIENT_COUNT"):
            self.parse(d)

    def test_provider_units_and_budget(self):
        d = config_data()
        d["provider"]["unit"] = "GB"
        with self.assertRaisesRegex(ContractError, "BUDGET_EXCEEDED"):
            self.parse(d)
        d["provider"]["model"] = "manual"
        self.parse(d)
        d["provider"] = None
        self.parse(d)

    def test_labels_revision_schedule_do_not_replace_object_identity(self):
        d = config_data()
        old = self.parse(d)
        d["revision"] = 2
        d["task_id"] = "00000000-0000-4000-8000-000000000009"
        d["inbound"]["remark"] = "renamed"
        d["clients"][0]["email"] = "renamed-client"
        d["clients"][0]["quota_bytes"] = 140 * 1024**3
        new = self.parse(d)
        self.assertEqual(old.object_fingerprint, new.object_fingerprint)
        self.assertNotEqual(old.fingerprint, new.fingerprint)
        d["clients"][0]["uuid"] = "00000000-0000-4000-8000-000000000099"
        self.assertNotEqual(new.object_fingerprint, self.parse(d).object_fingerprint)

    def test_url_boundary(self):
        for url in ("https://u:p@x", "https://x/?a", "https://x/#a", "https://x/a/../b",
                    "https://x/%2F", "http://example.invalid", "https://203.0.113.1", "https://x:0", "https://x/\n"):
            d = config_data()
            d["panel"]["url"] = url
            with self.subTest(url=url), self.assertRaises(ContractError):
                self.parse(d)
        d = config_data()
        d["panel"].update(url="http://127.0.0.1:8080", allow_loopback_http=True)
        self.parse(d)
        d["panel"].update(url="https://[::1]:8443", connect_address="::1")
        self.parse(d)

    def test_first_boundary_and_short_month(self):
        d = config_data()
        d["schedule"].update(day=31, short_month="clamp-last-day", first_boundary_utc="2027-02-28T20:44:25Z")
        self.parse(d)
        d["schedule"]["short_month"] = "skip"
        with self.assertRaisesRegex(ContractError, "BOUNDARY_ALIGNMENT"):
            self.parse(d)
        d["schedule"].update(day="last", short_month="not-applicable")
        self.parse(d)
        d["schedule"].update(timezone="CST")
        with self.assertRaises(ContractError):
            self.parse(d)

    def test_dst_nonexistent_and_ambiguous_boundary(self):
        d = config_data()
        d["schedule"].update(day=1, time="01:30:00", first_boundary_utc="2026-11-01T05:30:00Z")
        with self.assertRaisesRegex(ContractError, "DST_AMBIGUOUS"):
            self.parse(d)
        d["schedule"].update(day=8, time="02:30:00", first_boundary_utc="2026-03-08T07:30:00Z")
        with self.assertRaisesRegex(ContractError, "BOUNDARY_ALIGNMENT"):
            self.parse(d)
