"""Shared SUT memory boundary. Requires local Linux Docker/systemd/cgroup v2.

Production: sudo python3 deploy/resource_group.py install
CI: ensure/remove a uniquely named runtime slice; never mutate docker.service.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

MIB = 1024 * 1024
GROUP_MIB = 1632
RESERVE_MIB = 416
SERVICES = ('backend', 'mysql', 'redis', 'kafka', 'frontend', 'nginx')
DEFAULT_GROUP = 'shanpuproduction.slice'
CGROUP_ROOT = Path('/sys/fs/cgroup')
TEMPLATE = Path(__file__).with_name('shanpu-memory.slice')


def group_name(name):
    # No hyphens: systemd slice names encode hierarchy with hyphens.
    if not re.fullmatch(r'shanpu[a-z0-9]+\.slice', name):
        raise RuntimeError('Expected an isolated shanpu<alphanumeric>.slice name')
    return name


def command(args):
    return subprocess.check_output(args, text=True, timeout=15).strip()


def require_engine():
    info = json.loads(command(['docker', 'info', '--format', '{{json .}}']))
    if info.get('OSType') != 'linux' or info.get('KernelVersion') != os.uname().release:
        raise RuntimeError('Shared memory requires Docker on this local Linux host')
    if info.get('CgroupDriver') != 'systemd' or str(info.get('CgroupVersion')) != '2':
        raise RuntimeError('Shared memory requires Docker systemd cgroup driver and cgroup v2')
    if not Path('/run/systemd/system').is_dir():
        raise RuntimeError('Shared memory requires a running system systemd manager')
    if 'memory' not in (CGROUP_ROOT/'cgroup.controllers').read_text().split():
        raise RuntimeError('Host cgroup memory controller is unavailable')


def validate_services(services):
    parents = set()
    for name, service in services.items():
        parents.add(group_name(service.get('cgroup_parent', '')))
        if any(service.get(key) not in (None, 0, '0') for key in
               ('mem_limit', 'memswap_limit', 'mem_reservation', 'mem_swappiness')):
            raise RuntimeError('Shared memory drift: private memory limits on '+name)
        resources = service.get('deploy', {}).get('resources', {})
        if any(resources.get(kind, {}).get('memory') not in (None, 0, '0') for kind in ('limits', 'reservations')):
            raise RuntimeError('Shared memory drift: deployment memory limits on '+name)
    if len(parents) != 1: raise RuntimeError('All services must use the same shared memory slice')
    return {'memoryModel': 'shared-cgroup', 'groupMemoryMiB': GROUP_MIB, 'groupSwapMiB': 0,
            'cgroupParent': parents.pop()}


def group_path(name):
    name = group_name(name)
    path = command(['systemctl', 'show', name, '--property=ControlGroup', '--value'])
    if path != '/'+name:
        raise RuntimeError('Shared memory slice is not active at the expected cgroup path')
    return CGROUP_ROOT/name


def snapshot(name):
    root = group_path(name)
    counters = ('memory.current', 'memory.max', 'memory.swap.max', 'memory.events', 'memory.stat')
    result = {key: (root/key).read_text().strip() for key in counters}
    for key in ('memory.peak', 'memory.pressure'):
        if (root/key).exists(): result[key] = (root/key).read_text().strip()
    return {'name': name, 'path': '/'+name, 'model': 'shared-cgroup',
            'limitMiB': GROUP_MIB, 'swapLimitMiB': 0, 'resources': result}


def validate_snapshot(group, *, check_oom=True):
    r = group['resources']
    if r.get('memory.max') != str(GROUP_MIB*MIB) or r.get('memory.swap.max') != '0':
        raise RuntimeError('Kernel did not enforce shared 1632 MiB memory / zero swap')
    events = dict(line.split() for line in r['memory.events'].splitlines())
    if not {'max', 'oom', 'oom_kill'} <= events.keys():
        raise RuntimeError('Shared memory OOM counters are incomplete')
    if check_oom and any(int(events[k]) for k in ('oom', 'oom_kill')):
        raise RuntimeError('Shared SUT memory group recorded OOM: '+r['memory.events'].replace('\n', ', '))
    return group


def verify(name):
    return validate_snapshot(snapshot(name))


def process_cgroup(pid):
    if int(pid) <= 0: raise RuntimeError('Cannot verify cgroup of a stopped container')
    return next(line.split(':', 2)[2] for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines()
                if line.startswith('0::'))


def verify_member(container, name, *, inside=True):
    path = process_cgroup(container['State']['Pid'])
    belongs = path.startswith('/'+group_name(name)+'/')
    if belongs != inside:
        raise RuntimeError('Container does not have the required SUT memory group isolation')
    if inside:
        root = CGROUP_ROOT/path.lstrip('/')
        if (root/'memory.max').read_text().strip() != 'max':
            raise RuntimeError('SUT container still has a private memory cap')
        # Inspect and actual kernel ancestry must agree.
        if container['HostConfig'].get('CgroupParent') != name:
            raise RuntimeError('Docker memory parent differs from the intended slice')
    return path


def _privileged(action, name):
    if os.geteuid() == 0: return False
    subprocess.run(['sudo', '-n', 'python3', str(Path(__file__).resolve()), action, '--name', name], check=True)
    return True


def ensure(name=DEFAULT_GROUP, *, persistent=False):
    name = group_name(name)
    require_engine()
    if _privileged('install' if persistent else 'ensure', name): return verify(name)
    directory = Path('/etc/systemd/system' if persistent else '/run/systemd/system')
    path = directory/name
    other = Path('/run/systemd/system' if persistent else '/etc/systemd/system')/name
    contents = TEMPLATE.read_text()
    if other.exists(): raise RuntimeError('Slice name already exists in another lifecycle: '+name)
    if path.exists() and path.read_text() != contents:
        raise RuntimeError('Refusing to overwrite a different memory slice: '+name)
    path.write_text(contents)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    if persistent:
        subprocess.run(['systemctl', 'enable', name], check=True)
    subprocess.run(['systemctl', 'start', name], check=True)
    return verify(name)


def remove(name):
    name = group_name(name)
    if name == DEFAULT_GROUP or (Path('/etc/systemd/system')/name).exists():
        raise RuntimeError('Persistent production slices are not removed by CI cleanup')
    if _privileged('remove', name): return
    path = Path('/run/systemd/system')/name
    if not path.exists(): return
    if path.read_text() != TEMPLATE.read_text(): raise RuntimeError('Unknown slice definition; refusing cleanup')
    root = CGROUP_ROOT/name
    if root.exists():
        events = dict(line.split() for line in (root/'cgroup.events').read_text().splitlines())
        if events.get('populated') != '0': raise RuntimeError('Stop/remove this group containers before removing its slice')
    subprocess.run(['systemctl', 'stop', name], check=True)
    path.unlink()
    subprocess.run(['systemctl', 'daemon-reload'], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'ensure', 'verify', 'remove'))
    parser.add_argument('--name', default=DEFAULT_GROUP)
    args = parser.parse_args()
    if args.action in ('install', 'ensure'): value = ensure(args.name, persistent=args.action=='install')
    elif args.action == 'verify': require_engine(); value = verify(args.name)
    else: remove(args.name); value = {'removed': args.name}
    print(json.dumps(value))


if __name__ == '__main__': main()
