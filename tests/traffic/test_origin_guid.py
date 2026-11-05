"""3.11.0 在列表返回前为本机入站填入 GUID 的合成回归。"""
import json
from pathlib import Path
import tempfile
import unittest

from support import config_data, fixture_data
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.discovery import FixtureReader, discover
from vps_kit.traffic.contracts import ContractError, canonical
from vps_kit.traffic.execution import execute
from vps_kit.traffic.panel import ReadEndpoint, get_adapter, snapshot_read_budget
from vps_kit.traffic.panel_http import LocalPanelReader, ProcessResult
from vps_kit.traffic.panel_write import LocalPanelSession
from p2_support import harness, install


LOCAL_GUID = "11111111-1111-4111-8111-111111111111"
REMOTE_GUID = "22222222-2222-4222-8222-222222222222"
STATUS = "/panel/api/server/status"
INBOUNDS = "/panel/api/inbounds/list"


def annotated_fixture(version="3.11.0"):
    data = config_data(version)
    config = parse_config(json.dumps(data))
    fixture = fixture_data(data)
    inbound = fixture["responses"][INBOUNDS]["obj"][0]
    inbound.pop("nodeId")
    inbound["originNodeGuid"] = LOCAL_GUID
    fixture["responses"][STATUS] = {"success": True, "obj": {"panelGuid": LOCAL_GUID}}
    return config, fixture, inbound


