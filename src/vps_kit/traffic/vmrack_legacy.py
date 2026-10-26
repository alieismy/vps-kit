"""VMRack 已审核月脚本解释器。只解析原字节，不 source、不执行旧脚本。"""
from datetime import timedelta
from decimal import Decimal
import re
from urllib.parse import urlsplit

from .calendar import boundary, next_boundary, stamp
from .config import FIXED_SECONDS
from .contracts import canonical, digest, fields, require, text, utc
from .package import sha
from .sources import VMRACK, paths

PROFILES = {
    'VMRack-35': (600, 10, '09:50:00', '2026-11-09T01:50:00Z',
                  '657b23d23dc371d660a4215c105602ffec4107b2521a2faeb3890195f39dd70e'),
    'VMRack-49': (1000, 21, '10:35:00', '2026-11-20T02:35:00Z',
                  '82a6d6a82f8e063e7865b1623b9a04950b49b3b1f3b5eb60b25951d7efe27a8b')}
SERVICE_SHA = '3acfeb13c335a4ca1b6a26138d41b53d473b18e33ceaf03dc3d9223cbbb8020e'
TIMER_35_SHA = 'adec5bfae573dca9f010307df5e4e70e3c244ae27a77b2df6fbb081a885aaba7'
CRON_RETIRED = b'# Retired by vps-kit; automatic and manual legacy reset are not authorized.\n'


def old_schedule(profile):
    require(profile in PROFILES, 'VMRACK_SOURCE_NOT_REVIEWED')
    _, day, time, _, _ = PROFILES[profile]
    return {'day': day, 'time': time, 'timezone': 'Asia/Singapore', 'short_month': 'not-applicable'}


def literal_config(raw):
    try:
        lines = raw.decode('utf-8').splitlines()
    except UnicodeError:
        require(False, 'VMRACK_CONFIG_ENCODING')
    result = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = re.fullmatch(r"([A-Z_]+)='([^'\r\n]*)'", line)
        require(match is not None and match[1] not in result, 'VMRACK_LITERAL_CONFIG_ONLY')
        result[match[1]] = match[2]
    fields(result, 'PANEL_BASE_URL PANEL_RESOLVE CLIENT_EMAIL INBOUND_REMARK EXPECTED_TOTAL_BYTES', 'VMRACK_CONFIG_FIELDS')
    return result


