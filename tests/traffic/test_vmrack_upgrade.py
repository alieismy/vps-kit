"""P3 契约格式转换模型；旧发布物源码的拒绝行为另用保留包独立验证。"""
import copy
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from p2_support import ATTESTATIONS
from p3_support import harness, package, TOKEN_A
from vmrack_support import config
from vps_kit.traffic.contracts import ContractError,canonical,json_load
from vps_kit.traffic.control import Control
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.execution import execute
from vps_kit.traffic.package import OLD_CONTRACTS,sha,verify


def old_contract(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z: files={n:z.read(n) for n in z.namelist()}
    manifest=json_load(files['manifest.json']); manifest['contracts']=OLD_CONTRACTS
    files['manifest.json']=canonical(manifest).encode()
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for n,b in files.items(): z.writestr(n,b)
    verify(out.getvalue(),sha(out.getvalue()))
    return out.getvalue()


class ExtensionUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(); cls.root=Path(cls.tmp.name)
        cls.raw=package(cls.root); cls.old=old_contract(package(cls.root,'0.3.0a2'))

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def install_old(self,root):
        h=harness(root); cfg,fs,clock,j,b,d=h
        plan=d.plan('install',cfg,self.old,sha(self.old),TOKEN_A,'paused',ATTESTATIONS)
        d.apply(plan,self.old,TOKEN_A)
        return h

    def test_upgrade_records_format_and_preserves_monthly_config_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,fs,clock,j,b,d=self.install_old(Path(tmp))
            old=copy.deepcopy(j.load().events)
            p=d.plan('upgrade',cfg,self.raw,sha(self.raw),None,'paused',ATTESTATIONS)
            d.apply(p,self.raw)
            c=Control(j.load())
            self.assertEqual(c.view.events[:len(old)],old)
            self.assertEqual(c.config,cfg); self.assertEqual(c.next_authorization()[0],1)
            self.assertEqual(len(c.formats),1); self.assertFalse(b.panel.writes)
            result=d.rollback(p['operation_id'],cfg.data['task_id'])
            self.assertTrue(result['incompatible_format_file_exit'])
            self.assertEqual(len(Control(j.load()).formats),1)
            self.assertEqual(b.new_system.timer,'paused')
            with self.assertRaises(ContractError): d.guard(cfg)

    def test_format_downgrade_with_execution_ledger_is_rejected_before_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,fs,clock,j,b,d=self.install_old(Path(tmp))
            p=d.plan('upgrade',cfg,self.raw,sha(self.raw),None,'paused',ATTESTATIONS)
            d.apply(p,self.raw)
            clock.set(cfg.data['schedule']['first_boundary_utc'])
            execute(j,lambda _:b.panel,b.new_system,clock)
            clock.set('2027-01-23T12:00:00Z')
            before={n:fs.image(n) for n in p['before']}; head=j.load().head
            with self.assertRaisesRegex(ContractError,'ROLLBACK_FORMAT_INCOMPATIBLE'): d.rollback(p['operation_id'],cfg.data['task_id'])
            self.assertEqual(before,{n:fs.image(n) for n in p['before']}); self.assertEqual(j.load().head,head)

    def test_upgrade_pending_blocks_format_conversion(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg,fs,clock,j,b,d=self.install_old(Path(tmp))
            clock.set(cfg.data['schedule']['first_boundary_utc'])
            def lost(event,key):
                if event=='after_send': raise OSError('lost')
            b.panel.hook=lost
            with self.assertRaises(OSError): execute(j,lambda _:b.panel,b.new_system,clock)
            clock.set('2027-01-23T12:00:00Z')
            before=j.load().head
            with self.assertRaisesRegex(ContractError,'HISTORY_UNRESOLVED'): d.plan('upgrade',cfg,self.raw,sha(self.raw),None,'paused',ATTESTATIONS)
            self.assertEqual(j.load().head,before); self.assertFalse(Control(j.load()).formats)

    def test_native_fixed_install_and_later_revision_block_prior_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate=config(first='2027-02-07T01:50:00Z',version='3.10.2')
            cfg,fs,clock,j,b,d=harness(Path(tmp),candidate)
            p=d.plan('install',cfg,self.raw,sha(self.raw),TOKEN_A,'paused',ATTESTATIONS)
            d.apply(p,self.raw,TOKEN_A)
            from vps_kit.traffic.maintenance import Maintenance
            data=cfg.data; data['revision']=2; data['schedule']['timezone']='America/Toronto'
            manager=Maintenance(j,b.new_system,lambda _:b.panel,clock)
            change=manager.plan(parse_config(canonical(data)),'paused',ATTESTATIONS)
            manager.prepare(change); manager.apply(change)
            with self.assertRaisesRegex(ContractError,'ROLLBACK_CONFIG_CHANGED'): d.rollback(p['operation_id'],cfg.data['task_id'])
            self.assertEqual(Control(j.load()).next_authorization()[1].isoformat(),'2027-02-07T01:50:00+00:00')
            self.assertFalse(b.panel.writes)
