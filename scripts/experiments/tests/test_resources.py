"""Resource contracts must reject misconfigured containers before any load starts."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from infra import LIMITS, MIB, validate_config, validate_container, validate_runner_container, validate_runner_config, validate_engine
from runner import budgeted_matrix, parse_args


class ResourceTest(unittest.TestCase):
    def config(self):
        return {'x-test-machine': {'cpus': 2, 'memory_mib': 2048, 'host_reserve_mib': 608},
                'services': {name: {'cpus': cpu, 'mem_limit': memory * MIB, 'memswap_limit': memory * MIB,
                                    'pids_limit': 128, 'restart': 'no'} for name, (cpu, memory) in LIMITS.items()}}

    def container(self, cpu=.8, memory=512):
        return {'Image': 'sha256:fixture', 'State': {'Running': True, 'OOMKilled': False},
                'HostConfig': {'NanoCpus': round(cpu * 1e9), 'Memory': memory * MIB,
                               'MemorySwap': memory * MIB, 'PidsLimit': 128,
                               'RestartPolicy': {'Name': 'no'}}}

    def test_engine_without_resource_support_is_rejected(self):
        info = {key: True for key in ('MemoryLimit', 'SwapLimit', 'CpuCfsQuota', 'CpuCfsPeriod', 'PidsLimit')}
        info['OSType'] = 'linux'
        validate_engine(info)
        for feature in ('MemoryLimit', 'SwapLimit', 'CpuCfsQuota', 'PidsLimit'):
            with self.subTest(feature=feature), self.assertRaises(RuntimeError):
                validate_engine({**info, feature: False})

    def test_fixed_machine_budget_and_reserve(self):
        budget = validate_config(self.config())
        self.assertEqual(2, budget['cpuLimit'])
        self.assertEqual(2048, budget['containerMemoryMiB'] + budget['hostReserveMiB'])
        for mutation in ('extra_service', 'oversized', 'unlimited_swap', 'missing_cpu'):
            config = self.config()
            if mutation == 'extra_service': config['services']['load-runner'] = {}
            if mutation == 'oversized': config['services']['backend']['mem_limit'] += MIB
            if mutation == 'unlimited_swap': config['services']['backend']['memswap_limit'] = -1
            if mutation == 'missing_cpu': del config['services']['backend']['cpus']
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                validate_config(config)

    def test_engine_must_enforce_limits_and_reject_oom(self):
        validate_container('backend', self.container())
        for key, value in [('NanoCpus', 0), ('Memory', 0), ('MemorySwap', -1), ('Privileged', True)]:
            container = self.container()
            container['HostConfig'][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                validate_container('backend', container)
        container = self.container()
        container['State']['OOMKilled'] = True
        with self.assertRaises(RuntimeError): validate_container('backend', container)

    def test_runner_is_bounded_and_cannot_spawn_other_containers(self):
        validate_runner_container(self.container(cpu=1))
        validate_runner_config({'cpus': 1, 'mem_limit': str(512 * MIB), 'memswap_limit': str(512 * MIB),
                                'pids_limit': 128, 'restart': 'no'})
        container = self.container(cpu=1)
        container['Mounts'] = [{'Source': '/var/run/docker.sock'}]
        with self.assertRaises(RuntimeError): validate_runner_container(container)

    def test_safe_default_vus_and_duration_remain_bounded(self):
        points = budgeted_matrix(parse_args(['redis-filtering']))
        self.assertTrue(all(p['vus'] == 20 for p in points))
        self.assertTrue(all(60 <= p['seconds'] <= 600 for p in points))


if __name__ == '__main__':
    unittest.main()
