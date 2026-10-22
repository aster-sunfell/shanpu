"""Guarded production lifecycle: install shared slice once, verify before starting containers."""
import argparse
import json
from pathlib import Path
import subprocess
import resource_group

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('up', 'verify', 'down'))
    parser.add_argument('--env-file', default=str(ROOT/'.env.production'))
    args = parser.parse_args()
    compose = ['docker', 'compose', '--env-file', args.env_file, '-f', str(ROOT/'deploy/compose.2c2g.yml')]
    config = json.loads(subprocess.check_output([*compose, 'config', '--format', 'json'], text=True))
    if set(config['services']) != set(resource_group.SERVICES):
        raise RuntimeError('Expected the six production services in the shared memory group')
    budget = resource_group.validate_services(config['services'])
    name = budget['cgroupParent']
    if args.action == 'down':
        subprocess.run([*compose, 'down'], check=True)
        return  # Keep persistent slice for future starts; do not delete data volumes.
    resource_group.require_engine()
    # Explicit install is required once; never silently start an unbounded slice.
    if not (Path('/etc/systemd/system')/name).is_file():
        raise RuntimeError('Install persistent slice first: sudo python3 deploy/resource_group.py install --name '+name)
    resource_group.verify(name)
    if args.action == 'up':
        subprocess.run([*compose, 'up', '-d', '--wait', '--wait-timeout', '300'], check=True)
    for service in resource_group.SERVICES:
        identifier = subprocess.check_output([*compose, 'ps', '-a', '-q', service], text=True).strip()
        if not identifier: raise RuntimeError('Missing production container: '+service)
        container = json.loads(subprocess.check_output(['docker', 'inspect', identifier], text=True))[0]
        if not container['State']['Running'] or container['State'].get('OOMKilled'):
            raise RuntimeError('Production container unhealthy/OOM: '+service)
        resource_group.verify_member(container, name)
    print(json.dumps(resource_group.verify(name)))


if __name__ == '__main__': main()
