"""构建固定 a6 配套 r1；显式 tar metadata，不继承 Windows 权限，不重建程序。"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import stat
import tarfile

BUNDLE = 'vps-kit-0.5.0a6-release-support-r1'
ZIP_NAME = 'vps-kit-0.5.0a6.zip'
ZIP_SHA = 'f426ecf4cc605947f48989d0185ae1985223b53eb8a64757cf3b28f285209bcf'
CHECKSUM_SHA = 'd1005d8b9c6688afe2e639340beacd3b98d0820e8165e957ce184fb06daf988a'
PAYLOAD = Path(__file__).resolve().parent / 'payload'
SUPPORT_NAMES = ('README.md', 'VALIDATION.md', 'prepare-config.py',
                 'vmiss-monthly.template.json', 'vmrack-fixed.template.json')
INPUT_NAMES = (*SUPPORT_NAMES, ZIP_NAME, ZIP_NAME + '.sha256')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def with_manifest(payloads):
    if set(payloads) != set(INPUT_NAMES):
        raise ValueError('配套附件必须且只能包含固定七个输入文件')
    result = dict(payloads)
    result['SHA256SUMS'] = ''.join(f'{sha(payloads[n])}  {n}\n' for n in sorted(payloads)).encode()
    return result


def archive_bytes(payloads):
    payloads = with_manifest(payloads)
    stream = io.BytesIO()
    # 不使用 TarFile.add/gettarinfo：宿主 st_mode 在 Windows 会成为 0777/0666。
    with gzip.GzipFile(fileobj=stream, mode='wb', filename='', mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w', format=tarfile.USTAR_FORMAT) as tf:
            for name in [None, *sorted(payloads)]:
                info = tarfile.TarInfo(BUNDLE if name is None else f'{BUNDLE}/{name}')
                info.uid = info.gid = 0
                info.uname = info.gname = 'root'
                info.mtime = 0
                if name is None:
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o700
                    tf.addfile(info)
                else:
                    info.mode = 0o600
                    info.size = len(payloads[name])
                    tf.addfile(info, io.BytesIO(payloads[name]))
    return stream.getvalue()


def read_regular(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError(f'输入必须为普通文件，不能是链接：{path.name}')
    return path.read_bytes()


def build(release_dir, output_dir):
    release_dir, output_dir = Path(release_dir), Path(output_dir)
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError('输出目录必须尚不存在；保留旧包或失败现场，改用新目录')
    # 所有来源与固定摘要在首次写入前检查，错误包不留下伪交付物。
    payloads = {n: read_regular(PAYLOAD/n) for n in SUPPORT_NAMES}
    for name, expected in ((ZIP_NAME, ZIP_SHA), (ZIP_NAME+'.sha256', CHECKSUM_SHA)):
        raw = read_regular(release_dir/name)
        if sha(raw) != expected:
            raise ValueError(f'固定发布资产摘要不符：{name}')
        payloads[name] = raw
    raw = archive_bytes(payloads)
    output_dir.mkdir(mode=0o700)
    delivery = output_dir/BUNDLE
    delivery.mkdir(mode=0o700)
    for name, content in with_manifest(payloads).items():
        path = delivery/name
        with path.open('xb') as stream:
            stream.write(content)
        path.chmod(0o600)
    archive = output_dir/(BUNDLE+'.tar.gz')
    with archive.open('xb') as stream:
        stream.write(raw)
    archive.chmod(0o600)
    with (output_dir/(archive.name+'.sha256')).open('x', encoding='ascii', newline='\n') as stream:
        stream.write(f'{sha(raw)}  {archive.name}\n')
    return {'bundle': BUNDLE, 'archive_bytes': len(raw), 'archive_sha256': sha(raw),
            'manifest_sha256': sha(with_manifest(payloads)['SHA256SUMS']),
            'package_sha256': ZIP_SHA, 'directories': '0700', 'files': '0600',
            'owner': '0:0', 'program_rebuilt': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-dir', required=True, type=Path,
                        help='已下载的固定 ZIP 和发布摘要所在目录；不下载或执行其中内容')
    parser.add_argument('--output-dir', required=True, type=Path,
                        help='尚不存在、父目录已存在的新目录；不覆盖历史产物')
    args = parser.parse_args()
    print(json.dumps(build(args.release_dir, args.output_dir), ensure_ascii=False, indent=2))
