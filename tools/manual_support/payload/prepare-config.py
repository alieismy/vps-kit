#!/usr/bin/env python3
"""手册附件：仅 GET 采集单入站/单客户端并保存新候选；不是 vps-kit 产品命令。"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import uuid

sys.dont_write_bytecode = True


def bind_objects(template, inbound, detail, machine_hash, task_id, panel_binding_id):
    """只补真实身份，不改模板中的额度、协议、端口、策略和账期。"""
    from vps_kit.traffic.contracts import require, json_load, canonical
    from vps_kit.traffic.config import parse_config
    require(type(inbound) is dict and type(detail) is dict, 'SETUP_OBJECT')
    settings = inbound.get('settings')
    settings = json_load(settings) if isinstance(settings, str) else settings
    require(type(settings) is dict and type(settings.get('clients')) is list
            and len(settings['clients']) == 1, 'SETUP_REQUIRES_ONE_CLIENT')
    require(type(template.get('clients')) is list and len(template['clients']) == 1,
            'SETUP_TEMPLATE_ONE_CLIENT')
    c = detail.get('client')
    require(type(c) is dict, 'SETUP_CLIENT_DETAIL')
    d = copy.deepcopy(template)
    d['task_id'], d['panel_binding_id'], d['host_id_sha256'] = task_id, panel_binding_id, machine_hash
    for key in ('id', 'remark', 'listen'):
        require(key in inbound, 'SETUP_INBOUND_FIELD')
        d['inbound'][key] = inbound[key]
    for key, source in (('record_id', 'id'), ('uuid', 'uuid'), ('email', 'email')):
        require(source in c, 'SETUP_CLIENT_FIELD')
        d['clients'][0][key] = c[source]
    require(d['revision'] == 1 and d['panel']['version'] == '3.11.0', 'SETUP_VERSION')
    return parse_config(canonical(d))


class CapturedReader:
    """重用三个对象 GET；仅在来源校验需要时额外读取一次本机 status。"""
    def __init__(self, responses, live_reader=None):
        self.responses, self.attempts, self.paths = responses, 0, set()
        self.live_reader = live_reader

    def get(self, endpoint, email=None):
        from vps_kit.traffic.contracts import require
        from vps_kit.traffic.panel import ReadEndpoint
        path = endpoint.path(email)
        require(path not in self.paths, 'SETUP_READ_BUDGET')
        if endpoint is ReadEndpoint.STATUS and self.live_reader is not None:
            self.paths.add(path)
            self.attempts += 1
            return self.live_reader.get(endpoint)
        require(path in self.responses, 'SETUP_READ_BUDGET')
        self.paths.add(path)
        self.attempts += 1
        return self.responses[path]


def main():
    p = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    p.add_argument('--kit-root', required=True)
    p.add_argument('--template', required=True)
    p.add_argument('--token-file', required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--list', action='store_true')
    mode.add_argument('--inbound-id', type=int)
    p.add_argument('--save')
    a = p.parse_args()
    sys.path.insert(0, str(Path(a.kit_root).resolve() / 'src'))
    from vps_kit import __version__
    from vps_kit.traffic.config import EMAIL, panel_url
    from vps_kit.traffic.contracts import ContractError, read_file, json_load, require
    from vps_kit.traffic.discovery import discover
    from vps_kit.traffic.host import live_guard
    from vps_kit.traffic.native import linux_root, candidate_path, save_plan
    from vps_kit.traffic.panel import ReadEndpoint, unwrap
    from vps_kit.traffic.panel_http import LocalPanelReader, token_file
    try:
        linux_root()
        require(__version__ == '0.5.0a6', 'SETUP_KIT_VERSION')
        require(bool(a.save) == (a.inbound_id is not None), 'SETUP_SAVE_MODE')
        if a.save:
            dest = candidate_path(a.save)
            require(not dest.exists() and not dest.is_symlink(), 'PLAN_DESTINATION_EXISTS')
        d = json_load(read_file(a.template, protected=True))
        require(type(d) is dict and type(d.get('panel')) is dict, 'SETUP_TEMPLATE')
        panel_url(d['panel'])
        require(d['panel']['version'] == '3.11.0' and d['panel']['url'].startswith('http://127.0.0.1:2053/'),
                'SETUP_LOOPBACK_PANEL')
        mid = read_file('/etc/machine-id', limit=128).strip()
        require(re.fullmatch(b'[0-9a-f]{32}', mid) is not None, 'HOST_MACHINE_ID')
        task, binding = str(uuid.uuid4()), str(uuid.uuid4())
        machine_hash = hashlib.sha256(mid).hexdigest()
        bootstrap = SimpleNamespace(data={'panel': d['panel'], 'clients': [{}],
                                         'task_id': task, 'host_id_sha256': machine_hash})
        live_guard(bootstrap)
        reader = LocalPanelReader(bootstrap, token_file(a.token_file))
        raw_list = reader.get(ReadEndpoint.INBOUNDS)
        inbounds = unwrap(raw_list)
        require(type(inbounds) is list and all(type(i) is dict for i in inbounds), 'API_INBOUNDS_LIST')
        if a.list:
            print(json.dumps({'result': 'INBOUND_SELECTION_ONLY', 'inbounds': [
                {k: i.get(k) for k in ('id', 'remark', 'port', 'protocol')} for i in inbounds],
                'get_requests': 1, 'post_requests': 0, 'script_writes': 0}, ensure_ascii=False))
            return 0
        selected = [i for i in inbounds if i.get('id') == a.inbound_id]
        require(a.inbound_id > 0 and len(selected) == 1, 'SETUP_INBOUND_SELECTION')
        inbound = selected[0]
        settings = inbound.get('settings')
        settings = json_load(settings) if isinstance(settings, str) else settings
        require(type(settings) is dict and type(settings.get('clients')) is list
                and len(settings['clients']) == 1, 'SETUP_REQUIRES_ONE_CLIENT')
        embedded = settings['clients'][0]
        require(type(embedded) is dict and type(embedded.get('email')) is str
                and EMAIL.fullmatch(embedded['email']), 'API_EMAIL')
        email = embedded['email']
        raw_detail = reader.get(ReadEndpoint.CLIENT, email)
        cfg = bind_objects(d, inbound, unwrap(raw_detail), machine_hash, task, binding)
        raw_traffic = reader.get(ReadEndpoint.TRAFFIC, email)
        captured = CapturedReader({ReadEndpoint.INBOUNDS.path(): raw_list,
                                   ReadEndpoint.CLIENT.path(email): raw_detail,
                                   ReadEndpoint.TRAFFIC.path(email): raw_traffic}, live_reader=reader)
        snapshot = discover(cfg, captured, 'LOCAL_LIVE_GET')
        require(snapshot['inbound']['enabled'] and
                snapshot['inbound']['used_bytes'] < cfg.data['inbound']['quota_bytes'],
                'INSTALL_INBOUND_NOT_AVAILABLE')
        state, client = snapshot['clients'][0], cfg.data['clients'][0]
        require(state['config_enabled'] and state['traffic_enabled'] and
                state['used_bytes'] < client['quota_bytes'], 'INSTALL_CLIENT_NOT_AVAILABLE')
        save_plan(a.save, cfg.data)
        print(json.dumps({'result': 'CANDIDATE_SAVED', 'get_requests': reader.attempts,
                          'post_requests': 0, 'candidate_files_written': 1,
                          'task_id': task, 'config_sha256': cfg.fingerprint,
                          'timer_changed': False, 'runtime_acceptance': 'NOT_PROVEN'}))
        return 0
    except ContractError as e:
        print(json.dumps({'result': 'FAIL', 'code': e.code, 'automatic_retry': False, 'raw_input_hidden': True}))
        return 2
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'result': 'FAIL', 'code': 'SETUP_INPUT_OR_FILE_ERROR', 'raw_input_hidden': True}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
