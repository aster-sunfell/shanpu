"""Preflight gates, memory reserves, snapshot integrity and clean reset regressions."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
import actions
import preflight
from fixtures import POINTS, PRICE
from host import preflight_memory
from protocol import parse_args


class PreflightTest(unittest.TestCase):
    def test_default_dataset_covers_initial_ladders_with_margin_without_formal_wait_windows(self):
        args = parse_args(['redis-filtering', '--rates', '25,50,100,200'])
        plan = preflight.dataset_plan(args, 'full')
        self.assertEqual(283200, plan['initialLadderRequests'])
        self.assertEqual(339840, plan['seedUsers'])
        self.assertEqual(12000, plan['batchUsers'])
        self.assertEqual((5, 10, 10), (plan['probeRate'], plan['warmupSeconds'], plan['probeSeconds']))
        self.assertEqual('redis-filtering', args.plan)
        self.assertLess(preflight.dataset_plan(args, 'redis')['seedUsers'], plan['seedUsers'])

    def test_new_baseline_preflight_sizes_both_plan_specific_ladders_and_binds_them_to_marker(self):
        args = parse_args(['redis-filtering'])
        plan = preflight.dataset_plan(args, 'full')
        self.assertEqual(990000, plan['initialLadderRequests'])
        self.assertEqual(1188000, plan['seedUsers'])
        self.assertEqual(24000, plan['batchUsers'])
        self.assertEqual([200, 225, 250, 275, 300], plan['planRates']['kafka-buffering'])
        parameters = actions.experiment_parameters('full', None, 60, 3, 200, 4)
        self.assertEqual(plan['planRates'], parameters['rates'])
        self.assertEqual(200, parameters['vus'])
        self.assertNotEqual(parameters, actions.experiment_parameters('full', '200,300', 60, 3, 200, 4))

    def test_readiness_rate_is_capped_independently_of_capacity_and_preserves_mixed_eligible_requests(self):
        for rates, expected in (('25,50,100,200', 5), ('100,400', 5), ('1,2', 1)):
            with self.subTest(rates=rates):
                plan = preflight.dataset_plan(parse_args(['redis-filtering', '--rates', rates]), 'full')
                self.assertEqual(expected, plan['probeRate'])
                for seconds in (plan['warmupSeconds'], plan['probeSeconds']):
                    self.assertGreaterEqual(plan['probeRate']*seconds, 10)

    def resources(self, current=300, inactive=0, events='max 0\noom 0\noom_kill 0'):
        return [{'service': 'mysql', 'memoryLimitMiB': 384, 'resources': {
            'memory.current': str(current*1048576), 'memory.stat': f'inactive_file {inactive*1048576}',
            'memory.events': events}}]

    def test_mysql_startup_near_limit_is_rejected_and_file_cache_is_reported_separately(self):
        self.assertIn('error', preflight_memory(self.resources(current=369), 64))
        sample = preflight_memory(self.resources(current=370, inactive=100), 64)
        self.assertNotIn('error', sample)
        self.assertEqual((270, 114), (sample['workingMiB'], sample['headroomMiB']))
        self.assertIn('error', preflight_memory(self.resources(current=360), 32))

    def test_oom_or_unavailable_cgroup_evidence_cannot_pass_the_preflight(self):
        self.assertIn('error', preflight_memory(self.resources(events='max 0\noom 1\noom_kill 1'), 32))
        for rows in ([], [{'service': 'mysql'}], self.resources(events='')):
            with self.subTest(rows=rows), self.assertRaises(RuntimeError): preflight_memory(rows, 32)

    def test_host_preflight_stops_before_starting_load_when_mysql_has_no_startup_reserve(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'source.json').write_text(json.dumps({'sourceSha256': 'source'}))
            target = {'source': {'sourceSha256': 'source'},
                      'services': [{'service': name, 'startedAt': 'before'} for name in actions.LIMITS]}
            calls, overrides = [], []
            container = {'Image': 'image', 'State': {'Running': True, 'OOMKilled': False, 'StartedAt': 'before'}}

            def command(args, **kwargs):
                calls.append(args)
                if args[:2] == ['compose', 'sut']:
                    if 'config' in args: return json.dumps({'services': {n: {'cpuset': '0,1', 'pids_limit': 128} for n in actions.LIMITS}})
                    return args[-1]+'-id'
                if args[:2] == ['compose', 'runner']:
                    if '--file' in args:
                        overrides.append(json.loads(Path(args[args.index('--file')+1]).read_text()))
                    if 'config' in args: return json.dumps({'services': {'load-runner': {'cpuset': '2'}}})
                    return 'load-id'
                if args[:2] == ['docker', 'inspect']: return json.dumps([container]*(len(args)-2))
                if args[:2] == ['docker', 'version']: return '{}'
                return ''

            def validate(name, value, cpuset, pids):
                return {'service': name, 'startedAt': 'before', 'memoryLimitMiB': actions.LIMITS[name][1]}

            with patch.object(actions, 'RUNTIME', root), patch.object(actions, 'REPORTS', root), \
                    patch.dict(os.environ, {'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}), \
                    patch.object(actions, 'require_start_memory'), patch.object(actions, 'check_engine'), \
                    patch.object(actions, 'verify', return_value=target), patch.object(actions, 'source_metadata', return_value={'sourceSha256': 'source'}), \
                    patch.object(actions, 'actions_context', return_value={}), patch.object(actions, 'environment', return_value={}), \
                    patch.object(actions, 'compose', side_effect=lambda *a: ['compose', *a]), \
                    patch.object(actions, 'command', side_effect=command), \
                    patch.object(actions, 'validate_runner_config'), patch.object(actions, 'validate_runner_container'), \
                    patch.object(actions, 'validate_container', side_effect=validate), \
                    patch.object(actions, 'container_resources', return_value=self.resources(current=369)[0]['resources']), \
                    patch('host.memory', return_value={'MemAvailable': 4000*1048576}), patch('builtins.print'):
                with self.assertRaisesRegex(RuntimeError, 'headroom=15.0 MiB'):
                    actions.run_stage(['redis-filtering'], time.monotonic()+300, preflight=True)
            self.assertFalse(any(c[:3] == ['docker', 'start', '--attach'] for c in calls))
            directory = root/'preflight/123-1-preflight'
            self.assertEqual('failed', json.loads((directory/'manifest.json').read_text())['status'])
            self.assertTrue((directory/'containers-final.json').exists())
            self.assertEqual(['python3', '/workspace/scripts/experiments/lib/preflight.py'],
                             overrides[0]['services']['load-runner']['entrypoint'])

    def snapshot(self, directory, corrupt=None):
        planned = 10
        users = [[i, 100+i*10, POINTS-(0 if i==9 else PRICE)] for i in range(11)]
        orders = [[str(uid), uid, PRICE, 2] for i, uid, _ in users if i!=9]
        rows = {'users': users, 'orders': orders,
                'logs': [[uid, oid, -PRICE, 2] for oid, uid, *_ in orders],
                'bought': [row[1] for row in orders]}
        if corrupt == 'balance': rows['users'][0][2] += 1
        if corrupt == 'redis': rows['bought'].pop()
        if corrupt == 'warmup': rows['orders'].pop()
        for kind, data in rows.items():
            (directory/f'before-{kind}.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in data))
        (directory/'before.json').write_text(json.dumps({'stock': 109, 'redisStock': 109}))
        return {'scheduled': planned, 'path': 'B'}

    def test_snapshot_checks_mix_warmup_stock_balances_and_bought_set(self):
        for corrupt in (None, 'balance', 'redis', 'warmup'):
            with self.subTest(corrupt=corrupt), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                config = self.snapshot(directory, corrupt)
                if corrupt:
                    with self.assertRaises(RuntimeError): preflight.verify_fixture(directory, config)
                else:
                    self.assertEqual({'users': 11, 'orders': 10, 'logs': 10}, preflight.verify_fixture(directory, config))

    def test_missing_or_stale_preflight_blocks_formal_measurement_and_valid_marker_is_single_use(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(actions, 'RUNTIME', Path(temp)), \
                patch.object(actions, 'actions_context', return_value={'runId': '123'}), \
                patch.object(actions, 'initialize', return_value={'SUT_INSTANCE_ID': 'fresh'}):
            root = Path(temp)
            parameters = actions.experiment_parameters('full', '25,50', 60, 3, 100, 4)
            (root/'source.json').write_text(json.dumps({'sourceSha256': 'source'}))
            marker = {'status': 'completed', 'parameters': parameters, 'actions': {'runId': '123'}, 'source': 'source',
                      'reset': {'status': 'completed', 'instanceId': 'fresh', 'emptyJournal': True, 'emptyRedis': True,
                                'emptyTables': dict.fromkeys(actions.BUSINESS_TABLES, 0)}}
            with self.assertRaises(RuntimeError): actions.require_preflight(parameters)
            for change in ({'actions': {'runId': 'old'}}, {'source': 'old'}, {'parameters': {}},
                           {'reset': {**marker['reset'], 'instanceId': 'preflight'}},
                           {'reset': {**marker['reset'], 'emptyJournal': False}},
                           {'reset': {**marker['reset'], 'emptyTables': {'t_user': 0}}}):
                with self.subTest(change=change):
                    (root/'preflight-ready.json').write_text(json.dumps({**marker, **change}))
                    with self.assertRaises(RuntimeError): actions.require_preflight(parameters)
            (root/'preflight-ready.json').write_text(json.dumps(marker))
            actions.require_preflight(parameters)
            self.assertFalse((root/'preflight-ready.json').exists())

    def test_formal_suite_never_starts_a_stage_without_preflight(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(actions, 'RUNTIME', Path(temp)), \
                patch.object(actions, 'REPORTS', Path(temp)), patch.object(actions, 'run_stage') as stage:
            root = Path(temp)
            (root/'suite.json').write_text(json.dumps({'status': 'running', 'stages': []}))
            (root/'deadline.json').write_text(json.dumps({'monotonic': time.monotonic()+1000}))
            with self.assertRaisesRegex(RuntimeError, 'preflight'):
                actions.run_suite('full', '25,50', 60, 3, 100, 4, 10)
            stage.assert_not_called()
            self.assertEqual('failed', json.loads((root/'suite.json').read_text())['status'])

    def test_reset_replaces_instance_and_verifies_empty_database_redis_and_journal(self):
        for residual in (False, True):
            with self.subTest(residual=residual), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                previous = {'instanceId': 'old', 'services': [{'service': 'mysql', 'imageId': 'same'}]}
                current = {**previous, 'instanceId': 'new'}
                counts = '\n'.join(f'{name}\t{int(residual and name=="t_order")}' for name in actions.BUSINESS_TABLES)
                with patch.object(actions, 'ENV_FILE', root/'sut.env'), \
                        patch.object(actions, 'cleanup') as cleanup, patch.object(actions, 'start_services') as start, \
                        patch.object(actions, 'verify', return_value=current), patch('builtins.print'), \
                        patch.object(actions, 'initialize', return_value={'SUT_INSTANCE_ID': 'old'}), \
                        patch.object(actions, 'compose', side_effect=lambda *a: list(a)), \
                        patch.object(actions, 'command', side_effect=[counts, '', '0']) as command:
                    if residual:
                        with self.assertRaisesRegex(RuntimeError, 'business rows'): actions.reset_after_preflight(previous)
                    else:
                        result = actions.reset_after_preflight(previous)
                        self.assertTrue(result['emptyRedis'] and result['emptyJournal'])
                        self.assertEqual(3, command.call_count)
                    cleanup.assert_called_once()
                    start.assert_called_once()

    def test_reset_failure_never_creates_ready_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root/'preflight/123-1-preflight'
            directory.mkdir(parents=True)
            (directory/'manifest.json').write_text(json.dumps({'status': 'completed'}))
            (root/'suite.json').write_text(json.dumps({'status': 'running', 'stages': []}))
            (root/'deadline.json').write_text(json.dumps({'monotonic': time.monotonic()+1000}))
            with patch.object(actions, 'RUNTIME', root), patch.object(actions, 'REPORTS', root), \
                    patch.dict(os.environ, {'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}), \
                    patch.object(actions, 'verify'), patch.object(actions, 'run_stage'), patch.object(actions, 'collect'), \
                    patch.object(actions, 'reset_after_preflight', side_effect=RuntimeError('reset failed')):
                with self.assertRaisesRegex(RuntimeError, 'reset failed'):
                    actions.run_preflight('full', '25,50', 60, 3, 100, 4, 5)
            self.assertFalse((root/'preflight-ready.json').exists())
            self.assertEqual('failed', json.loads((directory/'manifest.json').read_text())['status'])

    def test_reset_mysql_shell_selects_configured_database_and_requires_it(self):
        # Execute the real sh command with a fake mysql binary, rather than
        # returning table counts without checking how the CLI was invoked.
        for database in ('shanpu_bench', 'database with spaces', None):
            with self.subTest(database=database), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                executable = root/'mysql'
                executable.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
from pathlib import Path
args = sys.argv[1:]
database = next((arg.split('=', 1)[1] for arg in args if arg.startswith('--database=')), None)
if database != os.environ['EXPECTED_DATABASE']:
    sys.exit('ERROR 1046 (3D000): No database selected')
Path(os.environ['MYSQL_CALL']).write_text(json.dumps({'args': args, 'passwordInEnv': bool(os.environ.get('MYSQL_PWD'))}))
print(os.environ['EMPTY_TABLE_COUNTS'])
''')
                executable.chmod(0o700)
                counts = '\n'.join(f'{name}\t0' for name in actions.BUSINESS_TABLES)
                env = {**os.environ, 'PATH': str(root)+os.pathsep+os.environ['PATH'],
                       'MYSQL_ROOT_PASSWORD': 'test-password-with-$-and-spaces',
                       'EXPECTED_DATABASE': database or 'shanpu_bench',
                       'MYSQL_CALL': str(root/'mysql-call.json'), 'EMPTY_TABLE_COUNTS': counts}
                env.pop('MYSQL_DATABASE', None)
                if database is not None: env['MYSQL_DATABASE'] = database

                def command(args, **kwargs):
                    if args[:4] == ['sut', 'exec', '-T', 'mysql']:
                        return subprocess.run(args[4:], env=env, check=True, text=True,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5).stdout
                    if args[:4] == ['sut', 'exec', '-T', 'backend']: return ''
                    if args[:4] == ['sut', 'exec', '-T', 'redis']: return '0'
                    self.fail(f'Unexpected command: {args[:4]}')

                previous = {'instanceId': 'old', 'services': []}
                with patch.object(actions, 'ENV_FILE', root/'sut.env'), patch.object(actions, 'cleanup'), \
                        patch.object(actions, 'start_services'), patch.object(actions, 'verify', return_value={**previous, 'instanceId': 'new'}), \
                        patch.object(actions, 'initialize', return_value={'SUT_INSTANCE_ID': 'old'}), \
                        patch.object(actions, 'compose', side_effect=lambda *a: list(a)), \
                        patch.object(actions, 'command', side_effect=command), patch('builtins.print'):
                    if database is None:
                        with self.assertRaises(subprocess.CalledProcessError): actions.reset_after_preflight(previous)
                        self.assertFalse((root/'mysql-call.json').exists())
                    else:
                        self.assertEqual(dict.fromkeys(actions.BUSINESS_TABLES, 0),
                                         actions.reset_after_preflight(previous)['emptyTables'])
                        call = json.loads((root/'mysql-call.json').read_text())
                        self.assertIn('--database='+database, call['args'])
                        self.assertTrue(call['passwordInEnv'])
                        self.assertNotIn(env['MYSQL_ROOT_PASSWORD'], ' '.join(call['args']))
                        query = call['args'][call['args'].index('--execute')+1]
                        for table in actions.BUSINESS_TABLES: self.assertIn('FROM '+table, query)

    def test_success_archives_preflight_before_reset_and_issues_marker_for_new_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = root/'preflight/123-1-preflight'
            directory.mkdir(parents=True)
            (directory/'manifest.json').write_text(json.dumps({'status': 'completed'}))
            (root/'suite.json').write_text(json.dumps({'status': 'running', 'stages': []}))
            (root/'source.json').write_text(json.dumps({'sourceSha256': 'source'}))
            (root/'deadline.json').write_text(json.dumps({'monotonic': time.monotonic()+1000}))
            reset = {'status': 'completed', 'instanceId': 'new', 'emptyJournal': True, 'emptyRedis': True,
                     'emptyTables': dict.fromkeys(actions.BUSINESS_TABLES, 0)}
            order = []
            with patch.object(actions, 'RUNTIME', root), patch.object(actions, 'REPORTS', root), \
                    patch.dict(os.environ, {'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1'}), \
                    patch.object(actions, 'actions_context', return_value={'runId': '123'}), \
                    patch.object(actions, 'initialize', return_value={'SUT_INSTANCE_ID': 'new'}), \
                    patch.object(actions, 'verify'), patch('builtins.print'), \
                    patch.object(actions, 'run_stage', side_effect=lambda *a, **kw: order.append('preflight')), \
                    patch.object(actions, 'collect', side_effect=lambda *a: order.append('archive')), \
                    patch.object(actions, 'reset_after_preflight', side_effect=lambda *a: order.append('reset') or reset):
                actions.run_preflight('full', '25,50', 60, 3, 100, 4, 5)
                self.assertEqual(['preflight', 'archive', 'reset'], order)
                self.assertEqual('validation', json.loads((root/'suite.json').read_text())['scope'])
                actions.require_preflight(actions.experiment_parameters('full', '25,50', 60, 3, 100, 4))

    def test_real_probe_orchestration_preserves_validation_scope_and_stops_after_a_bad_probe(self):
        for failure in (None, 'warmup-load', 'probe-load', 'warmup-correctness', 'probe-correctness',
                        'warmup-unresolved', 'probe-unresolved', 'probe-responses', 'probe-empty'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                directory = root/'reports/preflight/run'
                directory.mkdir(parents=True)
                (directory/'manifest.json').write_text(json.dumps({'stage': 'preflight', 'scope': 'validation'}))
                runtime = root/'runtime'
                runtime.mkdir()
                for name in ('target.json', 'load-host.json'): (runtime/name).write_text('{}')
                result = {'valid': True, 'sloPassed': False, 'unresolvedCount': 0, 'started': 50, 'completed': 5, 'eligible': 5,
                          'classes': {'eligible': {'failed': 0}, 'duplicate': {'failed': 0}},
                          'loadErrors': [], 'correctnessErrors': []}
                bad = {**result, 'valid': False, 'loadErrors': ['Missing arrivals']}
                if failure and failure.endswith('correctness'):
                    bad = {**result, 'valid': False, 'correctnessErrors': ['Stock conservation failed']}
                if failure and failure.endswith('unresolved'):
                    bad = {**result, 'unresolvedCount': 1}
                if failure == 'probe-responses':
                    bad = {**result, 'classes': {'eligible': {'failed': 0}, 'duplicate': {'failed': 1}}}
                if failure == 'probe-empty':
                    bad = {**result, 'eligible': 0, 'completed': 0}
                results = [result]*6 if failure is None else ([bad] if failure.startswith('warmup') else [result, bad])
                with patch.object(preflight, 'RUNTIME', runtime), patch.object(preflight, 'REPORTS', root/'reports'), \
                        patch.dict(os.environ, {'BENCH_CONTAINER': '1', 'GITHUB_ACTIONS': 'true',
                                              'BENCH_RUN_ID': 'run', 'BENCH_PREFLIGHT_MODE': 'full'}), \
                        patch.object(preflight, 'validate_deployment'), patch.object(preflight, 'check_host_health'), \
                        patch.object(preflight, 'mysql_settings', return_value={'version': '8.0.46'}), \
                        patch.object(preflight, 'dataset_plan', return_value={'seedUsers': 20, 'batchUsers': 10,
                            'probeRate': 5, 'warmupSeconds': 10, 'probeSeconds': 10}), \
                        patch.object(preflight, 'create_users'), patch.object(preflight, 'credential', return_value={'token': 'private-token'}), \
                        patch.object(preflight, 'require_idle'), patch.object(preflight, 'sql', return_value='7'), \
                        patch.object(preflight, 'prepare') as prepare, patch.object(preflight, 'verify_fixture', return_value={}), \
                        patch.object(preflight, 'release_redis') as release, \
                        patch.object(preflight, 'measure', side_effect=results) as measure, \
                        patch('builtins.print'):
                    args = parse_args(['redis-filtering'])
                    if failure is None:
                        preflight.run(args)
                        self.assertEqual(8, prepare.call_count)
                        self.assertEqual(['A', 'A', 'B', 'B', 'C', 'C'], [c.args[0]['path'] for c in measure.call_args_list])
                        self.assertEqual(['warmup-A', 'probe-A', 'warmup-B', 'probe-B', 'warmup-C', 'probe-C'],
                                         [c.args[2].name for c in measure.call_args_list])
                        self.assertEqual(6, len({c.args[0]['userPrefix'] for c in measure.call_args_list}))
                        self.assertTrue(all((c.args[0]['rate'], c.args[0]['seconds'], c.args[0]['scheduled']) == (5, 10, 50)
                                            for c in measure.call_args_list))
                        self.assertTrue(all(c.args[-1].validation_only for c in measure.call_args_list))
                    else:
                        reason = ('Stock conservation failed' if failure.endswith('correctness') else
                                  'Unresolved accepted orders' if failure.endswith('unresolved') else
                                  'Unexpected duplicate responses' if failure=='probe-responses' else
                                  'Eligible orders incomplete' if failure=='probe-empty' else 'Missing arrivals')
                        with self.assertRaisesRegex(RuntimeError, reason): preflight.run(args)
                        self.assertEqual(len(results), measure.call_count)
                        self.assertEqual(2+len(results)-1, release.call_count)
                summary = json.loads((directory/'preflight.json').read_text())
                self.assertEqual('validation', summary['scope'])
                self.assertEqual('completed' if failure is None else 'failed', summary['status'])
                self.assertEqual(len(results), len(summary['probes']))
                self.assertEqual('completed' if failure is None else 'failed', summary['probes'][-1]['status'])
                self.assertFalse((directory/'capacity.json').exists())
                self.assertNotIn('private-token', (directory/'preflight.json').read_text())


if __name__ == '__main__': unittest.main()
