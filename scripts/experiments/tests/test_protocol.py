import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from protocol import parse_args, matrix, comparisons, context_contract, validate_deployment, report


def rows(plan='redis-filtering'):
    args=parse_args([plan])
    result=[]
    for p in matrix(args):
        second=p['path'] == ('B' if plan=='redis-filtering' else 'C')
        r={**p,'valid':True,'sloPassed':not second,'started':p['scheduled'], 'eligible':1200,'completed':1200,
           'classes':{'eligible':{'requests':1200,'responseMs':{'p99':600 if second else 100},'errorRate':0},
                      'duplicate':{'responseMs':{'p99':30 if second else 80}}},
           'backendDelta':{'sqlCalls':{'mapper':1200 if second else 12000},'syncAttempts':1200 if second else 12000},
           'completionMs':{'p99':100},'peakPendingAccepted':10,'drainMs':20,
           'peakOutstanding':20 if second else 10,
           'pressure':{'valid':True,'peakStartRps':500 if second else 600,'peakActive':3 if second else 8},
           'burst':{'completionMs':{'p50':100,'p95':200,'p99':6000 if second else 2000},'completionPct':97.5 if second else 100,'recoveryMs':4000 if second else 2000,'acceptedWithinResponseDeadline':3900 if second else 3000,'completedWithinDeadline':3800 if second else 3900,'completed':4000,'onTimeAcceptedPct':97.5 if second else 75,'onTimeCompletedPct':95 if second else 97.5}}
        result.append((str(len(result)),r))
    return args,result


class ProtocolTest(unittest.TestCase):
    def test_fixed_matrices_interleave_and_have_independent_workloads(self):
        redis=list(matrix(parse_args(['redis-filtering'])))
        kafka=list(matrix(parse_args(['kafka-burst'])))
        self.assertEqual(['A','B','B','A','A','B'],[p['path'] for p in redis])
        self.assertEqual(['B','C','C','B','B','C'],[p['path'] for p in kafka])
        self.assertTrue(all(p['scheduled']==36000 and p['workload']=='mixed' for p in redis))
        self.assertTrue(all(p['scheduled']==11000 and p['recoverySeconds']==30 for p in kafka))

    def test_removed_search_flags_and_short_formal_runs_are_rejected(self):
        for extra in (['--stage','capacity'],['--rates','100,200'],['--baseline-report','x'],['--seconds','2']):
            with patch('sys.stderr'),self.assertRaises(SystemExit): parse_args(['redis-filtering',*extra])

    def test_valid_slo_failure_and_negative_gain_remain_comparable(self):
        args,result=rows()
        item=comparisons(args,result)[0]
        self.assertTrue(item['qualified'])
        self.assertEqual(500,item['metrics']['eligibleResponseP99Ms']['afterMinusBefore']['p50'])
        self.assertEqual(-300,item['metrics']['businessSqlPer1000Requests']['afterMinusBefore']['p50'])
        args,result=rows('kafka-burst')
        metrics=comparisons(args,result)[0]['metrics']
        self.assertEqual(-100,metrics['pressure.peakStartRps']['afterMinusBefore']['p50'])
        self.assertEqual(4000,metrics['burst.completionP99Ms']['afterMinusBefore']['p50'])
        self.assertNotIn('burst.acceptedWithinResponseDeadline',metrics)
        self.assertNotIn('eligibleResponseP99Ms',metrics)

    def test_kafka_has_no_arbitrary_deadline_and_missing_pressure_cannot_claim_shaving(self):
        args,result=rows('kafka-burst')
        from protocol import policy
        self.assertIsNone(policy(args)['completionP99Ms'])
        self.assertIsNone(policy(args)['responseP99Ms'])
        result[0][1].pop('pressure')
        item=comparisons(args,result)[0]
        self.assertFalse(item['pressureQualified'])
        self.assertNotIn('pressure.peakActive',item['metrics'])
        self.assertIn('burst.completionP99Ms',item['metrics'])

    def test_missing_invalid_small_sample_and_validation_cannot_claim_benefits(self):
        args,result=rows()
        self.assertFalse(comparisons(args,result[:-1])[0]['qualified'])
        result[0][1]['valid']=False
        self.assertFalse(comparisons(args,result)[0]['qualified'])
        result[0][1]['valid']=True
        for _,r in result: r['classes']['eligible']['requests']=2
        self.assertFalse(comparisons(args,result)[0]['qualified'])
        args.validation_only=True
        self.assertEqual([],comparisons(args,result))

    def test_report_emits_one_artifact_and_no_claims_after_failure(self):
        args,result=rows()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            report(root,{'status':'failed','scope':'performance'},result,args)
            value=json.loads((root/'experiment.json').read_text())
            self.assertEqual([],value['comparisons'])
            self.assertEqual({'experiment.json','report.md'},{p.name for p in root.iterdir()})

    def test_actions_checks_run_identity_environment_and_cpu_overlap(self):
        args = parse_args(['redis-filtering'])
        rows = [{'cpu': i, 'socket': 0, 'core': i//2, 'siblings': [i//2*2, i//2*2+1]} for i in range(4)]
        actions = {'runId': '123', 'attempt': '1', 'job': 'performance'}
        target = {'machineId': 'host', 'actions': actions, 'services': [{'cpuset': '0,1'}], 'environment': {'kernel': 'linux'}}
        load = {'machineId': 'host', 'actions': actions, 'cpuset': '2', 'environment': {'cpuTopology': rows}}
        validate_deployment(args, target, load)
        for change in ({'machineId': 'other'}, {'cpuset': '0'}, {'actions': {'runId': 'other'}}, {'environment': None}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                validate_deployment(args, target, {**load, **change})

    def test_environment_affinity_and_actions_run_are_bound_to_baseline(self):
        args = parse_args(['redis-filtering'])
        target = {'instanceId': 'sut', 'machineId': 'host', 'environment': {'kernel': 'one'},
                  'actions': {'runId': '123'}, 'services': [{'service': 'backend', 'cpuset': '0,1'}]}
        load = {'machineId': 'host', 'cpuset': '2', 'memoryMiB': 512, 'environment': {'kernel': 'one'}}
        contract = context_contract(target, {}, 'hash', load, args)
        for change in ({'cpuset': '3'}, {'memoryMiB': 1024}, {'environment': {'kernel': 'two'}}):
            self.assertNotEqual(contract, context_contract(target, {}, 'hash', {**load, **change}, args))
        self.assertNotEqual(contract, context_contract({**target, 'actions': {'runId': '456'}}, {}, 'hash', load, args))


if __name__ == '__main__': unittest.main()
