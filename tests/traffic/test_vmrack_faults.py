"""固定周期执行的故障与适配接口；无网络、无真实 timer。"""
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from p2_support import harness, install
from test_p2_faults import disable_client
from vmrack_support import config
from vps_kit.traffic.calendar import anchor_from_local, stamp, walk, wakeups
from vps_kit.traffic.contracts import ContractError, utc
from vps_kit.traffic.control import Control
from vps_kit.traffic.execution import execute
from vps_kit.traffic.native import calendar_check, ordered_locks, committed_or_detected_source
from vps_kit.traffic.panel_http import ProcessResult
from vps_kit.traffic.sources import VMRACK, paths


class FixedFaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def fixture(self,path=None,version='3.7.0',clients=1):
        d=config(version=version).data
        a=utc(d['schedule']['anchor_utc'],'TIME')
        d['schedule']['first_boundary_utc']=stamp(a+timedelta(days=90))
        d['inbound']['policy']='quota-recovery'
        d['clients'][0]['policy']='quota-recovery'
        if clients>1:
            d['clients']=[{'record_id':n,'uuid':f'00000000-0000-4000-8000-{n:012d}','email':'client-'+str(n),
                           'quota_bytes':n*1024**3,'policy':'quota-recovery'} for n in range(1,clients+1)]
        h=harness(path or self.root,data=d)
        install(h)
        h[1].set(d['schedule']['first_boundary_utc'])
        return h

    def test_all_step_intents_and_readback_failures_no_replay_both_versions(self):
        for version in ('3.7.0','3.10.2'):
            for key in ('client:11','inbound','enable'):
                for when in ('before_send','after_send'):
                    with self.subTest(version=version,key=key,when=when),tempfile.TemporaryDirectory() as tmp:
                        cfg,clock,j,panel,system,_=self.fixture(Path(tmp),version)
                        panel.responses['/panel/api/inbounds/list']['obj'][0].update(enable=False,up=cfg.data['inbound']['quota_bytes'],down=0)
                        def fault(stage,obj):
                            if (stage,obj)==(when,key): raise OSError('synthetic send ambiguity')
                        panel.hook=fault
                        with self.assertRaises(OSError): execute(j,lambda _:panel,system,clock)
                        count=len(panel.writes); head=j.load().head
                        for days in (0,1,30,60):
                            clock.set(stamp(utc(cfg.data['schedule']['first_boundary_utc'],'TIME')+timedelta(days=days)))
                            with self.assertRaises(ContractError): execute(j,lambda _:panel,system,clock)
                        self.assertEqual((len(panel.writes),j.load().head),(count,head))
                        self.assertEqual(len(panel.writes),len(set(panel.writes)))

    def test_durable_intent_fault_matrix_fixed_interval(self):
        for point in ('before_file_sync','after_file_sync','before_replace','after_replace','after_directory_sync'):
            for target in ('record','head'):
                with self.subTest(point=point,target=target),tempfile.TemporaryDirectory() as tmp:
                    cfg,clock,j,panel,system,_=self.fixture(Path(tmp))
                    number=len(j.load().events)+2; triggered=[]
                    def fault(name,stage):
                        selected=name==f'{number:08d}.json' if target=='record' else name=='head.json' and (j.root/'records'/f'{number:08d}.json').exists()
                        if selected and stage==point and not triggered:
                            triggered.append(1); raise OSError('synthetic persistence fault')
                    j.fault=fault
                    with self.assertRaises(OSError): execute(j,lambda _:panel,system,clock)
                    self.assertTrue(triggered); self.assertFalse(panel.writes)
                    j.fault=lambda *_:None
                    with self.assertRaises(ContractError): execute(j,lambda _:panel,system,clock)
                    self.assertFalse(panel.writes)

    def test_clock_window_ntp_program_and_retirement_drift_after_pending(self):
        for mode in ('jump','ntp','window','budget','program','retirement'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                cfg,clock,j,panel,system,_=self.fixture(Path(tmp))
                def hook(stage):
                    state=Control(j.load()).ledgers.get(1)
                    if state and state['steps'][0]['status']=='pending':
                        if mode=='jump': clock.wall+=timedelta(seconds=3)
                        elif mode=='ntp': clock.synced=False
                        elif mode=='window': clock.set(stamp(clock.wall+timedelta(seconds=240)))
                        elif mode=='budget': clock.set(stamp(clock.wall+timedelta(seconds=290)))
                        elif mode=='program': system.program_sha256='0'*64
                        else: raise ContractError('LEGACY_RUNNER_NOT_RETIRED')
                system.hook=hook
                with self.assertRaises(ContractError): execute(j,lambda _:panel,system,clock)
                self.assertFalse(panel.writes)
                self.assertEqual(Control(j.load()).unresolved(),[1])

    def test_sixteen_shared_pool_hold_and_personal_recovery_both_versions(self):
        for version in ('3.7.0','3.10.2'):
            with self.subTest(version=version),tempfile.TemporaryDirectory() as tmp:
                cfg,clock,j,panel,system,_=self.fixture(Path(tmp),version,16)
                for client in cfg.data['clients']: disable_client(panel,client,client['quota_bytes'])
                panel.responses['/panel/api/inbounds/list']['obj'][0].update(enable=False,up=cfg.data['inbound']['quota_bytes'],down=0)
                result=execute(j,lambda _:panel,system,clock)
                self.assertEqual(result['post_requests'],18)
                self.assertEqual(panel.writes,['client:'+str(n) for n in range(1,17)]+['inbound','enable'])
                self.assertEqual(result['get_requests'],33*(2+2*18))
        # 父池用完不能替代某个未满 Client 的恢复资格。
        with tempfile.TemporaryDirectory() as tmp:
            cfg,clock,j,panel,system,_=self.fixture(Path(tmp))
            disable_client(panel,cfg.data['clients'][0],1)
            panel.responses['/panel/api/inbounds/list']['obj'][0].update(enable=False,up=cfg.data['inbound']['quota_bytes'],down=0)
            with self.assertRaises(ContractError): execute(j,lambda _:panel,system,clock)
            self.assertFalse(panel.writes)

    def test_native_calendar_checks_24_days_and_each_business_point(self):
        cfg=config(first='2028-10-29T01:50:00Z'); now=utc('2026-10-24T00:00:00Z','TIME'); calls=[]
        def process(args,**kwargs):
            calls.append(args)
            count=int(next(x for x in args if x.startswith('--iterations=')).split('=')[1])
            base=datetime.strptime(next(x for x in args if x.startswith('--base-time=')).split('=',1)[1],'%Y-%m-%d %H:%M:%S UTC').replace(tzinfo=timezone.utc)
            values=wakeups(cfg.data['schedule'],base,count)
            raw='\n'.join(('Next elapse: ' if n==0 else f'Iteration #{n+1}: ')+v.strftime('%a %Y-%m-%d %H:%M:%S UTC') for n,v in enumerate(values))
            return ProcessResult(0,raw.encode())
        calendar_check(cfg,now,process)
        self.assertEqual(len(calls),49)
        with self.assertRaises(ContractError): calendar_check(cfg,now,lambda *a,**k:ProcessResult(0,b'Next elapse: Mon 2026-11-09 01:50:00 UTC'))

    def test_hold_and_unlimited_stop_fixed_clock(self):
        d=config(version='3.10.2').data
        d['schedule']['first_boundary_utc']='2027-02-07T01:50:00Z'
        held=d['clients'][0]; held['policy']='hold'
        d['clients'].append({**held,'record_id':12,'uuid':'00000000-0000-4000-8000-000000000012',
                             'email':'unlimited','policy':'stop','quota_bytes':0})
        cfg,clock,j,panel,system,manager=harness(self.root,data=d)
        disable_client(panel,held,1000)
        install((cfg,clock,j,panel,system,manager))
        before=copy.deepcopy(panel.responses['/panel/api/clients/traffic/'+held['email']])
        clock.set(d['schedule']['first_boundary_utc'])
        execute(j,lambda _:panel,system,clock)
        self.assertEqual(panel.writes,['client:12','inbound'])
        self.assertEqual(panel.responses['/panel/api/clients/traffic/'+held['email']],before)

    def test_local_anchor_input_and_dst_refusal(self):
        self.assertEqual(anchor_from_local('2026-11-16 10:05:00','Asia/Singapore'),'2026-11-16T02:05:00Z')
        for value in ('2026-03-08 02:30:00','2026-11-01 01:30:00'):
            with self.assertRaisesRegex(ContractError,'ANCHOR_DST'): anchor_from_local(value,'America/Toronto')

    def test_committed_source_survives_deleted_legacy_directory_and_lock_order(self):
        latest={'migration':{'source_kind':VMRACK},'plan':{'kind':'vmrack-migrate'}}
        with patch('vps_kit.traffic.deploy_files.DeploymentLog.read',return_value=(None,{},latest)),patch('pathlib.Path.exists',return_value=False):
            self.assertEqual(committed_or_detected_source(),VMRACK)
        from contextlib import contextmanager
        events=[]
        @contextmanager
        def lock(path):
            events.append(str(path)); yield
        with ordered_locks(Path('/new.lock'),VMRACK,lock): pass
        self.assertEqual(events,[str(Path('/new.lock')),str(Path(paths(VMRACK)['lock']))])
