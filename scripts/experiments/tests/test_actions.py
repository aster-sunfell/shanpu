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
                        'policy': policy(args), 'paths': {'B': {'passingRps': 100, 'failingRps': 105, 'bounded': True}}}
            (directory / 'capacity.json').write_text(json.dumps(baseline))
            with patch.object(actions, 'RUNTIME', root), patch.object(actions, 'REPORTS', root), \
                    patch.object(actions, 'run_stage', return_value=directory) as stage:
                actions.run_suite('kafka', '25,50', 60, 3, 20, 4, 10)
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
                    patch.object(actions, 'run_stage', return_value=directory) as stage:
                with self.assertRaisesRegex(RuntimeError, 'bracketed'):
                    actions.run_suite('kafka', '25,50', 60, 3, 20, 4, 10)
            self.assertEqual(1, stage.call_count)
            self.assertEqual('failed', json.loads((root / 'suite.json').read_text())['status'])

    def test_internal_deadline_stops_injection_before_job_timeout(self):
        with tempfile.TemporaryDirectory() as temp, patch('host.memory', return_value={'MemAvailable': 4000 * MIB}):
            monitor = HostMonitor(temp, lambda: [], lambda: None, lambda: None, 30, deadline=time.monotonic() - 1)
            monitor.sample()
            self.assertIn('deadline', monitor.failure)


if __name__ == '__main__':
    unittest.main()
