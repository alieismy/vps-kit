"""原生 v1 配置契约。解析无写入，不接受 legacy 配置冒充原生配置。"""
import calendar
import ipaddress
import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from datetime import timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import (ContractError, MAX_INT, boolean, canonical, digest,
                        fields, integer, json_load, require, text, utc)

SCHEMA = "vps-kit.traffic.config/v1"
SCHEMA_V2 = "vps-kit.traffic.config/v2"
FIXED_SECONDS = 2592000
VERSIONS = ("3.7.0", "3.10.2")
EMAIL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@+-]{0,127}\Z")


def uuid_value(value):
    try:
        require(type(value) is str and str(uuid.UUID(value)) == value, "CONFIG_UUID")
    except (ValueError, AttributeError):
        raise ContractError("CONFIG_UUID") from None


def panel_url(panel):
    fields(panel, "version url connect_address allow_loopback_http", "CONFIG_PANEL_FIELDS")
    require(panel["version"] in VERSIONS, "PANEL_VERSION_UNSUPPORTED")
    raw = text(panel["url"], "CONFIG_URL", 1024)
    require(raw.isascii() and not any(c.isspace() for c in raw), "CONFIG_URL")
    try:
        u = urlsplit(raw)
        require(u.scheme in ("http", "https") and u.hostname and u.username is None and u.password is None
                and not u.query and not u.fragment and "?" not in raw and "#" not in raw,
                "CONFIG_URL")
        require(not u.netloc.endswith(":") and (u.port is None or 1 <= u.port <= 65535), "CONFIG_URL_PORT")
    except ValueError:
        raise ContractError("CONFIG_URL") from None
    # base path 不接受编码分隔符或点段；curl 的 URL globbing 另行关闭。
    require(re.fullmatch(r"(?:/[A-Za-z0-9_-]+)*", u.path) is not None, "CONFIG_URL_PATH")
    require(re.fullmatch(r"[A-Za-z0-9.-]+|::1", u.hostname) is not None, "CONFIG_URL_HOST")
    require(panel["connect_address"] in ("127.0.0.1", "::1"), "CONFIG_LOOPBACK")
    boolean(panel["allow_loopback_http"], "CONFIG_HTTP_OPT_IN")
    try:
        ip = ipaddress.ip_address(u.hostname)
    except ValueError:
        ip = None
    if ip is not None:
        require(str(ip) == panel["connect_address"], "CONFIG_IP_TARGET")
    else:
        # curl 会把单整数、八/十六进制和缩写 IPv4 规范化；不能按普通 DNS 名放行。
        numeric = r"(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:\.(?:0[xX][0-9a-fA-F]+|[0-9]+)){0,3}\.?"
        require(re.fullmatch(numeric, u.hostname) is None, "CONFIG_URL_NUMERIC_HOST")
        labels = u.hostname.removesuffix(".").split(".")
        require(len(u.hostname) <= 253 and all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                                              for label in labels), "CONFIG_URL_HOST")
    if u.scheme == "http":
        require(panel["allow_loopback_http"] and u.hostname == panel["connect_address"],
                "CONFIG_HTTP_LOOPBACK_ONLY")
    return u


