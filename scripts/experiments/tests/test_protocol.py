import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from protocol import (parse_args, budgeted_matrix, capacities, comparisons, refinement_rates,
                      policy, validate_baseline, read_baseline, report, validate_deployment, context_contract)
from fixtures import credential


class ProtocolTest(unittest.TestCase):
    def test_baseline_contract_changes_when_actual_database_profile_changes(self):
        target = {'instanceId': 'sut', 'services': []}
        settings = {'version': '8.0.46', 'performance_schema': 'OFF', 'temptable_max_ram': '16777216'}
        contract = context_contract(target, {}, database_settings=settings)
        self.assertNotEqual(contract, context_contract(target, {}))
        for change in ({'performance_schema': 'ON'}, {'temptable_max_ram': '1073741824'}, {'version': '8.0.45'}):
            with self.subTest(change=change):
                self.assertNotEqual(contract, context_contract(target, {}, database_settings={**settings, **change}))

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
        b={'schemaVersion':3,'plan':'kafka-buffering','stage':'capacity','status':'completed',
           'scope':'performance','deployment':'github-actions','policy':policy(args),'contract':'same',
           'paths':{'B':{'passingRps':100,'failingRps':105,'bounded':True}}}
        validate_baseline(b,args,'same')
        for changed in ({'scope':'validation'},{'contract':'different'},{'status':'running'},{'policy':{}},
                        {'deployment':'legacy'}, {'deployment':None}):
            with self.subTest(changed=changed), self.assertRaises(RuntimeError): validate_baseline({**b,**changed},args,'same')
        b['paths']['B']['bounded']=False
        with self.assertRaises(RuntimeError): validate_baseline(b,args,'same')

    def test_new_generator_requires_remeasuring_old_100_vu_baseline(self):
        args = parse_args(['kafka-buffering'])
        baseline = {'schemaVersion': 3, 'plan': args.plan, 'stage': 'capacity', 'status': 'completed',
                    'scope': 'performance', 'deployment': args.deployment, 'policy': policy(args),
                    'parameters': {'vus': 100, 'request_timeout': args.request_timeout, 'drain_seconds': args.drain_seconds},
                    'paths': {'B': {'passingRps': 200, 'failingRps': 212, 'bounded': True}}}
        with self.assertRaisesRegex(RuntimeError, 'VUs'): validate_baseline(baseline, args)
        baseline['parameters']['vus'] = 200
        validate_baseline(baseline, args)
        target = {'instanceId': 'sut', 'services': []}
        original = context_contract(target, {}, args=args)
        args.vus = 100
        self.assertNotEqual(original, context_contract(target, {}, args=args))

    def test_actions_checks_run_identity_environment_and_cpu_overlap(self):
        args = self.args()
        rows = [{'cpu': i, 'socket': 0, 'core': i//2, 'siblings': [i//2*2, i//2*2+1]} for i in range(4)]
        actions = {'runId': '123', 'attempt': '1', 'job': 'performance'}
        target = {'machineId': 'host', 'actions': actions, 'services': [{'cpuset': '0,1'}], 'environment': {'kernel': 'linux'}}
        load = {'machineId': 'host', 'actions': actions, 'cpuset': '2', 'environment': {'cpuTopology': rows}}
        validate_deployment(args, target, load)
        for change in ({'machineId': 'other'}, {'cpuset': '0'}, {'actions': {'runId': 'other'}}, {'environment': None}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                validate_deployment(args, target, {**load, **change})

    def test_environment_affinity_and_actions_run_are_bound_to_baseline(self):
        args = self.args()
        target = {'instanceId': 'sut', 'machineId': 'host', 'environment': {'kernel': 'one'},
                  'actions': {'runId': '123'}, 'services': [{'service': 'backend', 'cpuset': '0,1'}]}
        load = {'machineId': 'host', 'cpuset': '2', 'memoryMiB': 512, 'environment': {'kernel': 'one'}}
        contract = context_contract(target, {}, 'hash', load, args)
        for change in ({'cpuset': '3'}, {'memoryMiB': 1024}, {'environment': {'kernel': 'two'}}):
            self.assertNotEqual(contract, context_contract(target, {}, 'hash', {**load, **change}, args))
        self.assertNotEqual(contract, context_contract({**target, 'actions': {'runId': '456'}}, {}, 'hash', load, args))

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
            self.assertEqual('performance',artifact['scope'])
            self.assertEqual('github-actions',artifact['deployment'])
            self.assertIn('GitHub Actions 单机受限资源性能测量',(directory/'report.md').read_text())
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
