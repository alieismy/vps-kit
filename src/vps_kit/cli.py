"""P1 只读接口与 P2 显式本机维护接口；错误不回显输入或凭据。"""
import argparse
import json

from . import __version__
from .traffic.config import parse_config
from .traffic.contracts import ContractError, json_load, read_file
from .traffic.discovery import FixtureReader, discover
from .traffic.host import live_guard, read_status
from .traffic.panel_http import LocalPanelReader, token_file
from .traffic.state import inspect_ledger
from .traffic import commands, deploy_commands


class Parser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs["allow_abbrev"] = False
        super().__init__(*args, **kwargs)

    def error(self, message):
        # argparse 的默认错误会回显误传的明文 Token 等参数。
        raise ContractError("CLI_ARGUMENTS")


def parser():
    p = Parser(prog="vps-kit", description="E1：月日历/固定 30 天流量任务；仅本地模拟验证，真实部署需另行验收。")
    p.add_argument("--version", action="version", version=__version__)
    tools = p.add_subparsers(dest="tool")
    traffic = tools.add_parser("traffic", help="流量任务接口")
    actions = traffic.add_subparsers(dest="action")
    check = actions.add_parser("validate-config", help="离线严格校验配置")
    check.add_argument("--config", required=True)
    ledger = actions.add_parser("inspect-state", help="离线核对单份账本及产生时配置；不证明实际执行")
    ledger.add_argument("--config", required=True)
    ledger.add_argument("--state", required=True)
    status = actions.add_parser("status", help="本机只读状态，不读 Token、不请求 API")
    status.add_argument("--config")
    discovery = actions.add_parser("discover", help="合成快照或本机 GET 核对；必须显式选择模式")
    discovery.add_argument("--config", required=True)
    modes = discovery.add_mutually_exclusive_group(required=True)
    modes.add_argument("--fixture")
    modes.add_argument("--local", action="store_true")
    discovery.add_argument("--token-file")
    commands.add_arguments(actions)
    deploy_commands.add_arguments(actions)
    return p


def main(argv=None):
    reader = None
    try:
        args = parser().parse_args(argv)
        if not args.tool or not args.action:
            print(json.dumps({"version": __version__, "phase": "E1", "actions": ["validate-config", "inspect-state", "status", "discover", *commands.ACTIONS, *deploy_commands.ACTIONS],
                              "write_actions": "EXPLICIT_LOCAL_ONLY", "script_writes": 0}, ensure_ascii=False))
            return 0
        if args.action in commands.ACTIONS:
            print(json.dumps(commands.run(args), ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        if args.action in deploy_commands.ACTIONS:
            print(json.dumps(deploy_commands.run(args), ensure_ascii=False, sort_keys=True, indent=2))
            return 0
        local = args.action == "discover" and args.local
        if args.action == "discover" and bool(args.token_file) != bool(local):
            raise ContractError("CLI_TOKEN_FILE_MODE")
        config = parse_config(read_file(args.config, protected=local)) if args.config else None
        if args.action == "validate-config":
            result = {"result": "CONFIG_VALID", **config.summary(), 'config_sha256':config.fingerprint,
                      'object_sha256':config.object_fingerprint, "script_writes": 0}
        elif args.action == "inspect-state":
            result = {"result": "STATE_CONTRACT_VALID", **inspect_ledger(read_file(args.state), config), "script_writes": 0}
        elif args.action == "status":
            result = read_status(config)
        else:
            if local:
                host = live_guard(config)
                reader = LocalPanelReader(config, token_file(args.token_file))
            else:
                reader = FixtureReader(json_load(read_file(args.fixture)), config)
            result = discover(config, reader, "LOCAL_LIVE_GET" if local else "SYNTHETIC")
            if local:
                result["host"] = host
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    except ContractError as e:
        print(json.dumps({"result": "FAIL", "code": e.code, "read_calls": reader.attempts if reader else 0,
                          "automatic_retry": False, "raw_input_hidden": True}))
        return 2
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        # 不回显配置、URL、API 对象和 traceback。测试仍须定位并消除非预期异常。
        print(json.dumps({"result": "FAIL", "code": "UNEXPECTED_INPUT_OR_ENVIRONMENT", "raw_input_hidden": True,
                          "read_calls": reader.attempts if reader else 0, "automatic_retry": False}))
        return 3
    except KeyboardInterrupt:
        print('{"result":"CANCELLED","effects":"REVIEW_PERSISTED_STATE","automatic_retry":false}')
        return 130
