import copy
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from p2_support import ATTESTATIONS
from p3_support import package
from support import config_data
from vmrack_support import config, harness, seed
from vps_kit.traffic.calendar import (expressions, maintenance_window, next_boundary, next_wakeup,
                                    preview, stamp, switching_boundary, walk, wakeups)
from vps_kit.traffic.config import parse_config
from vps_kit.traffic.contracts import ContractError, canonical, utc
from vps_kit.traffic.control import Control
from vps_kit.traffic.execution import execute
from vps_kit.traffic.package import sha
from vps_kit.traffic.sources import VMRACK, paths
from vps_kit.traffic.vmrack_legacy import literal_config, qualify


class FixedCalendarTests(unittest.TestCase):
    def test_three_plans_24_intervals_and_daily_wake(self):
        for profile,amount,clock in [('VMRack-18',1000,'02:05:00'),('VMRack-35',600,'01:50:00'),('VMRack-49',1000,'02:35:00')]:
            cfg=config(profile)
            s=cfg.data['schedule']; a=utc(s['anchor_utc'],'TIME')
            self.assertEqual(cfg.data['inbound']['quota_bytes'],amount//2*1024**3)
            self.assertEqual(expressions(s),['*-*-* '+clock+' UTC'])
            self.assertEqual(walk(s,a-timedelta(seconds=1),24),[a+timedelta(days=30*n) for n in range(24)])
            self.assertEqual(walk(s,a,1,direction=-1),[])
            self.assertEqual(next_boundary(s,a),a+timedelta(days=30))
            self.assertEqual(next_boundary(s,a,inclusive=True),a)
            self.assertEqual(next_wakeup(s,a),a+timedelta(days=1))
            view=preview(cfg,a-timedelta(seconds=1))
            self.assertEqual(view['schedule_kind'],'fixed-interval')
            self.assertEqual(view['next_24_wakeups_utc'],[stamp(a+timedelta(days=n)) for n in range(24)])
            self.assertNotEqual(view['next_24_wakeups_utc'][1],view['next_24'][1]['utc'])
            maintenance_window([s],a-timedelta(days=10))
            maintenance_window([s],a+timedelta(days=1))
            with self.assertRaises(ContractError): maintenance_window([s],a)

    def test_v1_digest_and_monthly_semantics_unchanged(self):
        d=config_data(); v1=parse_config(canonical(d)); v2=copy.deepcopy(d)
        v2['schema_version']='vps-kit.traffic.config/v2'; v2['schedule']['kind']='monthly-calendar'
        cfg=parse_config(canonical(v2))
        self.assertNotIn('kind',v1.data['schedule'])
        self.assertEqual(expressions(v1.data['schedule']),expressions(cfg.data['schedule']))
        start=utc('2026-01-01T00:00:00Z','TIME')
        self.assertEqual(walk(v1.data['schedule'],start,48),walk(cfg.data['schedule'],start,48))

    def test_reject_wrong_schema_interval_negative_first_fraction_and_overflow(self):
        good=config().data
        for k,v in [('interval_seconds',30),('interval_seconds',2592000.0),('first_boundary_utc','2026-10-10T01:50:00Z'),
                    ('first_boundary_utc','2026-11-09T01:50:00.0Z'),('kind','monthly-calendar'),('day',9)]:
            bad=copy.deepcopy(good); bad['schedule'][k]=v
            with self.subTest(k=k,v=v),self.assertRaises(ContractError): parse_config(canonical(bad))
        bad=copy.deepcopy(good); bad['schema_version']='vps-kit.traffic.config/v1'
        with self.assertRaises(ContractError): parse_config(canonical(bad))
        with self.assertRaisesRegex(ContractError,'CALENDAR_RANGE'):
            walk(good['schedule'],utc('9999-12-30T00:00:00Z','TIME'),24)

    def test_display_zone_far_first_leap_and_switch_lower_bound(self):
        d=config().data; d['schedule']['first_boundary_utc']='2028-10-29T01:50:00Z'
        # 24 intervals from 2026-11-09 = 2028-10-29, independent of leap/month length.
        cfg=parse_config(canonical(d)); now=utc('2026-10-01T00:00:00Z','TIME')
        p=preview(cfg,now,'America/Toronto')
        self.assertEqual(p['first_allowed_next_24_utc'][0],d['schedule']['first_boundary_utc'])
        d['schedule']['timezone']='America/Toronto'; new=parse_config(canonical(d))
        due=utc('2028-10-29T01:50:00Z','TIME')
        self.assertEqual(switching_boundary(cfg,new,now,due),due)
        d['schedule']['first_boundary_utc']='2028-11-28T01:50:00Z'
        with self.assertRaisesRegex(ContractError,'SWITCH_FIRST_BOUNDARY_CHANGED'):
            switching_boundary(cfg,parse_config(canonical(d)),now,due)
        points=walk(new.data['schedule'],now,24)
        self.assertEqual(set(int((b-a).total_seconds()) for a,b in zip(points,points[1:])),{2592000})

    def test_shell_configuration_is_never_evaluated(self):
        for raw in [b"source '/tmp/x'\n",b"PANEL_BASE_URL=$(id)\n",b"PANEL_BASE_URL='x'\nPANEL_BASE_URL='x'\n",b"export PANEL_BASE_URL='x'\n"]:
            with self.assertRaises(ContractError): literal_config(raw)


class VMRackDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pkg=tempfile.TemporaryDirectory(); cls.raw=package(Path(cls.pkg.name))

    @classmethod
    def tearDownClass(cls): cls.pkg.cleanup()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def plan(self,h,e): return h[-1].plan_vmrack(h[0],self.raw,sha(self.raw),e,ATTESTATIONS)

    def test_m1_35_49_preserve_objects_and_reset_once_at_natural_boundary(self):
        for profile in ('VMRack-35','VMRack-49'):
            with self.subTest(profile=profile),tempfile.TemporaryDirectory() as tmp:
                h=harness(Path(tmp),config(profile)); cfg,fs,clock,j,b,d=h
                e=seed(h,profile); p=self.plan(h,e)
                counters=copy.deepcopy(b.panel.responses)
                result=d.apply_vmrack(p,self.raw)
                self.assertEqual(result['timer'],'paused'); self.assertEqual(b.panel.responses,counters)
                self.assertEqual(Control(j.load()).next_authorization()[0],1)
                self.assertEqual(b.calls[:2],['pause_old','old_lock_after_pause'])
                head=j.load().head; reads=len(b.panel.reads)
                clock.set(stamp(utc(cfg.data['schedule']['anchor_utc'],'TIME')-timedelta(days=1)))
                self.assertEqual(execute(j,lambda _:b.panel,b.new_system,clock)['post_requests'],0)
                self.assertEqual((head,reads),(j.load().head,len(b.panel.reads)))
                clock.set(cfg.data['schedule']['anchor_utc'])
                self.assertEqual(execute(j,lambda _:b.panel,b.new_system,clock)['result'],'COMPLETED')
                self.assertEqual(b.panel.writes,['client:11','inbound'])
                head=j.load().head
                execute(j,lambda _:b.panel,b.new_system,clock)
                self.assertEqual(j.load().head,head)
                clock.set(stamp(clock.wall+timedelta(days=1)))
                self.assertEqual(execute(j,lambda _:b.panel,b.new_system,clock)['result'],'NOT_DUE')
                self.assertEqual(b.panel.writes,['client:11','inbound'])

    def test_m2_delayed_retirement_requires_same_transaction_reconfirmation(self):
        h=harness(self.root,now='2026-10-10T01:30:00Z'); cfg,fs,clock,j,b,d=h
        e=seed(h,mode='M2',expected_cutover='2026-10-10T01:49:00Z'); p=self.plan(h,e)
        def delay(event):
            if event=='reload': clock.set('2026-10-10T01:51:00Z')
        b.hook=delay
        result=d.apply_vmrack(p,self.raw)
        self.assertEqual(result['result'],'WAITING_CANDIDATE_RECONFIRMATION')
        self.assertEqual(result['first_boundary_utc'],'2026-12-09T01:50:00Z')
        self.assertFalse(j.load().events); self.assertFalse(b.panel.writes)
        with self.assertRaises(ContractError): d.apply_vmrack(p,self.raw)
        with self.assertRaises(ContractError): d.reconfirm(p['operation_id'],'0'*64,self.raw)
        b.hook=lambda *_:None
        d.reconfirm(p['operation_id'],result['candidate_sha256'],self.raw)
        self.assertEqual(stamp(Control(j.load()).next_authorization()[1]),'2026-12-09T01:50:00Z')
        self.assertEqual(d.log.read()[2]['plan'],p)
        self.assertEqual(b.old_timer,'paused'); self.assertEqual(b.new_system.timer,'paused')

    def test_m2_waiting_cancel_restores_only_files_and_never_enables(self):
        h=harness(self.root,now='2026-10-10T01:30:00Z'); cfg,fs,clock,j,b,d=h
        e=seed(h,mode='M2',expected_cutover='2026-10-10T01:49:00Z'); p=self.plan(h,e)
        b.hook=lambda event:clock.set('2026-10-10T01:51:00Z') if event=='reload' else None
        d.apply_vmrack(p,self.raw); b.hook=lambda *_:None
        result=d.rollback(p['operation_id'],cfg.data['task_id'])
        self.assertEqual(result['legacy_resume'],'NOT_AUTHORIZED')
        self.assertTrue(result['restored_legacy_entry_bypasses_new_history'])
        self.assertEqual(b.old_timer,'paused'); self.assertFalse(b.panel.writes)
        with self.assertRaises(ContractError): self.plan(h,e)

    def test_missing_object_late_action_wrong_grid_and_wrong_source_rejected(self):
        h=harness(self.root); e=seed(h); b=h[-2]
        for change in ('missing','late','offgrid','profile','18','custom','unknown-shell'):
            bad=copy.deepcopy(e)
            if change=='missing': bad['actions'].pop()
            elif change=='late': bad['actions'][0]['last_possible_at']='2026-10-10T01:56:00Z'
            elif change=='offgrid': bad['last_boundary_utc']='2026-10-11T01:50:00Z'
            elif change=='profile': bad['profile']='VMRack-49'
            elif change=='18': bad['profile']='VMRack-18'
            elif change=='custom': bad['official_build_confirmed']=False
            else:
                source=paths(VMRACK)['config']; h[1].write(source,b'PANEL_BASE_URL=$(id)\n')
            with self.subTest(change=change),self.assertRaises(ContractError): self.plan(h,bad)
        self.assertFalse(b.panel.writes); self.assertFalse(h[3].load().events)

    def test_empty_proof_rejected_even_with_matching_digest(self):
        h=harness(self.root); e=seed(h)
        proof=next(iter(e['evidence_files']))
        h[1].write(proof,b''); e['evidence_files'][proof]=sha(b'')
        with self.assertRaisesRegex(ContractError,'VMRACK_PROOF_EMPTY_OR_CHANGED'):
            self.plan(h,e)
        self.assertFalse(h[4].panel.reads or h[4].panel.writes)
        self.assertFalse(h[3].load().events)

    def test_provider_budget_must_match_confirmed_source_profile_bytes(self):
        for provider in (None, {'amount':'1000','unit':'GiB'}, {'amount':'600','unit':'GB'}):
            with self.subTest(provider=provider),tempfile.TemporaryDirectory() as tmp:
                data=config().data
                if provider is None: data['provider']=None
                else: data['provider'].update(**provider,model='manual')
                h=harness(Path(tmp),parse_config(canonical(data))); e=seed(h)
                with self.assertRaisesRegex(ContractError,'VMRACK_PROVIDER_BUDGET_BINDING'):
                    self.plan(h,e)
                self.assertFalse(h[4].panel.reads or h[4].panel.writes)
                self.assertFalse(h[3].load().events)
        # 允许等字节的明确十进制单位，不把显示字符串相等当作预算相等。
        with tempfile.TemporaryDirectory() as tmp:
            data=config().data; data['provider'].update(amount='644.2450944',unit='GB')
            h=harness(Path(tmp),parse_config(canonical(data))); e=seed(h)
            self.plan(h,e)
            self.assertFalse(h[4].panel.writes)

    def test_retirement_drift_and_registered_cron_fail_closed(self):
        for change in ('runner','mask','newmarker','deletedmarker','proofbackup','lock','cron','othercron'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as tmp:
                h=harness(Path(tmp)); cfg,fs,clock,j,b,d=h
                e=seed(h,cron=True); p=self.plan(h,e); d.apply_vmrack(p,self.raw)
                src=paths(VMRACK)
                if change=='runner': fs.write(src['runner'],b'changed',0o700)
                elif change=='mask': fs.write('/etc/systemd/system/'+src['timer'],b'changed',0o644)
                elif change=='newmarker': fs.write(src['state']+'/unknown',b'bad')
                elif change=='deletedmarker': fs.path(src['state']+'/client-reset.done').unlink()
                elif change=='proofbackup': fs.write(d.log.read()[2]['backup'][next(iter(e['evidence_files']))]['backup'],b'changed')
                elif change=='lock': b.lock_ok=False
                elif change=='cron': fs.write(e['retire_cron'][0],b'* * * * * root resetTraffic',0o644)
                else: fs.write('/etc/cron.d/other',b'* * * * * root resetTraffic',0o644)
                with self.assertRaises((ContractError,OSError)): d.guard(cfg)
                clock.set(cfg.data['schedule']['anchor_utc'])
                with self.assertRaises((ContractError,OSError)): execute(j,lambda _:b.panel,b.new_system,clock)
                self.assertFalse(b.panel.writes)

    def test_faults_after_each_install_write_allow_controlled_file_rollback(self):
        src=paths(VMRACK)
        for target,point in [(src['runner'],'after_replace'),('/etc/systemd/system/'+src['service'],'after_remove'),
                             ('/etc/vps-kit/traffic/api-token','after_sync'),('/usr/local/lib/vps-kit/current','after_sync')]:
            with self.subTest(target=target),tempfile.TemporaryDirectory() as tmp:
                h=harness(Path(tmp)); cfg,fs,clock,j,b,d=h
                e=seed(h); p=self.plan(h,e)
                def fault(path,stage):
                    if (path,stage)==(target,point): raise OSError('synthetic interruption')
                fs.fault=fault
                with self.assertRaises(OSError): d.apply_vmrack(p,self.raw)
                fs.fault=lambda *_:None
                self.assertEqual(d.log.read()[2]['stage'],'failed')
                d.rollback(p['operation_id'],cfg.data['task_id'])
                self.assertEqual({n:fs.image(n) for n in p['before']},p['before'])
                self.assertEqual(b.old_timer,'paused'); self.assertFalse(b.panel.writes)

    def test_sent_response_lost_pending_blocks_later_day_and_rollback_preserves(self):
        h=harness(self.root); cfg,fs,clock,j,b,d=h
        e=seed(h); p=self.plan(h,e); d.apply_vmrack(p,self.raw)
        clock.set(cfg.data['schedule']['anchor_utc'])
        def lost(event,key):
            if event=='after_send': raise OSError('response lost')
        b.panel.hook=lost
        with self.assertRaises(OSError): execute(j,lambda _:b.panel,b.new_system,clock)
        head=j.load().head
        for day in (1,30,60):
            clock.set(stamp(utc(cfg.data['schedule']['anchor_utc'],'TIME')+timedelta(days=day)))
            with self.assertRaisesRegex(ContractError,'HISTORY_UNRESOLVED'): execute(j,lambda _:b.panel,b.new_system,clock)
        self.assertEqual(b.panel.writes,['client:11'])
        clock.set('2027-01-09T12:00:00Z'); b.panel.hook=lambda *_:None
        d.rollback(p['operation_id'],cfg.data['task_id'])
        self.assertEqual(j.load().head,head)
        self.assertEqual(b.old_timer,'paused')

    def test_missed_boundary_never_catches_up_or_creates_ledger(self):
        h=harness(self.root); cfg,fs,clock,j,b,d=h
        p=self.plan(h,seed(h)); d.apply_vmrack(p,self.raw)
        head=j.load().head; reads=len(b.panel.reads)
        clock.set('2026-11-10T01:50:00Z')
        with self.assertRaisesRegex(ContractError,'MISSED_BOUNDARY'): execute(j,lambda _:b.panel,b.new_system,clock)
        self.assertEqual((j.load().head,len(b.panel.reads)),(head,reads))

    def test_official_3102_native_fixed_supported_but_legacy_and_traffic2_refused(self):
        cfg=config(version='3.10.2'); h=harness(self.root,cfg)
        with self.assertRaisesRegex(ContractError,'VMRACK_SOURCE_PANEL_VERSION'): self.plan(h,seed(h))
        bad=cfg.data; bad['panel']['version']='3.7.0+traffic.2'
        with self.assertRaisesRegex(ContractError,'PANEL_VERSION_UNSUPPORTED'): parse_config(canonical(bad))
