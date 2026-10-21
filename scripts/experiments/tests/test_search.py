"""Exercise bounded capacity search through the runner, with deterministic SUT responses."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
import runner
from protocol import capacities, parse_args, search_candidates


class CapacitySearchTest(unittest.TestCase):
    def run_search(self, passing, *, deadline='inf', invalid=False, adaptive=False, response=None, expected_error=None):
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
                        'workload': config['workload'], 'valid': not invalid,
                        'sloPassed': config['rate'] <= passing[config['path']], 'unresolvedCount': 0,
                        'classes': {'eligible': {'responseMs': {'samples': config['scheduled'], 'p99': 100}}},
                        'completionWindowRps': config['rate'], 'correctnessErrors': [],
                        'loadErrors': ['Insufficient VUs'] if invalid else [],
                        'started': config['scheduled'], 'scheduled': config['scheduled'],
                        'missingRequests': 0, 'generatorLagMs': {'p99': 1}}
                if response: value.update(response(config))
                return value

            def report(directory, meta, results, args):
                reports_written.append((dict(meta), list(results), args))

            with patch.object(runner, 'RUNTIME', runtime), patch.object(runner, 'REPORTS', reports), \
                    patch.dict(os.environ, {'BENCH_CONTAINER': '1', 'GITHUB_ACTIONS': 'true', 'SUT_INSTANCE_ID': 'instance',
                        'MYSQL_DATABASE': 'shanpu_bench', 'BENCH_RUN_ID': 'run', 'BENCH_HOST_STATUS': str(watch/'status.json'),
                        'BENCH_STAGE_DEADLINE': deadline}), \
                    patch.object(runner, 'validate_deployment'), patch.object(runner, 'check_host_health'), \
                    patch.object(runner, 'api', return_value=dict.fromkeys(('status', 'mysql', 'redis', 'kafka'), 'UP')), \
                    patch.object(runner, 'mysql_settings', return_value={}), \
                    patch.object(runner, 'create_users', return_value={}), patch.object(runner, 'credential', return_value={'token': 'private'}), \
                    patch.object(runner, 'require_idle', return_value=metrics), patch.object(runner, 'sql', return_value='7'), \
                    patch.object(runner, 'check_disk'), patch.object(runner, 'prepare', side_effect=prepare), \
                    patch.object(runner, 'warm_runtime'), patch.object(runner, 'measure', side_effect=measure), patch.object(runner, 'release_redis'), \
                    patch.object(runner, 'report', side_effect=report), patch('builtins.print'):
                arguments = ['kafka-buffering', *([] if adaptive else ['--rates', '200,225,250,275,300'])]
                if invalid or expected_error:
                    with self.assertRaisesRegex(RuntimeError, expected_error or 'Load generation invalid'):
                        runner.main(arguments)
                else:
                    self.assertEqual(0, runner.main(arguments))
            meta = json.loads((reports/'kafka-buffering/run/manifest.json').read_text())
            if adaptive:
                for entry in meta.get('probePoints', []):
                    data = json.loads((reports/'kafka-buffering/run'/entry['directory']/'result.json').read_text())
                    self.assertEqual(('probe', 'validation'), (data['phase'], data['scope']))
                self.assertTrue(all(r.get('phase') != 'probe' for _, r in reports_written[-1][1]))
            return calls, meta, capacities(reports_written[-1][2], reports_written[-1][1])

    def test_default_adaptive_runner_starts_at_300_and_separates_probe_evidence(self):
        calls, meta, bounds = self.run_search({'B': 330, 'C': 450}, adaptive=True)
        self.assertEqual([300, 300], [p['rate'] for p in calls[:2]])
        self.assertEqual('completed', meta['status'])
        self.assertLessEqual(len(meta['points']), 15)
        self.assertTrue(meta['probePoints'])
        self.assertTrue(all(b['bounded'] for b in bounds.values()))

    def test_adaptive_runner_retains_generator_limited_probe_and_continues_other_path(self):
        def response(point):
            if point['path'] == 'B' and point['rate'] >= 350:
                return {'valid': False, 'sloPassed': False,
                        'loadErrors': ['k6 exhausted its fixed VU pool (Insufficient VUs); arrivals were dropped']}
            return {}
        calls, meta, bounds = self.run_search({'B': 10000, 'C': 450}, adaptive=True, response=response)
        self.assertEqual('completed', meta['status'])
        self.assertIsNotNone(bounds['B']['passingRps'])
        self.assertIsNone(bounds['B']['failingRps'])
        self.assertTrue(bounds['C']['bounded'])
        self.assertTrue(any(p['state'] == 'generator-limited' for p in meta['search']['paths']['B']['probes']))

    def test_adaptive_runner_still_aborts_on_data_errors_and_unknown_load_failures(self):
        for response, error in (({'correctnessErrors': ['stock mismatch']}, 'Data/evidence invalid'),
                                ({'valid': False, 'loadErrors': ['k6 exit 1']}, 'Load generation invalid'),
                                ({'unresolvedCount': 1}, 'Data/evidence invalid')):
            with self.subTest(response=response):
                calls, meta, bounds = self.run_search({'B': 330, 'C': 450}, adaptive=True,
                                                      response=lambda p: response, expected_error=error)
                self.assertEqual(1, len(calls))
                self.assertEqual('failed', meta['status'])
                self.assertTrue(all(b['passingRps'] is None for b in bounds.values()))

    def test_paths_stop_above_their_own_failure_and_refine_independently(self):
        calls, meta, bounds = self.run_search({'B': 260, 'C': 230})
        for path, ceiling in (('B', 275), ('C', 250)):
            points = [p for p in calls if p['path'] == path]
            self.assertEqual(ceiling, max(p['rate'] for p in points))
            self.assertTrue(all(p['vus'] == 200 for p in points))
            b = bounds[path]
            self.assertTrue(b['bounded'])
            self.assertLessEqual(b['failingRps']-b['passingRps'], b['passingRps']*.1)
            for rate in {p['rate'] for p in points}:
                self.assertEqual({1, 2, 3}, {p['repeat'] for p in points if p['rate'] == rate})
        self.assertEqual('completed', meta['status'])
        self.assertTrue(meta['search']['history'])

    def test_all_pass_extends_ladder_only_within_expansion_limit(self):
        calls, meta, bounds = self.run_search({'B': 10000, 'C': 10000})
        self.assertEqual({375, 469}, {p['rate'] for p in calls if p['rate'] > 300})
        self.assertEqual(2, meta['search']['rounds']['B']['expand'])
        self.assertEqual('lower-bound', bounds['B']['meaning'])
        self.assertEqual(469, bounds['B']['passingRps'])

    def test_failing_anchor_searches_below_200_and_confirms_new_boundary(self):
        calls, _, bounds = self.run_search({'B': 190, 'C': 170})
        self.assertTrue(any(p['rate'] < 200 for p in calls))
        self.assertTrue(all(b['bounded'] for b in bounds.values()))
        self.assertEqual(200, max(p['rate'] for p in calls))

    def test_budget_stops_before_creating_an_unfinishable_rate_block(self):
        calls, meta, bounds = self.run_search({'B': 10000, 'C': 10000}, deadline='0')
        self.assertEqual([], calls)
        self.assertEqual('time-budget', meta['search']['stopReason'])
        self.assertEqual('completed', meta['status'])
        self.assertIsNone(bounds['B']['passingRps'])

    def test_invalid_injection_aborts_instead_of_becoming_a_failure_bound(self):
        calls, meta, bounds = self.run_search({'B': 10000, 'C': 10000}, invalid=True)
        self.assertEqual(1, len(calls))
        self.assertEqual('failed', meta['status'])
        self.assertIsNone(bounds['B']['failingRps'])

    def test_invalid_samples_and_non_monotonic_results_do_not_trigger_more_pressure(self):
        args = parse_args(['kafka-buffering'])
        for invalid, non_monotonic in ((True, False), (False, True)):
            rows = [('point', {'path': 'B', 'rate': rate, 'repeat': repeat, 'workload': 'steady',
                              'valid': not invalid, 'sloPassed': rate == 300,
                              'classes': {'eligible': {'responseMs': {'samples': 1000}}}, 'completionWindowRps': rate})
                    for rate in (200, 300) for repeat in (1, 2, 3)]
            self.assertEqual([], search_candidates(args, rows, {'B': {'expand': 0, 'refine': 0},
                                                                 'C': {'expand': 0, 'refine': 0}}))


if __name__ == '__main__': unittest.main()
