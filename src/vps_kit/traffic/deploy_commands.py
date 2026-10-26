"""P3 显式管理入口。计划仅写新计划文件，审计只读；所有真实管理限 Linux root --local。"""
import getpass
import os
from pathlib import Path
import sys
import warnings

from .config import parse_config, uuid_value
from .contracts import ContractError, canonical, json_load, read_file, require
from .deploy_native import context
from .deployment import validate_plan
from .host import binding_guard
from .native import bootstrap, candidate_path, linux_root, native_journal, protected_bytes, save_plan
from .package import LIMIT, build, sha, verify
from .panel_http import token_file

ACTIONS = ('package-build', 'package-verify', 'deployment-plan', 'deployment-apply', 'deployment-audit',
           'deployment-rollback', 'credential-input', 'deployment-reconfirm', 'vmrack-source-audit')


def add_arguments(actions):
    for name in ACTIONS:
        p = actions.add_parser(name, help='P3 ' + name)
        if name == 'package-build':
            p.add_argument('--source', required=True)
            p.add_argument('--output', required=True)
        elif name == 'package-verify':
            p.add_argument('--package', required=True)
            p.add_argument('--expect-sha256', required=True)
        else:
            p.add_argument('--local', action='store_true', required=True)
        if name in ('deployment-plan', 'deployment-apply', 'deployment-reconfirm'):
            p.add_argument('--package', required=name == 'deployment-reconfirm')
        if name in ('deployment-plan', 'deployment-apply'):
            p.add_argument('--credential-file')
            p.add_argument('--r2-prior-evidence')
        if name == 'deployment-plan':
            p.add_argument('--kind', choices=('install', 'migrate', 'upgrade', 'credential', 'vmrack-migrate'), required=True)
            p.add_argument('--config', required=True)
            p.add_argument('--package-sha256')
            p.add_argument('--activation', choices=('paused', 'enabled'), required=True)
            p.add_argument('--attestations', required=True)
            p.add_argument('--save', required=True)
            p.add_argument('--vmrack-evidence')
        elif name == 'deployment-apply':
            p.add_argument('--plan', required=True)
            p.add_argument('--expect-sha256', required=True)
        elif name == 'deployment-rollback':
            p.add_argument('--operation', required=True)
            p.add_argument('--task', required=True)
        elif name == 'credential-input':
            p.add_argument('--save', required=True)
        elif name == 'deployment-reconfirm':
            p.add_argument('--operation', required=True)
            p.add_argument('--expect-sha256', required=True)
        elif name == 'vmrack-source-audit':
            p.add_argument('--config', required=True)
            p.add_argument('--vmrack-evidence', required=True)


def package_bytes(path, protected=False):
    if path is None:
        return None
    if protected:
        return protected_bytes(Path(path), (0o600, 0o644), limit=LIMIT)
    with Path(path).open('rb') as stream:
        raw = stream.read(LIMIT + 1)
    require(len(raw) <= LIMIT, 'PACKAGE_SIZE')
    return raw


