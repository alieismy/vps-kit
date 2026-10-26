"""已知 r2 / V2.1.2 数据格式的独立只读解释器；绝不导入或执行旧 runner。"""
import configparser
from datetime import datetime, timezone, timedelta
import hashlib
import re
from zoneinfo import ZoneInfo

from .calendar import boundary, next_boundary, stamp, walk
from .contracts import ContractError, boolean, digest, fields, integer, json_load, require, utc
from .panel import recovery

R2_HASH = '68f57acf038936a3e9292b7f24920803932abd519ad8205ce993e92d21c3d877'
V212_HASH = '8ea17a5bee2a402627a62b63a115aa7df2ec1ee6f46927b325b5cbb3e5e116e8'
PROFILES = {'Basic': (500, '15:44:25'), 'Core': (1000, '17:41:14')}
RUNNER = '/usr/local/sbin/vmiss-3xui-traffic-reset'
CONFIG = '/etc/vmiss-traffic-reset/reset.json'
TOKEN = '/etc/vmiss-traffic-reset/api-token'
STATE = '/var/lib/vmiss-traffic-reset'
TIMER = 'vmiss-3xui-traffic-reset.timer'
SERVICE = 'vmiss-3xui-traffic-reset.service'
RETIRED = b'#!/bin/sh\nprintf "%s\\n" "VMISS_RUNNER_RETIRED; USE_VPS_KIT_AUDIT; RESET_REFUSED" >&2\nexit 78\n'


def raw_hash(raw):
    return hashlib.sha256(raw).hexdigest()


def time_value(value):
    require(type(value) is str, 'LEGACY_TIMESTAMP')
    try:
        v = datetime.fromisoformat(value)
    except ValueError:
        require(False, 'LEGACY_TIMESTAMP')
    require(v.tzinfo is not None, 'LEGACY_TIMESTAMP')
    return v.astimezone(timezone.utc)


