"""Reject environment drift before any pressure is generated."""
import copy
from pathlib import Path
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
from runtime_alignment import render_configs, validate_alignment


@unittest.skipUnless(shutil.which('docker'), 'Compose CLI required; no Docker daemon or services are started')
class RuntimeAlignmentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.configs = render_configs()

    def test_resolved_production_sut_and_local_middleware_are_aligned(self):
        self.assertEqual('aligned', validate_alignment(*self.configs)['status'])

    def test_only_explicit_experiment_differences_are_allowed(self):
        for service, key, value in [('backend', 'cpus', 1), ('backend', 'pids_limit', 200),
                                    ('backend', 'entrypoint', ['java', '-Xmx1g']),
                                    ('nginx', 'volumes', []), ('frontend', 'mem_limit', '999'),
                                    ('mysql', 'command', ['--max-connections=500']),
                                    ('redis', 'image', 'redis:other')]:
            configs = copy.deepcopy(self.configs)
            configs[1]['services'][service][key] = value
            with self.subTest(service=service, key=key), self.assertRaisesRegex(RuntimeError, 'drift'):
                validate_alignment(*configs)

    def test_local_middleware_cannot_override_production_limits(self):
        configs = copy.deepcopy(self.configs)
        configs[2]['services']['mysql']['mem_limit'] = '1073741824'
        with self.assertRaisesRegex(RuntimeError, 'development mysql'):
            validate_alignment(*configs)

    def test_local_is_manual_while_production_and_sut_restart_automatically(self):
        production, sut, development = self.configs
        self.assertTrue(all(s['restart'] == 'unless-stopped' for s in production['services'].values()))
        self.assertTrue(all(s['restart'] == 'unless-stopped' for s in sut['services'].values()))
        self.assertTrue(all(s['restart'] == 'no' for s in development['services'].values()))
        configs = copy.deepcopy(self.configs)
        configs[2]['services']['redis']['restart'] = 'unless-stopped'
        with self.assertRaisesRegex(RuntimeError, 'on demand'): validate_alignment(*configs)
        configs = copy.deepcopy(self.configs)
        configs[1]['services']['backend']['restart'] = 'no'
        with self.assertRaisesRegex(RuntimeError, 'drift'): validate_alignment(*configs)

    def test_experiment_cannot_override_business_properties_or_mounts(self):
        for key, value in [('SERVER_TOMCAT_MAX_CONNECTIONS', '128'), ('JAVA_OPTS', '-Xmx1g'),
                           ('SPRING_PROFILES_ACTIVE', 'sut'), ('SHANPU_EXPERIMENT_JOURNAL', '/tmp/commits.jsonl')]:
            configs = copy.deepcopy(self.configs)
            configs[1]['services']['backend']['environment'][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                validate_alignment(*configs)
        configs = copy.deepcopy(self.configs)
        configs[1]['services']['backend']['volumes'][0]['read_only'] = True
        with self.assertRaises(RuntimeError): validate_alignment(*configs)

    def test_drift_error_never_prints_credentials(self):
        configs = copy.deepcopy(self.configs)
        configs[1]['services']['backend']['environment']['MYSQL_PASSWORD'] = 'private-secret'
        with self.assertRaises(RuntimeError) as raised: validate_alignment(*configs)
        self.assertIn('environment', str(raised.exception))
        self.assertNotIn('private-secret', str(raised.exception))


if __name__ == '__main__': unittest.main()