def verify_source(source, cfg, evidence):
    d, p = cfg.data, paths(VMRACK)
    require(d['panel']['version'] == '3.7.0', 'VMRACK_SOURCE_PANEL_VERSION')
    profile = evidence['profile']
    require(profile in PROFILES, 'VMRACK_SOURCE_NOT_REVIEWED')
    amount, _, _, anchor, fingerprint = PROFILES[profile]
    old = literal_config(source['config'])
    url = urlsplit(d['panel']['url'])
    resolve = f'{url.hostname}:{url.port or 443}:127.0.0.1'
    require(url.scheme == 'https' and d['panel']['connect_address'] == '127.0.0.1'
            and old['PANEL_BASE_URL'] == d['panel']['url'] and old['PANEL_RESOLVE'] == resolve,
            'VMRACK_PANEL_BINDING')
    require(old['EXPECTED_TOTAL_BYTES'] == str(amount * 1024**3), 'VMRACK_PROFILE_MIXED')
    require(old['INBOUND_REMARK'] == d['inbound']['remark']
            and any(c['email'] == old['CLIENT_EMAIL'] for c in d['clients']), 'VMRACK_OBJECT_BINDING')
    require(d['schedule'].get('kind') == 'fixed-interval', 'VMRACK_FIXED_REQUIRED')
    # 商家可改变锚点，但接管必须明确再次证明本台供应商边界，不能沿用另一台标签。
    require(evidence['provider_anchor_utc'] == d['schedule']['anchor_utc'], 'VMRACK_ANCHOR_BINDING')
    require(evidence['provider_amount_gib'] == amount and type(evidence['provider_amount_gib']) is int,
            'VMRACK_PROFILE_MIXED')
    provider = d['provider']
    require(provider is not None and Decimal(provider['amount']) * (10**9 if provider['unit'] == 'GB' else 1024**3)
            == amount * 1024**3, 'VMRACK_PROVIDER_BUDGET_BINDING')
    require(evidence['candidate_sha256'] == cfg.fingerprint and evidence['object_sha256'] == cfg.object_fingerprint,
            'VMRACK_CANDIDATE_BINDING')
    require(evidence['source_images_sha256'] == digest(source['images']), 'VMRACK_SOURCE_EVIDENCE_CHANGED')
    raw = source['runner']
    needle = ("test \"$PANEL_RESOLVE\" = '" + resolve + "'").encode()
    require(raw.count(needle) == 1, 'VMRACK_RUNNER_BINDING')
    normalized = raw.replace(needle, b'test "$PANEL_RESOLVE" = \'<APPROVED_RESOLVE>\'')
    require(sha(normalized) == fingerprint, 'VMRACK_UNKNOWN_RUNNER')
    timer = source['timer']
    if profile == 'VMRack-49':
        timer = timer.replace(b'*-*-21 10:35:00', b'*-*-10 09:50:00')
    require(sha(timer) == TIMER_35_SHA and sha(source['service']) == SERVICE_SHA, 'VMRACK_UNIT_CONTRACT')
    for name, raw in source['states'].items():
        require(name in ('client-reset.done', 'inbound-reset.done') and
                re.fullmatch(rb'[0-9]{4}-(?:0[1-9]|1[0-2])\n?', raw), 'VMRACK_MARKER_FORMAT')
    for logical in evidence['retire_cron']:
        require(re.fullmatch(r'/etc/cron\.d/vmrack(?:-traffic)?-reset', logical), 'VMRACK_CRON_PATH')
        raw = source['cron'][logical].decode('utf-8')
        lines = [x.strip() for x in raw.splitlines() if x.strip() and not x.lstrip().startswith('#')]
        require(len(lines) == 1 and re.fullmatch(r'[0-9*,/ -]+\s+root\s+' + re.escape(p['runner']) + r' --run', lines[0]), 'VMRACK_CRON_UNREVIEWED')


def inspect_evidence(e):
    fields(e, 'schema profile candidate_sha256 object_sha256 source_images_sha256 provider_anchor_utc provider_amount_gib '
           'mode actor evidence_files retire_cron inventory_reviewed no_other_entries official_build_confirmed '
           'late_effects_closed processes_closed_at server_closed_at expected_cutover_utc quarantine_accepted no_resets_after_actions last_boundary_utc actions',
           'VMRACK_EVIDENCE_FIELDS')
    require(e['schema'] == 'vps-kit.vmrack-evidence/v1' and e['profile'] in PROFILES and e['mode'] in ('M1','M2'), 'VMRACK_EVIDENCE_KIND')
    text(e['actor'], 'VMRACK_ACTOR', 100)
    for key in ('candidate_sha256','object_sha256','source_images_sha256'):
        require(type(e[key]) is str and re.fullmatch('[0-9a-f]{64}', e[key]), 'VMRACK_EVIDENCE_HASH')
    require(all(e[k] is True for k in ('inventory_reviewed','no_other_entries','official_build_confirmed','late_effects_closed')), 'VMRACK_CONFIRMATIONS')
    require(type(e['retire_cron']) is list and len(e['retire_cron']) <= 2 and len(set(e['retire_cron'])) == len(e['retire_cron']), 'VMRACK_CRON_LIST')
    require(type(e['evidence_files']) is dict and 1 <= len(e['evidence_files']) <= 16, 'VMRACK_PROOF_REQUIRED')
    for path, checksum in e['evidence_files'].items():
        require(type(path) is str and path.startswith('/root/') and '..' not in path.split('/')
                and re.fullmatch('[0-9a-f]{64}', checksum), 'VMRACK_PROOF_PATH_OR_HASH')
    for k in ('processes_closed_at','server_closed_at','expected_cutover_utc','provider_anchor_utc'):
        utc(e[k], 'VMRACK_EVIDENCE_TIME')
    require(type(e['actions']) is list, 'VMRACK_ACTIONS')
    if e['mode'] == 'M2':
        require(e['quarantine_accepted'] is True and e['no_resets_after_actions'] is None and e['last_boundary_utc'] is None and not e['actions'], 'VMRACK_QUARANTINE_CONFIRMATION')
    else:
        require(e['quarantine_accepted'] is False and e['no_resets_after_actions'] is True and e['actions'], 'VMRACK_M1_EVIDENCE')
        utc(e['last_boundary_utc'], 'VMRACK_LAST_BOUNDARY')
    return e


