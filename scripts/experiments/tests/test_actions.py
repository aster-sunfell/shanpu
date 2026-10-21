"""CI boundaries, baseline lifecycle and deadline regressions without launching Docker."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch, MagicMock

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

    def test_each_run_executes_exactly_one_experiment_and_preserves_inconclusive(self):
        for plan in ('redis-filtering', 'kafka-burst'):
            for status in ('completed', 'inconclusive'):
                with tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    (root/'suite.json').write_text(json.dumps({'status':'running', 'stages':[]}))
                    (root/'deadline.json').write_text(json.dumps({'monotonic':time.monotonic()+1000}))
                    (root/'experiment.json').write_text(json.dumps({'status':status}))
                    with patch.object(actions,'RUNTIME',root), patch.object(actions,'REPORTS',root), \
                            patch.object(actions,'require_preflight') as gate, patch.object(actions,'run_stage',return_value=root) as stage:
                        actions.run_experiment(plan,30)
                    self.assertEqual(1, stage.call_count)
                    self.assertEqual([plan,'--run-type','measurement'], stage.call_args.args[0])
                    gate.assert_called_once_with(actions.experiment_parameters(plan))
                    self.assertEqual(status,json.loads((root/'suite.json').read_text())['status'])

    def test_gateway_errors_are_retained_and_redacted_even_when_stage_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);directory=root/'run';directory.mkdir()
            env=root/'sut.env';env.write_text('PASSWORD=private-secret\n')
            process=MagicMock();process.poll.return_value=None
            def start(*args,**kwargs):
                kwargs['stderr'].write('early error private-secret\n')
                return process
            with patch.object(actions,'RUNTIME',root),patch.object(actions,'ENV_FILE',env), \
                 patch.object(actions,'initialize',return_value={'PASSWORD':'private-secret'}), \
                 patch.object(actions.subprocess,'Popen',side_effect=start):
                with self.assertRaisesRegex(RuntimeError,'business failure'):
                    with actions.gateway_capture('nginx',directory):
                        raise RuntimeError('business failure')
            self.assertEqual('early error [REDACTED]\n',(directory/'nginx-error.log').read_text())
            process.terminate.assert_called_once()

    def test_internal_deadline_stops_injection_before_job_timeout(self):
        with tempfile.TemporaryDirectory() as temp, patch('host.memory', return_value={'MemAvailable': 4000 * MIB}):
            monitor = HostMonitor(temp, lambda: [], lambda: None, lambda: None, 30, deadline=time.monotonic() - 1)
            monitor.sample()
            self.assertIn('deadline', monitor.failure)


if __name__ == '__main__':
    unittest.main()
