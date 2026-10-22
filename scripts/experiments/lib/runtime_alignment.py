"""Compare resolved Compose services without starting containers or exposing secrets."""
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from host import cpu_list, cpu_string
from protocol import DEFAULT_VUS

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'deploy'))
import resource_group
MIDDLEWARE = ('mysql', 'redis', 'kafka')


def validate_backend_keepalive(development, production, shared, application):
    """Check the shared upstream policy, separately from client keepalive settings."""
    def uncomment(value):
        return re.sub(r'#[^\n]*', '', value)

    for label, config in (('development', development), ('production', production)):
        # Port placeholders contain braces but are not Nginx blocks.
        config = re.sub(r'\$\{\w+\}', 'PORT', uncomment(config))
        blocks = re.findall(r'\bupstream\s+backend\s*\{([^{}]*)\}', config)
        if (len(blocks) != 1
                or len(re.findall(r'\binclude\s+/etc/nginx/backend-keepalive\.conf\s*;', blocks[0])) != 1
                or re.search(r'\bkeepalive(?:_\w+)?\s', blocks[0])):
            raise RuntimeError(f'{label} backend upstream must include only the shared keepalive policy')
    shared = uncomment(shared)
    pool = re.findall(r'\bkeepalive\s+(\d+)\s*;', shared)
    timeout = re.findall(r'\bkeepalive_timeout\s+(\d+(?:ms|s))\s*;', shared)
    # The repository uses block-style YAML; scope the scalar to server.tomcat.
    application = uncomment(application) + '\n'
    server = re.search(r'^server:[ \t]*\n((?:[ \t]+[^\n]*\n|\n)*)', application, re.M)
    tomcat = re.search(r'^  tomcat:[ \t]*\n((?: {4}[^\n]*\n|\n)*)', server[1], re.M) if server else None
    backend = re.findall(r'^    keep-alive-timeout:[ \t]*(\d+(?:ms|s))[ \t]*$', tomcat[1], re.M) if tomcat else []
    if len(pool) != 1 or int(pool[0]) <= 0 or len(timeout) != 1 or len(backend) != 1:
        raise RuntimeError('Explicit shared upstream keepalive and Tomcat keep-alive-timeout are required')

    def milliseconds(value):
        return int(value[:-2]) if value.endswith('ms') else int(value[:-1]) * 1000

    upstream_ms, backend_ms = milliseconds(timeout[0]), milliseconds(backend[0])
    if not 0 < upstream_ms < backend_ms:
        raise RuntimeError('Upstream keepalive timeout must be positive and shorter than Tomcat keep-alive-timeout')
    return {'idleConnections': int(pool[0]), 'upstreamTimeoutMs': upstream_ms, 'tomcatTimeoutMs': backend_ms}


def validate_shared_cpu_config(services):
    """A common cpuset bounds aggregate execution without fragmenting CPU time."""
    sets = set()
    for name, service in services.items():
        for key in ('cpus','cpu_quota','cpu_period','cpu_shares','cpu_count','cpu_percent','cpu_rt_runtime','cpu_rt_period'):
            if service.get(key) not in (None,0,0.0,'0'):
                raise RuntimeError('Shared CPU services must not set local CPU quotas/weights: '+name)
        resources = service.get('deploy',{}).get('resources',{})
        if any(resources.get(kind,{}).get('cpus') not in (None,0,0.0,'0') for kind in ('limits','reservations')):
            raise RuntimeError('Shared CPU services must not set deployment CPU quotas: '+name)
        cpus = cpu_list(service.get('cpuset',''))
        if len(cpus)!=2: raise RuntimeError('Each SUT service must share exactly two logical CPUs: '+name)
        sets.add(cpu_string(cpus))
    if len(sets)!=1: raise RuntimeError('All services must use the same shared CPU set')
    return {'cpuModel':'shared-cpuset','cpuLimit':2,'cpuset':sets.pop()}


