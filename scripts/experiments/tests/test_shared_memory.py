"""Reject split/unbounded memory, verify actual ancestry, and preserve worker-only OOM evidence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'deploy'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
import resource_group as group
from host import HostMonitor, MIB
from protocol import context_contract, parse_args


class SharedMemoryTest(unittest.TestCase):
    def sample(self, **resources):
        return {'name':'shanputest.slice','resources':{'memory.max':str(1632*MIB), 'memory.swap.max':'0',
                'memory.events':'low 0\nhigh 0\nmax 0\noom 0\noom_kill 0', **resources}}

    def test_single_parent_rejects_nested_and_hidden_private_caps(self):
        services={n:{'cgroup_parent':'shanputest.slice'} for n in group.SERVICES}
        self.assertEqual(1632,group.validate_services(services)['groupMemoryMiB'])
        for change in ({'cgroup_parent':'shanpuother.slice'},{'cgroup_parent':''}, {'mem_limit':64*MIB},
                       {'memswap_limit':-1},{'mem_reservation':32*MIB},
                       {'deploy':{'resources':{'limits':{'memory':64*MIB}}}}):
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                group.validate_services({**services,'nginx':{**services['nginx'],**change}})
        for name in ('system.slice','../../evil.slice','shanpu-test.slice'):
            with self.assertRaises(RuntimeError):group.group_name(name)

    def test_kernel_max_swap_and_oom_cannot_be_hidden_by_running_pid_one(self):
        group.validate_snapshot(self.sample())
        for change in ({'memory.max':'max'},{'memory.max':str(2048*MIB)},{'memory.swap.max':'max'},
                       {'memory.events':'oom 0'}, {'memory.events':'max 100\noom 1\noom_kill 1'}):
            with self.subTest(change=change),self.assertRaises(RuntimeError):group.validate_snapshot(self.sample(**change))

    def test_actual_membership_and_private_kernel_cap_are_checked(self):
        container={'State':{'Pid':123},'HostConfig':{'CgroupParent':'shanputest.slice'}}
        with tempfile.TemporaryDirectory() as temp,patch.object(group,'CGROUP_ROOT',Path(temp)):
            leaf=Path(temp)/'shanputest.slice/docker-abc.scope';leaf.mkdir(parents=True)
            (leaf/'memory.max').write_text('max')
            with patch.object(group,'process_cgroup',return_value='/shanputest.slice/docker-abc.scope'):
                group.verify_member(container,'shanputest.slice')
                with self.assertRaises(RuntimeError):group.verify_member(container,'shanputest.slice',inside=False)
                (leaf/'memory.max').write_text(str(64*MIB))
                with self.assertRaisesRegex(RuntimeError,'private'):group.verify_member(container,'shanputest.slice')
            with patch.object(group,'process_cgroup',return_value='/system.slice/docker-abc.scope'):
                with self.assertRaises(RuntimeError):group.verify_member(container,'shanputest.slice')
                group.verify_member(container,'shanputest.slice',inside=False)

    def test_watchdog_samples_parent_even_when_container_inspect_is_cached(self):
        with tempfile.TemporaryDirectory() as temp,patch('host.memory',return_value={'MemAvailable':8000*MIB}):
            sample=self.sample(**{'memory.events':'max 2\noom 1\noom_kill 1'})
            watch=HostMonitor(temp,lambda:[],lambda:None,lambda:None,10,
                              observe_memory=lambda:(sample,'Shared SUT memory group recorded OOM'))
            watch.last_observation=__import__('time').monotonic()
            watch.sample()
            self.assertIn('OOM',watch.failure)
            saved=json.loads((Path(temp)/'status.json').read_text())
            self.assertEqual(sample,saved['sharedMemory'])

    def test_source_contract_distinguishes_shared_from_old_split_memory(self):
        target={'instanceId':'same','services':[]}
        args=parse_args(['redis-filtering'])
        old=context_contract(target,{},'source',{},args)
        new=context_contract({**target,'memoryModel':'shared-cgroup','groupMemoryMiB':1632}, {},'source',{},args)
        self.assertNotEqual(old,new)

    def test_cleanup_refuses_persistent_production_group(self):
        with self.assertRaisesRegex(RuntimeError,'Persistent'):group.remove(group.DEFAULT_GROUP)


if __name__=='__main__':unittest.main()
