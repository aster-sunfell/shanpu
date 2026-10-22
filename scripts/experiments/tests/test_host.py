"""Watchdog regressions use synthetic pressure and never launch services."""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
from host import (MIB, HostMonitor, check_host_health, cpu_list, require_start_memory,
                  select_affinity, validate_affinity, finalize_host_evidence)
from protocol import parse_args
from runner import measure


class HostTest(unittest.TestCase):
    def rows(self):
        return [{'cpu':i,'socket':0,'core':i//2,'siblings':[i//2*2,i//2*2+1]} for i in range(8)]

    def test_cpu_selection_keeps_smt_together_for_two_disjoint_pairs(self):
        with patch('host.topology', return_value=self.rows()[:4]):
            pins = select_affinity()
            self.assertEqual(('0,1', '2,3', ''), tuple(pins[k] for k in ('sut', 'runner', 'reserved')))
            self.assertEqual([], pins['crossGroupSmt'])
        self.assertEqual([0,1,2,4], cpu_list('0-2,4'))
        for sut, load in (('0,2','0'), ('0,2','8'), ('0','4')):
            with self.subTest(sut=sut, load=load), self.assertRaises(RuntimeError):
                validate_affinity(sut, load, self.rows())
        with patch('host.topology', return_value=self.rows()[:2]), self.assertRaisesRegex(RuntimeError, 'four'):
            select_affinity()

    def test_malformed_cpu_ranges_are_rejected(self):
        for value in ('-1','3-1','0-9999999','0-1-2','x'):
            with self.subTest(value=value),self.assertRaises(RuntimeError):cpu_list(value)

    def test_start_requires_available_runner_memory(self):
        with patch('host.memory', return_value={'MemAvailable': 2*1024*MIB}):
            with self.assertRaisesRegex(RuntimeError, '5120'):
                require_start_memory()

    def monitor(self,directory):
        return HostMonitor(directory,Mock(return_value=[]),Mock(),Mock(),30)

    def test_pressure_latches_failure_without_clearing_after_recovery(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch('host.memory',return_value={'MemAvailable':900*MIB}):
            monitor=self.monitor(temp)
            monitor.sample()
            self.assertIn('1024',monitor.failure)
            monitor.emergency_stop.assert_not_called()
            with patch('host.memory',return_value={'MemAvailable':4000*MIB}):monitor.sample()
            self.assertIsNotNone(monitor.failure)
            self.assertEqual(2,len((Path(temp)/'samples.jsonl').read_text().splitlines()))

    def test_critical_pressure_stops_owned_containers_once(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch('host.memory',return_value={'MemAvailable':400*MIB}):
            monitor=self.monitor(temp);monitor.sample();monitor.sample()
            monitor.emergency_stop.assert_called_once()

    def test_critical_stop_still_runs_if_evidence_disk_cannot_be_written(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch('host.memory',return_value={'MemAvailable':400*MIB}), \
             patch('host.atomic_json',side_effect=OSError('disk full')):
            monitor=self.monitor(temp)
            with self.assertRaises(OSError):monitor.sample()
            monitor.emergency_stop.assert_called_once()

    def test_failed_observation_and_bounded_fallback(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch('host.memory',return_value={'MemAvailable':4000*MIB}):
            monitor=self.monitor(temp)
            monitor.observe.side_effect=RuntimeError('target restarted')
            monitor.sample()
            monitor.failed_at=time.monotonic()-31
            monitor.sample()
            monitor.force_runner_stop.assert_called_once()
            self.assertEqual('target restarted',monitor.failure)

    def test_clock_rollback_invalidates_actions_measurement(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch('host.memory',return_value={'MemAvailable':4000*MIB}):
            monitor=self.monitor(temp)
            monitor.clock_origin=(time.time()+1,time.monotonic())
            monitor.sample()
            self.assertIn('clock',monitor.failure)

    def test_missing_or_stale_watchdog_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{'BENCH_HOST_STATUS':str(Path(temp)/'status.json')}):
            with self.assertRaises(RuntimeError):check_host_health()
            file=Path(os.environ['BENCH_HOST_STATUS'])
            file.write_text(json.dumps({'monotonicSeconds':time.monotonic()-31,'failure':None}))
            with self.assertRaisesRegex(RuntimeError,'stale'):check_host_health()
            file.write_text(json.dumps({'monotonicSeconds':time.monotonic(),'failure':'low memory'}))
            with self.assertRaisesRegex(RuntimeError,'low memory'):check_host_health()

    def test_host_failure_invalidates_reports_even_after_runner_reported_completed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);directory=root/'report';watch=root/'watch'
            directory.mkdir();watch.mkdir()
            value={'status':'completed','comparisons':[{}]}
            (directory/'experiment.json').write_text(json.dumps(value))
            (directory/'manifest.json').write_text(json.dumps({'status':'completed'}))
            (directory/'report.md').write_text('状态：completed。SQL 调用减少 90%')
            (watch/'status.json').write_text(json.dumps({'failure':'host low memory'}))
            (watch/'samples.jsonl').write_text('{}\n')
            finalize_host_evidence(directory,watch,'host low memory')
            artifact=json.loads((directory/'experiment.json').read_text())
            self.assertEqual('failed',artifact['status'])
            self.assertEqual([],artifact['comparisons'])
            self.assertEqual('failed',json.loads((directory/'manifest.json').read_text())['status'])
            self.assertNotIn('100%',(directory/'report.md').read_text())
            self.assertTrue((directory/'host-watch/samples.jsonl').exists())

    def test_interruption_stops_k6_then_collects_commits_and_marks_point_invalid(self):
        class Load:
            returncode=None
            def poll(self):return self.returncode
            def send_signal(self,value):
                self.signal=value
                self.returncode=130
            def wait(self,timeout):return self.returncode
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            t=int(time.time()*1000)-1000
            rows={'before-users':[[0,7,1000]],'after-users':[[0,7,900]],
                  'before-orders':[],'after-orders':[['900',7,100,2]],
                  'before-logs':[],'after-logs':[[7,'900',-100,2]],
                  'before-bought':[],'after-bought':[7]}
            for name,values in rows.items():
                (directory/f'{name}.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in values))
            for phase,stock in (('before',10),('after',9)):
                (directory/f'{phase}.json').write_text(json.dumps({'stock':stock,'redisStock':stock}))
            events=[{'type':'start','requestId':'steady:0','index':0,'userId':7,'trafficClass':'eligible','startedAtMs':t,'injectionEndMs':t+100},
                    {'type':'finish','requestId':'steady:0','index':0,'startedAtMs':t,'finishedAtMs':t+20,'outcome':'accepted','responseMs':20,'orderId':'900'}]
            (directory/'requests.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in events))
            commit={'activityId':12,'userId':7,'orderId':'900','commitObservedAtMs':t+30}
            journal=Mock(has_more=False,partial_tail=False)
            def reader(offset,config,token,evidence):
                journal.poll.side_effect=lambda:evidence.add_commits([commit]) if not evidence.scalar('SELECT COUNT(*) FROM commits') else None
                return journal
            before={'timeMs':int(time.time()*1000),'settings':{},'localCacheEnabled':False,'journalOffset':0,
                    'events':0,'sqlCalls':{},'processStartedAtMs':1,
                    'transactionPressure':{'version':2,'windowId':1,'activityId':12,'active':0,'started':0,'buckets':[]}}
            load=Load()
            args=parse_args(['kafka-burst'])
            config=dict(activityId=12,path='C',workload='steady',seconds=1,baseSeconds=0,baseRate=1,
                        scheduled=1,rate=1,points=1000,plan='kafka-burst',repeat=1,requestTimeout=1,vus=100)
            with patch('runner.api',return_value=before['transactionPressure']), \
                 patch('runner.require_idle',return_value=before),patch('runner.mysql_counters',return_value={}), \
                 patch('runner.observation.fresh_metrics',side_effect=lambda:{**before,'timeMs':int(time.time()*1000),'events':1}), \
                 patch('runner.lag',return_value={'0':0}),patch('runner.sql',return_value='0'), \
                 patch('runner.Sampler') as sampler,patch('runner.JournalReader',side_effect=reader), \
                 patch('runner.subprocess.Popen',return_value=load),patch('runner.snapshot') as snapshot, \
                 patch('runner.check_host_health',side_effect=[None]+[RuntimeError('low host memory')]*5):
                sampler.return_value.__enter__.return_value.failure=None
                result=measure(config,directory,directory,'test-token',args)
            self.assertEqual(signal.SIGINT,load.signal)
            self.assertEqual(1,result['completed'])
            self.assertFalse(result['valid'])
            self.assertTrue(any('Measurement interrupted' in value for value in result['correctnessErrors']))
            snapshot.assert_called_once_with(config,directory,'after',check_host=False)


if __name__=='__main__':unittest.main()
