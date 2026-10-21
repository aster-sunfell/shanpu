"""Fixed matrix execution, recovery and fatal evidence boundaries."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
import runner
from analysis import LATE_START_ERROR


class FixedExperimentTest(unittest.TestCase):
    def run_experiment(self, *, response=None, expected_error=None, run_type='measurement', warm_error=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime, reports = root/'runtime', root/'reports'
            runtime.mkdir()
            watch = runtime/'watch'
            watch.mkdir()
            (watch/'status.json').write_text('{}')
            (watch/'samples.jsonl').write_text('')
            target = {'instanceId': 'instance', 'machineMemoryMiB': 2048, 'cpuLimit': 2, 'services': []}
            for name, value in [('target.json', target), ('load-host.json', {}),
                                ('source.json', {'sourceSha256': 'source'})]:
                (runtime/name).write_text(json.dumps(value))
            metrics = {'settings': {}, 'localCacheEnabled': True, 'processStartedAtMs': 1}
            calls, reports_written = [], []

            def prepare(config, *args): config['activityId'] = len(calls)+1

            def measure(config, *args):
                calls.append(dict(config))
                value = {'path': config['path'], 'rate': config['rate'], 'repeat': config['repeat'],
                        'workload': config['workload'], 'valid': True,
                        'sloPassed': False, 'unresolvedCount': 0,
                        'classes': {'eligible': {'requests': config['scheduled'], 'errorRate': 0, 'responseMs': {'samples': config['scheduled'], 'p99': 100}}},
                        'completionMs': {'p99': 100}, 'completed': config['scheduled'], 'peakPendingAccepted': 1, 'drainMs': 0,
                        'completionWindowRps': config['rate'], 'correctnessErrors': [],
                        'loadErrors': [],
                        'started': config['scheduled'], 'scheduled': config['scheduled'],
                        'missingRequests': 0, 'generatorLagMs': {'p99': 1}}
                if response: value.update(response(config))
                return value

            def report(directory, meta, results, args):
                reports_written.append((dict(meta), list(results), args))

            with patch.object(runner, 'RUNTIME', runtime), patch.object(runner, 'REPORTS', reports), \
                    patch.dict(os.environ, {'BENCH_CONTAINER': '1', 'GITHUB_ACTIONS': 'true', 'SUT_INSTANCE_ID': 'instance',
                        'MYSQL_DATABASE': 'shanpu_bench', 'BENCH_RUN_ID': 'run', 'BENCH_HOST_STATUS': str(watch/'status.json'),
                        'BENCH_STAGE_DEADLINE': 'inf'}), \
                    patch.object(runner, 'validate_deployment'), patch.object(runner, 'check_host_health'), \
                    patch.object(runner, 'api', return_value=dict.fromkeys(('status', 'mysql', 'redis', 'kafka'), 'UP')), \
                    patch.object(runner, 'mysql_settings', return_value={}), \
                    patch.object(runner, 'create_users', return_value={}), patch.object(runner, 'credential', return_value={'token': 'private'}), \
                    patch.object(runner, 'require_idle', return_value=metrics), patch.object(runner, 'sql', return_value='7'), \
                    patch.object(runner, 'check_disk'), patch.object(runner, 'prepare', side_effect=prepare), \
                    patch.object(runner, 'warm_runtime',side_effect=warm_error), patch.object(runner, 'measure', side_effect=measure), patch.object(runner, 'release_redis'), \
                    patch.object(runner, 'report', side_effect=report), patch('builtins.print'):
                arguments = ['kafka-burst','--run-type',run_type]
                if expected_error:
                    with self.assertRaisesRegex(RuntimeError, expected_error):
                        runner.main(arguments)
                else:
                    self.assertEqual(0, runner.main(arguments))
            meta = json.loads((reports/'kafka-burst/run/manifest.json').read_text())
            return calls,meta

    def test_all_slo_failures_are_results_and_do_not_change_fixed_rates(self):
        calls,meta=self.run_experiment()
        self.assertEqual(6,len(calls))
        self.assertEqual('completed',meta['status'])
        self.assertTrue(all(p['rate']==600 for p in calls))

    def test_load_failure_retains_evidence_and_continues_after_idle_check(self):
        calls,meta=self.run_experiment(response=lambda p: {'valid':False,'loadErrors':['Offered load not achieved']} if p['path']=='B' else {})
        self.assertEqual(6,len(calls))
        self.assertEqual('inconclusive',meta['status'])

    def test_calibration_is_one_baseline_run_and_never_repeats_or_changes_candidates(self):
        calls,meta=self.run_experiment(run_type='calibration')
        self.assertEqual([400,600,800],[p['rate'] for p in calls])
        self.assertEqual({'B'},{p['path'] for p in calls})
        self.assertEqual('calibration',meta['scope'])
        self.assertEqual('completed',meta['status'])

    def test_unstable_warmup_is_inconclusive_without_formal_points(self):
        calls,meta=self.run_experiment(warm_error=runner.WarmupInconclusive('unstable'))
        self.assertEqual([],calls)
        self.assertEqual('inconclusive',meta['status'])

    def test_corruption_unknown_load_error_and_unresolved_abort(self):
        for change,reason in [({'correctnessErrors':['corrupt']},'Data/evidence invalid'),
                              ({'unresolvedCount':1},'Data/evidence invalid'),
                              ({'loadErrors':['k6 exit 1']},'Load generation invalid')]:
            calls,meta=self.run_experiment(response=lambda p: change,expected_error=reason)
            self.assertEqual(1,len(calls))
            self.assertEqual('failed',meta['status'])

if __name__ == '__main__': unittest.main()
