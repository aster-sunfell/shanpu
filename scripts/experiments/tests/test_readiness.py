import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import ExitStack
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from readiness import assess
from protocol import parse_args, matrix, comparisons, calibration_summary
import runner


class ReadinessTest(unittest.TestCase):
    def test_stable_drifting_missing_and_unhealthy_windows(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'samples.jsonl'
            samples=[{'timeMs':t,'backend':{'processCpuTimeNs':t*300000,'poolWaitMs':t*.01}} for t in range(0,30000,2000)]
            path.write_text(''.join(json.dumps(s)+'\n' for s in samples))
            result={'valid':True,'classes':{'eligible':{'failed':0}},'completed':600,'eligible':600,
                    'warmupWindows':[{'startMs':i*10000,'endMs':(i+1)*10000,'requests':2000,
                    'eligibleResponseMs':{'samples':200,'p95':50}} for i in range(3)]}
            self.assertTrue(assess(result,path)['stable'])
            result['warmupWindows'][2]['eligibleResponseMs']['p95']=150
            self.assertFalse(assess(result,path)['stable'])
            result['warmupWindows'][2]['eligibleResponseMs']['p95']=50
            result['classes']['eligible']['failed']=1
            self.assertFalse(assess(result,path)['stable'])
            result['classes']['eligible']['failed']=0
            path.write_text('')
            self.assertFalse(assess(result,path)['stable'])

    def warm(self, stable, corrupt=False):
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            root=Path(temp); calls=[]
            def measured(p,*unused):
                calls.append((p['path'],p['seconds']))
                return {'valid':True,'correctnessErrors':['corrupt'] if corrupt else [],'loadErrors':[], 'unresolvedCount':0}
            for key in ('check_host_health','prepare','require_idle','release_redis'):
                stack.enter_context(patch.object(runner,key))
            stack.enter_context(patch.object(runner,'measure',side_effect=measured))
            stack.enter_context(patch.object(runner,'assess',return_value={'stable':stable,'errors':[]}))
            stack.enter_context(patch('builtins.print'))
            if corrupt:
                with self.assertRaisesRegex(RuntimeError,'data invalid'):runner.warm_runtime(parse_args(['redis-filtering']),1,root/'private',root,'token')
            elif not stable:
                with self.assertRaises(runner.WarmupInconclusive):runner.warm_runtime(parse_args(['redis-filtering']),1,root/'private',root,'token')
            else: runner.warm_runtime(parse_args(['redis-filtering']),1,root/'private',root,'token')
            return calls,json.loads((root/'warmup.json').read_text())

    def test_bounded_equal_warmup_and_unsafe_evidence(self):
        calls,summary=self.warm(True)
        self.assertEqual([('A',30),('B',30),('B',30),('A',30)],calls)
        self.assertEqual('completed',summary['status'])
        calls,summary=self.warm(False)
        self.assertEqual(6,len(calls)); self.assertEqual('inconclusive',summary['status'])
        calls,summary=self.warm(False,corrupt=True)
        self.assertEqual(1,len(calls)); self.assertEqual('failed',summary['status'])


class CalibrationTest(unittest.TestCase):
    def test_only_three_fixed_baseline_candidates_never_formal_comparisons(self):
        for plan,baseline in [('redis-filtering','A'),('kafka-burst','B')]:
            args=parse_args([plan,'--run-type','calibration'])
            points=list(matrix(args))
            self.assertEqual([400,600,800],[p['rate'] for p in points])
            self.assertEqual({baseline},{p['path'] for p in points})
            self.assertEqual([],comparisons(args,[]))

    def test_recommendation_uses_baseline_pressure_not_improvement_and_never_applies(self):
        args=parse_args(['redis-filtering','--run-type','calibration'])
        def point(rate,p99,wait,valid=True,errors=0):
            return str(rate),{'rate':rate,'valid':valid,'outcomes':{'network_error':errors},
                'backendDelta':{'poolWaitCount':wait},'classes':{'eligible':{'responseMs':{'p99':p99}}}}
        rows=[point(400,150,0),point(600,450,10),point(800,800,20)]
        result=calibration_summary(args,rows)
        self.assertEqual(600,result['recommendedRate']); self.assertFalse(result['automaticallyApplied'])
        self.assertEqual([400,600,800],[p['rate'] for p in matrix(args)])
        self.assertIsNone(calibration_summary(args,[point(400,500,5,errors=1),point(600,500,5,valid=False),point(800,2000,20)])['recommendedRate'])

if __name__=='__main__': unittest.main()
