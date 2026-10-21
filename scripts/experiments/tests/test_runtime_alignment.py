"""Reject environment drift before any pressure is generated."""
import copy
from pathlib import Path
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
from runtime_alignment import ROOT, render_configs, validate_alignment, validate_backend_keepalive


class BackendKeepaliveTest(unittest.TestCase):
    def setUp(self):
        self.configs = [(ROOT/name).read_text() for name in
            ('deploy/nginx.conf.template', 'deploy/nginx.2c2g.conf', 'deploy/nginx.backend-keepalive.conf',
             'backend/src/main/resources/application-2c2g.yml')]

    def test_shared_timeout_expires_before_backend(self):
        self.assertEqual({'idleConnections': 16, 'upstreamTimeoutMs': 3000, 'tomcatTimeoutMs': 5000},
                         validate_backend_keepalive(*self.configs))

    def test_client_timeout_cannot_substitute_for_upstream_policy(self):
        for index in (0, 1):
            values = self.configs.copy()
            values[index] = values[index].replace('include /etc/nginx/backend-keepalive.conf;', 'keepalive 16;')
            with self.subTest(index=index), self.assertRaisesRegex(RuntimeError, 'shared keepalive'):
                validate_backend_keepalive(*values)

    def test_include_outside_upstream_or_commented_out_is_rejected(self):
        for replacement in ('# include /etc/nginx/backend-keepalive.conf;', ''):
            values = self.configs.copy()
            values[0] = values[0].replace('include /etc/nginx/backend-keepalive.conf;', replacement)
            values[0] += '\ninclude /etc/nginx/backend-keepalive.conf;\n'
            with self.subTest(replacement=replacement), self.assertRaises(RuntimeError):
                validate_backend_keepalive(*values)

    def test_unsafe_and_missing_timeouts_are_rejected(self):
        for timeout in ('0s', '5s', '6s', '5000ms', '60000ms', 'invalid'):
            values = self.configs.copy()
            values[2] = values[2].replace('3s', timeout)
            with self.subTest(timeout=timeout), self.assertRaises(RuntimeError):
                validate_backend_keepalive(*values)
        for index, directive in ((2, 'keepalive_timeout 3s;'), (3, 'keep-alive-timeout: 5s')):
            values = self.configs.copy()
            values[index] = values[index].replace(directive, '')
            with self.subTest(index=index), self.assertRaises(RuntimeError):
                validate_backend_keepalive(*values)

    def test_shorter_backend_timeout_is_rejected_and_milliseconds_are_supported(self):
        values = self.configs.copy()
        values[3] = values[3].replace('keep-alive-timeout: 5s', 'keep-alive-timeout: 2s')
        with self.assertRaisesRegex(RuntimeError, 'shorter than Tomcat'):
            validate_backend_keepalive(*values)
        values = self.configs.copy()
        values[2] = values[2].replace('3s', '3000ms')
        self.assertEqual(3000, validate_backend_keepalive(*values)['upstreamTimeoutMs'])


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
            with self.subTest(service=service, key=key), self.assertRaisesRegex(RuntimeError, 'drift|Shared CPU'):
                validate_alignment(*configs)

    def test_local_middleware_cannot_override_production_limits(self):
        configs = copy.deepcopy(self.configs)
        configs[2]['services']['mysql']['mem_limit'] = '1073741824'
        with self.assertRaisesRegex(RuntimeError, 'development mysql'):
            validate_alignment(*configs)

    def test_shared_keepalive_mount_cannot_be_removed_replaced_or_writable(self):
        for index in (0, 1, 2):
            for change in ('remove', 'source', 'writable'):
                configs = copy.deepcopy(self.configs)
                mounts = configs[index]['services']['nginx']['volumes']
                mount = next(m for m in mounts if m['target'] == '/etc/nginx/backend-keepalive.conf')
                if change == 'remove': mounts.remove(mount)
                elif change == 'source': mount['source'] = '/tmp/unrelated-keepalive.conf'
                else: mount['read_only'] = False
                with self.subTest(index=index, change=change), self.assertRaises(RuntimeError):
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
