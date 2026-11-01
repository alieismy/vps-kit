"""无网络验证：输入配对、范围预算、失败分类及 CLI 隔离。"""
import argparse
import csv
import io
import json
import os
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from vps_kit.cli import main
from vps_kit.reality import commands, probe
from vps_kit.reality.inputs import (Candidate, ProbeError, RejectedRow, SCANNER_HEADER,
                                   dns_name, scanner_rows, single_candidate)


ROOT = Path(__file__).resolve().parents[2]


def scanner_csv(rows):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(SCANNER_HEADER)
    for ip, origin, cn in rows:
        writer.writerow([ip, origin, 'TLS 1.3', 'h2', 'X25519', '1000', 'ECDSA', 'RSA', cn, 'Test, CA', 'N/A'])
    return output.getvalue().encode()


def args(**kwargs):
    values = dict(target='192.0.2.1:443', sni='cover.example', from_scanner=None, port=None, timeout='5')
    values.update(kwargs)
    return argparse.Namespace(**values)


def passed(candidate, address, timeout, context):
    return {**probe.candidate_result(candidate), 'address': address, 'status': 'BASELINE_PASS',
            'code': 'BASELINE_MATCH'}


class RealityInputTests(unittest.TestCase):
    def test_normalizes_names_and_ipv6_without_replacing_sni(self):
        candidate = single_candidate('[2001:db8::1]:8443', 'Cover.Example.')
        self.assertEqual((candidate.target_host, candidate.port, candidate.sni), ('2001:db8::1', 8443, 'cover.example'))
        self.assertEqual(single_candidate('TARGET.Example:443', 'cover.example').target_host, 'target.example')
        self.assertEqual(dns_name('例子.example'), 'xn--fsqu00a.example')

    def test_invalid_names_and_ambiguous_target_are_rejected(self):
        for name in ('*.example.com', '192.0.2.1', 'localhost', 'x..example', '-x.example',
                     'x_.example', 'x.example/secret', 'cover.example\n', 'user@host.example', 'a' * 64 + '.example'):
            with self.subTest(name=name), self.assertRaises(ProbeError):
                dns_name(name)
        for target in ('192.0.2.1', '2001:db8::1:443', 'https://cover.example:443',
                       '[fe80::1%eth0]:443', '[192.0.2.1]:443', 'cover.example:0', 'cover.example:65536'):
            with self.subTest(target=target), self.assertRaises(ProbeError):
                single_candidate(target, 'cover.example')

    def test_csv_preserves_scanned_ip_and_prefers_domain_origin(self):
        rows = scanner_rows(scanner_csv([('192.0.2.2', 'origin.example', '*.example'),
                                         ('2001:db8::2', '2001:db8::/64', 'certificate.example')]), 8443)
        self.assertEqual(rows[0], Candidate('192.0.2.2', 8443, 'origin.example', 2, 'scanner_origin'))
        self.assertEqual(rows[1], Candidate('2001:db8::2', 8443, 'certificate.example', 3, 'scanner_certificate_cn'))

    def test_csv_wildcard_invalid_ip_and_invalid_origin_stay_unresolved(self):
        rows = scanner_rows(scanner_csv([('192.0.2.1', '192.0.2.0/24', '*.cover.example'),
                                         ('not-an-ip', 'cover.example', 'cover.example'),
                                         ('192.0.2.1', 'https://hidden.invalid/path', 'cover.example')]), 443)
        self.assertEqual(rows, [RejectedRow(2, 'SPECIFIC_SNI_REQUIRED'), RejectedRow(3, 'SCANNER_IP_INVALID'),
                                RejectedRow(4, 'SCANNER_ORIGIN_INVALID')])

    def test_csv_header_shape_size_and_count_fail_atomically(self):
        cases = [b'IP,ORIGIN,CERT_DOMAIN\n', scanner_csv([]), scanner_csv([]) + b'one,two\n',
                 b'x' * (256 * 1024 + 1), scanner_csv([('192.0.2.1', 'cover.example', '')] * 101),
                 scanner_csv([]) + b'"unterminated', b'\xff']
        for raw in cases:
            with self.subTest(size=len(raw)), self.assertRaises(ProbeError):
                scanner_rows(raw, 443)

    def test_literal_candidate_never_resolves_dns(self):
        with patch.object(socket, 'getaddrinfo', side_effect=AssertionError('unexpected DNS')):
            self.assertEqual(probe.resolve_addresses(Candidate('192.0.2.1', 443, 'cover.example')), ['192.0.2.1'])

    def test_dns_addresses_deduplicate_without_mixing_names(self):
        records = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 443)),
                   (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 443)),
                   (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:db8::1', 443, 0, 0))]
        with patch.object(socket, 'getaddrinfo', return_value=records) as lookup:
            self.assertEqual(probe.resolve_addresses(Candidate('target.example', 443, 'different.example')),
                             ['192.0.2.1', '2001:db8::1'])
            self.assertEqual(lookup.call_args.args[0], 'target.example')

    def test_scoped_dns_address_is_not_silently_retargeted(self):
        with patch.object(socket, 'getaddrinfo', return_value=[(socket.AF_INET6, 1, 6, '', ('fe80::1', 443, 0, 2))]):
            with self.assertRaisesRegex(ProbeError, 'SCOPED_DNS_ADDRESS_UNSUPPORTED'):
                probe.resolve_addresses(Candidate('target.example', 443, 'cover.example'))


class RealityRuntimeTests(unittest.TestCase):
    def test_environment_uses_process_metadata_without_host_commands(self):
        with patch('platform.uname', side_effect=AssertionError('host platform lookup')),\
                patch('subprocess.Popen', side_effect=AssertionError('external program')):
            details = probe.environment()
        self.assertEqual(details['platform'], sys.platform)
        self.assertEqual(details['implementation'], sys.implementation.name)
        self.assertEqual(details['python'], '.'.join(map(str, sys.version_info[:3])))

    def test_clienthello_offers_only_x25519_and_no_keylog_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            keylog = Path(tmp) / 'must-not-exist'
            with patch.dict(os.environ, {'SSLKEYLOGFILE': str(keylog)}):
                context = probe.create_context()
            self.assertFalse(keylog.exists())
            self.assertIsNone(context.keylog_filename)
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertFalse(context.hostname_checks_common_name)
            incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
            client = context.wrap_bio(incoming, outgoing, server_hostname='cover.example')
            with self.assertRaises(ssl.SSLWantReadError):
                client.do_handshake()
            record = outgoing.read()
        self.assertEqual(record[0], 22)
        self.assertEqual(record[5], 1)
        hello = record[9:9 + int.from_bytes(record[6:9], 'big')]
        pos = 34
        pos += 1 + hello[pos]
        pos += 2 + int.from_bytes(hello[pos:pos + 2], 'big')
        pos += 1 + hello[pos]
        end = pos + 2 + int.from_bytes(hello[pos:pos + 2], 'big')
        pos += 2
        extensions = {}
        while pos < end:
            kind = int.from_bytes(hello[pos:pos + 2], 'big')
            length = int.from_bytes(hello[pos + 2:pos + 4], 'big')
            extensions[kind] = hello[pos + 4:pos + 4 + length]
            pos += 4 + length
        self.assertEqual(extensions[10], b'\x00\x02\x00\x1d')
        self.assertEqual(extensions[51][2:6], b'\x00\x1d\x00\x20')
        self.assertEqual(len(extensions[51]), 38)

    def test_unsupported_environment_does_not_silently_fallback(self):
        with patch.object(ssl, 'OPENSSL_VERSION_INFO', (1, 1, 1, 0, 0)):
            with self.assertRaisesRegex(ProbeError, 'PROBE_RUNTIME_UNSUPPORTED'):
                probe.create_context()

    def test_failed_connection_is_incomplete_not_no_tls_support(self):
        with patch.object(socket, 'socket', side_effect=OSError('SECRET network details')):
            result = probe.probe_address(Candidate('192.0.2.1', 443, 'cover.example'), '192.0.2.1', 1, object())
        self.assertEqual((result['status'], result['code']), ('INCOMPLETE', 'TCP_CONNECT_FAILED'))
        self.assertNotIn('SECRET', json.dumps(result))

    def test_closed_handshake_is_incomplete(self):
        for error in (ssl.SSLEOFError('SECRET EOF'), ssl.SSLZeroReturnError('SECRET close_notify'),
                      ConnectionResetError('SECRET reset')):
            with self.subTest(error=type(error).__name__), patch.object(socket, 'socket') as factory:
                factory.return_value.__enter__.return_value.getpeername.return_value = ('192.0.2.1', 443)
                context = Mock()
                context.wrap_socket.side_effect = error
                result = probe.probe_address(Candidate('192.0.2.1', 443, 'cover.example'),
                                             '192.0.2.1', 1, context)
            self.assertEqual((result['status'], result['code']), ('INCOMPLETE', 'TLS_CONNECTION_FAILED'))
            self.assertEqual(result['connected_ip'], '192.0.2.1')
            self.assertNotIn('tls_version', result)
            self.assertNotIn('certificate_verified', result)
            self.assertNotIn('SECRET', json.dumps(result))
            self.assertIn('finished_at', result)

    def test_tls_alert_is_baseline_failure(self):
        with patch.object(socket, 'socket') as factory:
            factory.return_value.__enter__.return_value.getpeername.return_value = ('192.0.2.1', 443)
            context = Mock()
            context.wrap_socket.side_effect = ssl.SSLError(ssl.SSL_ERROR_SSL, 'SECRET protocol alert')
            result = probe.probe_address(Candidate('192.0.2.1', 443, 'cover.example'),
                                         '192.0.2.1', 1, context)
        self.assertEqual((result['status'], result['code']), ('BASELINE_FAIL', 'TLS_HANDSHAKE_FAILED'))
        self.assertNotIn('SECRET', json.dumps(result))


class RealityCommandTests(unittest.TestCase):
    def test_success_and_baseline_failure_exit_codes(self):
        with patch.object(probe, 'create_context', return_value=object()), patch.object(probe, 'probe_address', side_effect=passed):
            report, code = commands.run(args())
        self.assertEqual(code, 0)
        self.assertEqual(report['connections_attempted'], 1)
        self.assertEqual(report['completed_rows'], 1)
        self.assertEqual(report['pending_rows'], 0)
        self.assertEqual(report['http'], 'NOT_TESTED')
        self.assertEqual(report['reality'], 'NOT_TESTED')
        with patch.object(probe, 'create_context', return_value=object()), patch.object(probe, 'probe_address', return_value={'status':'BASELINE_FAIL'}):
            self.assertEqual(commands.run(args())[1], 1)

    def test_invalid_options_fail_before_network(self):
        for options in (dict(sni=None), dict(target='', sni=None), dict(port='8443'), dict(timeout='nan'), dict(timeout='inf'),
                        dict(timeout='0'), dict(timeout='31'), dict(timeout='bad'),
                        dict(target=None, from_scanner='missing', sni='cover.example')):
            with self.subTest(options=options), patch.object(probe, 'create_context', side_effect=AssertionError('network setup')):
                self.assertEqual(commands.run(args(**options))[1], 2)

    def test_scanner_port_is_explicit_and_bad_rows_do_not_connect(self):
        raw = scanner_csv([('192.0.2.1', '192.0.2.1', '*.example'), ('192.0.2.2', 'cover.example', 'other.example')])
        for value, expected, source in ((None, 443, 'scanner_cli_default'), ('8443', 8443, 'scanner_cli_override')):
            with self.subTest(port=value), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'candidates.csv'
                path.write_bytes(raw)
                with patch.object(probe, 'create_context', return_value=object()), patch.object(probe, 'probe_address', side_effect=passed) as connect:
                    report, code = commands.run(args(target=None, sni=None, from_scanner=str(path), port=value))
                self.assertEqual(code, 1)
                self.assertEqual(report['port_source'], source)
                self.assertEqual(connect.call_count, 1)
                self.assertEqual(connect.call_args.args[0].port, expected)
                self.assertEqual(connect.call_args.args[1], '192.0.2.2')

    def test_bad_csv_tail_prevents_earlier_valid_rows_from_connecting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'candidates.csv'
            path.write_bytes(scanner_csv([('192.0.2.1', 'cover.example', '')]) + b'bad,tail\n')
            with patch.object(probe, 'create_context', side_effect=AssertionError('must not begin')):
                report, code = commands.run(args(target=None, sni=None, from_scanner=str(path)))
            self.assertEqual((code, report['code']), (2, 'SCANNER_ROW_SHAPE'))

    def test_host_budget_is_incomplete_and_preserves_both_address_families(self):
        addresses = ['192.0.2.1', '2001:db8::1'] + [f'192.0.2.{i}' for i in range(2, 18)]
        with patch.object(probe, 'create_context', return_value=object()), patch.object(probe, 'resolve_addresses', return_value=addresses), patch.object(probe, 'probe_address', side_effect=passed) as connect:
            report, code = commands.run(args(target='target.example:443'))
        self.assertEqual((code, connect.call_count), (1, 16))
        self.assertEqual(report['results'][-1]['unprobed_count'], 2)
        self.assertEqual(report['results'][1]['address'], '2001:db8::1')

    def test_connection_budget_stops_connecting_and_reports_remaining_rows(self):
        rows = [Candidate(f'192.0.2.{i}', 443, 'cover.example', i + 1) for i in range(1, 4)]
        with patch.object(commands, 'read_scanner', return_value=rows), patch.object(probe, 'create_context', return_value=object()), patch.object(probe, 'MAX_CONNECTIONS', 2), patch.object(probe, 'probe_address', side_effect=passed) as connect:
            report, code = commands.run(args(target=None, sni=None, from_scanner='unused'))
        self.assertEqual((code, connect.call_count), (1, 2))
        self.assertEqual(report['completed_rows'], 3)
        self.assertEqual(report['results'][-1]['source_row'], 4)
        self.assertEqual(report['results'][-1]['unprobed_count'], 1)

    def test_dns_failure_remains_incomplete(self):
        with patch.object(probe, 'create_context', return_value=object()), patch.object(probe, 'resolve_addresses', side_effect=socket.gaierror('private details')):
            report, code = commands.run(args(target='target.example:443'))
        self.assertEqual((code, report['results'][0]['code']), (1, 'DNS_LOOKUP_FAILED'))
        self.assertNotIn('private details', json.dumps(report))

    def test_cancel_preserves_completed_rows_and_counts_current_as_pending(self):
        rows = [Candidate('192.0.2.1', 443, 'cover.example', 2), Candidate('192.0.2.2', 443, 'cover.example', 3)]
        with patch.object(commands, 'read_scanner', return_value=rows), patch.object(probe, 'create_context', return_value=object()), patch.object(probe, 'probe_address', side_effect=[{'status':'BASELINE_PASS'}, KeyboardInterrupt()]):
            report, code = commands.run(args(target=None, sni=None, from_scanner='unused'))
        self.assertEqual(code, 130)
        self.assertEqual((report['completed_rows'], report['pending_rows'], report['connections_attempted']), (1, 1, 2))
        self.assertEqual(len(report['results']), 1)

    def test_runtime_error_is_not_remote_failure(self):
        with patch.object(probe, 'create_context', side_effect=ProbeError('PROBE_RUNTIME_UNSUPPORTED')):
            report, code = commands.run(args())
        self.assertEqual(code, 2)
        self.assertEqual(report['connections_attempted'], 0)
        self.assertEqual(report['results'], [])

    def test_cli_routes_and_help_do_not_probe_or_call_traffic(self):
        for arguments in (['reality'], ['reality', 'probe', '--target', '192.0.2.1:443'],
                          ['reality', 'probe', '--tar', '192.0.2.1:443', '--sni', 'cover.example']):
            with self.subTest(argv=arguments), patch.object(probe, 'create_context', side_effect=AssertionError('unexpected probe')), patch('sys.stdout', new_callable=io.StringIO) as output:
                code = main(arguments)
            self.assertIn(code, (0, 2))
            self.assertIsInstance(json.loads(output.getvalue()), dict)
        with patch.object(probe, 'create_context', side_effect=AssertionError('traffic imported probe')), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(main(['traffic', 'validate-config', '--config', str(ROOT / 'tests/traffic/fixtures/3.7.0/config.json')]), 0)
