"""只生成工具自己的 unit；不接受用户指令文本、额外触发器或 drop-in。"""
from .calendar import expressions
from .config import uuid_value
from .contracts import require


def stem(task_id):
    uuid_value(task_id)
    return "vps-kit-traffic-" + task_id


def render(config, legacy=False):
    name = stem(config.data["task_id"])
    service = f"""[Unit]
Description=vps-kit authorized traffic boundary
After=x-ui.service time-sync.target

[Service]
Type=oneshot
User=root
UMask=0077
ExecStart=/usr/local/bin/vps-kit traffic execute --local --task {config.data['task_id']}
RemainAfterExit=no
Restart=no
TimeoutStartSec=300s
RuntimeDirectory=vps-kit-traffic
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=yes
StateDirectory=vps-kit/traffic
StateDirectoryMode=0700
LoadCredential=api-token:/etc/vps-kit/traffic/api-token
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ReadWritePaths=/var/lib/vps-kit/traffic /run/vps-kit-traffic
"""
    calendar = "\n".join("OnCalendar=" + e for e in expressions(config.data["schedule"]))
    timer = f"""[Unit]
Description=vps-kit future traffic boundary

[Timer]
{calendar}
Unit={name}.service
Persistent=false
AccuracySec=1s
RandomizedDelaySec=0
WakeSystem=false

[Install]
WantedBy=timers.target
"""
    if legacy:
        from .sources import paths, VMISS
        runtime = paths(VMISS if legacy is True else legacy)['runtime']
        service = service.replace('RuntimeDirectory=vps-kit-traffic\n', 'RuntimeDirectory=vps-kit-traffic ' + runtime + '\n')
        service = service.replace('ReadWritePaths=/var/lib/vps-kit/traffic /run/vps-kit-traffic\n',
                                  'ReadWritePaths=/var/lib/vps-kit/traffic /run/vps-kit-traffic /run/' + runtime + '\n')
    return {name + ".timer": timer, name + ".service": service}


def verify_text(config, actual, legacy=False):
    require(actual == render(config, legacy), "UNIT_CONTENT_DRIFT")