class OriginGuidTests(unittest.TestCase):
    def test_311_local_read_annotation(self):
        config, fixture, inbound = annotated_fixture()
        reader = FixtureReader(fixture, config)
        result = discover(config, reader, "SYNTHETIC")
        self.assertEqual(result["result"], "READ_CONTRACT_PASS")
        self.assertEqual(result["script_writes"], 0)
        self.assertEqual(result["read_calls"], 6)
        self.assertEqual(result["read_budget"], 6)
        self.assertEqual(reader.paths[:2], [INBOUNDS, STATUS])
        self.assertNotIn(LOCAL_GUID, canonical(result))
        self.assertEqual(result["topology_provenance"], "MANUAL_REVIEW_REQUIRED")

    def test_remote_node_even_with_matching_guid_stops_before_status(self):
        for node in (1, 0, -1, False, "1", [], {}):
            config, fixture, inbound = annotated_fixture()
            inbound["nodeId"] = node
            reader = FixtureReader(fixture, config)
            with self.subTest(node=node), self.assertRaisesRegex(ContractError, "API_REMOTE_NODE"):
                discover(config, reader, "SYNTHETIC")
            self.assertEqual(reader.paths, [INBOUNDS])

    def test_remote_guid_without_node_id_is_rejected(self):
        config, fixture, inbound = annotated_fixture()
        inbound["originNodeGuid"] = REMOTE_GUID
        reader = FixtureReader(fixture, config)
        with self.assertRaisesRegex(ContractError, "API_REMOTE_ORIGIN") as error:
            discover(config, reader, "SYNTHETIC")
        self.assertEqual(reader.paths, [INBOUNDS, STATUS])
        self.assertNotIn(REMOTE_GUID, str(error.exception))

    def test_malformed_origin_rejected_before_status(self):
        for value in (None, False, 0, [], {}, "remote", " " + LOCAL_GUID,
                      "00000000-0000-0000-0000-000000000000"):
            config, fixture, inbound = annotated_fixture()
            inbound["originNodeGuid"] = value
            reader = FixtureReader(fixture, config)
            with self.subTest(value=value), self.assertRaisesRegex(ContractError, "API_ORIGIN_GUID"):
                discover(config, reader, "SYNTHETIC")
            self.assertEqual(reader.attempts, 1)

    def test_missing_or_invalid_status_never_trusts_inbound_identity(self):
        for status in ({}, {"panelGuid": None}, {"panelGuid": ""}, {"panelGuid": False},
                       {"panelGuid": "not-a-guid"}, [], None):
            config, fixture, _ = annotated_fixture()
            fixture["responses"][STATUS]["obj"] = status
            reader = FixtureReader(fixture, config)
            with self.subTest(status=status), self.assertRaisesRegex(ContractError, "API_PANEL_GUID"):
                discover(config, reader, "SYNTHETIC")
            self.assertEqual(reader.attempts, 2)
        config, fixture, _ = annotated_fixture()
        fixture["responses"][STATUS] = {"success": False, "msg": "PRIVATE"}
        with self.assertRaisesRegex(ContractError, "API_ENVELOPE"):
            discover(config, FixtureReader(fixture, config), "SYNTHETIC")

    def test_legacy_versions_keep_origin_rejection_and_no_status(self):
        for version in ("3.7.0", "3.10.2"):
            config, fixture, _ = annotated_fixture(version)
            fixture["responses"].pop(STATUS)
            reader = FixtureReader(fixture, config)
            with self.subTest(version=version), self.assertRaisesRegex(ContractError, "API_REMOTE_ORIGIN"):
                discover(config, reader, "SYNTHETIC")
            self.assertEqual(reader.attempts, 1)

    def test_empty_or_omitted_origin_keeps_original_request_count(self):
        for missing in (False, True):
            config, fixture, inbound = annotated_fixture()
            inbound["originNodeGuid"] = ""
            inbound["nodeId"] = None
            if missing:
                del inbound["originNodeGuid"]
            fixture["responses"].pop(STATUS)
            reader = FixtureReader(fixture, config)
            self.assertEqual(discover(config, reader, "SYNTHETIC")["read_calls"], 5)
            self.assertNotIn(STATUS, reader.paths)

    def test_adapter_without_identity_reader_fails_closed(self):
        config, fixture, _ = annotated_fixture()
        with self.assertRaisesRegex(ContractError, "API_LOCAL_ORIGIN_UNVERIFIED"):
            get_adapter("3.11.0").inbound(canonical(fixture["responses"][INBOUNDS]), config)

    def test_status_transport_failure_is_not_retried(self):
        for failure in (ProcessResult(0, b'PRIVATE\n302'), ProcessResult(7, b'PRIVATE'),
                        ProcessResult(0, b'PRIVATE\n401')):
            config, fixture, _ = annotated_fixture()
            calls = []
            def transport(args, **kwargs):
                calls.append(args)
                self.assertNotIn("X" * 48, repr(args))
                self.assertEqual(args[args.index("--request") + 1], "GET")
                if args[-1].endswith(STATUS):
                    return failure
                return ProcessResult(0, canonical(fixture["responses"][INBOUNDS]).encode() + b'\n200')
            reader = LocalPanelReader(config, "X" * 48, transport)
            with self.assertRaises(ContractError) as error:
                discover(config, reader, "LOCAL_LIVE_GET")
            self.assertNotIn("PRIVATE", str(error.exception))
            self.assertEqual(len(calls), 2)

    def test_request_budget_and_status_route_arguments(self):
        config, fixture, _ = annotated_fixture()
        with self.assertRaisesRegex(ContractError, "API_ROUTE_ARGUMENT"):
            ReadEndpoint.STATUS.path("unexpected")
        reader = LocalPanelReader(config, "X" * 48, lambda *a, **k: ProcessResult(0, b'{}\n200'))
        for _ in range(snapshot_read_budget(config)):
            reader.get(ReadEndpoint.STATUS)
        with self.assertRaisesRegex(ContractError, "API_CALL_BUDGET"):
            reader.get(ReadEndpoint.STATUS)
        session = LocalPanelSession(config, "X" * 48)
        self.assertEqual(session.reader.budget, 6 * (2 + 2 * 4))

    def test_execution_rechecks_local_identity_at_each_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            h = harness(Path(directory), "3.11.0")
            cfg, clock, journal, panel, system, manager = h
            panel.responses[INBOUNDS]["obj"][0]["originNodeGuid"] = LOCAL_GUID
            panel.responses[STATUS] = {"success": True, "obj": {"panelGuid": LOCAL_GUID}}
            install(h)
            clock.set(cfg.data["schedule"]["first_boundary_utc"])
            result = execute(journal, lambda _: panel, system, clock)
            self.assertEqual((result["get_requests"], result["post_requests"]), (48, 3))
            self.assertEqual(panel.reads.count(STATUS), 8)
            self.assertEqual(panel.writes, ["client:11", "client:12", "inbound"])

    def test_execution_identity_drift_before_first_post_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            h = harness(Path(directory), "3.11.0")
            cfg, clock, journal, panel, system, manager = h
            panel.responses[INBOUNDS]["obj"][0]["originNodeGuid"] = LOCAL_GUID
            panel.responses[STATUS] = {"success": True, "obj": {"panelGuid": LOCAL_GUID}}
            install(h)
            def drift(stage, path):
                if path == STATUS and panel.reads.count(STATUS) == 2:
                    panel.responses[STATUS]["obj"]["panelGuid"] = REMOTE_GUID
            panel.hook = drift
            clock.set(cfg.data["schedule"]["first_boundary_utc"])
            with self.assertRaisesRegex(ContractError, "API_REMOTE_ORIGIN"):
                execute(journal, lambda _: panel, system, clock)
            self.assertEqual(panel.writes, [])
            self.assertEqual(panel.reads.count(STATUS), 2)
