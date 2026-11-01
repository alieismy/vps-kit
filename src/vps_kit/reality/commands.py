"""有限候选串行检查；报告未完成项，运行期不调用外部程序。"""
import math

from .inputs import MAX_ROWS, ProbeError, RejectedRow, port_number, read_scanner, single_candidate


def add_arguments(tools):
    parser = tools.add_parser('reality', help='REALITY 候选的 TLS 基线探测')
    actions = parser.add_subparsers(dest='action')
    probe = actions.add_parser('probe', help='单目标或 RealiTLScanner v0.2.3 CSV 配对复验；将发送 TCP/TLS')
    source = probe.add_mutually_exclusive_group(required=True)
    source.add_argument('--target', help='HOST:PORT，IPv6 使用 [IP]:PORT')
    source.add_argument('--from-scanner', metavar='FILE', help='v0.2.3 CSV，不执行扫描器')
    probe.add_argument('--sni', help='单目标的具体 DNS 名称，不接受 IP/通配符')
    probe.add_argument('--port', help='仅 CSV 输入；扫描端口，默认 443（CSV 不含端口）')
    probe.add_argument('--timeout', default='5', help='每次 TCP/TLS 阶段超时秒数，0.1–30；不含 OS DNS 等待')


def run(args):
    report = {'result': 'FAIL', 'schema': 'vps-kit.reality-probe/v1', 'results': [],
              'http': 'NOT_TESTED', 'reality': 'NOT_TESTED', 'script_writes': 0,
              'automatic_retry': False, 'connections_attempted': 0, 'input_rows': 0,
              'completed_rows': 0, 'pending_rows': 0}
    try:
        if args.target is not None:
            if not args.sni or args.port is not None:
                raise ProbeError('PROBE_ARGUMENTS')
            rows = [single_candidate(args.target, args.sni)]
            report['port_source'] = 'explicit_target'
        else:
            if args.sni is not None:
                raise ProbeError('PROBE_ARGUMENTS')
            port = port_number(args.port) if args.port is not None else 443
            rows = read_scanner(args.from_scanner, port)
            report['port_source'] = 'scanner_cli_override' if args.port is not None else 'scanner_cli_default'
        try:
            timeout = float(args.timeout)
        except ValueError:
            raise ProbeError('PROBE_TIMEOUT_INVALID') from None
        if not math.isfinite(timeout) or not 0.1 <= timeout <= 30:
            raise ProbeError('PROBE_TIMEOUT_INVALID')
        report.update(input_rows=len(rows), pending_rows=len(rows), timeout_seconds=timeout)
        # ssl 的缺失/不兼容不能影响原有 traffic 命令或离线参数帮助。
        try:
            from . import probe
        except ImportError:
            raise ProbeError('PROBE_SSL_UNAVAILABLE') from None
        report.update(environment=probe.environment(), started_at=probe.timestamp(),
                      baseline='TLS1.3_X25519_H2_TRUSTED_SAN',
                      limits={'rows': MAX_ROWS, 'addresses_per_host': probe.MAX_ADDRESSES,
                              'connections': probe.MAX_CONNECTIONS})
        context = probe.create_context()
        for row in rows:
            if isinstance(row, RejectedRow):
                report['results'].append({'source_row': row.source_row, 'status': 'INCOMPLETE', 'code': row.code})
            else:
                try:
                    addresses = probe.resolve_addresses(row)
                except (OSError, ProbeError) as error:
                    report['results'].append({**probe.candidate_result(row), 'status': 'INCOMPLETE',
                                              'code': error.code if isinstance(error, ProbeError) else 'DNS_LOOKUP_FAILED'})
                else:
                    tested = 0
                    for address in addresses[:probe.MAX_ADDRESSES]:
                        if report['connections_attempted'] >= probe.MAX_CONNECTIONS:
                            break
                        report['connections_attempted'] += 1
                        report['results'].append(probe.probe_address(row, address, timeout, context))
                        tested += 1
                    # 预算耗尽仍逐行报告未覆盖范围，不把已通过的地址泛化为整个域名通过。
                    if tested < len(addresses):
                        report['results'].append({**probe.candidate_result(row), 'status': 'INCOMPLETE',
                                                  'code': 'ADDRESS_BUDGET_EXHAUSTED',
                                                  'unprobed_count': len(addresses) - tested,
                                                  'unprobed_address_sample': addresses[tested:tested + 16]})
            report['completed_rows'] += 1
            report['pending_rows'] -= 1
        report.update(result='PROBE_COMPLETE', finished_at=probe.timestamp())
        return report, 0 if report['results'] and all(item['status'] == 'BASELINE_PASS' for item in report['results']) else 1
    except ProbeError as error:
        report.update(code=error.code, raw_input_hidden=True)
        return report, 2
    except KeyboardInterrupt:
        report.update(result='CANCELLED', code='PROBE_CANCELLED')
        return report, 130
