import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vmrack_support import harness, seed, config
from p3_support import TOKEN_A
from vps_kit.cli import main
from vps_kit.traffic.contracts import ContractError, canonical
from vps_kit.traffic.package import sha


class VMRackCliTests(unittest.TestCase):
    def test_audit_reports_incomplete_and_retired_owner_as_blocked(self):
        from p2_support import harness as native_harness, install
        from vps_kit.traffic.commands import audit
        from vps_kit.traffic.control import Control
        from vps_kit.traffic.contracts import utc
        with tempfile.TemporaryDirectory() as tmp:
            h=native_harness(Path(tmp),data=config().data)
            cfg,clock,j,panel,system,manager=h
            clock.set('2026-10-24T00:00:00Z')
            install(h)
            wall=utc(cfg.data['schedule']['first_boundary_utc'],'TIME')
            with patch('vps_kit.traffic.commands.datetime') as dt:
                dt.now.return_value=wall
                current=Control(j.load()); head=j.load().head
                self.assertEqual(audit(current)['eligibility'],'IN_WINDOW')
                current.maintenance['stage']='failed'
                report=audit(current)
                self.assertEqual((report['eligibility'],report['eligibility_reason']),('BLOCKED','MAINTENANCE_INCOMPLETE'))
                current=Control(j.load()); current.owner['status']='retired'
                report=audit(current)
                self.assertEqual((report['eligibility'],report['eligibility_reason']),('BLOCKED','OWNER_NOT_ACTIVE'))
                self.assertEqual(report['eligibility_scope'],'CONTROL_AND_TIME_ONLY')
                self.assertEqual(j.load().head,head)
                self.assertFalse(panel.reads or panel.writes)

    def test_source_audit_no_api_writes_or_credentials_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,fs,clock,j,b,d=harness(Path(tmp)); evidence=seed((cfg,fs,clock,j,b,d))
            evidence['source_images_sha256']='0'*64
            def content(path,**kwargs): return canonical(cfg.data if path=='candidate' else evidence)
            snapshot=lambda:{str(p.relative_to(fs.root)):sha(p.read_bytes()) for p in fs.root.rglob('*') if p.is_file()}
            before=snapshot()
            with patch('vps_kit.traffic.deploy_commands.linux_root'),patch('vps_kit.traffic.deploy_commands.native_journal',return_value=j),patch('vps_kit.traffic.deploy_commands.context',return_value=d),patch('vps_kit.traffic.deploy_commands.read_file',side_effect=content),patch('sys.stdout',new_callable=io.StringIO) as out:
                code=main(['traffic','vmrack-source-audit','--local','--config','candidate','--vmrack-evidence','evidence'])
            self.assertEqual(code,0,out.getvalue())
            result=json.loads(out.getvalue()); self.assertEqual(result['script_writes'],0)
            self.assertEqual(result['api_requests'],0); self.assertFalse(b.panel.reads)
            self.assertNotIn(TOKEN_A,out.getvalue()); self.assertNotIn(cfg.data['panel']['url'],out.getvalue())
            self.assertEqual(snapshot(),before)

    def test_reconfirm_wrong_host_before_bootstrap(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,fs,clock,j,b,d=harness(Path(tmp)); operation='00000000-0000-4000-8000-000000000099'
            latest={'operation_id':operation,'stage':'waiting-candidate-reconfirmation',
                    'qualification':{'candidate':cfg.data}}
            with patch('vps_kit.traffic.deploy_commands.linux_root'),patch('vps_kit.traffic.deploy_commands.native_journal',return_value=j),patch('vps_kit.traffic.deploy_commands.context',return_value=d),patch('vps_kit.traffic.deploy_files.DeploymentLog.read',return_value=(None,{},latest)),patch('vps_kit.traffic.deploy_commands.binding_guard',side_effect=ContractError('HOST_BINDING_MISMATCH')),patch('vps_kit.traffic.deploy_commands.bootstrap') as boot,patch('sys.stdout',new_callable=io.StringIO) as out:
                code=main(['traffic','deployment-reconfirm','--local','--operation',operation,'--expect-sha256','0'*64,'--package','synthetic'])
            self.assertEqual(code,2); self.assertEqual(json.loads(out.getvalue())['code'],'HOST_BINDING_MISMATCH')
            boot.assert_not_called(); self.assertFalse(j.root.exists())

    def test_monthly_to_fixed_and_back_cannot_shorten_old_authorization(self):
        from vps_kit.traffic.calendar import switching_boundary
        from vps_kit.traffic.contracts import utc
        from vps_kit.traffic.config import parse_config
        from support import config_data
        old=parse_config(canonical(config_data()))
        now=utc('2026-10-24T00:00:00Z','TIME'); due=utc('2027-01-22T20:44:25Z','TIME')
        with self.assertRaisesRegex(ContractError,'SWITCH_SHORTENS_PERIOD'):
            switching_boundary(old,config(),now,due)
        fixed=config(first='2027-02-07T01:50:00Z')
        self.assertEqual(switching_boundary(old,fixed,now,due),utc('2027-02-07T01:50:00Z','TIME'))
        with self.assertRaisesRegex(ContractError,'SWITCH_SHORTENS_PERIOD'):
            switching_boundary(fixed,old,now,utc('2027-02-07T01:50:00Z','TIME'))
