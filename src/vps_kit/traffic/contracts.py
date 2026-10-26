"""不可信 JSON 和文件的共同表示边界；错误不携带原始输入。"""
import hashlib
import json
import math
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
MAX_INT = 2**63 - 1


class ContractError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require(ok, code):
    if not ok:
        raise ContractError(code)


def integer(value, code, minimum=0, maximum=MAX_INT):
    require(type(value) is int and minimum <= value <= maximum, code)
    return value


def boolean(value, code):
    require(type(value) is bool, code)
    return value


def text(value, code, maximum=256, empty=False):
    require(type(value) is str and (empty or bool(value)) and len(value) <= maximum, code)
    require(not any(ord(c) < 32 or ord(c) == 127 for c in value), code)
    return value


def fields(value, expected, code):
    require(type(value) is dict and set(value) == set(expected.split()), code)
    return value


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def json_load(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            require(k not in result, "JSON_DUPLICATE_KEY")
            result[k] = v
        return result

    def invalid(_):
        raise ContractError("JSON_NONFINITE")

    def real(number):
        value = float(number)
        require(math.isfinite(value), "JSON_NONFINITE")
        return value

    def strings_and_depth(value, depth=0):
        require(depth <= 64, "JSON_DEPTH")
        if isinstance(value, str):
            value.encode("utf-8")  # 也拒绝 JSON 转义形成的孤立 surrogate。
        elif isinstance(value, dict):
            for k, v in value.items():
                k.encode("utf-8")
                strings_and_depth(v, depth + 1)
        elif isinstance(value, list):
            for v in value:
                strings_and_depth(v, depth + 1)

    require(isinstance(raw, (str, bytes)) and len(raw) <= MAX_BYTES, "JSON_SIZE")
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        require(len(raw.encode("utf-8")) <= MAX_BYTES, "JSON_SIZE")
        parsed = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid, parse_float=real)
        strings_and_depth(parsed)
        return parsed
    except (ValueError, UnicodeError, RecursionError):
        raise ContractError("JSON_INVALID") from None


def read_file(path, protected=False, limit=MAX_BYTES):
    """不创建文件；拒绝链接和目录。生产文件检查 root:root 与 0600。"""
    try:
        p = Path(path)
        require(not p.is_symlink(), "FILE_SYMLINK")
        if protected:
            require(os.name == "posix", "PROTECTED_FILE_REQUIRES_POSIX")
            # 拒绝不可信目录及祖先链接，避免凭据路径被替换。
            for parent in [p.absolute().parent, *p.absolute().parents[1:]]:
                s = parent.lstat()
                require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                        "FILE_PARENT_PERMISSIONS")
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as f:
            s = os.fstat(f.fileno())
            require(stat.S_ISREG(s.st_mode), "FILE_NOT_REGULAR")
            if protected:
                require(s.st_uid == 0 and s.st_gid == 0 and stat.S_IMODE(s.st_mode) == 0o600,
                        "FILE_PERMISSIONS")
            require(s.st_size <= limit, "FILE_SIZE")
            raw = f.read(limit + 1)
            require(len(raw) <= limit, "FILE_SIZE")
            return raw
    except OSError:
        raise ContractError("FILE_READ_FAILED") from None


def utc(value, code):
    require(type(value) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value), code)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        raise ContractError(code) from None
