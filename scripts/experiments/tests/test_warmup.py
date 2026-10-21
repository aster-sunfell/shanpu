"""Fixed warmup tolerates trends, but never hides corrupt or unresolved orders."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from protocol import parse_args, warmup_matrix
import runner


class WarmupTest(unittest.TestCase):
    def test_equal_fixed_schedule_and_removed_calibration(self):
        for plan,paths in [('redis-filtering','ABBAAB'),('kafka-burst','BCCBBC')]:
            points=list(warmup_matrix(parse_args([plan])))
            self.assertEqual(list(paths),[p['path'] for p in points])
            self.assertEqual([100,100,200,200,200,200],[p['rate'] for p in points])
            self.assertTrue(all(p['seconds']==30 for p in points))
            self.assertEqual(30000,sum(p['scheduled'] for p in points))
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            parse_args(['redis-filtering','--run-type','calibration'])

    def warm(self, changes=None, error=None, idle_error=False):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root=Path(temp); calls=[]; events=[]
            def measured(p,*unused):
                calls.append(dict(p));events.append('measure')
                value={'valid':True,'correctnessErrors':[],'loadErrors':[], 'unresolvedCount':0,
                       'classes':{'eligible':{'failed':0}},'completed':600,'eligible':600,'started':p['scheduled'],
                       'warmupWindows':[{'eligibleResponseMs':{'p95':v}} for v in (900,400,40)]}
                if changes: value.update(changes(len(calls)))
                return value
            def idle(*args):
                events.append('idle')
                if idle_error: raise RuntimeError('not idle')
            for key in ('check_host_health','prepare'):
                stack.enter_context(patch.object(runner,key))
            stack.enter_context(patch.object(runner,'require_idle',side_effect=idle))
            stack.enter_context(patch.object(runner,'release_redis',side_effect=lambda *a:events.append('release')))
            stack.enter_context(patch.object(runner,'measure',side_effect=measured))
            stack.enter_context(patch('builtins.print'))
            if error:
                with self.assertRaisesRegex(RuntimeError,error):
                    runner.warm_runtime(parse_args(['redis-filtering']),1,root/'private',root,'token')
            else: runner.warm_runtime(parse_args(['redis-filtering']),1,root/'private',root,'token')
            return calls,json.loads((root/'warmup.json').read_text()),events

    def test_latency_trend_does_not_cancel_or_extend_fixed_warmup(self):
        calls,summary,events=self.warm()
        self.assertEqual(6,len(calls));self.assertEqual('completed',summary['status'])
        self.assertEqual(['measure','idle','release']*6,events)

    def test_early_invalid_load_and_response_failure_can_recover(self):
        for changes in ({'valid':False,'loadErrors':['Offered load not achieved']},
                        {'classes':{'eligible':{'failed':1}},'completed':599}):
            calls,summary,events=self.warm(lambda n:changes if n==1 else {})
            self.assertEqual(6,len(calls));self.assertEqual('completed',summary['status'])
            self.assertFalse(summary['points'][0]['healthy'])
            self.assertTrue(summary['points'][0]['recoveryVerified'])
            self.assertEqual(['measure','idle','release']*6,events)

    def test_each_paths_final_block_must_be_healthy(self):
        for failed_block in (5,6):
            calls,summary,_=self.warm(lambda n:{'classes':{'eligible':{'failed':1}},'completed':599} if n==failed_block else {},'Final warmup')
            self.assertEqual(6,len(calls));self.assertEqual('inconclusive',summary['status'])

    def test_corruption_unresolved_and_unknown_load_errors_abort_without_cleanup(self):
        for changes in ({'correctnessErrors':['corrupt']},{'unresolvedCount':1},{'loadErrors':['k6 exit 1']}):
            calls,summary,events=self.warm(lambda n:changes,'Warmup')
            self.assertEqual(1,len(calls));self.assertEqual('failed',summary['status'])
            self.assertEqual(['measure'],events)

    def test_failed_idle_check_never_releases_data(self):
        _,summary,events=self.warm(error='not idle',idle_error=True)
        self.assertEqual('failed',summary['status']);self.assertEqual(['measure','idle'],events)

if __name__=='__main__': unittest.main()
