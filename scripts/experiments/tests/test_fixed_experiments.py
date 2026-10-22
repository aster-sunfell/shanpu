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
    def run_experiment(self, *, response=None, expected_error=None, warm_error=None):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime, reports = root/'runtime', root/'reports'
            runtime.mkdir()
            watch = runtime/'watch'
            watch.mkdir()
            (watch/'status.json').write_text('{}')
            (watch/'samples.jsonl').write_text('')
            target = {'instanceId': 'instance', 'machineMemoryMiB': 2048, 'cpuLimit': 2, 'cpuModel': 'shared-cpuset', 'services': []}
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
                        'sloPassed': None, 'unresolvedCount': 0,
                        'pressure':{'valid':True,'peakStartRps':600,'peakActive':8},
                        'burst':{'completionMs':{'p99':8000}},
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
                arguments = ['kafka-burst']
                if expected_error:
                    with self.assertRaisesRegex(RuntimeError, expected_error):
                        runner.main(arguments)
                else:
                    self.assertEqual(0, runner.main(arguments))
            meta = json.loads((reports/'kafka-burst/run/manifest.json').read_text())
            return calls,meta

    def test_no_shaving_and_slow_orders_are_results_and_do_not_change_fixed_rates(self):
        calls,meta=self.run_experiment()
        self.assertEqual(6,len(calls))
        self.assertEqual('completed',meta['status'])
        self.assertTrue(all(p['rate']==1000 for p in calls))

    def test_missing_pressure_stops_before_repeating_invalid_statistics(self):
        calls,meta=self.run_experiment(response=lambda p: {'pressure':{'valid':False,'errors':['missing window']}}, expected_error='missing window')
        self.assertEqual(1,len(calls))
        self.assertEqual('failed',meta['status'])

    def test_load_failure_retains_evidence_and_stops_remaining_points(self):
        calls,meta=self.run_experiment(response=lambda p: {'valid':False,'loadErrors':['Offered load not achieved']} if p['path']=='B' else {}, expected_error='Offered load not achieved')
        self.assertEqual(1,len(calls))
        self.assertEqual('failed',meta['status'])

    def test_unhealthy_warmup_is_inconclusive_without_formal_points(self):
        calls,meta=self.run_experiment(warm_error=runner.WarmupInconclusive('unhealthy'))
        self.assertEqual([],calls)
        self.assertEqual('inconclusive',meta['status'])

    def test_corruption_unknown_load_error_and_unresolved_abort(self):
        for change,reason in [({'correctnessErrors':['corrupt']},'Measurement invalid'),
                              ({'unresolvedCount':1},'Measurement invalid'),
                              ({'loadErrors':['k6 exit 1']},'Measurement invalid')]:
            calls,meta=self.run_experiment(response=lambda p: change,expected_error=reason)
            self.assertEqual(1,len(calls))
            self.assertEqual('failed',meta['status'])

if __name__ == '__main__': unittest.main()
