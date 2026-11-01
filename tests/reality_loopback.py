"""显式运行的合成本机 TLS 验证；不访问公网，不属于离线单元测试入口。"""
import ipaddress
import json
from pathlib import Path
import socket
import ssl
import struct
import sys
import tempfile
import threading
import unittest

sys.dont_write_bytecode = True
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "tests/reality"))

from tls_material import CA_CERT, CN_ONLY_CERT, EXPIRED_CERT, SERVER_CERT, SERVER_KEY
from vps_kit.reality.inputs import Candidate
from vps_kit.reality.probe import create_context, probe_address


def guard(event, args):
    if event in ("socket.connect", "socket.bind", "socket.getaddrinfo"):
        host = args[0] if event == "socket.getaddrinfo" else args[1][0]
        if host not in ("127.0.0.1", "::1"):
            raise AssertionError("TLS 测试只允许数值回环地址，无公网连接或名称解析")
    if event in ("socket.gethostbyaddr", "socket.gethostbyname", "subprocess.Popen"):
        raise AssertionError("TLS 测试禁止 DNS 和子进程")


class OneConnectionServer:
    """每个用例只接受一个连接，线程和套接字均在退出时关闭。"""

    def __init__(self, context, host="127.0.0.1"):
        self.context = context
        self.host = host
        self.sni = []
        self.application_bytes = b""
        self.error = None
        self.peer_closed = None
        self.context.set_servername_callback(lambda _socket, name, _context: self.sni.append(name))

    def __enter__(self):
        family = socket.AF_INET6 if ipaddress.ip_address(self.host).version == 6 else socket.AF_INET
        self.listener = socket.socket(family, socket.SOCK_STREAM)
        try:
            self.listener.bind((self.host, 0))
            self.listener.listen(1)
            self.listener.settimeout(4)
            self.port = self.listener.getsockname()[1]
            self.thread = threading.Thread(target=self.serve, daemon=True)
            self.thread.start()
            return self
        except BaseException:
            self.listener.close()
            raise

    def serve(self):
        try:
            connection, _ = self.listener.accept()
            with connection:
                connection.settimeout(4)
                with self.context.wrap_socket(connection, server_side=True) as tls:
                    self.application_bytes = tls.recv(1)
        except ssl.SSLError:
            # 拒绝错误证书、协议或曲线是负向用例的预期结果。
            pass
        except (ConnectionResetError, ConnectionAbortedError) as error:
            # Windows 可在客户端拒绝证书后返回 TCP reset；check_pair 仍要求
            # 客户端明确报告 BASELINE_FAIL，不能将连接异常当作测试通过。
            self.peer_closed = type(error).__name__
        except Exception as error:
            self.error = error

    def __exit__(self, *_args):
        self.thread.join(timeout=5)
        self.listener.close()
        if self.thread.is_alive():
            raise AssertionError("TLS 测试服务线程未退出")
        if self.error is not None:
            raise self.error


class RealityLoopbackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.material = tempfile.TemporaryDirectory(prefix="vps-kit-synthetic-tls-")
        cls.addClassCleanup(cls.material.cleanup)
        cls.folder = Path(cls.material.name)
        cls.key = cls.folder / "server.key"
        cls.key.write_text(SERVER_KEY, encoding="ascii")
        cls.certificates = {}
        for name, pem in (("valid", SERVER_CERT), ("cn_only", CN_ONLY_CERT), ("expired", EXPIRED_CERT)):
            path = cls.folder / (name + ".pem")
            path.write_text(pem, encoding="ascii")
            cls.certificates[name] = path

    def server_context(self, *, certificate="valid", curve="X25519", alpn=("h2",), tls12=False):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        version = ssl.TLSVersion.TLSv1_2 if tls12 else ssl.TLSVersion.TLSv1_3
        context.minimum_version = version
        context.maximum_version = version
        # 探针在握手后立即关闭；不让服务端发送本测试不用的会话票据，
        # 避免 Windows 在 TLS 1.3 post-handshake 写入时发生 reset 竞态。
        context.num_tickets = 0
        context.set_ecdh_curve(curve)
        if alpn:
            context.set_alpn_protocols(list(alpn))
        context.load_cert_chain(str(self.certificates[certificate]), str(self.key))
        return context

    def check_pair(self, *, name="cover.example", trusted=True, host="127.0.0.1", **server_options):
        context = create_context()
        if trusted:
            context.load_verify_locations(cadata=CA_CERT)
        with OneConnectionServer(self.server_context(**server_options), host) as server:
            candidate = Candidate("target.example", server.port, name)
            result = probe_address(candidate, host, 2.0, context)
        self.assertEqual(server.application_bytes, b"", "探针不应发送 HTTP 或其他应用数据")
        if server.peer_closed is not None:
            self.assertEqual(result["status"], "BASELINE_FAIL", server.peer_closed)
        return result, server

    def test_x25519_h2_trusted_pair_passes(self):
        result, server = self.check_pair()
        self.assertEqual((result["status"], result["code"]), ("BASELINE_PASS", "BASELINE_MATCH"))
        self.assertEqual(result["tls_version"], "TLSv1.3")
        self.assertEqual(result["alpn"], "h2")
        self.assertEqual(result["connected_ip"], "127.0.0.1")
        self.assertEqual(server.sni, ["cover.example"])

    def test_single_label_wildcard_passes(self):
        result, _ = self.check_pair(name="one.example.test")
        self.assertEqual(result["status"], "BASELINE_PASS")

    def test_wildcard_does_not_match_multiple_labels(self):
        result, _ = self.check_pair(name="two.one.example.test")
        self.assertEqual(result["code"], "CERTIFICATE_VERIFY_FAILED")

    def test_wrong_sni_fails_name_verification(self):
        result, _ = self.check_pair(name="wrong.example")
        self.assertEqual(result["code"], "CERTIFICATE_VERIFY_FAILED")

    def test_untrusted_ca_fails(self):
        result, _ = self.check_pair(trusted=False)
        self.assertEqual(result["code"], "CERTIFICATE_VERIFY_FAILED")

    def test_cn_without_san_fails(self):
        result, _ = self.check_pair(certificate="cn_only")
        self.assertEqual(result["code"], "CERTIFICATE_VERIFY_FAILED")

    def test_expired_certificate_fails(self):
        result, _ = self.check_pair(certificate="expired")
        self.assertEqual(result["code"], "CERTIFICATE_VERIFY_FAILED")

    def test_tls12_only_fails(self):
        result, _ = self.check_pair(tls12=True)
        self.assertEqual(result["code"], "TLS_HANDSHAKE_FAILED")

    def test_p256_only_fails(self):
        result, _ = self.check_pair(curve="prime256v1")
        self.assertEqual(result["code"], "TLS_HANDSHAKE_FAILED")

    def test_http11_only_is_not_h2(self):
        result, _ = self.check_pair(alpn=("http/1.1",))
        self.assertEqual((result["status"], result["code"]), ("BASELINE_FAIL", "ALPN_NOT_H2"))

    def test_no_alpn_is_not_h2(self):
        result, _ = self.check_pair(alpn=())
        self.assertEqual(result["code"], "ALPN_NOT_H2")

    def check_early_close(self, behavior):
        errors, received = [], []
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(4)

            def serve():
                try:
                    connection, _ = listener.accept()
                    with connection:
                        connection.settimeout(4)

                        def receive(length):
                            data = b""
                            while len(data) < length:
                                chunk = connection.recv(length - len(data))
                                if not chunk:
                                    raise AssertionError("ClientHello 未完整到达")
                                data += chunk
                            return data

                        header = receive(5)
                        self.assertEqual(header[0], 22)
                        length = int.from_bytes(header[3:5], "big")
                        self.assertTrue(0 < length <= 16384)
                        hello = receive(length)
                        self.assertEqual(hello[0], 1)
                        self.assertEqual(int.from_bytes(hello[1:4], "big") + 4, len(hello))
                        received.append(hello)
                        # 先读完整记录，避免带未读数据关闭时，FIN 被系统变成 RST。
                        if behavior == "close_notify":
                            connection.sendall(bytes.fromhex("15030300020100"))
                        if behavior == "rst":
                            layout = "hh" if sys.platform == "win32" else "ii"
                            connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                                  struct.pack(layout, 1, 0))
                        else:
                            connection.shutdown(socket.SHUT_WR)
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            candidate = Candidate("target.example", listener.getsockname()[1], "cover.example")
            try:
                result = probe_address(candidate, "127.0.0.1", 2, create_context())
            finally:
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive(), "提前关闭测试服务线程未退出")
        if errors:
            raise errors[0]
        self.assertEqual(len(received), 1)
        self.assertEqual((result["status"], result["code"]), ("INCOMPLETE", "TLS_CONNECTION_FAILED"))
        self.assertNotIn("tls_version", result)
        self.assertNotIn("certificate_verified", result)

    def test_fin_before_serverhello_is_incomplete(self):
        self.check_early_close("fin")

    def test_close_notify_before_serverhello_is_incomplete(self):
        self.check_early_close("close_notify")

    def test_reset_before_serverhello_is_incomplete(self):
        self.check_early_close("rst")

    def test_ipv6_loopback_when_available(self):
        try:
            listener = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        except OSError:
            self.skipTest("IPv6 socket unavailable")
        try:
            listener.bind(("::1", 0))
            listener.listen(1)
            listener.settimeout(2)
            # 某些 Windows 环境允许 IPv6 bind，却禁止本机 IPv6 connect。
            # 先验证原生 TCP 自连；不能用 probe 的失败结果决定跳过 TLS 验证。
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect(("::1", listener.getsockname()[1], 0, 0))
                accepted, _ = listener.accept()
                accepted.close()
        except OSError:
            self.skipTest("IPv6 loopback TCP unavailable")
        finally:
            listener.close()
        result, _ = self.check_pair(host="::1")
        self.assertEqual(result["status"], "BASELINE_PASS")
        self.assertEqual(result["connected_ip"], "::1")


def main():
    sys.addaudithook(guard)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(RealityLoopbackTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({
        "result": "REALITY_LOOPBACK_PASS" if result.wasSuccessful() else "REALITY_LOOPBACK_FAIL",
        "tests": result.testsRun,
        "skipped": len(result.skipped),
        "python": sys.version.split()[0],
        "openssl": ssl.OPENSSL_VERSION,
        "evidence": "SYNTHETIC_TLS_LOOPBACK_ONLY",
        "public_connections": 0,
        "http_requests": 0,
        "reality_xray_integration": "NOT_TESTED",
    }, sort_keys=True))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
