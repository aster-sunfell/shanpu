"""Compare resolved Compose services without starting containers or exposing secrets."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
MIDDLEWARE = ('mysql', 'redis', 'kafka')


def _equal(expected, actual, label):
    if expected != actual:
        # Do not print values: resolved environment and commands contain credentials.
        keys = sorted(key for key in expected.keys() | actual.keys() if expected.get(key) != actual.get(key))
        raise RuntimeError(f'Runtime configuration drift in {label}: '+', '.join(keys))


def validate_alignment(production, sut, development):
    baseline = production['services']
    if set(sut['services']) != set(baseline):
        raise RuntimeError('SUT must run every production service')
    _equal(production.get('x-test-machine', {}), sut.get('x-test-machine', {}), 'machine budget')
    for name, service in baseline.items():
        actual = copy.deepcopy(sut['services'][name])
        if name == 'backend':
            environment = actual.get('environment', {})
            if environment.pop('SPRING_PROFILES_ACTIVE', None) != 'prod,experiment,sut':
                raise RuntimeError('SUT must activate prod,experiment,sut')
            environment['SPRING_PROFILES_ACTIVE'] = service['environment']['SPRING_PROFILES_ACTIVE']
            if not environment.pop('SUT_INSTANCE_ID', None):
                raise RuntimeError('SUT must identify its disposable instance')
            if environment.pop('SHANPU_EXPERIMENT_JOURNAL', None) != '/evidence/commits.jsonl':
                raise RuntimeError('SUT must publish evidence to its dedicated volume')
            if actual.pop('volumes', None) != [{'type': 'volume', 'source': 'evidence',
                                                'target': '/evidence', 'volume': {}}]:
                raise RuntimeError('SUT backend must only add the dedicated evidence volume')
            build = actual.get('build', {})
            if (Path(build.get('dockerfile', '')).resolve() != ROOT/'backend/Dockerfile'
                    or build.get('target') != 'experiment'
                    or set(build) != {'context', 'dockerfile', 'target'}):
                raise RuntimeError('SUT must use the shared backend Dockerfile experiment target')
            if actual.get('image') != 'shanpu-experiment-sut:ci':
                raise RuntimeError('Unexpected SUT backend image')
            actual['build'], actual['image'] = service['build'], service['image']
        _equal(service, actual, 'SUT '+name)
    for name in MIDDLEWARE:
        actual = copy.deepcopy(development['services'][name])
        actual.pop('container_name', None)
        if actual.get('restart') != 'no':
            raise RuntimeError('Development services must only start on demand: '+name)
        actual['restart'] = baseline[name]['restart']
        _equal(baseline[name], actual, 'development '+name)
    nginx = development['services']['nginx']
    if nginx.get('restart') != 'no':
        raise RuntimeError('Development gateway must only start on demand')
    if any(service.get('restart') != 'unless-stopped' for service in baseline.values()):
        raise RuntimeError('Production services must restart unless explicitly stopped')
    if nginx['image'] != baseline['nginx']['image']:
        raise RuntimeError('Development must use the production Nginx image')
    api_mounts = [mount for mount in baseline['nginx']['volumes'] if mount['target'] == '/etc/nginx/api.conf']
    if not api_mounts or api_mounts != [mount for mount in nginx.get('volumes', []) if mount['target'] == '/etc/nginx/api.conf']:
        raise RuntimeError('Development must share the production API proxy configuration')
    digest = hashlib.sha256()
    for name in ('deploy/compose.2c2g.yml', 'deploy/nginx.2c2g.conf', 'deploy/nginx.api.conf',
                 'backend/Dockerfile', 'backend/docker/java.options', 'backend/docker/entrypoint.sh',
                 'backend/src/main/resources/application.yml', 'backend/src/main/resources/application-prod.yml',
                 'backend/src/main/resources/application-2c2g.yml', 'frontend/Dockerfile'):
        digest.update(name.encode()+b'\0'+(ROOT/name).read_bytes()+b'\0')
    return {'status': 'aligned', 'services': sorted(baseline),
            'developmentMiddleware': list(MIDDLEWARE),
            'sharedConfigurationSha256': digest.hexdigest(),
            'baselineSha256': hashlib.sha256((ROOT/'deploy/compose.2c2g.yml').read_bytes()).hexdigest()}


def render_configs():
    # Only structural differences are relevant here. Use placeholders, never local secrets.
    environment = {**os.environ, 'MYSQL_ROOT_PASSWORD': 'alignment-check', 'REDIS_PASSWORD': 'alignment-check',
                   'JWT_SECRET': 'alignment-check', 'SUT_INSTANCE_ID': 'alignment-check',
                   'BENCH_ROOT': str(ROOT), 'BENCH_RUNTIME': '/tmp/shanpu-alignment',
                   'BENCH_REPORTS': '/tmp/shanpu-alignment/reports'}
    configs = []
    for path in ('deploy/compose.2c2g.yml', 'experiments/compose.sut.yml', 'docker-compose.yml'):
        result = subprocess.run(['docker', 'compose', '--env-file', '/dev/null', '-f', str(ROOT/path),
                                 'config', '--format', 'json'], check=True, capture_output=True,
                                text=True, env=environment)
        configs.append(json.loads(result.stdout))
    return configs


def check_alignment():
    return validate_alignment(*render_configs())


if __name__ == '__main__':
    print(json.dumps(check_alignment(), ensure_ascii=False))