def run(args):
    if args.action == 'package-build':
        return build(args.source, args.output)
    if args.action == 'package-verify':
        manifest, _ = verify(package_bytes(args.package), args.expect_sha256)
        return {'result': 'PACKAGE_VALID', 'version': manifest['version'], 'files': len(manifest['files']),
                'sha256': args.expect_sha256, 'author_authentication': 'NOT_PROVEN', 'script_writes': 0}
    linux_root()
    journal = native_journal(deployment_guard=False)
    if args.action == 'credential-input':
        path = candidate_path(args.save)
        require(sys.stdin.isatty(), 'TOKEN_REQUIRES_HIDDEN_TTY')
        try:
            with warnings.catch_warnings():
                # isatty 不能证明终端可关闭回显；禁止 getpass fallback 的可见读取。
                warnings.simplefilter('error', getpass.GetPassWarning)
                token = getpass.getpass('API Token（隐藏输入）: ')
        except (getpass.GetPassWarning, EOFError):
            raise ContractError('TOKEN_HIDDEN_INPUT_UNAVAILABLE') from None
        deployment = context(journal)
        deployment.backend.validate_token(token)
        from .journal import DurableFiles
        DurableFiles().directory(path.parent)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write((token + '\n').encode())
            stream.flush()
            os.fsync(stream.fileno())
        DurableFiles().sync_directory(path.parent)
        return {'result': 'CREDENTIAL_CANDIDATE_SAVED', 'active_credential_modified': False, 'token_hidden': True}
    prior = (json_load(read_file(args.r2_prior_evidence, protected=True))
             if getattr(args, 'r2_prior_evidence', None) else None)
    deployment = context(journal, prior)
    if args.action in ('deployment-reconfirm', 'vmrack-source-audit') or getattr(args,'kind',None) == 'vmrack-migrate':
        from .vmrack_deployment import VMRackDeployment
        deployment = VMRackDeployment(journal,deployment.fs,deployment.backend,deployment.clock)
    if args.action == 'vmrack-source-audit':
        from .contracts import digest
        cfg = parse_config(read_file(args.config,protected=True))
        evidence = json_load(read_file(args.vmrack_evidence,protected=True))
        source = deployment.backend.collect_vmrack(cfg,evidence,audit=True)
        return {'result':'VMRACK_SOURCE_AUDIT','source_images_sha256':digest(source['images']),
                'candidate_sha256':cfg.fingerprint,'object_sha256':cfg.object_fingerprint,
                'profile':evidence['profile'],'script_writes':0,'api_requests':0,
                'history_strength':'MARKERS_NOT_EXACTLY_ONCE_EVIDENCE','source_review':'KNOWN_TEMPLATE_ONLY'}
    if args.action == 'deployment-reconfirm':
        uuid_value(args.operation)
        latest = deployment.log.read()[2]
        require(latest and latest['operation_id'] == args.operation and latest['stage'] == 'waiting-candidate-reconfirmation', 'VMRACK_RECONFIRM_STAGE')
        cfg = parse_config(canonical(latest['qualification']['candidate']))
        binding_guard(cfg)
        bootstrap()
        return deployment.reconfirm(args.operation,args.expect_sha256,package_bytes(args.package,protected=True))
    if args.action == 'deployment-audit':
        view, operations, latest = deployment.log.read()
        control_head = journal.load().head
        # 失败/部分部署仍能查看阶段；完整性检查不等于清零或业务验收。
        if latest and latest['stage'] == 'complete' and latest['active_release']:
            from .control import Control
            deployment.guard(Control(journal.load()).config)
        require(deployment.log.read()[0].head == view.head and journal.load().head == control_head, 'DEPLOY_READONLY_CHANGED')
        return {'result': 'DEPLOYMENT_AUDIT', 'operations': len(operations), 'deployment_head_sha256': view.head,
                'latest_operation': latest['operation_id'] if latest else None,
                'kind': latest['kind'] if latest else None, 'stage': latest['stage'] if latest else 'ABSENT',
                'version': latest['active_release']['version'] if latest and latest['active_release'] else None,
                'migration': latest['migration']['status'] if latest and latest['migration'] else None,
                'error_code': latest['error'] if latest else None,
                'script_writes': 0, 'api_requests': 0, 'runtime_acceptance': 'NOT_PROVEN'}
    if args.action == 'deployment-rollback':
        uuid_value(args.operation)
        uuid_value(args.task)
        _, operations, latest = deployment.log.read()
        require(latest is not None and args.operation in operations and latest['operation_id'] == args.operation, 'ROLLBACK_LATEST_OPERATION_REQUIRED')
        cfg = validate_plan(latest['plan'])
        require(cfg.data['task_id'] == args.task, 'ROLLBACK_TASK')
        binding_guard(cfg)
        bootstrap()
        return deployment.rollback(args.operation, args.task)
    raw = package_bytes(args.package, protected=True)
    credential = token_file(args.credential_file) if args.credential_file else None
    if args.action == 'deployment-plan':
        cfg = parse_config(read_file(args.config, protected=True))
        require((args.kind == 'credential') == (raw is None and args.package_sha256 is None), 'PACKAGE_ARGUMENTS')
        attestations = json_load(read_file(args.attestations, protected=True))
        if args.kind == 'vmrack-migrate':
            require(args.vmrack_evidence and credential is None and args.activation == 'paused' and prior is None, 'VMRACK_PLAN_ARGUMENTS')
            p = deployment.plan_vmrack(cfg,raw,args.package_sha256,json_load(read_file(args.vmrack_evidence,protected=True)),attestations)
        else:
            require(args.vmrack_evidence is None, 'VMRACK_EVIDENCE_WRONG_SOURCE')
            p = deployment.plan(args.kind, cfg, raw, args.package_sha256, credential, args.activation,attestations)
        save_plan(args.save, p)
        return {'result': 'DEPLOYMENT_PLAN_SAVED', 'kind': p['kind'], 'operation_id': p['operation_id'],
                'sha256': p['sha256'], 'version': p['release']['version'], 'activation': p['activation'],
                'next_boundary_utc': p['next_boundary_utc'], 'next_period_id': p['next_period_id'],
                'token_hidden': True, 'timer_changed': False, 'post_requests': 0}
    plan = json_load(read_file(args.plan, protected=True))
    require(type(plan) is dict and plan.get('sha256') == args.expect_sha256, 'DEPLOY_PLAN_CONFIRMATION')
    binding_guard(validate_plan(plan))
    if plan['kind'] == 'vmrack-migrate':
        from .vmrack_deployment import VMRackDeployment
        require(credential is None and prior is None, 'VMRACK_APPLY_ARGUMENTS')
        journal = native_journal(deployment_guard=False,migration_setup=True)
        deployment = VMRackDeployment(journal,deployment.fs,deployment.backend,deployment.clock)
        bootstrap()
        return deployment.apply_vmrack(plan,raw)
    bootstrap()
    return deployment.apply(plan, raw, credential)
