"""固定清单的离线发行包；不下载、不解压任意路径、不执行包内代码。"""
import ast
import hashlib
import io
from pathlib import Path, PurePosixPath
import re
import stat
import tomllib
import zipfile
import zlib

from .contracts import ContractError, canonical, fields, json_load, require

SCHEMA = 'vps-kit.release/v1'
LIMIT = 16 * 1024 * 1024
REQUIRED = {'bin/vps-kit', 'pyproject.toml', 'src/vps_kit/__init__.py', 'src/vps_kit/cli.py',
            'src/vps_kit/traffic/__init__.py', 'src/vps_kit/traffic/deployment.py'}
# 新构建显式选取公开说明；历史包验证继续沿用原有文档路径契约。
PUBLIC_ROOT_FILES = ('bin/vps-kit', 'pyproject.toml', 'README.md', 'LICENSE',
                     'SECURITY.md', 'CONTRIBUTING.md', 'CHANGELOG.md')
PUBLIC_DOCS = (
    'README.md', 'cli.md',
    'l2-l3-isolated-acceptance-plan.md',
    'l2-l3-isolated-acceptance-review-20261024.md',
    'l2-l3-isolated-acceptance-review-20261025.md',
    'p1-contract.md', 'p1-review-20261024.md', 'p1-source-evidence.json', 'p1-work-record.md',
    'p2-contract.md', 'p2-review-20261024.md', 'p2-validation.md', 'p2-work-record.md',
    'p3-contract.md', 'p3-review-20261024.md', 'p3-validation.md', 'p3-work-record.md',
    'vmrack-contract.md', 'vmrack-fixed-30-day-extension-proposal.md',
    'vmrack-fixed-30-day-extension-review-20261024.md', 'vmrack-review-20261025.md',
    'vmrack-validation.md', 'vmrack-work-record.md', 'vps-traffic-reset-tool-proposal.md',
)
VERSION = re.compile(r'0\.[0-9]+\.[0-9]+(?:a[0-9]+)?')
OLD_CONTRACTS = ['config/v1', 'control/v1', 'ledger/v2', 'deployment/v1']
CONTRACTS = OLD_CONTRACTS + ['config/v2', 'fixed-30-day/v1', 'vmrack-import/v1', 'deployment/v2']
LAUNCHER = br'''#!/usr/bin/python3 -B
import os, re, sys
from pathlib import Path
sys.dont_write_bytecode = True
base = Path('/usr/local/lib/vps-kit')
target = os.readlink(base / 'current')
if not re.fullmatch(r'releases/0\.[0-9]+\.[0-9]+(?:a[0-9]+)?', target):
    raise SystemExit('INVALID_RELEASE_POINTER')
sys.path.insert(0, str(base / target / 'src'))
from vps_kit.cli import main
raise SystemExit(main())
'''


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def allowed(name):
    path = PurePosixPath(name)
    return (not path.is_absolute() and '..' not in path.parts and '\\' not in name
            and str(path) == name and (name in PUBLIC_ROOT_FILES
            or (name.startswith('src/vps_kit/') and name.endswith('.py'))
            or (name.startswith('tests/') and name.endswith('.py'))
            or name in ('tests/traffic/fixtures/README.md', 'tests/traffic/fixtures/SHA256SUMS')
            or re.fullmatch(r'tests/traffic/fixtures/3\.(?:7\.0|10\.2)/(?:config|panel|ledger)\.json', name)
            or name in ('tests/traffic/fixtures/vmrack/runner-35.sh','tests/traffic/fixtures/vmrack/legacy.service','tests/traffic/fixtures/vmrack/legacy.timer')
            or (name.startswith('doc/') and path.suffix in ('.md', '.json'))
            or name in ('examples/3.7.0.json', 'examples/3.10.2.json')))


def program_digest(files):
    from .contracts import digest
    content = [('entry', sha(LAUNCHER))]
    content.extend((name.removeprefix('src/vps_kit/'), sha(raw)) for name, raw in sorted(files.items())
                   if name.startswith('src/vps_kit/') and name.endswith('.py'))
    return digest(content)


def inspect_manifest(manifest):
    """磁盘/部署记录中的 manifest 也要重新经过路径与类型边界。"""
    from .contracts import digest
    fields(manifest, 'schema version files program_sha256 contracts', 'PACKAGE_MANIFEST')
    require(manifest['schema'] == SCHEMA and type(manifest['version']) is str
            and VERSION.fullmatch(manifest['version']), 'PACKAGE_VERSION')
    require(manifest['contracts'] in (OLD_CONTRACTS, CONTRACTS), 'PACKAGE_CONTRACTS')
    require(type(manifest['files']) is dict and 1 <= len(manifest['files']) < 160, 'PACKAGE_FILE_SET')
    require(REQUIRED <= set(manifest['files']), 'PACKAGE_REQUIRED')
    for name, spec in manifest['files'].items():
        require(allowed(name), 'PACKAGE_PATH')
        require(not any(str(p) in manifest['files'] for p in PurePosixPath(name).parents), 'PACKAGE_PATH_COLLISION')
        fields(spec, 'sha256 size mode', 'PACKAGE_FILE_META')
        require(type(spec['sha256']) is str and re.fullmatch('[a-f0-9]{64}', spec['sha256'])
                and type(spec['size']) is int and 0 <= spec['size'] <= LIMIT
                and type(spec['mode']) is int and spec['mode'] == (0o755 if name == 'bin/vps-kit' else 0o644), 'PACKAGE_FILE_META')
    require(sum(v['size'] for v in manifest['files'].values()) <= LIMIT, 'PACKAGE_SIZE')
    hashes = [('entry', sha(LAUNCHER))] + [(n.removeprefix('src/vps_kit/'), v['sha256'])
               for n,v in sorted(manifest['files'].items()) if n.startswith('src/vps_kit/') and n.endswith('.py')]
    require(digest(hashes) == manifest['program_sha256'], 'PACKAGE_PROGRAM')
    return manifest


