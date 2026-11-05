"""配套归档回归；与产品 ZIP 测试分离，不连接面板或 VPS。"""
import io
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from vps_kit.traffic import journal
from vps_kit.traffic.contracts import ContractError


class ArchiveDirectory:
    """把归档目录 metadata 投入真实 guard；不是 Linux 解包实测。"""
    def __init__(self, member=None):
        self.member = member
        self.parents = [ArchiveDirectory()] if member is not None else []

    def lstat(self):
        m = self.member
        return SimpleNamespace(st_mode=stat.S_IFDIR | (m.mode if m else 0o700),
                               st_uid=m.uid if m else 0)


def directory_guard(member):
    with patch.object(journal, 'os', SimpleNamespace(name='posix')):
        journal.DurableFiles().directory(ArchiveDirectory(member))


class CompanionTests(unittest.TestCase):
    def test_original_helper_bytes_preserved(self):
        import build
        # a6 附件原件含 CRLF；Git 换行转换也必须被此固定摘要发现。
        raw = (build.PAYLOAD / 'prepare-config.py').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         '8bdfe402f8991bee425d3411578452040feb60a1eb40c0c54ddef89e87244d1a')

    def test_legacy_windows_metadata_reaches_real_guard(self):
        m = tarfile.TarInfo('legacy'); m.mode = 0o777; m.uid = 0
        with self.assertRaisesRegex(ContractError, 'JOURNAL_DIRECTORY_UNSAFE'):
            directory_guard(m)
        m.mode = 0o700
        directory_guard(m)

    def test_archive_contract_and_guard(self):
        import build
        payloads = {name: b'fixture\n' for name in build.INPUT_NAMES}
        raw = build.archive_bytes(payloads)
        with tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz') as tf:
            members = tf.getmembers()
            self.assertEqual(len(members), 9)
            root = members[0]
            self.assertTrue(root.isdir())
            self.assertEqual(root.name, build.BUNDLE)
            directory_guard(root)
            self.assertEqual(root.mode, 0o700)
            for m in members:
                self.assertEqual((m.uid, m.gid, m.uname, m.gname, m.mtime), (0, 0, 'root', 'root', 0))
                self.assertFalse(m.pax_headers)
                if m is not root:
                    self.assertTrue(m.isfile())
                    self.assertEqual(m.mode, 0o600)
                    name = PurePosixPath(m.name).name
                    self.assertEqual(tf.extractfile(m).read(), build.with_manifest(payloads)[name])
            self.assertEqual({m.name for m in members[1:]},
                             {f'{build.BUNDLE}/{n}' for n in build.with_manifest(payloads)})
            lines = tf.extractfile(f'{build.BUNDLE}/SHA256SUMS').read().decode().splitlines()
            self.assertEqual(len(lines), 7)
            for line in lines:
                digest, name = line.split('  ')
                self.assertEqual(digest, hashlib.sha256(tf.extractfile(f'{build.BUNDLE}/{name}').read()).hexdigest())

    def test_deterministic_order_and_no_host_metadata(self):
        import build
        payloads = {name: name.encode() for name in build.INPUT_NAMES}
        self.assertEqual(build.archive_bytes(payloads), build.archive_bytes(dict(reversed(list(payloads.items())))))

    def test_unexpected_payload_rejected(self):
        import build
        for name in ('../escape', '/absolute', 'extra'):
            payloads = {n: b'x' for n in build.INPUT_NAMES}; payloads[name] = b'x'
            with self.assertRaises(ValueError): build.archive_bytes(payloads)
        with self.assertRaises(ValueError): build.archive_bytes({})

    def test_bad_release_does_not_create_output(self):
        import build
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); release = root/'release'; release.mkdir()
            (release/build.ZIP_NAME).write_bytes(b'wrong package')
            (release/(build.ZIP_NAME+'.sha256')).write_bytes(b'wrong checksum')
            with self.assertRaises(ValueError): build.build(release, root/'out')
            self.assertFalse((root/'out').exists())

    def test_existing_output_preserved(self):
        import build
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'out'; out.mkdir(); sentinel = out/'keep'; sentinel.write_bytes(b'history')
            with self.assertRaises(FileExistsError): build.build(Path(tmp), out)
            self.assertEqual(sentinel.read_bytes(), b'history')
            self.assertEqual(list(out.iterdir()), [sentinel])

    def test_bad_checksum_does_not_create_output(self):
        import build
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); release = root/'release'; release.mkdir()
            raw = b'synthetic package'
            (release/build.ZIP_NAME).write_bytes(raw)
            (release/(build.ZIP_NAME+'.sha256')).write_bytes(b'wrong checksum')
            with patch.object(build, 'ZIP_SHA', build.sha(raw)):
                with self.assertRaisesRegex(ValueError, 'sha256'):
                    build.build(release, root/'out')
            self.assertFalse((root/'out').exists())

    def test_input_link_rejected_before_read(self):
        import build
        from unittest.mock import Mock
        path = Mock(); path.name = 'input'
        path.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
        with self.assertRaises(ValueError): build.read_regular(path)
        path.read_bytes.assert_not_called()

    @unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                         '需要 Linux root；Windows 不冒充 POSIX 解包验收')
    def test_linux_root_tar_and_real_protected_read(self):
        import build
        import subprocess
        from vps_kit.traffic.native import protected_bytes
        with tempfile.TemporaryDirectory(prefix='support-test-', dir='/root') as tmp:
            root = Path(tmp)
            payloads = {name: b'fixture\n' for name in build.INPUT_NAMES}
            archive = root/'bundle.tar.gz'; archive.write_bytes(build.archive_bytes(payloads))
            subprocess.run(['sh', '-c', 'umask 077; exec tar --extract --gzip --file="$1" --directory="$2" --no-same-owner --no-same-permissions',
                            'support-test', str(archive), str(root)], check=True)
            target = root/build.BUNDLE
            journal.DurableFiles().directory(target)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            for p in target.iterdir():
                self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
                protected_bytes(p, modes=(0o600, 0o644))
            target.chmod(0o777)
            with self.assertRaisesRegex(ContractError, 'JOURNAL_DIRECTORY_UNSAFE'):
                protected_bytes(target/build.ZIP_NAME, modes=(0o600, 0o644))
            target.chmod(0o700)
            (target/build.ZIP_NAME).chmod(0o666)
            with self.assertRaisesRegex(ContractError, 'NATIVE_FILE_PERMISSIONS'):
                protected_bytes(target/build.ZIP_NAME, modes=(0o600, 0o644))


if __name__ == '__main__':
    unittest.main(verbosity=2)
