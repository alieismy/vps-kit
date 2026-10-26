"""执行器专用的三类写端点；管理和发现模块不持有此接口。"""
from urllib.parse import quote

from .contracts import MAX_BYTES, json_load, require
from .panel_http import LocalPanelReader, bounded_process, curl_arguments


class LocalPanelSession:
    def __init__(self, config, token, execute=bounded_process):
        self.config = config
        self.reader = LocalPanelReader(config, token, execute)
        n = len(config.data["clients"])
        m = sum(c["policy"] != "hold" for c in config.data["clients"])
        self.reader.budget = (1 + 2 * n) * (2 + 2 * (m + 2))
        self.sent = set()

    def get(self, endpoint, email=None):
        return self.reader.get(endpoint, email)

    def send(self, key):
        cfg = self.config.data
        clients = {"client:" + str(c["record_id"]): c for c in cfg["clients"] if c["policy"] != "hold"}
        if key in clients:
            path = "/panel/api/clients/resetTraffic/" + quote(clients[key]["email"], safe="")
        elif key == "inbound":
            path = f"/panel/api/inbounds/{cfg['inbound']['id']}/resetTraffic"
        elif key == "enable":
            require(cfg["inbound"]["policy"] == "quota-recovery", "WRITE_ENABLE_POLICY")
            path = f"/panel/api/inbounds/setEnable/{cfg['inbound']['id']}"
        else:
            require(False, "WRITE_TARGET_NOT_ALLOWED")
        require(key not in self.sent, "WRITE_REPLAY_FORBIDDEN")
        self.sent.add(key)  # 即便进程失败，也不允许本次会话重试。
        args = curl_arguments(cfg["panel"], "POST")
        if key == "enable":
            args += ["--header", "Content-Type: application/json", "--data-binary", '{"enable":true}']
        args += ["--url", cfg["panel"]["url"] + path]
        response = self.reader.execute(args, stdin=("Authorization: Bearer " + self.reader._token + "\n").encode("ascii"))
        require(response.returncode == 0, "API_WRITE_TRANSPORT_UNCERTAIN")
        require(len(response.stdout) <= MAX_BYTES + 4, "API_WRITE_RESPONSE_SIZE")
        body, separator, status = response.stdout.rpartition(b"\n")
        require(separator and status == b"200", "API_WRITE_HTTP_UNCERTAIN")
        result = json_load(body)
        require(type(result) is dict and result.get("success") is True, "API_WRITE_RESULT_UNCERTAIN")
