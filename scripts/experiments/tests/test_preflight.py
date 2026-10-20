"""Preflight gates, memory reserves, snapshot integrity and clean reset regressions."""
import json
import os
from pathlib import Path
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
        self.assertEqual((200, 3), (plan['probeRate'], plan['probeSeconds']))
        self.assertEqual('redis-filtering', args.plan)
        self.assertLess(preflight.dataset_plan(args, 'redis')['seedUsers'], plan['seedUsers'])

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
                    if 'config' in args: return json.dumps({'services': {n: {'cpuset': '0,1'} for n in actions.LIMITS}})
                    return args[-1]+'-id'
                if args[:2] == ['compose', 'runner']:
                    if '--file' in args:
                        overrides.append(json.loads(Path(args[args.index('--file')+1]).read_text()))
                    if 'config' in args: return json.dumps({'services': {'load-runner': {'cpuset': '2'}}})
                    return 'load-id'
                if args[:2] == ['docker', 'inspect']: return json.dumps([container]*(len(args)-2))
                if args[:2] == ['docker', 'version']: return '{}'
                return ''

            def validate(name, value, cpuset):
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
        for valid in (True, False):
            with self.subTest(valid=valid), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                directory = root/'reports/preflight/run'
                directory.mkdir(parents=True)
                (directory/'manifest.json').write_text(json.dumps({'stage': 'preflight', 'scope': 'validation'}))
                runtime = root/'runtime'
                runtime.mkdir()
                for name in ('target.json', 'load-host.json'): (runtime/name).write_text('{}')
                result = {'valid': valid, 'unresolvedCount': 0, 'started': 3, 'completed': 3,
                          'loadErrors': [] if valid else ['Missing arrivals'], 'correctnessErrors': []}
                with patch.object(preflight, 'RUNTIME', runtime), patch.object(preflight, 'REPORTS', root/'reports'), \
                        patch.dict(os.environ, {'BENCH_CONTAINER': '1', 'GITHUB_ACTIONS': 'true',
                                              'BENCH_RUN_ID': 'run', 'BENCH_PREFLIGHT_MODE': 'full'}), \
                        patch.object(preflight, 'validate_deployment'), patch.object(preflight, 'check_host_health'), \
                        patch.object(preflight, 'mysql_settings', return_value={'version': '8.0.46'}), \
                        patch.object(preflight, 'dataset_plan', return_value={'seedUsers': 20, 'batchUsers': 10, 'probeRate': 1, 'probeSeconds': 3}), \
                        patch.object(preflight, 'create_users'), patch.object(preflight, 'credential', return_value={'token': 'private-token'}), \
                        patch.object(preflight, 'require_idle'), patch.object(preflight, 'sql', return_value='7'), \
                        patch.object(preflight, 'prepare') as prepare, patch.object(preflight, 'verify_fixture', return_value={}), \
                        patch.object(preflight, 'release_redis'), patch.object(preflight, 'measure', return_value=result) as measure, \
                        patch('builtins.print'):
                    args = parse_args(['redis-filtering'])
                    if valid:
                        preflight.run(args)
                        self.assertEqual(5, prepare.call_count)
                        self.assertEqual(['A', 'B', 'C'], [c.args[0]['path'] for c in measure.call_args_list])
                        self.assertTrue(all(c.args[-1].validation_only for c in measure.call_args_list))
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'Missing arrivals'): preflight.run(args)
                        self.assertEqual(1, measure.call_count)
                summary = json.loads((directory/'preflight.json').read_text())
                self.assertEqual('validation', summary['scope'])
                self.assertEqual('completed' if valid else 'failed', summary['status'])
                self.assertFalse((directory/'capacity.json').exists())
                self.assertNotIn('private-token', (directory/'preflight.json').read_text())


if __name__ == '__main__': unittest.main()
