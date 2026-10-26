"""本机 curl GET 专用传输。禁止任意路径、重定向、重试及代理环境。"""
import os
import re
import subprocess
import threading
from dataclasses import dataclass

from .config import panel_url
from .contracts import ContractError, MAX_BYTES, read_file, require
from .panel import ReadEndpoint


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: bytes


def bounded_process(args, stdin=b"", timeout=12, limit=MAX_BYTES + 32):
    """捕获输出有界；不把 stderr、参数或凭据写入异常。不使用 shell。"""
    env = {k: v for k, v in os.environ.items()
           if "proxy" not in k.lower() and k not in ("CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR", "SSLKEYLOGFILE")}
    env.update({"LC_ALL": "C", "TZ": "UTC"})
    try:
        p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             env=env, shell=False)
    except OSError:
        raise ContractError("PROCESS_UNAVAILABLE") from None
    over = threading.Event()
    read_error = threading.Event()
    out = bytearray()

    def drain(pipe, cap, keep):
        count = 0
        try:
            with pipe:
                while chunk := pipe.read(4096):
                    count += len(chunk)
                    if count > cap:
                        over.set()
                        p.kill()
                        return
                    if keep:
                        out.extend(chunk)
        except (OSError, ValueError):
            read_error.set()

    readers = [threading.Thread(target=drain, args=(p.stdout, limit, True), daemon=True),
               threading.Thread(target=drain, args=(p.stderr, 8192, False), daemon=True)]
    try:
        for t in readers:
            t.start()
        try:
            with p.stdin:
                p.stdin.write(stdin)
        except BrokenPipeError:
            pass
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ContractError("PROCESS_TIMEOUT") from None
    except BaseException:
        # 包括 Ctrl+C：返回 CANCELLED 前必须停止/回收本次读取子进程，不能留它继续访问。
        try:
            if p.poll() is None:
                p.kill()
            p.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            raise ContractError("PROCESS_CLEANUP_FAILED") from None
        raise
    finally:
        if not p.stdin.closed:
            p.stdin.close()
        for t in readers:
            if t.ident is not None:
                t.join(timeout=1)
    require(not over.is_set(), "PROCESS_OUTPUT_LIMIT")
    require(not read_error.is_set(), "PROCESS_OUTPUT_READ")
    require(not any(t.is_alive() for t in readers), "PROCESS_OUTPUT_INCOMPLETE")
    return ProcessResult(p.returncode, bytes(out))


def token_file(path):
    raw = read_file(path, protected=True, limit=128)
    try:
        token = raw.decode("ascii").removesuffix("\n")
    except UnicodeError:
        raise ContractError("TOKEN_FORMAT") from None
    require(re.fullmatch(r"[A-Za-z0-9]{48}", token) is not None, "TOKEN_FORMAT")
    return token


def curl_arguments(panel, method):
    require(method in ("GET", "POST"), "API_METHOD")
    u = panel_url(panel)
    port = u.port or (443 if u.scheme == "https" else 80)
    address = "[::1]" if panel["connect_address"] == "::1" else "127.0.0.1"
    return ["/usr/bin/curl", "-q", "--silent", "--show-error", "--globoff", "--noproxy", "*", "--proxy", "",
            "--proto", "=" + u.scheme, "--max-redirs", "0", "--retry", "0", "--connect-timeout", "3", "--max-time", "10",
            "--request", method, "--header", "@-", "--write-out", "\n%{http_code}",
            "--connect-to", f"::{address}:{port}"]


class LocalPanelReader:
    def __init__(self, config, token, execute=bounded_process):
        require(type(token) is str and re.fullmatch(r"[A-Za-z0-9]{48}", token), "TOKEN_FORMAT")
        self.panel = config.data["panel"]
        self.url = panel_url(self.panel)
        self._token = token
        self.execute = execute
        self.attempts = 0
        self.budget = 1 + 2 * len(config.data["clients"])

    def get(self, endpoint, email=None):
        require(isinstance(endpoint, ReadEndpoint), "API_ENDPOINT_NOT_ALLOWED")
        path = endpoint.path(email)
        require(self.attempts < self.budget, "API_CALL_BUDGET")
        args = curl_arguments(self.panel, "GET")
        args += ["--url", self.panel["url"] + path]
        self.attempts += 1
        r = self.execute(args, stdin=("Authorization: Bearer " + self._token + "\n").encode("ascii"))
        require(r.returncode == 0, "API_TRANSPORT")
        require(len(r.stdout) <= MAX_BYTES + 4, "API_RESPONSE_SIZE")
        body, sep, status = r.stdout.rpartition(b"\n")
        require(sep and status == b"200", "API_HTTP_STATUS")
        return body