def schedule_validate(schedule):
    require(type(schedule) is dict, 'CONFIG_SCHEDULE_FIELDS')
    kind = schedule.get('kind', 'monthly-calendar')
    require(kind in ('monthly-calendar', 'fixed-interval'), 'CONFIG_SCHEDULE_KIND')
    zone = text(schedule.get('timezone'), "CONFIG_TIMEZONE", 100)
    require(zone == "UTC" or ("/" in zone and not zone.startswith("/") and ".." not in zone
                             and re.fullmatch(r"[A-Za-z0-9_+/-]+", zone)), "CONFIG_TIMEZONE")
    try:
        tz = ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ContractError("TIMEZONE_DATA_OR_NAME") from None
    first = utc(schedule.get('first_boundary_utc'), 'CONFIG_FIRST_BOUNDARY')
    if kind == 'fixed-interval':
        fields(schedule, 'kind anchor_utc interval_seconds timezone first_boundary_utc', 'CONFIG_SCHEDULE_FIELDS')
        integer(schedule['interval_seconds'], 'CONFIG_INTERVAL', FIXED_SECONDS, FIXED_SECONDS)
        anchor = utc(schedule['anchor_utc'], 'CONFIG_ANCHOR')
        # UTC 整秒的唯一表示；保留旧配置的原格式，不在读取历史时补写字段。
        for key in ('anchor_utc', 'first_boundary_utc'):
            require(type(schedule[key]) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', schedule[key]), 'CONFIG_FIXED_TIMESTAMP')
        delta = first - anchor
        require(delta >= timedelta(0) and delta % timedelta(seconds=FIXED_SECONDS) == timedelta(0), 'CONFIG_BOUNDARY_ALIGNMENT')
        return
    fields(schedule, "day short_month time timezone first_boundary_utc" + (' kind' if 'kind' in schedule else ''), "CONFIG_SCHEDULE_FIELDS")
    day = schedule["day"]
    if day != "last":
        integer(day, "CONFIG_DAY", 1, 31)
    policy = schedule["short_month"]
    require(policy in ("skip", "clamp-last-day", "not-applicable"), "CONFIG_SHORT_MONTH")
    require((day == "last" or day <= 28) == (policy == "not-applicable"), "CONFIG_SHORT_MONTH")
    require(type(schedule["time"]) is str and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d", schedule["time"]),
            "CONFIG_TIME")
    try:
        local = first.astimezone(tz)
    except (ValueError, OverflowError):
        raise ContractError("CONFIG_BOUNDARY_RANGE") from None
    last = calendar.monthrange(local.year, local.month)[1]
    target_day = last if day == "last" else min(day, last) if policy == "clamp-last-day" else day
    require(local.day == target_day and local.strftime("%H:%M:%S") == schedule["time"]
            and local.microsecond == 0, "CONFIG_BOUNDARY_ALIGNMENT")
    # 不在 P1 选择 DST 重叠的哪一个实例；模糊的首次边界显式拒绝。
    wall = local.replace(tzinfo=None)
    require(wall.replace(tzinfo=tz, fold=0).utcoffset() == wall.replace(tzinfo=tz, fold=1).utcoffset(),
            "CONFIG_BOUNDARY_DST_AMBIGUOUS")


@dataclass(frozen=True)
class Config:
    """仅通过 parse_config 构建；规范化快照避免调用方意外修改已校验值。"""
    canonical_json: str

    @property
    def data(self):
        return json_load(self.canonical_json)

    @property
    def fingerprint(self):
        return digest(self.data)

    @property
    def object_fingerprint(self):
        d = self.data
        i = d["inbound"]
        # 排除任务名、显示标签、额度、修订、日历；修改它们不能更换对象身份。
        return digest({"host": d["host_id_sha256"], "panel": d["panel_binding_id"],
                       "inbound": {k: i[k] for k in ("id", "listen", "port", "protocol")},
                       "clients": sorted([[c["record_id"], c["uuid"]] for c in d["clients"]])})

    def summary(self):
        d = self.data
        return {"schema": d['schema_version'], "panel_version": d["panel"]["version"], "revision": d["revision"],
                "client_count": len(d["clients"]), "inbound_quota_bytes": d["inbound"]["quota_bytes"],
                "client_quotas": [{"index": n + 1, "quota_bytes": c["quota_bytes"], "policy": c["policy"]}
                                  for n, c in enumerate(d["clients"])],
                "provider_budget": d["provider"], "schedule": d["schedule"],
                "validation_scope": "CONFIG_CONTRACT_ONLY", "timer_readiness": "NOT_ASSESSED"}


def parse_config(raw):
    d = json_load(raw)
    fields(d, "schema_version task_id revision host_id_sha256 panel_binding_id panel inbound clients schedule provider",
           "CONFIG_FIELDS")
    require(d["schema_version"] in (SCHEMA, SCHEMA_V2), "CONFIG_SCHEMA")
    require(type(d['schedule']) is dict and (('kind' in d['schedule']) == (d['schema_version'] == SCHEMA_V2)), 'CONFIG_SCHEDULE_VERSION')
    uuid_value(d["task_id"])
    uuid_value(d["panel_binding_id"])
    integer(d["revision"], "CONFIG_REVISION", 1)
    require(type(d["host_id_sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", d["host_id_sha256"]), "CONFIG_HOST_BINDING")
    panel_url(d["panel"])
    inbound = fields(d["inbound"], "id remark listen port protocol quota_bytes policy", "CONFIG_INBOUND_FIELDS")
    integer(inbound["id"], "CONFIG_INBOUND_ID", 1)
    text(inbound["remark"], "CONFIG_REMARK")
    listen = text(inbound["listen"], "CONFIG_LISTEN", 100, empty=True)
    if listen:
        try:
            ipaddress.ip_address(listen)
        except ValueError:
            raise ContractError("CONFIG_LISTEN") from None
    integer(inbound["port"], "CONFIG_PORT", 1, 65535)
    require(inbound["protocol"] == "vless", "CONFIG_PROTOCOL")
    integer(inbound["quota_bytes"], "CONFIG_INBOUND_QUOTA", 1)
    require(inbound["policy"] in ("stop", "quota-recovery"), "CONFIG_INBOUND_POLICY")
    require(type(d["clients"]) is list and 1 <= len(d["clients"]) <= 16, "CONFIG_CLIENT_COUNT")
    for c in d["clients"]:
        fields(c, "record_id uuid email quota_bytes policy", "CONFIG_CLIENT_FIELDS")
        integer(c["record_id"], "CONFIG_CLIENT_ID", 1)
        uuid_value(c["uuid"])
        require(type(c["email"]) is str and EMAIL.fullmatch(c["email"]), "CONFIG_EMAIL")
        integer(c["quota_bytes"], "CONFIG_CLIENT_QUOTA")
        require(c["policy"] in ("stop", "quota-recovery", "hold"), "CONFIG_CLIENT_POLICY")
        require(c["policy"] != "quota-recovery" or c["quota_bytes"] > 0, "CONFIG_RECOVERY_UNLIMITED")
    for k in ("record_id", "uuid", "email"):
        require(len({c[k] for c in d["clients"]}) == len(d["clients"]), "CONFIG_CLIENT_DUPLICATE")
    require(any(c["policy"] != "hold" for c in d["clients"]), "CONFIG_NO_MANAGED_CLIENT")
    d["clients"].sort(key=lambda c: c["record_id"])
    schedule_validate(d["schedule"])
    p = d["provider"]
    if p is not None:
        fields(p, "amount unit model reserve_percent other_reserved_bytes", "CONFIG_PROVIDER_FIELDS")
        require(type(p["amount"]) is str and re.fullmatch(r"[1-9]\d{0,12}(?:\.\d{1,9})?", p["amount"]), "CONFIG_PROVIDER_AMOUNT")
        require(p["unit"] in ("GB", "GiB"), "CONFIG_PROVIDER_UNIT")
        require(p["model"] in ("manual", "proxy-duplex-2"), "CONFIG_PROVIDER_MODEL")
        integer(p["reserve_percent"], "CONFIG_RESERVE", 0, 90)
        integer(p["other_reserved_bytes"], "CONFIG_OTHER_RESERVE")
        byte_count = Decimal(p["amount"]) * (10**9 if p["unit"] == "GB" else 1024**3)
        require(byte_count == int(byte_count) and 0 < byte_count <= MAX_INT, "CONFIG_PROVIDER_BYTES")
        budget = byte_count * (100 - p["reserve_percent"]) / 100 - p["other_reserved_bytes"]
        require(budget > 0, "CONFIG_BUDGET")
        if p["model"] == "proxy-duplex-2":
            require(inbound["quota_bytes"] * 2 <= budget, "CONFIG_BUDGET_EXCEEDED")
    return Config(canonical(d))
