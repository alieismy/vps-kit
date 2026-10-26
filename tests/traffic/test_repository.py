import ast
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest
from urllib.parse import unquote
import zipfile

from support import ROOT
from vps_kit.traffic.contracts import ContractError, canonical
from vps_kit.traffic.package import PUBLIC_DOCS, PUBLIC_ROOT_FILES, build, program_digest, sha, verify


class RepositoryTests(unittest.TestCase):
    def test_fixed_fixture_hashes(self):
        directory = ROOT / "tests/traffic/fixtures"
        lines = (directory / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        expected = {f"{v}/{n}.json" for v in ("3.7.0", "3.10.2") for n in ("config", "panel", "ledger")}
        found = set()
        for line in lines:
            digest, name = line.split("  ")
            self.assertIn(name, expected)
            self.assertNotIn(name, found)
            found.add(name)
            raw = (directory / name).read_bytes()
            self.assertNotIn(b"\r", raw)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)
        self.assertEqual(found, expected)

    def test_python_311_syntax_and_read_adapter_stays_readonly(self):
        for p in [*ROOT.glob("src/**/*.py"), ROOT / "bin/vps-kit"]:
            tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p.relative_to(ROOT)), feature_version=(3, 11))
            names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            if p.name in ("discovery.py", "panel.py", "panel_http.py"):
                self.assertTrue(names.isdisjoint({"post", "reset", "apply", "migrate", "rollback"}))

    def test_p1_doc_links_and_evidence_pins(self):
        # 公开文档必须自洽；本机存在的忽略目录不能替代公开可访问目标。
        docs = [ROOT / n for n in PUBLIC_ROOT_FILES if n.endswith('.md')]
        docs += [ROOT / 'doc' / n for n in PUBLIC_DOCS if n.endswith('.md')]
        docs += [ROOT / 'tests/traffic/fixtures/README.md']
        for p in docs:
            raw = p.read_text(encoding="utf-8")
            self.assertEqual(raw.count("```") % 2, 0)
            for target in re.findall(r"\]\(([^)]+)\)", raw):
                if "://" not in target and not target.startswith("#"):
                    resolved = (p.parent / unquote(target.split("#")[0])).resolve()
                    self.assertTrue(resolved.is_relative_to(ROOT), (p.name, target))
                    self.assertNotIn('.tmp', resolved.relative_to(ROOT).parts, (p.name, target))
                    self.assertFalse(resolved.name.startswith('g0-'), (p.name, target))
                    self.assertTrue(resolved.exists(), (p.name, target))
        source = json.loads((ROOT / "doc/p1-source-evidence.json").read_text(encoding="utf-8"))
        for item in source["sources"]:
            self.assertRegex(item["blob_sha"], r"^[a-f0-9]{40}$")
            self.assertRegex(item["url"], r"^https://github.com/MHSanaei/3x-ui/blob/v3\.(?:7\.0|10\.2)/")

    def test_release_version_and_p2_command_documentation(self):
        import tomllib
        from vps_kit import __version__
        from vps_kit.traffic.commands import ACTIONS
        from vps_kit.traffic.deploy_commands import ACTIONS as P3_ACTIONS
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["project"]["version"], __version__)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        cli = (ROOT / 'doc/cli.md').read_text(encoding='utf-8')
        contract = (ROOT / "doc/p2-contract.md").read_text(encoding="utf-8")
        self.assertIn(__version__, readme)
        self.assertIn(__version__, contract)
        for action in ACTIONS:
            self.assertIn(action, cli)
            self.assertIn(action, contract)
        p3 = (ROOT / 'doc/p3-contract.md').read_text(encoding='utf-8')
        self.assertIn(__version__, p3)
        for action in P3_ACTIONS:
            self.assertIn(action, cli)
            self.assertIn(action, p3)

    def test_new_build_requires_license_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / 'release.zip'
            with self.assertRaisesRegex(ContractError, 'PACKAGE_LICENSE_REQUIRED'):
                build(root, output)
            self.assertFalse(output.exists())

    def test_public_package_excludes_private_reports_and_has_complete_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'
            shutil.copytree(ROOT, root, ignore=shutil.ignore_patterns('.git', '.tmp', '.github', '__pycache__',
                                                                    'build', 'dist', '*.egg-info'))
            # 即使本地存在新的私有文档，也不能被 doc/ 通配选入公开包。
            (root / 'doc/private-inventory.md').write_text('SYNTHETIC_PRIVATE_REPORT', encoding='utf-8')
            output = Path(tmp) / 'release.zip'
            build(root, output)
            _, files = verify(output.read_bytes(), sha(output.read_bytes()))
            self.assertEqual(files['LICENSE'], (ROOT / 'LICENSE').read_bytes())
            self.assertTrue(set(PUBLIC_ROOT_FILES) <= files.keys())
            self.assertNotIn('doc/private-inventory.md', files)
            for name in ('g0-local-inventory-and-lab-preparation-plan.md', 'g0-preparation-review-20261025.md'):
                self.assertNotIn('doc/' + name, files)
            self.assertFalse(any('.tmp' in Path(name).parts for name in files))
            for name, raw in files.items():
                if not name.endswith('.md'):
                    continue
                for target in re.findall(r'\]\(([^)]+)\)', raw.decode('utf-8')):
                    if '://' in target or target.startswith('#'):
                        continue
                    resolved = (root / name).parent / unquote(target.split('#')[0])
                    self.assertTrue(resolved.resolve().is_relative_to(root), (name, target))
                    member = resolved.resolve().relative_to(root).as_posix()
                    self.assertTrue(member in files or any(n.startswith(member + '/') for n in files),
                                    (name, target, 'target absent from package'))

    def test_legacy_manifest_without_publication_files_remains_readable(self):
        # 模型证明旧格式不强制补许可证；保留的真实历史 ZIP 另行离线复核。
        from vps_kit import __version__
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'release.zip'
            build(ROOT, output)
            manifest, files = verify(output.read_bytes(), sha(output.read_bytes()))
        for name in ('LICENSE', 'SECURITY.md', 'CONTRIBUTING.md', 'CHANGELOG.md'):
            del files[name]
        old_version = '0.4.0a2'
        for name in ('pyproject.toml', 'src/vps_kit/__init__.py'):
            files[name] = files[name].replace(__version__.encode(), old_version.encode())
        manifest['version'] = old_version
        manifest['program_sha256'] = program_digest(files)
        manifest['files'] = {n: {'sha256': sha(b), 'size': len(b), 'mode': 0o755 if n == 'bin/vps-kit' else 0o644}
                             for n, b in files.items()}
        result = io.BytesIO()
        with zipfile.ZipFile(result, 'w') as archive:
            for name, raw in {**files, 'manifest.json': canonical(manifest).encode()}.items():
                archive.writestr(name, raw)
        checked, old_files = verify(result.getvalue(), sha(result.getvalue()))
        self.assertEqual(checked['version'], old_version)
        self.assertNotIn('LICENSE', old_files)