def qualify(cfg, evidence, cutover, now):
    """给出最早可授权网格点；不把输入确认当成自动证明的服务器事实。"""
    e = inspect_evidence(evidence)
    require(all(utc(e[k], 'VMRACK_TIME') <= now for k in ('processes_closed_at','server_closed_at')),
            'VMRACK_PROOF_FROM_FUTURE')
    schedule = cfg.data['schedule']
    if e['mode'] == 'M2':
        low = max(cutover, utc(e['processes_closed_at'], 'VMRACK_TIME'), utc(e['server_closed_at'], 'VMRACK_TIME'))
        low += timedelta(seconds=FIXED_SECONDS)
    else:
        previous = utc(e['last_boundary_utc'], 'VMRACK_LAST_BOUNDARY')
        anchor = utc(schedule['anchor_utc'], 'VMRACK_ANCHOR')
        require((previous-anchor) % timedelta(seconds=FIXED_SECONDS) == timedelta(0), 'VMRACK_HISTORY_OFF_GRID')
        old = old_schedule(e['profile'])
        from zoneinfo import ZoneInfo
        local = previous.astimezone(ZoneInfo(old['timezone']))
        require(boundary(old, local.year, local.month) == previous, 'VMRACK_OLD_WINDOW_NOT_ALIGNED')
        keys = {'inbound'} | {'client:'+str(c['record_id']) for c in cfg.data['clients']}
        seen = set()
        last = previous
        for action in e['actions']:
            fields(action, 'object first_possible_at last_possible_at evidence_sha256', 'VMRACK_ACTION_FIELDS')
            require(action['object'] in keys and action['object'] not in seen, 'VMRACK_ACTION_SET')
            seen.add(action['object'])
            begin, end = (utc(action[k], 'VMRACK_ACTION_TIME') for k in ('first_possible_at','last_possible_at'))
            require(previous <= begin <= end < previous + timedelta(seconds=300), 'VMRACK_LATE_OR_MID_CYCLE_ACTION')
            require(action['evidence_sha256'] in e['evidence_files'].values(), 'VMRACK_ACTION_PROOF')
            last = max(last, end)
        require(seen == keys, 'VMRACK_OBJECT_HISTORY_MISSING')
        require(last <= min(utc(e['processes_closed_at'],'VMRACK_TIME'), utc(e['server_closed_at'],'VMRACK_TIME')) <= now,
                'VMRACK_LATE_EFFECTS_OPEN')
        low = previous + timedelta(seconds=FIXED_SECONDS)
    low = max(low, now + timedelta(seconds=901))
    return next_boundary(schedule, low, inclusive=True)


def imported_history(cfg, source, evidence, cutover, now):
    minimum = qualify(cfg, evidence, cutover, now)
    due = utc(cfg.data['schedule']['first_boundary_utc'], 'VMRACK_FIRST')
    require(due >= minimum, 'VMRACK_FIRST_TOO_EARLY')
    return {'schema': 'vps-kit.vmrack-import/v1', 'source_kind': VMRACK,
            'evidence': evidence, 'cutover_utc': stamp(cutover), 'minimum_boundary_utc': stamp(minimum),
            'next_boundary_utc': stamp(due), 'next_period_id': 1,
            'state_sha256': digest({n:sha(b) for n,b in source['states'].items()})}
