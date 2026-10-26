"""标准库测试入口；拒绝本测试进程的真实网络连接。"""
import sys
import subprocess
from pathlib import Path
import unittest

sys.dont_write_bytecode = True
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "tests/traffic"))


def no_network(event, args):
    if event in ("socket.connect", "socket.bind", "socket.getaddrinfo", "socket.gethostbyaddr", "socket.gethostbyname"):
        raise AssertionError("测试禁止真实网络访问")
    if event == "subprocess.Popen":
        # Windows 的 audit executable 可为 None，args 为 list2cmdline 后的字符串。
        allowed = (args[0] is not None and Path(args[0]).resolve() == Path(sys.executable).resolve())
        if args[0] is None and isinstance(args[1], str):
            allowed = args[1].startswith(subprocess.list2cmdline([sys.executable]) + " ")
        if not allowed:
            raise AssertionError("无网络测试仅允许受控 Python 子进程；curl 回环测试使用独立入口")


sys.addaudithook(no_network)
suite = unittest.defaultTestLoader.discover(str(root / "tests/traffic"), pattern=sys.argv[1] if len(sys.argv) == 2 else "test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