def schedule(cfg):
    require(type(cfg.get('instance')) is str and cfg['instance'] in PROFILES, 'LEGACY_INSTANCE')
    require(type(cfg.get('first_cycle')) is str and re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', cfg['first_cycle']), 'LEGACY_FIRST')
    s = {'day': 22, 'time': PROFILES[cfg['instance']][1], 'timezone': 'America/Toronto', 'short_month': 'not-applicable'}
    year, month = map(int, cfg['first_cycle'].split('-'))
    return {**s, 'first_boundary_utc': stamp(boundary(s, year, month))}


def compatible(cfg, target, schema=2):
    d = target.data
    common = 'schema instance panel_url inbound_listen inbound_port first_cycle machine_id'
    fields(cfg, common + (' provider inbound_quota_bytes inbound_disabled_policy clients' if schema == 2 else ' disabled_policy'), 'LEGACY_CONFIG_FIELDS')
    require(type(cfg['schema']) is int and cfg['schema'] == schema, 'LEGACY_SCHEMA')
    require(d['panel']['version'] == '3.7.0' and cfg['panel_url'] == d['panel']['url'], 'LEGACY_PANEL_MISMATCH')
    require(type(cfg['machine_id']) is str and re.fullmatch(r'[0-9a-f]{32}', cfg['machine_id'])
            and raw_hash(cfg['machine_id'].encode()) == d['host_id_sha256'], 'LEGACY_HOST')
    integer(cfg['inbound_port'], 'LEGACY_PORT', 1, 65535)
    require(cfg['inbound_listen'] == d['inbound']['listen'] and cfg['inbound_port'] == d['inbound']['port']
            and d['inbound']['remark'] == '🇺🇸 LA-Reality', 'LEGACY_IDENTITY')
    s = schedule(cfg)
    if schema == 2:
        fields(cfg['provider'], 'amount unit unit_confirmed accounting proxy_factor reserve_percent', 'LEGACY_PROVIDER')
        p = cfg['provider']
        integer(p['amount'], 'LEGACY_AMOUNT', 1)
        integer(p['proxy_factor'], 'LEGACY_FACTOR', 2, 2)
        boolean(p['unit_confirmed'], 'LEGACY_UNIT_CONFIRMED')
        require(p['amount'] == PROFILES[cfg['instance']][0] and p['unit'] in ('GB', 'GiB')
                and (p['unit_confirmed'] or p['unit'] == 'GB') and p['accounting'] == 'in+out', 'LEGACY_PROVIDER')
        integer(p['reserve_percent'], 'LEGACY_RESERVE', 0, 90)
        require(d['provider'] == {'amount': str(p['amount']), 'unit': p['unit'], 'model': 'proxy-duplex-2',
                'reserve_percent': p['reserve_percent'], 'other_reserved_bytes': 0}, 'MIGRATION_BUDGET_CHANGED')
        integer(cfg['inbound_quota_bytes'], 'LEGACY_QUOTA', 1)
        require(cfg['inbound_quota_bytes'] == d['inbound']['quota_bytes'] and cfg['inbound_disabled_policy'] == d['inbound']['policy'], 'MIGRATION_QUOTA_OR_POLICY_CHANGED')
        expected = sorted([{'email': c['email'], 'quota_bytes': c['quota_bytes'], 'disabled_policy': c['policy']} for c in d['clients']], key=lambda c: c['email'])
        require(cfg['clients'] == expected and s == d['schedule'], 'MIGRATION_CLIENTS_OR_SCHEDULE_CHANGED')
    else:
        require(cfg['disabled_policy'] in ('stop', 'quota-recovery'), 'LEGACY_POLICY')
        require(any(c['email'] == cfg['instance'] for c in d['clients']), 'LEGACY_V1_CLIENT_BINDING')
    return s


def units(cfg, raw_timer, raw_service):
    def parse(raw):
        p = configparser.ConfigParser(interpolation=None, strict=True)
        p.optionxform = str
        try:
            p.read_string(raw.decode())
        except (configparser.Error, UnicodeError):
            raise ContractError('LEGACY_UNIT_FORMAT') from None
        require(not p.defaults(), 'LEGACY_UNIT_DEFAULTS')
        return {s: dict(p[s]) for s in p.sections()}
    timer, service = parse(raw_timer), parse(raw_service)
    require(set(timer) == {'Unit', 'Timer', 'Install'} and set(service) == {'Unit', 'Service'}, 'LEGACY_UNIT_SECTIONS')
    require(set(timer['Unit']) == {'Description'} and bool(timer['Unit']['Description'].strip()), 'LEGACY_DESCRIPTION')
    require(set(service['Unit']) == {'Description', 'Requires', 'After'} and bool(service['Unit']['Description'].strip())
            and service['Unit']['Requires'] == 'x-ui.service' and service['Unit']['After'] == 'x-ui.service time-sync.target', 'LEGACY_DEPENDENCIES')
    require(timer['Timer'] == {'OnCalendar': f"*-*-22 {PROFILES[cfg['instance']][1]} America/Toronto", 'AccuracySec': '1s',
            'RandomizedDelaySec': '0', 'Persistent': 'false', 'Unit': SERVICE} and timer['Install'] == {'WantedBy': 'timers.target'}, 'LEGACY_TIMER')
    expected = {'Type': 'oneshot', 'Restart': 'no', 'TimeoutStartSec': '300s', 'ExecStart': RUNNER + ' --run',
                'LoadCredential': 'api-token:' + TOKEN, 'User': 'root', 'Group': 'root', 'UMask': '0077',
                'RuntimeDirectory': 'vmiss-traffic-reset', 'RuntimeDirectoryMode': '0700', 'RuntimeDirectoryPreserve': 'yes',
                'StateDirectory': 'vmiss-traffic-reset', 'StateDirectoryMode': '0700', 'StandardOutput': 'journal',
                'StandardError': 'journal', 'NoNewPrivileges': 'true', 'PrivateTmp': 'true', 'ProtectSystem': 'strict', 'ProtectHome': 'true'}
    require(service['Service'] == expected, 'LEGACY_SERVICE')


def completed(raw, cfg, target, cycle):
    """校验原生旧证据，不将它重新签发成新执行器的 ledger。"""
    value = json_load(raw)
    require(type(value) is dict and type(value.get('schema')) is int and value['schema'] in (1, 2), 'LEGACY_LEDGER_SCHEMA')
    require(value.get('cycle') == cycle and value.get('binding') == digest(cfg), 'LEGACY_LEDGER_BINDING')
    s = schedule(cfg)
    year, month = map(int, cycle.split('-'))
    edge = boundary(s, year, month)
    require(cycle >= cfg['first_cycle'], 'LEGACY_BEFORE_FIRST')
    d = target.data
    specs = {c['email']: c for c in d['clients']}
    schema = value['schema']
    old_clients = cfg['clients'] if schema == 2 else [{'email': cfg['instance'], 'quota_bytes': PROFILES[cfg['instance']][0] * 1024**3, 'disabled_policy': cfg['disabled_policy']}]
    bindings = {e: digest([c['record_id'], c['uuid']]) for e, c in specs.items()}
    def snap(v, selected):
        if schema == 1:
            fields(v, 'id client_binding inbound_bytes client_bytes inbound_enabled client_enabled traffic_enabled', 'LEGACY_SNAPSHOT')
            require(type(v['id']) is int and v['id'] == d['inbound']['id'] and v['client_binding'] == bindings[cfg['instance']], 'LEGACY_SNAPSHOT_ID')
            result = {'inbound': {'enabled': boolean(v['inbound_enabled'], 'LEGACY_ENABLE'), 'used_bytes': integer(v['inbound_bytes'], 'LEGACY_BYTES')},
                      cfg['instance']: {'enabled': boolean(v['client_enabled'], 'LEGACY_ENABLE'), 'traffic_enabled': boolean(v['traffic_enabled'], 'LEGACY_ENABLE'), 'used_bytes': integer(v['client_bytes'], 'LEGACY_BYTES')}}
        else:
            fields(v, 'inbound clients', 'LEGACY_SNAPSHOT')
            ib = fields(v['inbound'], 'id enabled bytes embedded_binding', 'LEGACY_SNAPSHOT')
            require(type(ib['id']) is int and ib['id'] == d['inbound']['id'] and ib['embedded_binding'] == digest({c['email']: specs[c['email']]['uuid'] for c in old_clients}), 'LEGACY_SNAPSHOT_ID')
            require(type(v['clients']) is dict and set(v['clients']) == set(selected), 'LEGACY_SNAPSHOT_CLIENTS')
            result = {'inbound': {'enabled': boolean(ib['enabled'], 'LEGACY_ENABLE'), 'used_bytes': integer(ib['bytes'], 'LEGACY_BYTES')}}
            for e, c in v['clients'].items():
                fields(c, 'binding record_id enabled traffic_enabled bytes', 'LEGACY_CLIENT_SNAPSHOT')
                require(type(c['record_id']) is int and c['record_id'] == specs[e]['record_id'] and c['binding'] == bindings[e], 'LEGACY_CLIENT_ID')
                result[e] = {'enabled': boolean(c['enabled'], 'LEGACY_ENABLE'), 'traffic_enabled': boolean(c['traffic_enabled'], 'LEGACY_ENABLE'), 'used_bytes': integer(c['bytes'], 'LEGACY_BYTES')}
        return result
    keys = (['client:' + c['email'] for c in old_clients] if schema == 2 else ['client']) + ['inbound', 'enable']
    steps = fields(value.get('steps'), ' '.join(keys), 'LEGACY_STEPS')
    require(all(type(v) is dict and v.get('status') in ('confirmed', 'not_required') for v in steps.values())
            and value.get('completed_at'), 'LEGACY_UNRESOLVED')
    if schema == 1:
        require(value.get('inbound_id') == d['inbound']['id'] and value.get('client_binding') == bindings[cfg['instance']], 'LEGACY_IDENTITY')
        initial = snap(steps['client'].get('before'), [cfg['instance']])
        require(type(value.get('recover_inbound')) is bool and value['recover_inbound'] == (not initial['inbound']['enabled']), 'LEGACY_RECOVERY')
    else:
        require(value.get('instance') == cfg['instance'], 'LEGACY_INSTANCE')
        initial = snap(value.get('initial'), [c['email'] for c in old_clients])
    quota = cfg['inbound_quota_bytes'] if schema == 2 else PROFILES[cfg['instance']][0] * 1024**3
    policy = cfg['inbound_disabled_policy'] if schema == 2 else cfg['disabled_policy']
    recovery(policy, quota, initial['inbound']['used_bytes'], initial['inbound']['enabled'], initial['inbound']['enabled'])
    for c in old_clients:
        v = initial[c['email']]
        recovery(c['disabled_policy'], c['quota_bytes'], v['used_bytes'], v['enabled'], v['traffic_enabled'])
    previous = edge
    for key in keys:
        entry = steps[key]
        email = key[7:] if key.startswith('client:') else cfg['instance'] if key == 'client' else None
        skip = (email is not None and next(c for c in old_clients if c['email'] == email)['disabled_policy'] == 'hold') or (key == 'enable' and initial['inbound']['enabled'])
        require((entry['status'] == 'not_required') == skip, 'LEGACY_SKIP_REASON')
        if skip:
            continue
        if schema == 1 and key == 'enable' and entry.get('evidence') == 'already_enabled':
            observed = time_value(entry.get('observed_at'))
            require(observed >= previous, 'LEGACY_TIME_ORDER')
            previous = observed
            continue
        sent, confirmed = time_value(entry.get('sent_at')), time_value(entry.get('confirmed_at'))
        require(previous <= sent <= confirmed and 0 <= (sent - edge).total_seconds() < 240, 'LEGACY_TIME_ORDER')
        before, after = snap(entry.get('before'), [email] if email else []), snap(entry.get('after'), [email] if email else [])
        target_key = email or 'inbound'
        if key == 'enable':
            require(not before['inbound']['enabled'] and after['inbound']['enabled'], 'LEGACY_ENABLE_RECOVERY')
        else:
            require(before[target_key]['used_bytes'] == 0 or after[target_key]['used_bytes'] < before[target_key]['used_bytes'], 'LEGACY_RESET_EVIDENCE')
            require(before['inbound']['enabled'] == after['inbound']['enabled'] == initial['inbound']['enabled'], 'LEGACY_INBOUND_PHASE')
            if email:
                require(after[email]['enabled'] and after[email]['traffic_enabled'], 'LEGACY_CLIENT_RECOVERY')
        previous = confirmed
    final = snap(value.get('final'), [c['email'] for c in old_clients])
    require(time_value(value['completed_at']) >= previous and final['inbound']['enabled'], 'LEGACY_FINAL')
    for c in old_clients:
        enabled = c['disabled_policy'] != 'hold'
        require(final[c['email']]['enabled'] is enabled and final[c['email']]['traffic_enabled'] is enabled, 'LEGACY_FINAL_CLIENT')
    return {'cycle': cycle, 'schema': schema, 'sha256': raw_hash(raw), 'completed_at': stamp(time_value(value['completed_at'])), 'boundary_utc': stamp(edge)}


def history(config_raw, state_files, prior, target, now):
    """prior 含 V2.1.2 config/manifest 原字节；只在存在 schema 1 时要求。"""
    cfg = json_load(config_raw)
    s = compatible(cfg, target)
    records, markers = [], {}
    v1 = None
    if prior:
        fields(prior, 'config manifest', 'LEGACY_PRIOR_FILES')
        v1 = json_load(prior['config'])
        compatible(v1, target, 1)
        m = json_load(prior['manifest'])
        fields(m, 'instance machine_id files state_before new_runner candidate original_inbound_id original_client_binding old_cycle status', 'LEGACY_MANIFEST_FIELDS')
        fields(m['files'], 'runner config service timer', 'LEGACY_MANIFEST_FILES')
        require(all(type(v) is str and re.fullmatch('[0-9a-f]{64}', v) for v in m['files'].values()), 'LEGACY_MANIFEST_HASH')
        require(type(m['state_before']) is dict and v1['instance'] == cfg['instance']
                and m.get('instance') == cfg['instance'] and m.get('machine_id') == cfg['machine_id']
                and m.get('files', {}).get('runner') == V212_HASH and m['files'].get('config') == raw_hash(prior['config'])
                and m.get('new_runner') == R2_HASH and m.get('candidate') == raw_hash(config_raw)
                and m.get('status') == 'installed', 'LEGACY_PROVENANCE')
        original = next(c for c in target.data['clients'] if c['email'] == cfg['instance'])
        require(type(m['original_inbound_id']) is int and m['original_inbound_id'] == target.data['inbound']['id']
                and m['original_client_binding'] == digest([original['record_id'], original['uuid']]), 'LEGACY_PROVENANCE_IDENTITY')
        old_cycle = walk(s, utc(s['first_boundary_utc'], 'LEGACY_FIRST'), 1, direction=-1)[0].astimezone(ZoneInfo(s['timezone'])).strftime('%Y-%m')
        require(m['old_cycle'] == old_cycle and 'v2/' + old_cycle + '.json' in m['state_before'], 'LEGACY_PROVENANCE_CYCLE')
        # 原安装时已有的 marker/账本全部保留；不能只验证当前还在的 schema 1 文件。
        for name, expected in m['state_before'].items():
            require(name in ('client-reset.done', 'inbound-reset.done') or re.fullmatch(r'v2/20\d{2}-(0[1-9]|1[0-2])\.json', name), 'LEGACY_PRIOR_STATE_PATH')
            require(type(expected) is str and re.fullmatch('[0-9a-f]{64}', expected)
                    and name in state_files and raw_hash(state_files[name]) == expected, 'LEGACY_PRIOR_STATE_PROVENANCE')
            if name.endswith('.json'):
                original_state = json_load(state_files[name])
                require(type(original_state) is dict and type(original_state.get('schema')) is int
                        and original_state['schema'] == 1 and name[3:-5] <= old_cycle, 'LEGACY_PRIOR_STATE_SCHEMA')
    for name, raw in sorted(state_files.items()):
        if name in ('client-reset.done', 'inbound-reset.done'):
            cycle = raw.decode().strip()
            require(re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])', cycle), 'LEGACY_MARKER')
            markers[name] = cycle
            continue
        require(re.fullmatch(r'v2/20\d{2}-(0[1-9]|1[0-2])\.json', name), 'LEGACY_STATE_UNKNOWN')
        cycle = name[3:-5]
        value = json_load(raw)
        require(type(value) is dict and type(value.get('schema')) is int and value['schema'] in (1, 2), 'LEGACY_LEDGER_SCHEMA')
        source = cfg if value.get('schema') == 2 else v1
        require(source is not None, 'LEGACY_SOURCE_CONFIG_MISSING')
        if value.get('schema') == 1:
            require(cycle < cfg['first_cycle'] and prior and json_load(prior['manifest']).get('state_before', {}).get(name) == raw_hash(raw), 'LEGACY_PRIOR_STATE_PROVENANCE')
        record = completed(raw, source, target, cycle)
        require(time_value(record['completed_at']) <= now, 'LEGACY_FUTURE_HISTORY')
        records.append({**record, 'path': STATE + '/' + name})
    require(not markers or (len(markers) == 2 and len(set(markers.values())) == 1), 'LEGACY_MARKERS_PARTIAL')
    first = utc(s['first_boundary_utc'], 'LEGACY_FIRST')
    latest = walk(s, now, 1, direction=-1, inclusive=True)[0]
    evidence_first = utc(schedule(v1)['first_boundary_utc'], 'LEGACY_FIRST') if v1 else first
    month_count = max(0, (latest.year - evidence_first.year) * 12 + latest.month - evidence_first.month + 2)
    require(month_count <= 1200, 'LEGACY_HISTORY_RANGE')
    required = [b for b in walk(s, evidence_first - timedelta(seconds=1), count=max(1, month_count)) if b <= latest] if evidence_first <= latest else []
    cycles = {r['cycle'] for r in records}
    require(all(b.astimezone(ZoneInfo(s['timezone'])).strftime('%Y-%m') in cycles for b in required), 'LEGACY_DUE_HISTORY_MISSING')
    if markers:
        require(next(iter(markers.values())) < (v1 or cfg)['first_cycle'], 'LEGACY_MARKER_OVERLAP_OR_FUTURE')
    due = max(first, next_boundary(s, now))
    return {'records': records, 'markers': markers, 'next_boundary_utc': stamp(due), 'next_period_id': len(records) + 1,
            'source_first_boundary_utc': stamp(first), 'state_sha256': digest({n: raw_hash(b) for n, b in sorted(state_files.items())})}
