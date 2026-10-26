"""只读本机发现，固定路径和 systemctl show 白名单；无 timer 修改能力。"""
import hashlib
import os
import platform
import re
import sys
from pathlib import Path

from .contracts import ContractError, read_file, require
from .panel_http import bounded_process

PROPERTIES = ("LoadState", "ActiveState", "SubState", "UnitFileState", "MainPID", "NeedDaemonReload",
              "NextElapseUSecRealtime", "Persistent", "Job")


def command(args, execute=bounded_process):
    result = execute(args, limit=32768)
    require(result.returncode == 0, "HOST_COMMAND_FAILED")
    try:
        return result.stdout.decode("utf-8").strip()
    except UnicodeError:
        raise ContractError("HOST_COMMAND_ENCODING") from None


def read_status(config=None, execute=bounded_process):
    if platform.system() != "Linux":
        return {"platform": platform.system(), "target_platform_match": False,
                "local_status": "LINUX_SYSTEMD_REQUIRED", "api_requests": 0, "script_writes": 0}
    release = platform.freedesktop_os_release()
    supported = (release.get("ID"), release.get("VERSION_ID")) in {("debian", "12"), ("debian", "13"), ("ubuntu", "24.04")}
    require(read_file("/proc/1/comm", limit=128).strip() == b"systemd", "HOST_SYSTEMD_PID1")
    systemd = command(["/usr/bin/systemctl", "--version"], execute).partition("\n")[0]
    curl = command(["/usr/bin/curl", "--version"], execute).partition("\n")[0]
    systemd_match = re.match(r"systemd (\d+)\b", systemd)
    curl_match = re.match(r"curl (\d+\.\d+\.\d+)\b", curl)
    require(systemd_match is not None and curl_match is not None, "HOST_DEPENDENCY_VERSION")
    compatible = supported and int(systemd_match[1]) >= 252 and sys.version_info >= (3, 11)
    units = ["x-ui.service", "vmiss-3xui-traffic-reset.timer", "vmiss-3xui-traffic-reset.service",
             'vmrack-3xui-traffic-reset.timer', 'vmrack-3xui-traffic-reset.service']
    if config:
        stem = "vps-kit-traffic-" + config.data["task_id"]
        units += [stem + ".timer", stem + ".service"]
    states = []
    for unit in units:
        result = execute(["/usr/bin/systemctl", "show", unit, "--no-pager", "--property=" + ",".join(PROPERTIES)], limit=32768)
        try:
            raw = result.stdout.decode("utf-8").strip()
        except UnicodeError:
            raise ContractError("HOST_UNIT_ENCODING") from None
        props = {}
        for line in raw.splitlines():
            k, sep, v = line.partition("=")
            require(sep and k in PROPERTIES and k not in props, "HOST_UNIT_OUTPUT")
            props[k] = v
        # 未安装的新/旧 unit 允许不存在；总线故障和其他 systemctl 错误不能冒充不存在。
        require(result.returncode == 0 or (result.returncode == 1 and props.get("LoadState") == "not-found"),
                "HOST_UNIT_QUERY_FAILED")
        require(all(k in props for k in ("LoadState", "ActiveState", "SubState")), "HOST_UNIT_OUTPUT")
        states.append({"unit": unit, "properties": props})
    ntp = command(["/usr/bin/timedatectl", "show", "--property=NTPSynchronized", "--value"], execute)
    require(ntp in ("yes", "no"), "HOST_NTP_OUTPUT")
    return {"platform": "Linux", "os": release.get("ID"), "os_version": release.get("VERSION_ID"),
            "target_platform_match": compatible, "python": platform.python_version(),
            "systemd_major": int(systemd_match[1]), "curl_version": curl_match[1],
            "ntp_synchronized": ntp == "yes", "units": states,
            "api_requests": 0, "script_writes": 0, "existing_reset_jobs": "PARTIAL_KNOWN_UNIT_INVENTORY",
            "history_blocking": "NOT_ASSESSED", "timer_readiness": "NOT_ASSESSED"}


def binding_guard(config, execute=bounded_process):
    require(platform.system() == "Linux" and os.geteuid() == 0, "LIVE_REQUIRES_LINUX_ROOT")
    host = read_status(config, execute)
    require(host["target_platform_match"], "HOST_OS_UNSUPPORTED")
    mid = read_file("/etc/machine-id", limit=128).strip()
    require(re.fullmatch(b"[0-9a-f]{32}", mid), "HOST_MACHINE_ID")
    require(hashlib.sha256(mid).hexdigest() == config.data["host_id_sha256"], "HOST_BINDING_MISMATCH")
    version = command(["/usr/local/x-ui/x-ui", "-v"], execute)
    require(re.fullmatch(r"v?(3\.7\.0|3\.10\.2)", version) and version.removeprefix("v") == config.data["panel"]["version"],
            "HOST_PANEL_VERSION_MISMATCH")
    require(Path("/usr/bin/curl").is_file(), "HOST_CURL_MISSING")
    host["panel_version"] = version.removeprefix("v")
    host["installation_provenance"] = "NOT_PROVEN_BY_VERSION_STRING"
    return host


def live_guard(config, execute=bounded_process):
    host = binding_guard(config, execute)
    require(host["units"][0]["properties"].get("ActiveState") == "active", "HOST_PANEL_INACTIVE")
    return host
