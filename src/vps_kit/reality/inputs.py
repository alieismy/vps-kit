"""候选表示边界；扫描器输出只是线索，不是已验证配置。"""
import csv
from dataclasses import dataclass
import io
import ipaddress
import os
import re
import stat


MAX_ROWS = 100
MAX_FILE_BYTES = 256 * 1024
SCANNER_HEADER = 'IP,ORIGIN,TLS,ALPN,CURVE,CERT_LENGTH,CERT_SIGNATURE,CERT_PUBLICKEY,CERT_DOMAIN,CERT_ISSUER,GEO_CODE'.split(',')


class ProbeError(Exception):
    """仅携带稳定错误代码，不回显原始输入。"""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Candidate:
    target_host: str
    port: int
    sni: str
    source_row: int | None = None
    sni_source: str = 'explicit'


@dataclass(frozen=True)
class RejectedRow:
    source_row: int
    code: str


def dns_name(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ProbeError('DNS_NAME_INVALID')
    try:
        name = value.removesuffix('.').encode('idna').decode('ascii').lower()
    except UnicodeError:
        raise ProbeError('DNS_NAME_INVALID') from None
    labels = name.split('.')
    if (len(name) > 253 or len(labels) < 2 or name.replace('.', '').isdigit()
            or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label) for label in labels)):
        raise ProbeError('DNS_NAME_INVALID')
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return name
    raise ProbeError('DNS_NAME_INVALID')


def ip_literal(value):
    # 不接受带 scope 的 IPv6：报告必须能直接标识本次连接的地址。
    if '%' in value:
        raise ProbeError('IP_ADDRESS_INVALID')
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        raise ProbeError('IP_ADDRESS_INVALID') from None


def port_number(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,5}', value) or not 1 <= int(value) <= 65535:
        raise ProbeError('PORT_INVALID')
    return int(value)


def single_candidate(target, sni):
    if target.startswith('['):
        match = re.fullmatch(r'\[([^\[\]]+)\]:([0-9]+)', target)
        if not match:
            raise ProbeError('TARGET_INVALID')
        host, port = match.groups()
        host = ip_literal(host)
        if ':' not in host:
            raise ProbeError('TARGET_INVALID')
    else:
        if target.count(':') != 1:
            raise ProbeError('TARGET_INVALID')
        host, port = target.split(':')
        try:
            host = ip_literal(host)
        except ProbeError:
            host = dns_name(host)
    return Candidate(host, port_number(port), dns_name(sni))


def scanner_rows(raw, port):
    if len(raw) > MAX_FILE_BYTES:
        raise ProbeError('SCANNER_FILE_SIZE')
    try:
        reader = csv.reader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
        if next(reader, None) != SCANNER_HEADER:
            raise ProbeError('SCANNER_HEADER_V023_REQUIRED')
        rows = []
        while True:
            source_row = reader.line_num + 1
            row = next(reader, None)
            if row is None:
                break
            if len(row) != len(SCANNER_HEADER):
                raise ProbeError('SCANNER_ROW_SHAPE')
            if len(rows) >= MAX_ROWS:
                raise ProbeError('SCANNER_ROW_LIMIT')
            try:
                address = ip_literal(row[0])
            except ProbeError:
                rows.append(RejectedRow(source_row, 'SCANNER_IP_INVALID'))
                continue
            try:
                name, origin = dns_name(row[1]), 'scanner_origin'
            except ProbeError:
                try:
                    ipaddress.ip_network(row[1], strict=False)
                except ValueError:
                    rows.append(RejectedRow(source_row, 'SCANNER_ORIGIN_INVALID'))
                    continue
                try:
                    name, origin = dns_name(row[8]), 'scanner_certificate_cn'
                except ProbeError:
                    rows.append(RejectedRow(source_row, 'SPECIFIC_SNI_REQUIRED'))
                    continue
            rows.append(Candidate(address, port, name, source_row, origin))
        if not rows:
            raise ProbeError('SCANNER_NO_ROWS')
        return rows
    except (UnicodeError, csv.Error):
        raise ProbeError('SCANNER_CSV_INVALID') from None


def read_scanner(path, port):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                raise ProbeError('SCANNER_FILE_SIZE_OR_TYPE')
            return scanner_rows(stream.read(MAX_FILE_BYTES + 1), port)
    except OSError:
        raise ProbeError('SCANNER_FILE_READ') from None
