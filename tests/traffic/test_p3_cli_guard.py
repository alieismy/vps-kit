"""P3 CLI 在创建目录/锁之前核对目标主机；无网络、无真实系统命令。"""
import io
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from p2_support import ATTESTATIONS
from p3_support import TOKEN_A, harness, package
from vps_kit.cli import main
from vps_kit.traffic.contracts import ContractError, canonical
from vps_kit.traffic.package import sha


class CliHostGuardTests(unittest.TestCase):
    def test_apply_wrong_host_creates_no_directory_or_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cfg,fs,clock,journal,b,d=harness(root)
            raw=package(root)
            p=d.plan('install',cfg,raw,sha(raw),TOKEN_A,'paused',ATTESTATIONS)
            with patch('vps_kit.traffic.deploy_commands.linux_root'),patch('vps_kit.traffic.deploy_commands.context',return_value=d),patch('vps_kit.traffic.deploy_commands.package_bytes',return_value=raw),patch('vps_kit.traffic.deploy_commands.token_file',return_value=TOKEN_A),patch('vps_kit.traffic.deploy_commands.read_file',return_value=canonical(p)),patch('vps_kit.traffic.deploy_commands.binding_guard',side_effect=ContractError('HOST_BINDING_MISMATCH')),patch('vps_kit.traffic.deploy_commands.bootstrap') as boot,patch('sys.stdout',new_callable=io.StringIO):
                result=main(['traffic','deployment-apply','--local','--plan','synthetic','--expect-sha256',p['sha256'],'--package','synthetic','--credential-file','synthetic'])
            self.assertEqual(result,2)
            boot.assert_not_called()
            self.assertFalse(journal.root.exists())
            self.assertFalse(d.log.journal.root.exists())

    def test_rollback_wrong_host_does_not_pause_or_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cfg,fs,clock,journal,b,d=harness(root)
            raw=package(root)
            p=d.plan('install',cfg,raw,sha(raw),TOKEN_A,'enabled',ATTESTATIONS)
            d.apply(p,raw,TOKEN_A)
            before=(journal.load().head,d.log.read()[0].head,list(b.new_system.calls))
            with patch('vps_kit.traffic.deploy_commands.linux_root'),patch('vps_kit.traffic.deploy_commands.context',return_value=d),patch('vps_kit.traffic.deploy_commands.binding_guard',side_effect=ContractError('HOST_BINDING_MISMATCH')),patch('vps_kit.traffic.deploy_commands.bootstrap') as boot,patch('sys.stdout',new_callable=io.StringIO):
                result=main(['traffic','deployment-rollback','--local','--operation',p['operation_id'],'--task',cfg.data['task_id']])
            self.assertEqual(result,2)
            boot.assert_not_called()
            self.assertEqual(before,(journal.load().head,d.log.read()[0].head,b.new_system.calls))
            self.assertEqual(b.new_system.timer,'waiting')
