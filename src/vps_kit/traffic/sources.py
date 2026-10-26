"""来源类型到固定路径的受信映射；计划不得提供任意锁或退役目标。"""
from .contracts import require
from . import legacy

VMISS = 'vmiss-r2/v1'
VMRACK = 'vmrack-monthly/v1'


def paths(kind=VMISS):
    require(kind in (VMISS, VMRACK), 'MIGRATION_SOURCE_KIND')
    name = 'vmiss' if kind == VMISS else 'vmrack'
    base = name + '-traffic-reset'
    return {'runner': '/usr/local/sbin/' + name + '-3xui-traffic-reset',
            'config': '/etc/' + base + ('/reset.json' if kind == VMISS else '/reset.conf'),
            'token': '/etc/' + base + '/api-token', 'state': '/var/lib/' + base,
            'runtime': base, 'lock': '/run/' + base + '/reset.lock',
            'timer': name + '-3xui-traffic-reset.timer', 'service': name + '-3xui-traffic-reset.service',
            'retired': legacy.RETIRED if kind == VMISS else
                b'#!/bin/sh\nprintf "%s\\n" "VMRACK_RUNNER_RETIRED; USE_VPS_KIT_AUDIT; RESET_REFUSED" >&2\nexit 78\n'}


def kind_of(migration):
    return migration.get('source_kind', VMISS) if migration else None


def plan_kind(plan):
    return VMRACK if plan['kind'] == 'vmrack-migrate' else VMISS
