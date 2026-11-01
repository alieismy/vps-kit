"""直接连接固定地址的 TLS 基线；没有 HTTP、代理或 REALITY 协议实现。"""
import ipaddress
import socket
import ssl
import sys
import time
from datetime import datetime, timezone

from .inputs import ProbeError


MAX_ADDRESSES = 16
MAX_CONNECTIONS = 256


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def environment():
    # platform.system() 在 Windows 可能退回执行 ver；这里只读取进程已有元数据。
    return {'platform': sys.platform, 'python': '.'.join(map(str, sys.version_info[:3])),
            'implementation': sys.implementation.name, 'openssl': ssl.OPENSSL_VERSION,
            'network_path': 'CURRENT_OS_ROUTE', 'http': 'NOT_TESTED', 'reality': 'NOT_TESTED'}


def create_context():
    if (sys.implementation.name != 'cpython' or sys.version_info < (3, 11)
            or ssl.OPENSSL_VERSION_INFO[0] != 3 or not ssl.HAS_TLSv1_3 or not ssl.HAS_ALPN):
        raise ProbeError('PROBE_RUNTIME_UNSUPPORTED')
    try:
        # 不使用会读取 SSLKEYLOGFILE 并写出会话密钥的 create_default_context。
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_default_certs(ssl.Purpose.SERVER_AUTH)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        # 上游 CPython 3.11.4+ 的 OpenSSL 3 路径使用 SSL_CTX_set1_groups；
        # 发行版可能回移植支持，以实际设置结果为准，不依赖私有 SSL 属性。
        # 配套回环测试必须证明只有 X25519 的服务可通过，而 P-256-only 被拒绝。
        context.set_ecdh_curve('X25519')
        context.set_alpn_protocols(['h2', 'http/1.1'])
        context.hostname_checks_common_name = False
        return context
    except (ValueError, ssl.SSLError, NotImplementedError, OSError):
        raise ProbeError('PROBE_TLS_CAPABILITY_UNAVAILABLE') from None


def resolve_addresses(candidate):
    try:
        ipaddress.ip_address(candidate.target_host)
    except ValueError:
        records = socket.getaddrinfo(candidate.target_host, candidate.port, socket.AF_UNSPEC, socket.SOCK_STREAM)
        addresses = []
        for family, _, _, _, address in records:
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            # scoped IPv6 不在首期表示范围，不能静默去掉 scope 后改连别处。
            if family == socket.AF_INET6 and address[3]:
                raise ProbeError('SCOPED_DNS_ADDRESS_UNSUPPORTED')
            value = str(ipaddress.ip_address(address[0]))
            if value not in addresses:
                addresses.append(value)
        if not addresses:
            raise ProbeError('DNS_NO_ADDRESSES')
        return addresses
    return [candidate.target_host]


def candidate_result(candidate):
    return {'target_host': candidate.target_host, 'port': candidate.port, 'sni': candidate.sni,
            'source_row': candidate.source_row, 'sni_source': candidate.sni_source}


def probe_address(candidate, address, timeout, context):
    result = {**candidate_result(candidate), 'address': address, 'started_at': timestamp(),
              'status': 'INCOMPLETE', 'http': 'NOT_TESTED', 'reality': 'NOT_TESTED'}
    ip = ipaddress.ip_address(address)
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    destination = (address, candidate.port, 0, 0) if ip.version == 6 else (address, candidate.port)
    phase = 'tcp'
    started = time.monotonic()
    try:
        with socket.socket(family, socket.SOCK_STREAM) as connection:
            connection.settimeout(timeout)
            connection.connect(destination)
            result['connected_ip'] = str(ipaddress.ip_address(connection.getpeername()[0]))
            result['tcp_ms'] = round((time.monotonic() - started) * 1000, 3)
            phase = 'tls'
            started = time.monotonic()
            # 始终新建完整握手，不把别处的解析/证书或恢复会话当成本次证据。
            with context.wrap_socket(connection, server_hostname=candidate.sni) as tls:
                result['tls_ms'] = round((time.monotonic() - started) * 1000, 3)
                result.update(tls_version=tls.version(), alpn=tls.selected_alpn_protocol(),
                              certificate_verified=True,
                              certificate_not_after=tls.getpeercert().get('notAfter'),
                              key_exchange_check='X25519_ONLY_FULL_HANDSHAKE')
                if result['tls_version'] != 'TLSv1.3' or tls.session_reused:
                    result.update(status='INCOMPLETE', code='TLS_BASELINE_NOT_CONFIRMED')
                elif result['alpn'] != 'h2':
                    result.update(status='BASELINE_FAIL', code='ALPN_NOT_H2')
                else:
                    result.update(status='BASELINE_PASS', code='BASELINE_MATCH')
    except ssl.SSLCertVerificationError as error:
        # 仅报告验证失败；不能据此归因为目标证书错误，亦可能是本机信任库/时间问题。
        result.update(status='BASELINE_FAIL', code='CERTIFICATE_VERIFY_FAILED', verify_code=error.verify_code)
    except (TimeoutError, socket.timeout):
        result.update(code='TCP_TIMEOUT' if phase == 'tcp' else 'TLS_TIMEOUT')
    except (ssl.SSLEOFError, ssl.SSLZeroReturnError):
        # 握手提前关闭没有取得完整基线证据，不能据此判定目标不兼容。
        result.update(code='TLS_CONNECTION_FAILED')
    except ssl.SSLError:
        result.update(status='BASELINE_FAIL', code='TLS_HANDSHAKE_FAILED')
    except OSError:
        result.update(code='TCP_CONNECT_FAILED' if phase == 'tcp' else 'TLS_CONNECTION_FAILED')
    result['finished_at'] = timestamp()
    return result
