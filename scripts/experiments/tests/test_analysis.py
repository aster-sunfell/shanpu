import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from analysis import Evidence, percentiles, write_json

POLICY = {'responseP99Ms': 500, 'completionP99Ms': 5000, 'maxErrorRate': .001}


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.config = dict(activityId=12, path='C', workload='steady', seconds=1, baseSeconds=0,
                           scheduled=1, rate=1, points=1000)
        self.start = dict(type='start', requestId='steady:0', index=0, userId=7, trafficClass='eligible',
                          startedAtMs=1000, injectionEndMs=2000)
        self.finish = dict(type='finish', requestId='steady:0', index=0, startedAtMs=1000, finishedAtMs=1020,
                           outcome='accepted', responseMs=20, orderId='900')
        self.commit = dict(activityId=12, userId=7, orderId='900', commitObservedAtMs=2100)
        self.rows = {'before-users': [[0,7,1000]], 'after-users': [[0,7,900]],
                     'before-orders': [], 'after-orders': [['900',7,100,2]],
                     'before-logs': [], 'after-logs': [[7,'900',-100,2]],
                     'before-bought': [], 'after-bought': [7]}

    def tearDown(self): self.temp.cleanup()

    def run_evidence(self, cutoff=3000, k6=None):
        for name, rows in self.rows.items():
            (self.directory / f'{name}.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
        write_json(self.directory / 'before.json', {'stock':10,'redisStock':10})
        write_json(self.directory / 'after.json', {'stock':9,'redisStock':9})
        (self.directory / 'requests.jsonl').write_text(json.dumps(self.start)+'\n'+(json.dumps(self.finish)+'\n' if self.finish else ''))
        e = Evidence(self.directory)
        try:
            e.load_snapshot('before'); e.load_snapshot('after'); e.load_requests(); e.add_commits([self.commit])
            return e.analyze(self.config, k6 or {}, cutoff, POLICY)
        finally: e.close()

    def test_acceptance_completion_drain_and_on_time_are_distinct(self):
        result = self.run_evidence()
        self.assertEqual([], result['correctnessErrors'])
        self.assertEqual([], result['loadErrors'])
        self.assertEqual(1, result['accepted'])
        self.assertEqual(1, result['peakHttpInFlight'])
        self.assertEqual(0, result['completionWindowRps'])
        self.assertEqual(1100, result['completionMs']['p99'])
        self.assertEqual(100, result['drainMs'])
        self.assertEqual(1, result['peakPendingAccepted'])
        self.assertEqual(1, result['completedWithinDeadline'])

    def test_unfinished_has_no_zero_latency_or_false_coverage(self):
        result = self.run_evidence(cutoff=2050)
        self.assertEqual(1, result['unresolvedCount'])
        self.assertEqual(0, result['completed'])
        self.assertIsNone(result['completionMs']['p99'])
        self.assertIsNone(result['drainMs'])
        self.assertFalse(result['valid'])

    def test_timeout_can_commit_but_fails_eligible_slo(self):
        self.finish.update(outcome='timeout', orderId=None)
        r = self.run_evidence()
        self.assertEqual(0, r['accepted'])
        self.assertEqual(1, r['completed'])
        self.assertEqual(1, r['committedAfterResponseFailure'])
        self.assertEqual(1, r['classes']['eligible']['errorRate'])
        self.assertTrue(r['valid'])
        self.assertFalse(r['sloPassed'])

    def test_duplicate_point_log_is_detected(self):
        self.rows['after-logs'] *= 2
        self.assertTrue(any('Point log mismatch' in s for s in self.run_evidence()['correctnessErrors']))

    def test_dropped_load_is_not_a_service_capacity_failure(self):
        r = self.run_evidence(k6={'metrics':{'dropped_iterations':{'values':{'count':1}}}})
        self.assertEqual([], r['correctnessErrors'])
        self.assertIn('Offered load not achieved', r['loadErrors'])
        self.assertFalse(r['valid'])

    def test_delayed_injection_is_invalid_even_when_total_count_matches(self):
        self.start['scheduledAtMs']=800
        result=self.run_evidence()
        self.assertEqual(1,result['started'])
        self.assertIn('Injection timing does not follow the arrival schedule',result['loadErrors'])
        self.assertFalse(result['valid'])

    def test_commit_before_response_has_no_false_pending_peak(self):
        self.commit['commitObservedAtMs'] = 1010
        self.assertEqual(0, self.run_evidence()['peakPendingAccepted'])

    def test_completion_deadline_is_per_request_not_after_injection(self):
        self.commit['commitObservedAtMs'] = 6100
        r = self.run_evidence(cutoff=7000)
        self.assertEqual(1, r['completed'])
        self.assertEqual(0, r['completedWithinDeadline'])
        self.assertFalse(r['sloPassed'])

    def test_growing_backlog_cannot_be_hidden_by_successful_drain(self):
        count=600
        config={**self.config,'seconds':60,'scheduled':count,'rate':10}
        for phase in ('before','after'):
            rows={'users':[[i,100+i,1000 if phase=='before' else 900] for i in range(count)],
                  'orders':[] if phase=='before' else [[str(i),100+i,100,2] for i in range(count)],
                  'logs':[] if phase=='before' else [[100+i,str(i),-100,2] for i in range(count)],
                  'bought':[] if phase=='before' else [100+i for i in range(count)]}
            for kind,values in rows.items():
                (self.directory/f'{phase}-{kind}.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in values))
            write_json(self.directory/f'{phase}.json',{'stock':count+100 if phase=='before' else 100,'redisStock':count+100 if phase=='before' else 100})
        with (self.directory/'requests.jsonl').open('w') as file:
            for i in range(count):
                t=1000+i*100
                file.write(json.dumps({**self.start,'requestId':str(i),'index':i,'userId':100+i,'startedAtMs':t,'injectionEndMs':61000})+'\n')
                file.write(json.dumps({**self.finish,'requestId':str(i),'index':i,'startedAtMs':t,'finishedAtMs':t+10,'orderId':str(i)})+'\n')
        e=Evidence(self.directory)
        try:
            e.load_snapshot('before');e.load_snapshot('after');e.load_requests()
            e.add_commits({**self.commit,'userId':100+i,'orderId':str(i),'commitObservedAtMs':1010+i*150} for i in range(count))
            result=e.analyze(config,{},100000,{**POLICY,'completionP99Ms':100000})
            self.assertEqual(count,result['completed'])
            self.assertEqual(1,result['peakHttpInFlight'])
            self.assertTrue(result['valid'])
            self.assertFalse(result['backlog']['stable'])
            self.assertFalse(result['sloPassed'])
        finally:e.close()

    def test_missing_values_remain_null(self):
        self.assertIsNone(percentiles([])['p99'])


if __name__ == '__main__': unittest.main()
