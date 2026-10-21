"""Resource contracts must reject misconfigured containers before any load starts."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from actions import MEMORY_LIMITS, MIB, validate_config, validate_container, validate_runner_container, validate_runner_config, validate_engine
from runner import budgeted_matrix, parse_args
from host import validate_shared_cpu_resources


class ResourceTest(unittest.TestCase):
    def config(self):
        return {'x-test-machine': {'cpus': 2, 'memory_mib': 2048, 'host_reserve_mib': 416},
                'services': {name: {'cpuset': '0,1', 'mem_limit': memory * MIB, 'memswap_limit': memory * MIB,
                                    'pids_limit': 128, 'restart': 'unless-stopped'} for name, memory in MEMORY_LIMITS.items()}}

    def container(self, cpu=0, memory=512, restart='unless-stopped'):
        return {'Image': 'sha256:fixture', 'State': {'Running': True, 'OOMKilled': False},
                'HostConfig': {'NanoCpus': round(cpu * 1e9), 'CpusetCpus': '0,1', 'Memory': memory * MIB,
                               'Ulimits':[{'Name':'nofile','Soft':8192,'Hard':8192}], 'MemorySwap': memory * MIB, 'PidsLimit': 128,
                               'RestartPolicy': {'Name': restart}}}

    def test_shared_cpu_contract_rejects_fragmented_or_mismatched_sets(self):
        for change in ({'cpus': .75}, {'cpu_quota':75000}, {'cpu_shares':512},
                       {'cpuset':'0,2'}, {'cpuset':'0-2'},
                       {'deploy':{'resources':{'limits':{'cpus':'0.75'}}}}):
            config=self.config()
            config['services']['backend'].update(change)
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                validate_config(config)
        config=self.config()
        config['services']['backend']['cpuset']='0-1'
        self.assertEqual('shared-cpuset',validate_config(config)['cpuModel'])

    def test_actual_kernel_cpu_settings_must_match_the_shared_budget(self):
        resources={'cpu.max':'max 100000','cpuset.cpus.effective':'0-1'}
        validate_shared_cpu_resources(resources,'0,1')
        for change in ({'cpu.max':'75000 100000'},{'cpu.max':''},
                       {'cpuset.cpus.effective':'0'},{'cpuset.cpus.effective':'0,2'},
                       {'cpuset.cpus.effective':''}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                validate_shared_cpu_resources({**resources,**change},'0,1')

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
            if mutation == 'missing_cpu': del config['services']['backend']['cpuset']
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                validate_config(config)

    def test_engine_must_enforce_limits_and_reject_oom(self):
        validate_container('backend', self.container())
        for key, value in [('NanoCpus', 750000000), ('CpuQuota', 75000), ('CpuShares', 512), ('CpusetCpus', '0'), ('Memory', 0), ('MemorySwap', -1), ('Privileged', True)]:
            container = self.container()
            container['HostConfig'][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                validate_container('backend', container)
        container = self.container()
        container['State']['OOMKilled'] = True
        with self.assertRaises(RuntimeError): validate_container('backend', container)

    def test_actual_pid_limit_must_equal_the_resolved_deployment(self):
        validate_container('backend', self.container(), expected_pids=128)
        with self.assertRaisesRegex(RuntimeError, 'PID limit'):
            validate_container('backend', self.container(), expected_pids=64)

    def test_target_auto_restarts_but_load_cannot_repeat_after_a_failure(self):
        with self.assertRaises(RuntimeError):
            validate_container('backend', self.container(restart='no'))
        with self.assertRaises(RuntimeError):
            validate_runner_container(self.container(cpu=1,restart='unless-stopped'))

    def test_docker_affinity_must_match_compose_before_start(self):
        container=self.container()
        container['HostConfig']['CpusetCpus']='0,2'
        validate_container('backend',container,'0,2')
        with self.assertRaisesRegex(RuntimeError,'affinity'):validate_container('backend',container,'0,4')
        container=self.container(cpu=1,memory=2048,restart='no')
        container['HostConfig']['CpusetCpus']='4'
        validate_runner_container(container,'4')
        with self.assertRaisesRegex(RuntimeError,'affinity'):validate_runner_container(container,'2')

    def test_runner_is_bounded_and_cannot_spawn_other_containers(self):
        validate_runner_container(self.container(cpu=1,memory=2048,restart='no'))
        validate_runner_config({'cpus': 1, 'mem_limit': str(2048 * MIB), 'memswap_limit': str(2048 * MIB),
                                'pids_limit': 128, 'restart': 'no','ulimits':{'nofile':{'soft':8192,'hard':8192}}})
        container = self.container(cpu=1,memory=2048,restart='no')
        container['Mounts'] = [{'Source': '/var/run/docker.sock'}]
        with self.assertRaises(RuntimeError): validate_runner_container(container)

    def test_descriptor_budget_is_enforced_on_backend_gateway_and_generator(self):
        for service in ('backend','nginx','load-runner'):
            cpu,memory=(1,2048) if service=='load-runner' else (0,MEMORY_LIMITS[service])
            value=self.container(cpu=cpu,memory=memory,restart='no' if service=='load-runner' else 'unless-stopped')
            value['HostConfig']['Ulimits'][0]['Soft']=1024
            with self.assertRaisesRegex(RuntimeError,'descriptor'):
                if service=='load-runner': validate_runner_container(value)
                else: validate_container(service,value)

    def test_safe_default_vus_and_duration_remain_bounded(self):
        points = budgeted_matrix(parse_args(['redis-filtering']))
        self.assertTrue(all(p['vus'] == 1200 for p in points))
        self.assertTrue(all(60 <= p['seconds'] <= 600 for p in points))
        with patch('sys.stderr'), self.assertRaises(SystemExit):
            parse_args(['redis-filtering', '--vus', '1201'])


if __name__ == '__main__':
    unittest.main()