def verify(raw, expected):
    try:
        return _verify(raw, expected)
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, zlib.error, SyntaxError, UnicodeError, ValueError, KeyError, TypeError):
        raise ContractError('PACKAGE_FORMAT') from None


def _verify(raw, expected):
    require(type(raw) is bytes and len(raw) <= LIMIT and sha(raw) == expected, 'PACKAGE_HASH')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        items = archive.infolist()
        names = [i.filename for i in items]
        require(len(names) == len(set(names)) and 1 < len(names) <= 160, 'PACKAGE_MEMBERS')
        require(set(names) == {'manifest.json'} | {n for n in names if allowed(n)}, 'PACKAGE_PATH')
        require(sum(i.file_size for i in items) <= LIMIT and all(not i.is_dir() and not i.flag_bits & 1
                and not stat.S_ISLNK(i.external_attr >> 16) for i in items), 'PACKAGE_MEMBER_TYPE_OR_SIZE')
        manifest = json_load(archive.read('manifest.json'))
        inspect_manifest(manifest)
        require(type(manifest['files']) is dict and set(manifest['files']) == set(names) - {'manifest.json'}, 'PACKAGE_FILE_SET')
        files = {n: archive.read(n) for n in manifest['files']}
    require(REQUIRED <= set(files), 'PACKAGE_REQUIRED')
    for name, value in files.items():
        fields(manifest['files'][name], 'sha256 size mode', 'PACKAGE_FILE_META')
        require(manifest['files'][name] == {'sha256': sha(value), 'size': len(value), 'mode': 0o755 if name == 'bin/vps-kit' else 0o644}, 'PACKAGE_FILE_HASH')
        if name.endswith('.py') or name == 'bin/vps-kit':
            ast.parse(value.decode('utf-8'), feature_version=(3, 11))
    require(tomllib.loads(files['pyproject.toml'].decode())['project']['version'] == manifest['version'], 'PACKAGE_VERSION_DRIFT')
    tree = ast.parse(files['src/vps_kit/__init__.py'].decode())
    versions = [n.value.value for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
                and any(isinstance(t, ast.Name) and t.id == '__version__' for t in n.targets)]
    require(versions == [manifest['version']] and program_digest(files) == manifest['program_sha256'], 'PACKAGE_PROGRAM')
    return manifest, files


def build(root, output):
    root, output = Path(root), Path(output)
    # LICENSE 是新构建的必需输入，不加入历史 manifest 的 REQUIRED，保留旧包读取能力。
    require((root / 'LICENSE').is_file() and not (root / 'LICENSE').is_symlink(), 'PACKAGE_LICENSE_REQUIRED')
    selected = [root / name for name in PUBLIC_ROOT_FILES]
    selected += sorted((root / 'src/vps_kit').rglob('*.py'))
    selected += sorted((root / 'tests').rglob('*.py'))
    selected += [root / 'tests/traffic/fixtures' / name for name in ('README.md', 'SHA256SUMS')]
    selected += sorted((root / 'tests/traffic/fixtures').glob('*/*.json'))
    selected += sorted((root / 'tests/traffic/fixtures/vmrack').glob('*'))
    selected += [root / 'doc' / name for name in PUBLIC_DOCS]
    require(all(p.is_file() and not p.is_symlink() for p in selected), 'PACKAGE_SOURCE_TYPE')
    files = {p.relative_to(root).as_posix(): p.read_bytes() for p in selected}
    for version in ('3.7.0', '3.10.2'):
        files['examples/' + version + '.json'] = (root / 'tests/traffic/fixtures' / version / 'config.json').read_bytes()
    version = tomllib.loads(files['pyproject.toml'].decode())['project']['version']
    manifest = {'schema': SCHEMA, 'version': version, 'contracts': CONTRACTS,
                'program_sha256': program_digest(files), 'files': {n: {'sha256': sha(b), 'size': len(b),
                'mode': 0o755 if n == 'bin/vps-kit' else 0o644} for n, b in sorted(files.items())}}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted({**files, 'manifest.json': (canonical(manifest) + '\n').encode()}.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = (stat.S_IFREG | (0o755 if name == 'bin/vps-kit' else 0o644)) << 16
            archive.writestr(info, raw, compress_type=zipfile.ZIP_DEFLATED)
    raw = buffer.getvalue()
    verify(raw, sha(raw))
    with output.open('xb') as stream:
        stream.write(raw)
    return {'result': 'PACKAGE_BUILT', 'version': version, 'sha256': sha(raw), 'files': len(files), 'deployment': 'NOT_REQUESTED'}
