"""显式运行的 curl 回环验证；不属于无网络单元测试，不访问 VPS。"""
import json
import shutil
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "tests/traffic"))

from support import config_data, fixture_data
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import ContractError, canonical
from vps_kit.traffic.discovery import discover
from vps_kit.traffic.panel import ReadEndpoint
from vps_kit.traffic.panel_http import LocalPanelReader, bounded_process


def guard(event, args):
    if event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyaddr", "socket.gethostbyname"):
        raise AssertionError("Python 测试进程不做外连或 DNS")
    if event == "socket.bind" and args[1][0] != "127.0.0.1":
        raise AssertionError("测试仅允许 IPv4 回环监听")


class LoopbackServer(HTTPServer):
    def server_bind(self):
        # 避免 HTTPServer 默认 getfqdn 触发反向 DNS。
        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"
        self.server_port = self.server_address[1]


def main():
    curl = shutil.which("curl.exe" if sys.platform == "win32" else "curl")
    if curl is None:
        print('{"result":"NOT_RUN","reason":"CURL_NOT_FOUND"}')
        return 2
    sys.addaudithook(guard)
    context = {"calls": 0, "responses": {}, "redirect": False, "authorized": 0}
    token = "Synthetic" + "X" * 39

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            context["calls"] += 1
            if self.path == "/probe":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"loopback")
                return
            if self.headers.get("Authorization") != "Bearer " + token:
                self.send_error(401)
                return
            context["authorized"] += 1
            if context["redirect"]:
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/must-not-follow")
                self.end_headers()
                return
            path = self.path.removeprefix("/example")
            body = canonical(context["responses"][path]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = LoopbackServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        port = server.server_port
        # 该整数只表示 127.0.0.1；即使旧 --resolve 失配也只能命中本机。
        probe = bounded_process([curl, "-q", "--silent", "--show-error", "--proxy", "", "--noproxy", "*",
                                 "--connect-timeout", "1", "--max-time", "2", "--resolve", f"2130706433:{port}:127.0.0.2",
                                 "--url", f"http://2130706433:{port}/probe", "--write-out", "\n%{remote_ip}"])
        numeric_resolve_bypassed = probe.returncode == 0 and probe.stdout == b"loopback\n127.0.0.1"
        assert numeric_resolve_bypassed, "当前 curl 未复现数字 IP 规范化；不能报告该项实测成功"
        total_reads = 0
        for version in ("3.7.0", "3.10.2"):
            d = config_data(version)
            d["panel"].update(url=f"http://127.0.0.1:{port}/example", allow_loopback_http=True)
            config = parse_config(json.dumps(d))
            context["responses"] = fixture_data(d)["responses"]

            def execute(args, **kwargs):
                assert args[0] == "/usr/bin/curl"
                assert args[args.index("--connect-to") + 1] == f"::127.0.0.1:{port}"
                assert args[-1].startswith(f"http://127.0.0.1:{port}/example/panel/api/")
                assert token not in repr(args)
                return bounded_process([curl, *args[1:]], **kwargs)

            reader = LocalPanelReader(config, token, execute)
            report = discover(config, reader, "LOCAL_LIVE_GET")
            assert report["read_calls"] == 5 and report["result"] == "READ_CONTRACT_PASS"
            total_reads += reader.attempts
        context["redirect"] = True
        before_redirect = context["calls"]
        try:
            LocalPanelReader(config, token, execute).get(ReadEndpoint.INBOUNDS)
        except ContractError as error:
            assert error.code == "API_HTTP_STATUS"
        else:
            raise AssertionError("重定向未被拒绝")
        assert context["calls"] == before_redirect + 1
        assert context["authorized"] == total_reads + 1
        print(json.dumps({"result": "LOOPBACK_TRANSPORT_PASS", "evidence": "SYNTHETIC_HTTP_LOCAL_ONLY",
                          "numeric_resolve_bypass_reproduced": True, "adapter_reads": total_reads,
                          "total_local_gets": context["calls"], "redirect_followed": False,
                          "credentials_in_argv": False, "vps_requests": 0, "tls_systemd_panel_integration": "NOT_TESTED"}))
        return 0
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
