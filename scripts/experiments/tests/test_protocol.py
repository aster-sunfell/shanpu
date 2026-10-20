import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from protocol import (parse_args, budgeted_matrix, capacities, comparisons, refinement_rates,
                      policy, validate_baseline, read_baseline, report)
from fixtures import credential


class ProtocolTest(unittest.TestCase):
    def args(self, *extra): return parse_args(['redis-filtering', '--rates', '100,200', *extra])

    def row(self, path, rate, repeat, ok=True, valid=True, samples=1000, latency=100):
        return ('point', dict(path=path, rate=rate, repeat=repeat, workload='mixed', valid=valid, sloPassed=ok,
                             classes={'eligible':{'responseMs':{'samples':samples,'p99':latency}}}, completionWindowRps=rate/10,
                             acceptedWithinResponseDeadline=10, completedWithinDeadline=9))

    def test_old_demonstration_flags_and_short_formal_runs_are_rejected(self):
        for extra in (['--workloads','valid'], ['--calibrate'], ['--q','2'], ['--seconds','2'], ['--repeats','1']):
            with self.subTest(extra=extra), patch('sys.stderr'), self.assertRaises(SystemExit): self.args(*extra)

    def test_matrix_is_one_mix_or_one_steady_and_keeps_paired_order(self):
        args = self.args('--validation-only', '--repeats', '2')
        points = budgeted_matrix(args)
        self.assertEqual({'mixed'}, {p['workload'] for p in points})
        self.assertEqual(['A','B','B','A','A','B','B','A'], [p['path'] for p in points])
        args = parse_args(['kafka-buffering'])
        self.assertEqual({'steady'}, {p['workload'] for p in budgeted_matrix(args)})

    def test_formal_window_extends_to_meet_sample_target_without_more_memory(self):
        args=self.args('--rates','10,25,50')
        points=budgeted_matrix(args)
        for rate in args.rates:
            samples=sum(p['scheduled']//10 for p in points if p['rate']==rate and p['path']=='A')
            self.assertGreaterEqual(samples,1000)
        self.assertEqual(334,next(p['seconds'] for p in points if p['rate']==10))

    def test_validation_never_outputs_comparison_percentages(self):
        args=self.args('--validation-only','--repeats','1')
        rows=[self.row(p,100,1,latency=100 if p=='A' else 10) for p in ('A','B')]
        self.assertEqual([],comparisons(args,rows))

    def test_high_request_count_does_not_materialize_users_or_fail_old_20k_guard(self):
        points = budgeted_matrix(self.args('--rates','1000', '--seconds','300'))
        self.assertTrue(all(p['scheduled']==300000 for p in points))
        self.assertLess(len(json.dumps(points)), 10000)

    def test_invalid_and_small_sample_points_never_bracket_capacity(self):
        args = self.args()
        results = [self.row('A',100,i) for i in range(1,4)]
        results += [self.row('A',200,i,ok=False,valid=False) for i in range(1,4)]
        c = capacities(args, results)['A']
        self.assertEqual('lower-bound', c['meaning'])
        self.assertIsNone(c['failingRps'])
        small = [self.row('B',100,i,samples=1) for i in range(1,4)]
        self.assertIsNone(capacities(args, small)['B']['passingRps'])

    def test_capacity_refines_real_pass_fail_interval(self):
        args=self.args()
        rows=[self.row('A',rate,i,ok=rate==100) for rate in (100,200) for i in range(1,4)]
        self.assertEqual([150], refinement_rates(args,rows))
        self.assertTrue(capacities(args,rows)['A']['bounded'])

    def test_non_monotonic_results_are_inconclusive(self):
        args=self.args()
        rows=[self.row('A',rate,i,ok=rate==200) for rate in (100,200) for i in range(1,4)]
        self.assertEqual('inconclusive', capacities(args,rows)['A']['meaning'])

    def test_latency_reduction_is_paired_and_requires_every_repeat(self):
        args=self.args()
        rows=[self.row(p,100,i,latency=100 if p=='A' else 60) for p in ('A','B') for i in range(1,4)]
        value=comparisons(args,rows)[0]
        self.assertEqual(40,value['latencyReductionPct']['p50'])
        self.assertIsNone(comparisons(args,rows[:-1])[0]['latencyReductionPct'])

    def test_baseline_rejects_smoke_wrong_slo_and_changed_contract(self):
        args=parse_args(['kafka-buffering'])
        b={'schemaVersion':2,'plan':'kafka-buffering','stage':'capacity','status':'completed',
           'scope':'performance','policy':policy(args),'contract':'same',
           'paths':{'B':{'passingRps':100,'failingRps':105,'bounded':True}}}
        validate_baseline(b,args,'same')
        for changed in ({'scope':'validation'},{'contract':'different'},{'status':'running'},{'policy':{}}):
            with self.subTest(changed=changed), self.assertRaises(RuntimeError): validate_baseline({**b,**changed},args,'same')
        b['paths']['B']['bounded']=False
        with self.assertRaises(RuntimeError): validate_baseline(b,args,'same')

    def test_formal_report_preserves_capacity_uncertainty_and_paired_latency(self):
        args=self.args()
        rows=[]
        for rate in (100,200,400):
            for path in ('A','B'):
                for repeat in range(1,4):
                    name,r=self.row(path,rate,repeat,ok=rate<(200 if path=='A' else 400),latency=100 if path=='A' else 60)
                    r.update(actualRequestRps=rate,completionMs={'p99':100},peakPendingAccepted=0,drainMs=0)
                    rows.append((name,r))
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp)
            report(directory,{'scope':'performance','status':'completed','contract':'test'},rows,args)
            artifact=json.loads((directory/'capacity.json').read_text())
            self.assertEqual(100,artifact['capacityGainPct'])
            self.assertEqual([0,300],artifact['capacityGainRangePct'])
            self.assertEqual(40,artifact['comparisons'][0]['latencyReductionPct']['p50'])
            self.assertIn('区间',(directory/'report.md').read_text())

    def test_baseline_raw_evidence_cannot_be_loaded_into_host_memory(self):
        with tempfile.NamedTemporaryFile() as file:
            file.seek(2*1024*1024)
            file.write(b'x');file.flush()
            with self.assertRaises(RuntimeError):read_baseline(file.name)

    def test_compact_credentials_handle_gaps_and_fixed_timestamp(self):
        pool={'prefix':'xabc','issuedAt':1000,'count':4,'segments':[{'index':0,'userId':7,'count':2},{'index':2,'userId':40,'count':2}]}
        with patch.dict('os.environ',{'JWT_SECRET':'test-secret'}):
            self.assertEqual(41,credential(pool,3)['userId'])
            self.assertEqual(credential(pool,3)['token'],credential(pool,3)['token'])


if __name__ == '__main__': unittest.main()
