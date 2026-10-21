"""CI boundaries, baseline lifecycle and deadline regressions without launching Docker."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import actions
from host import HostMonitor, MIB
from protocol import VERSION, parse_args, policy


class ActionsTest(unittest.TestCase):
    def test_final_diagnostics_record_stopped_mysql_without_exporting_inspect_secrets(self):
        value = {'Image': 'sha256:mysql', 'RestartCount': 0,
                 'State': {'Status': 'exited', 'Running': False, 'OOMKilled': True, 'ExitCode': 137,
                           'StartedAt': 'before', 'FinishedAt': 'after'},
                 'Config': {'Env': ['MYSQL_ROOT_PASSWORD=private-secret']},
                 'HostConfig': {'Binds': ['/private/path']}}
        with tempfile.TemporaryDirectory() as temp, patch.object(actions, 'command', return_value=json.dumps([value])), \
                patch('builtins.print') as output:
            path = Path(temp) / 'containers-final.json'
            actions.capture_final_containers({'mysql': 'container-id'}, path)
            row = json.loads(path.read_text())['containers'][0]
            self.assertEqual('mysql', row['service'])
            self.assertTrue(row['state']['OOMKilled'])
            self.assertEqual(137, row['state']['ExitCode'])
            self.assertNotIn('private-secret', path.read_text())
            self.assertNotIn('/private/path', path.read_text())
            self.assertIn('OOM=True, exit=137', output.call_args.args[0])

    def test_execution_rejects_workstations_and_self_hosted_runners(self):
        for value in ({}, {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'self-hosted'}):
            with patch.dict(os.environ, value, clear=True), self.assertRaisesRegex(RuntimeError, 'GitHub-hosted'):
                actions.require_actions()

    def test_container_socket_is_not_part_of_runner_volume_contract(self):
        text = (Path(__file__).resolve().parents[3] / 'experiments/compose.runner.yml').read_text()
        self.assertNotIn('docker.sock', text)
        self.assertIn('/bench/runtime', text)
        self.assertIn('/bench/reports', text)

    def test_capacity_and_burst_reuse_same_job_baseline_and_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'suite.json').write_text(json.dumps({'status': 'running', 'stages': []}))
            (root / 'deadline.json').write_text(json.dumps({'monotonic': time.monotonic() + 1000}))
            directory = root / 'kafka-buffering' / 'capacity'
            directory.mkdir(parents=True)
            args = parse_args(['kafka-buffering'])
            baseline = {'schemaVersion': VERSION, 'plan': 'kafka-buffering', 'stage': 'capacity',
                        'status': 'completed', 'scope': 'performance', 'deployment': 'github-actions',
                        'policy': policy(args), 'paths': {p: {'passingRps': 100, 'failingRps': 105, 'bounded': True}
                                                       for p in ('B', 'C')}}
            (directory / 'capacity.json').write_text(json.dumps(baseline))
            with patch.object(actions, 'RUNTIME', root), patch.object(actions, 'REPORTS', root), \
                    patch.object(actions, 'require_preflight'), \
                    patch.object(actions, 'run_stage', return_value=directory) as stage:
                actions.run_suite('kafka', '25,50', 60, 3, 20, 4, 30)
            self.assertEqual(2, stage.call_count)
            first, second = stage.call_args_list
            self.assertEqual(first.args[1], second.args[1])
            self.assertIn(str(directory / 'capacity.json'), second.args[0])
            self.assertIn('burst', second.args[0])
            self.assertEqual('completed', json.loads((root / 'suite.json').read_text())['status'])
            baseline['paths']['B']['bounded'] = False
            (directory / 'capacity.json').write_text(json.dumps(baseline))
            (root / 'suite.json').write_text(json.dumps({'status': 'running', 'stages': []}))
            with patch.object(actions, 'RUNTIME', root), patch.object(actions, 'REPORTS', root), \
                    patch.object(actions, 'require_preflight'), \
                    patch.object(actions, 'run_stage', return_value=directory) as stage:
                actions.run_suite('kafka', '25,50', 60, 3, 20, 4, 10)
            self.assertEqual(1, stage.call_count)
            suite = json.loads((root / 'suite.json').read_text())
            self.assertEqual('inconclusive', suite['status'])
            self.assertEqual('burst', suite['skipped'][0]['stage'])
            self.assertIn('bracketed', suite['skipped'][0]['reason'])
            baseline['paths']['B']['bounded'] = True
            (directory/'capacity.json').write_text(json.dumps(baseline))
            (root/'suite.json').write_text(json.dumps({'status': 'running', 'stages': []}))
            with patch.object(actions, 'RUNTIME', root), patch.object(actions, 'REPORTS', root), \
                    patch.object(actions, 'require_preflight'), patch('builtins.print'), \
                    patch.object(actions, 'run_stage', return_value=directory) as stage:
                actions.run_suite('kafka', '25,50', 60, 3, 20, 4, 10)
            self.assertEqual(1, stage.call_count)
            suite = json.loads((root/'suite.json').read_text())
            self.assertEqual('inconclusive', suite['status'])
            self.assertIn('budget', suite['skipped'][0]['reason'])

    def test_unbounded_baseline_with_invalid_provenance_still_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'suite.json').write_text(json.dumps({'status': 'running', 'stages': []}))
            (root/'deadline.json').write_text(json.dumps({'monotonic': time.monotonic()+1000}))
            (root/'capacity.json').write_text(json.dumps({'paths': {'B': {'passingRps': 200}}}))
            with patch.object(actions, 'RUNTIME', root), patch.object(actions, 'REPORTS', root), \
                    patch.object(actions, 'require_preflight'), patch.object(actions, 'run_stage', return_value=root), \
                    self.assertRaisesRegex(RuntimeError, 'protocol'):
                actions.run_suite('kafka', None, 60, 3, 200, 4, 10)
            self.assertEqual('failed', json.loads((root/'suite.json').read_text())['status'])

    def test_default_full_suite_uses_distinct_ladders_and_same_200_vu_generator_for_burst(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'suite.json').write_text(json.dumps({'status': 'running', 'stages': []}))
            (root/'deadline.json').write_text(json.dumps({'monotonic': time.monotonic()+1000}))
            called = []

            def stage(arguments, deadline):
                args = parse_args(arguments)
                called.append(args)
                directory = root/args.plan/args.stage
                directory.mkdir(parents=True)
                if args.stage == 'capacity':
                    paths = ('A', 'B') if args.plan == 'redis-filtering' else ('B', 'C')
                    baseline = {'schemaVersion': VERSION, 'plan': args.plan, 'stage': args.stage,
                                'status': 'completed', 'scope': 'performance', 'deployment': args.deployment,
                                'policy': policy(args), 'parameters': vars(args),
                                'paths': {p: {'passingRps': 200, 'failingRps': 210, 'bounded': True} for p in paths}}
                    (directory/'capacity.json').write_text(json.dumps(baseline))
                return directory

            with patch.object(actions, 'RUNTIME', root), patch.object(actions, 'REPORTS', root), \
                    patch.object(actions, 'require_preflight'), patch.object(actions, 'run_stage', side_effect=stage):
                actions.run_suite('full', None, 60, 3, 200, 4, 30)
            self.assertEqual(3, len(called))
            self.assertEqual([200, 250, 300, 350, 400], called[0].rates)
            self.assertEqual([200, 225, 250, 275, 300], called[1].rates)
            self.assertEqual('burst', called[2].stage)
            self.assertEqual(200, called[2].q)
            self.assertTrue(all(args.vus == 200 for args in called))
            self.assertEqual('completed', json.loads((root/'suite.json').read_text())['status'])

    def test_global_budget_exhaustion_skips_new_capacity_stage_without_marking_tool_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'suite.json').write_text(json.dumps({'status': 'running', 'stages': []}))
            (root/'deadline.json').write_text(json.dumps({'monotonic': time.monotonic()+100}))
            with patch.object(actions, 'RUNTIME', root), patch.object(actions, 'REPORTS', root), \
                    patch.object(actions, 'require_preflight'), patch.object(actions, 'run_stage') as stage:
                actions.run_suite('full', None, 60, 3, 200, 4, 10)
            stage.assert_not_called()
            self.assertEqual('inconclusive', json.loads((root/'suite.json').read_text())['status'])

    def test_internal_deadline_stops_injection_before_job_timeout(self):
        with tempfile.TemporaryDirectory() as temp, patch('host.memory', return_value={'MemAvailable': 4000 * MIB}):
            monitor = HostMonitor(temp, lambda: [], lambda: None, lambda: None, 30, deadline=time.monotonic() - 1)
            monitor.sample()
            self.assertIn('deadline', monitor.failure)


if __name__ == '__main__':
    unittest.main()