def validate_connection_budget(services, development, vus=DEFAULT_VUS):
    """Proxy connections include both downstream clients and upstream sockets."""
    def integer(pattern, text):
        match = re.search(pattern,text,re.M)
        if not match: raise RuntimeError('Missing explicit connection/descriptor setting')
        return int(match[1])
    values = {}
    for name in ('deploy/nginx.2c2g.conf', 'deploy/nginx.conf.template'):
        text = re.sub(r'#[^\n]*', '', (ROOT/name).read_text())
        connections = integer(r'worker_connections\s+(\d+)',text)
        descriptors = integer(r'worker_rlimit_nofile\s+(\d+)',text)
        if connections < 2*vus+128 or descriptors < connections+128:
            raise RuntimeError('Nginx connection/file budget cannot cover client and upstream VUs')
        values[name] = connections
    if len(set(values.values())) != 1: raise RuntimeError('Development and production Nginx connection budgets differ')
    text = (ROOT/'backend/src/main/resources/application-2c2g.yml').read_text()
    connections = integer(r'^    max-connections: (\d+)',text)
    if connections < vus+128: raise RuntimeError('Tomcat socket budget cannot cover VUs plus headroom')
    for service in (services['backend'],services['nginx'],development['services']['nginx']):
        limits = service.get('ulimits',{}).get('nofile',{})
        if any(limits.get(k,0) < 65536 for k in ('soft','hard')):
            raise RuntimeError('Backend and gateway require explicit 65536 descriptor limits')
    return {'vus':vus,'nginxConnections':next(iter(values.values())), 'tomcatConnections':connections,'nofile':65536}


def _equal(expected, actual, label):
    if expected != actual:
        # Do not print values: resolved environment and commands contain credentials.
        keys = sorted(key for key in expected.keys() | actual.keys() if expected.get(key) != actual.get(key))
        raise RuntimeError(f'Runtime configuration drift in {label}: '+', '.join(keys))


def validate_alignment(production, sut, development):
    baseline = production['services']
    cpu_budget = validate_shared_cpu_config(baseline)
    validate_shared_cpu_config(sut['services'])
    validate_shared_cpu_config(development['services'])
    memory_budget = resource_group.validate_services(baseline)
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
        if actual.get('cgroup_parent', ''):
            raise RuntimeError('Development middleware must not join the production memory group')
        actual['cgroup_parent'] = baseline[name]['cgroup_parent']
        _equal(baseline[name], actual, 'development '+name)
    nginx = development['services']['nginx']
    if nginx.get('cgroup_parent', '') or nginx.get('mem_limit') or nginx.get('deploy',{}).get('resources',{}).get('limits',{}).get('memory'):
        raise RuntimeError('Development gateway must not inherit a private or production memory cap')
    if nginx.get('restart') != 'no':
        raise RuntimeError('Development gateway must only start on demand')
    if any(service.get('restart') != 'unless-stopped' for service in baseline.values()):
        raise RuntimeError('Production services must restart unless explicitly stopped')
    if nginx['image'] != baseline['nginx']['image']:
        raise RuntimeError('Development must use the production Nginx image')
    api_mounts = [mount for mount in baseline['nginx']['volumes'] if mount['target'] == '/etc/nginx/api.conf']
    if not api_mounts or api_mounts != [mount for mount in nginx.get('volumes', []) if mount['target'] == '/etc/nginx/api.conf']:
        raise RuntimeError('Development must share the production API proxy configuration')
    for label, service in (('production', baseline['nginx']), ('development', nginx)):
        mounts = [mount for mount in service.get('volumes', []) if mount['target'] == '/etc/nginx/backend-keepalive.conf']
        if (len(mounts) != 1 or mounts[0].get('type') != 'bind' or mounts[0].get('read_only') is not True
                or Path(mounts[0].get('source', '')).resolve() != ROOT/'deploy/nginx.backend-keepalive.conf'):
            raise RuntimeError(f'{label} must mount the shared backend keepalive policy read-only')
    keepalive = validate_backend_keepalive(*((ROOT/name).read_text() for name in
        ('deploy/nginx.conf.template', 'deploy/nginx.2c2g.conf', 'deploy/nginx.backend-keepalive.conf',
         'backend/src/main/resources/application-2c2g.yml')))
    from protocol import DEFAULT_VUS
    connection_budget = validate_connection_budget(baseline, development, DEFAULT_VUS)
    digest = hashlib.sha256()
    for name in ('deploy/compose.2c2g.yml', 'deploy/nginx.2c2g.conf', 'deploy/nginx.api.conf',
                 'deploy/nginx.backend-keepalive.conf',
                 'deploy/resource_group.py', 'deploy/shanpu-memory.slice',
                 'backend/Dockerfile', 'backend/docker/java.options', 'backend/docker/entrypoint.sh',
                 'backend/src/main/resources/application.yml', 'backend/src/main/resources/application-prod.yml',
                 'backend/src/main/resources/application-2c2g.yml', 'frontend/Dockerfile'):
        digest.update(name.encode()+b'\0'+(ROOT/name).read_bytes()+b'\0')
    return {'status': 'aligned', 'services': sorted(baseline),
            'sharedCpu':cpu_budget, 'sharedMemory':memory_budget, 'backendKeepalive': keepalive, 'connectionBudget': connection_budget,
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
