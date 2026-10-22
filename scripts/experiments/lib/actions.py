"""GitHub Actions only: isolated ephemeral services, bounded load and evidence."""
import argparse
from contextlib import contextmanager
import json
import hashlib
import tempfile
import signal
import os
from pathlib import Path
import secrets
import subprocess
import shutil
import sys
import time
from host import (atomic_json, HostMonitor, cpu_list, cpu_string, environment, require_start_memory,
                  select_affinity, container_resources, finalize_host_evidence, preflight_memory, validate_shared_cpu_resources)
from runtime_alignment import check_alignment, validate_shared_cpu_config


def require_actions():
    if (os.environ.get('GITHUB_ACTIONS') != 'true'
            or os.environ.get('RUNNER_ENVIRONMENT') != 'github-hosted'
            or sys.platform != 'linux'):
        raise RuntimeError('Performance execution is supported only on GitHub-hosted Linux Actions runners')
    for name in ('BENCH_RUNTIME', 'BENCH_REPORTS', 'GITHUB_RUN_ID', 'GITHUB_SHA', 'GITHUB_REPOSITORY'):
        if not os.environ.get(name):
            raise RuntimeError('Missing Actions environment: ' + name)
    if not Path(os.environ['BENCH_RUNTIME']).is_absolute() or not Path(os.environ['BENCH_REPORTS']).is_absolute():
        raise RuntimeError('Actions runtime and report directories must be absolute')


def actions_context():
    return {'runId': os.environ['GITHUB_RUN_ID'], 'attempt': os.environ.get('GITHUB_RUN_ATTEMPT', '1'),
            'job': os.environ.get('GITHUB_JOB'), 'repository': os.environ['GITHUB_REPOSITORY'],
            'commit': os.environ['GITHUB_SHA'], 'ref': os.environ.get('GITHUB_REF'),
            'runUrl': f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
            'runnerImage': os.environ.get('ImageOS'), 'runnerImageVersion': os.environ.get('ImageVersion')}


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = Path(os.environ.get('BENCH_RUNTIME', '/bench/runtime'))
REPORTS = Path(os.environ.get('BENCH_REPORTS', '/bench/reports'))
ENV_FILE = RUNTIME / "sut.env"
MIB = 1024 * 1024
MEMORY_LIMITS = {"backend":512,"mysql":384,"redis":96,"kafka":384,"frontend":192,"nginx":64}
BUSINESS_TABLES = ('t_user', 't_product', 't_seckill_activity', 't_order', 't_point_log', 't_order_dlq_log')


def command(args, *, capture=True, **kwargs):
    return subprocess.run(args, check=True, text=True, cwd=ROOT,
                          stdout=subprocess.PIPE if capture else None, **kwargs).stdout


def initialize():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if not ENV_FILE.exists():
        values = {"MYSQL_ROOT_PASSWORD": secrets.token_hex(24), "REDIS_PASSWORD": secrets.token_hex(24),
                  "JWT_SECRET": secrets.token_hex(32), "SUT_INSTANCE_ID": secrets.token_hex(12)}
        fd = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            for key, value in values.items():
                file.write(f"{key}={value}\n")
    # Disposable credentials stay outside public report and artifact directories.
    values = dict(line.split("=", 1) for line in ENV_FILE.read_text().splitlines() if line and not line.startswith("#"))
    return values


def compose(kind, *args):
    # Published ports and disposable DB name differ; service/runtime limits are inherited unchanged.
    os.environ.update(MYSQL_PORT="13306", REDIS_PORT="16379", KAFKA_PORT="19092",
                      BACKEND_PORT="18081", GATEWAY_PORT="18080", GATEWAY_BIND_ADDRESS="127.0.0.1",
                      MYSQL_DATABASE="shanpu_bench")
    affinity = RUNTIME / 'cpu-affinity.json'
    if affinity.exists():
        pins = json.loads(affinity.read_text())
        os.environ['SUT_CPUSET'] = pins['sut']
        os.environ['RUNNER_CPUSET'] = pins['runner']
    return ["docker", "compose", "--project-name", "shanpu-sut" if kind == "sut" else "shanpu-load",
            "--env-file", str(ENV_FILE), "--file", str(ROOT / f"experiments/compose.{kind}.yml"), *args]


def available_memory():
    data = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return int(data["MemAvailable"].split()[0]) * 1024


def require_memory(mib):
    if available_memory() < mib * MIB:
        raise RuntimeError(f"Available host memory is below {mib} MiB; refusing to start more work")



def validate_engine(info):
    required = ("MemoryLimit", "SwapLimit", "CpuCfsQuota", "CpuCfsPeriod", "PidsLimit")
    if info.get("OSType") != "linux" or any(info.get(key) is not True for key in required):
        raise RuntimeError("Docker engine must support memory, swap, CPU quota and PID limits; refusing uncapped execution")
    if info.get('KernelVersion') and info['KernelVersion'] != os.uname().release:
        raise RuntimeError('Docker must use this Linux host kernel; remote/Desktop engines make host guards unreliable')


def check_engine():
    endpoint = json.loads(command(['docker', 'context', 'inspect', '--format', '{{json .Endpoints.docker.Host}}']))
    if os.environ.get('DOCKER_HOST') and not os.environ.get('DOCKER_CONTEXT'):
        endpoint = os.environ['DOCKER_HOST']
    if not endpoint.startswith('unix://'):
        raise RuntimeError('Use the local Linux Docker Unix socket; remote Docker contexts are unsupported')
    validate_engine(json.loads(command(['docker', 'info', '--format', '{{json .}}'])))

def validate_config(config):
    budget = config.get("x-test-machine", {})
    if budget != {"cpus": 2, "memory_mib": 2048, "host_reserve_mib": 416}:
        raise RuntimeError("Target-machine budget must remain exactly 2 CPUs / 2048 MiB")
    if set(config["services"]) != set(MEMORY_LIMITS):
        raise RuntimeError("Unexpected services in the target-machine budget")
    cpu_budget = validate_shared_cpu_config(config['services'])
    total_memory = 0
    for name, mib in MEMORY_LIMITS.items():
        service = config["services"][name]
        if (int(service.get("mem_limit", 0)) != mib * MIB
                or int(service.get("memswap_limit", 0)) != mib * MIB
                or service.get("restart") != "unless-stopped" or not 0 < service.get("pids_limit", 0) <= 192):
            raise RuntimeError(f"Invalid hard resource limits for {name}")
        total_memory += mib
    if total_memory + budget["host_reserve_mib"] != 2048:
        raise RuntimeError("Target budget sum is inconsistent")
    return {**cpu_budget, "containerMemoryMiB": total_memory,
            "hostReserveMiB": 416, "machineMemoryMiB": 2048}


def validate_container(name, container, expected_cpuset=None, expected_pids=None):
    mib = MEMORY_LIMITS[name]
    actual = container["HostConfig"]
    if (actual.get("NanoCpus",0) != 0 or actual.get('CpuQuota',0) not in (0,-1)
            or actual.get('CpuPeriod',0) not in (0,100000) or actual.get('CpuShares',0) not in (0,1024)
            or actual.get('CpuRealtimeRuntime',0) or actual.get('CpuRealtimePeriod',0)
            or actual.get("Memory") != mib * MIB
            or actual.get("MemorySwap") != mib * MIB or not 0 < actual.get("PidsLimit", 0) <= 192
            or actual.get("Privileged") or actual.get("NetworkMode") == "host"
            or actual.get("RestartPolicy", {}).get("Name") != "unless-stopped"):
        raise RuntimeError(f"Docker did not apply the required CPU/memory/swap limits to {name}")
    if name in ('backend','nginx'): validate_nofile(actual)
    actual_cpuset = actual.get('CpusetCpus', '')
    if len(cpu_list(actual_cpuset))!=2:
        raise RuntimeError('Docker must bind each SUT container to two shared logical CPUs')
    if expected_pids is not None and actual.get('PidsLimit') != expected_pids:
        raise RuntimeError(f'Docker did not apply the required PID limit to {name}')
    if expected_cpuset is not None and cpu_list(actual_cpuset or '') != cpu_list(expected_cpuset or ''):
        raise RuntimeError(f'Docker did not apply the required CPU affinity to {name}')
    if (container["State"].get("OOMKilled") or not container["State"].get("Running")
            or container["State"].get("Health", {}).get("Status", "healthy") != "healthy"):
        raise RuntimeError(f"Target service {name} has stopped or was OOM-killed")
    return {"service": name, "imageId": container["Image"], "cpuLimit": None, "cpuModel":"shared-cpuset",
            "memoryLimitMiB": mib, "memorySwapMiB": mib, "oomKilled": False,
            'pidsLimit': actual['PidsLimit'],
            'restartPolicy': actual['RestartPolicy']['Name'],
            'startedAt': container['State'].get('StartedAt'),
            'cpuset': cpu_string(cpu_list(actual_cpuset)) if actual_cpuset else ''}


def machine_id():
    return hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest()


def capture_final_containers(identifiers, path):
    """Preserve terminal state even when docker start --attach raises on failure.

    Docker inspect also contains passwords and tokens, so export a field allowlist.
    Read state before validation: a stopped/OOM-killed service is evidence itself.
    """
    names = [name for name, identifier in identifiers.items() if identifier]
    values = json.loads(command(['docker', 'inspect', *(identifiers[name] for name in names)], timeout=5))
    rows = []
    for name, value in zip(names, values):
        state = value.get('State', {})
        rows.append({'service': name, 'imageId': value.get('Image'),
                     'restartCount': value.get('RestartCount'),
                     'state': {key: state.get(key) for key in
                               ('Status', 'Running', 'OOMKilled', 'ExitCode', 'StartedAt', 'FinishedAt')}})
    atomic_json(path, {'timeMs': int(time.time()*1000), 'containers': rows})
    for row in rows:
        state = row['state']
        print(f"Container final state {row['service']}: status={state['Status']}, "
              f"running={state['Running']}, OOM={state['OOMKilled']}, exit={state['ExitCode']}, "
              f"restarts={row['restartCount']}", flush=True)


def verify():
    alignment = check_alignment()
    config = json.loads(command(compose("sut", "config", "--format", "json")))
    result = validate_config(config)
    services = []
    for name in MEMORY_LIMITS:
        identifier = command(compose("sut", "ps", "-a", "-q", name)).strip()
        if not identifier:
            raise RuntimeError(f"Missing target container: {name}")
        container = json.loads(command(["docker", "inspect", identifier]))[0]
        row=validate_container(name, container, config['services'][name].get('cpuset', ''),
                               config['services'][name]['pids_limit'])
        row['cpuResources'] = container_resources(container)
        validate_shared_cpu_resources(row['cpuResources'],row['cpuset'])
        services.append(row)
    result.update(instanceId=initialize()["SUT_INSTANCE_ID"], services=services, machineId=machine_id(),
                  actions=actions_context(),
                  source=json.loads((RUNTIME / "sut-source.json").read_text()),
                  environment=environment(), configurationAlignment=alignment, verifiedAtMs=int(time.time() * 1000))
    (RUNTIME / "target.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def source_metadata(scope="runner"):
    digest = hashlib.sha256()
    if scope == "sut":
        names = ("pom.xml", "backend", "experiments/observer", "experiments/application",
                 "experiments/compose.sut.yml", "deploy", "frontend")
    else:
        names = ("scripts/experiments", "experiments/docker/Runner.Dockerfile",
                 "experiments/requirements.txt", "experiments/compose.runner.yml")
    paths = []
    for name in names:
        root = ROOT / name
        paths.extend([root] if root.is_file() else (p for p in root.rglob("*") if p.is_file()
                     and not {"target", "__pycache__", "node_modules", ".next", ".git"}.intersection(p.parts)))
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0")
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(65536), b""):
                digest.update(chunk)
    return {"scope": scope, "gitCommit": command(["git", "rev-parse", "HEAD"]).strip(),
            "gitStatus": command(["git", "status", "--short"]), "sourceSha256": digest.hexdigest()}


def build_images(kind, names):
    cache = os.environ.get('BENCH_BUILD_CACHE')
    if not cache:
        command(compose(kind, 'build', *names), capture=False)
        return
    root=Path(cache)
    root.mkdir(parents=True,exist_ok=True)
    services={}
    for name in names:
        previous,fresh=root/name,root/(name+'-new')
        if fresh.exists(): shutil.rmtree(fresh)
        build={'cache_to':[f'type=local,dest={fresh},mode=max']}
        if (previous/'index.json').exists(): build['cache_from']=[f'type=local,src={previous}']
        services[name]={'build':build}
    with tempfile.NamedTemporaryFile(mode='w',suffix='.json',dir=RUNTIME) as file:
        json.dump({'services':services},file);file.flush()
        try:
            command(compose(kind,'--file',file.name,'build',*names),capture=False)
        except subprocess.CalledProcessError:
            print('Build cache unavailable; rebuilding without cache import/export',flush=True)
            command(compose(kind,'build',*names),capture=False)
            return
    for name in names:
        previous,fresh=root/name,root/(name+'-new')
        if not (fresh/'index.json').exists():
            print('Build completed without cache export: '+name,flush=True)
            continue
        if previous.exists(): shutil.rmtree(previous)
        fresh.rename(previous)


def build():
    require_memory(1024)
    # Build never overlaps a running target stack on the same Docker host.
    if command(compose("sut", "ps", "-q")).strip():
        raise RuntimeError("Stop the target stack before building; build and measurement must not overlap")
    output = RUNTIME / "build"
    output.mkdir(parents=True, exist_ok=True)
    source = source_metadata()
    sut_source = source_metadata("sut")
    build_script = ("mkdir -p /build && cd /source && "
                    "tar --exclude=target -cf - pom.xml backend experiments | tar -xf - -C /build && "
                    "cd /build && mvn -B -ntp '-Dtest=!DatabaseMigrationTest,!OrderConcurrencyTest' "
                    "-Dsurefire.failIfNoSpecifiedTests=false '-DargLine=-Xmx192m -XX:ActiveProcessorCount=2' package && "
                    "cp experiments/application/target/shanpu-experiment-app-1.0.0.jar /output/app.jar && "
                    "mkdir -p /output/docker && "
                    "cp backend/docker/java.options /output/docker/java.options && "
                    "cp backend/docker/entrypoint.sh /output/docker/entrypoint.sh")
    m2 = os.environ.get('BENCH_M2')
    if m2: Path(m2).mkdir(parents=True, exist_ok=True)
    command(["docker", "run", "--rm", "--name", "shanpu-experiment-build", "--cpus", "2",
             "--memory", "768m", "--memory-swap", "768m", "--pids-limit", "128", "--ulimit", "nofile=65536:65536",
             "-e", "MAVEN_OPTS=-Xms64m -Xmx384m -XX:ActiveProcessorCount=2",
             "-v", f"{ROOT}:/source:ro", "-v", f"{output}:/output",
             "-v", f"{m2 or 'shanpu-experiment-m2'}:/root/.m2", "maven:3.9-eclipse-temurin-17",
             "sh", "-c", build_script], capture=False)
    build_images("sut", ("backend", "frontend"))
    build_images("runner", ("load-runner",))
    if source["sourceSha256"] != source_metadata()["sourceSha256"] or sut_source["sourceSha256"] != source_metadata("sut")["sourceSha256"]:
        raise RuntimeError("Sources changed during build; build again to capture a consistent image")
    (RUNTIME / "source.json").write_text(json.dumps(source, indent=2) + "\n")
    (RUNTIME / "sut-source.json").write_text(json.dumps(sut_source, indent=2) + "\n")


def validate_nofile(host_config):
    limits = next((v for v in host_config.get('Ulimits',[]) if v['Name']=='nofile'),{})
    if any(limits.get(k,0) < 65536 for k in ('Soft','Hard')):
        raise RuntimeError('Docker did not apply the 65536 file descriptor budget')


def validate_runner_config(service):
    if (float(service.get("cpus", 0)) != 2 or int(service.get("mem_limit", 0)) != 4096 * MIB
            or int(service.get("memswap_limit", 0)) != 4096 * MIB or service.get("pids_limit") != 128
            or service.get("restart") != "no"):
        raise RuntimeError("Load runner must have fixed 2 CPU / 4096 MiB limits")
    limits = service.get('ulimits',{}).get('nofile',{})
    if any(limits.get(k,0) < 65536 for k in ('soft','hard')): raise RuntimeError('Load runner requires 65536 descriptors')
    for mount in service.get("volumes", []):
        if "docker.sock" in mount.get("source", ""):
            raise RuntimeError("The load runner must not access the Docker socket")


def validate_runner_container(container, expected_cpuset=None):
    actual = container["HostConfig"]
    if (actual.get("NanoCpus") != 2000000000 or actual.get("Memory") != 4096 * MIB
            or actual.get("MemorySwap") != 4096 * MIB or actual.get("PidsLimit") != 128
            or actual.get("Privileged") or actual.get("RestartPolicy", {}).get("Name") != "no"):
        raise RuntimeError("Docker did not apply the load runner limits")
    validate_nofile(actual)
    if expected_cpuset is not None and cpu_list(actual.get('CpusetCpus', '') or '') != cpu_list(expected_cpuset or ''):
        raise RuntimeError('Docker did not apply the load runner CPU affinity')
    if any("docker.sock" in mount.get("Source", "") for mount in container.get("Mounts", [])):
        raise RuntimeError("Load runner has an unexpected Docker socket mount")



def prepare_actions():
    require_start_memory()
    check_engine()
    check_alignment()
    REPORTS.mkdir(parents=True, exist_ok=True)
    for name, value in initialize().items():
        # Mask disposable secrets before Docker emits any build/startup output.
        print(f'::add-mask::{value}', flush=True)
    pins = select_affinity()
    atomic_json(RUNTIME / 'cpu-affinity.json', pins)
    atomic_json(RUNTIME / 'actions.json', actions_context())
    atomic_json(RUNTIME / 'deadline.json', {'monotonic': time.monotonic() + 75 * 60})
    atomic_json(REPORTS / 'environment.json', {'actions': actions_context(), 'cpuAllocation': pins,
                                               'host': environment(), 'targetBudget': validate_config(
        json.loads(command(compose('sut', 'config', '--format', 'json'))))})
    atomic_json(REPORTS / 'suite.json', {'status': 'running', 'actions': actions_context(), 'stages': []})
    print(json.dumps({'cpuAllocation': pins, 'environment': environment()}, ensure_ascii=False), flush=True)


def start_services():
    require_start_memory()
    check_alignment()
    config = json.loads(command(compose('sut', 'config', '--format', 'json')))
    validate_config(config)
    command(compose('sut', 'create'), capture=False)
    for name in MEMORY_LIMITS:
        identifier = command(compose('sut', 'ps', '-a', '-q', name)).strip()
        value = json.loads(command(['docker', 'inspect', identifier]))[0]
        value['State'] = {'Running': True, 'OOMKilled': False}
        validate_container(name, value, config['services'][name]['cpuset'], config['services'][name]['pids_limit'])
    command(compose('sut', 'up', '-d', '--wait', '--wait-timeout', '300'), capture=False)
    verify()


@contextmanager
def gateway_capture(identifier, directory):
    # Follow stderr before injection so Docker access-log rotation cannot erase early errors.
    # Raw runtime files are private; publish only redacted diagnostics.
    path = RUNTIME / ('gateway-'+directory.name+'.log')
    try:
        with path.open('w') as file:
            process = subprocess.Popen(['docker','logs','--follow',identifier],stdout=subprocess.DEVNULL,stderr=file)
            try:
                yield
            finally:
                if process.poll() is None:
                    process.terminate()
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
    finally:
        log = path.read_text()
        if ENV_FILE.exists():
            for secret in initialize().values(): log=log.replace(secret,'[REDACTED]')
        (directory/'nginx-error.log').write_text(log)


def run_stage(arguments, deadline, *, preflight=False):
    from protocol import parse_args, VERSION
    args = parse_args(arguments)
    require_start_memory()
    check_engine()
    target = verify()
    source = json.loads((RUNTIME / 'source.json').read_text())
    if source['sourceSha256'] != source_metadata()['sourceSha256']:
        raise RuntimeError('Runner sources changed after build')
    if target['source']['sourceSha256'] != source_metadata('sut')['sourceSha256']:
        raise RuntimeError('SUT sources changed after build')
    config = json.loads(command(compose('sut', 'config', '--format', 'json')))
    identifier_list = [command(compose('sut', 'ps', '-q', name)).strip() for name in MEMORY_LIMITS]
    run_id = (f"{os.environ['GITHUB_RUN_ID']}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}-preflight" if preflight else
              f"{os.environ['GITHUB_RUN_ID']}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}-{args.plan}-{secrets.token_hex(3)}")
    watch = RUNTIME / 'host-watch' / run_id
    converted = list(arguments)
    overrides = {'command': converted, 'user': f'{os.getuid()}:{os.getgid()}',
                 'environment': {'BENCH_RUN_ID': run_id,
                                 'BENCH_STAGE_DEADLINE': str(deadline),
                                 'BENCH_HOST_STATUS': str(Path('/bench/runtime/host-watch') / run_id / 'status.json')}}
    if preflight:
        overrides['entrypoint'] = ['python3', '/workspace/scripts/experiments/lib/preflight.py']
    result_dir = REPORTS / ('preflight' if preflight else args.plan) / run_id
    # Create evidence location before startup, so even a failed container launch is reported.
    result_dir.mkdir(parents=True)
    atomic_json(result_dir / 'manifest.json', {'schemaVersion': VERSION, 'plan': args.plan, 'stage': 'preflight' if preflight else 'experiment',
                'scope': 'validation' if preflight or args.validation_only else 'performance', 'status': 'running',
                'deployment': 'github-actions', 'source': source, 'actions': actions_context()})
    identifier, watchdog, failure = None, None, None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', dir=RUNTIME) as override:
            json.dump({'services': {'load-runner': overrides}}, override)
            override.flush()
            invocation = compose('runner', '--file', override.name)
            service = json.loads(command(invocation + ['config', '--format', 'json']))['services']['load-runner']
            validate_runner_config(service)
            command(invocation + ['create', '--force-recreate', 'load-runner'], capture=False)
            identifier = command(invocation + ['ps', '-a', '-q', 'load-runner']).strip()
            container = json.loads(command(['docker', 'inspect', identifier]))[0]
            validate_runner_container(container, service['cpuset'])
            load_host = {'machineId': machine_id(), 'cpus': 2, 'memoryMiB': 4096, 'environment': environment(),
                         'actions': actions_context(), 'cpuset': service['cpuset'], 'imageId': container['Image'],
                         'engine': json.loads(command(['docker', 'version', '--format', '{{json .Server}}']))}
            atomic_json(RUNTIME / 'load-host.json', load_host)
            expected = {row['service']: row['startedAt'] for row in target['services']}
            memory_observations = 0

            def observe():
                nonlocal memory_observations
                values = json.loads(command(['docker', 'inspect', *identifier_list, identifier], timeout=5))
                rows = []
                for name, value in zip(MEMORY_LIMITS, values[:-1]):
                    row = validate_container(name, value, config['services'][name]['cpuset'], config['services'][name]['pids_limit'])
                    if row['startedAt'] != expected[name]:
                        raise RuntimeError('SUT container restarted: ' + name)
                    row['resources'] = container_resources(value)
                    validate_shared_cpu_resources(row['resources'],config['services'][name]['cpuset'])
                    rows.append(row)
                validate_runner_container(values[-1], service['cpuset'])
                if values[-1]['State'].get('OOMKilled'):
                    raise RuntimeError('Load runner was OOM-killed')
                rows.append({'service': 'load-runner', 'resources': container_resources(values[-1])})
                sample = preflight_memory(rows, 64 if memory_observations == 0 else 32)
                sample['timeMs'] = int(time.time()*1000)
                with (result_dir/'memory-checks.jsonl').open('a') as log:
                    log.write(json.dumps(sample)+'\n')
                memory_observations += 1
                if sample.get('error'): raise RuntimeError(sample['error'])
                return rows

            def stop(ids):
                command(['docker', 'stop', '--timeout', '5', *ids], timeout=20)

            watchdog = HostMonitor(watch, observe, lambda: stop([identifier, *identifier_list]),
                                   lambda: stop([identifier]), (min(args.drain_seconds, 30) if preflight else args.drain_seconds) + args.request_timeout + 30,
                                   deadline=deadline)
            with gateway_capture(dict(zip(MEMORY_LIMITS,identifier_list))['nginx'],result_dir), watchdog:
                command(['docker', 'start', '--attach', identifier], capture=False)
                container_state = json.loads(command(['docker', 'inspect', identifier]))[0]['State']
            watchdog.last_observation = 0
            watchdog.sample()
            if container_state['ExitCode'] or container_state.get('OOMKilled') or watchdog.failure:
                raise RuntimeError(f"Load failed: exit={container_state['ExitCode']}, OOM={container_state.get('OOMKilled')}, watchdog={watchdog.failure}")
    except BaseException as error:
        failure = str(error) or type(error).__name__
        raise
    finally:
        try:
            capture_final_containers({**dict(zip(MEMORY_LIMITS, identifier_list)), 'load-runner': identifier},
                                     result_dir / 'containers-final.json')
        except Exception as error:
            # Never hide the runner error if diagnostic collection itself fails.
            print(f'Final container diagnostics unavailable: {type(error).__name__}', file=sys.stderr)
        finalize_host_evidence(result_dir, watch, failure or (watchdog.failure if watchdog else None))
        if identifier:
            command(['docker', 'stop', '--timeout', '15', identifier])
            command(['docker', 'rm', identifier])
    return result_dir


def verify_clean_start():
    """Check this job's live, unused environment immediately before warmup."""
    target = verify()
    if target['instanceId'] != initialize()['SUT_INSTANCE_ID']:
        raise RuntimeError('Startup identity does not match this job')
    tables = BUSINESS_TABLES
    query = ' UNION ALL '.join(f"SELECT '{name}',COUNT(*) FROM {name}" for name in tables)
    result = command(compose('sut', 'exec', '-T', 'mysql', 'sh', '-c',
                            'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot '
                            '--database="${MYSQL_DATABASE:?MYSQL_DATABASE is required for startup verification}" '
                            '--batch --skip-column-names --execute "$1"',
                            'sh', query), timeout=30)
    counts = {name: int(count) for name, count in (line.split('\t') for line in result.splitlines())}
    if set(counts) != set(tables) or any(counts.values()):
        raise RuntimeError('Initial environment contains business rows in the formal measurement database')
    command(compose('sut', 'exec', '-T', 'backend', 'sh', '-c', 'test ! -s /evidence/commits.jsonl'), timeout=10)
    redis_size = command(compose('sut', 'exec', '-T', 'redis', 'sh', '-c',
                                'REDISCLI_AUTH="$REDIS_PASSWORD" exec redis-cli DBSIZE'), timeout=10)
    if redis_size.strip() != '0':
        raise RuntimeError('Initial environment contains Redis data in the formal environment')
    return {'status': 'completed', 'instanceId': target['instanceId'], 'emptyTables': counts,
            'emptyJournal': True, 'emptyRedis': True}



def run_fixture_check(experiment, minutes):
    """Full fixture growth/probe regression, never part of a performance run."""
    if not 1 <= minutes <= 12: raise RuntimeError('Fixture regression requires a 1–12 minute budget')
    verify_clean_start()
    return run_stage([experiment], time.monotonic()+minutes*60, preflight=True)


def check_generator(experiment):
    output=REPORTS/'generator-check'
    output.mkdir(parents=True,exist_ok=True)
    pins=json.loads((RUNTIME/'cpu-affinity.json').read_text())
    source=source_metadata()['sourceSha256']
    marker=output/'check.json'
    marker.unlink(missing_ok=True)
    command(['docker','run','--rm','--network','none','--cpus','2','--cpuset-cpus',pins['runner'],
             '--memory','4g','--memory-swap','4g','--pids-limit','128','--ulimit','nofile=65536:65536',
             '--user',f'{os.getuid()}:{os.getgid()}', '-v',f'{output}:/check',
             '--entrypoint','python3','shanpu-experiment-runner:ci',
             '/workspace/scripts/experiments/checks/k6_recovery.py','--output','/check','--experiment',experiment,'--short'],capture=False)
    result=json.loads(marker.read_text())
    if result.get('status')!='passed': raise RuntimeError('Current machine generator check failed')
    result.update(sourceSha256=source,machineId=machine_id(),actions=actions_context(),cpuset=pins['runner'])
    atomic_json(marker,result)


def require_generator_check(experiment):
    try:
        marker=json.loads((REPORTS/'generator-check/check.json').read_text())
        pins=json.loads((RUNTIME/'cpu-affinity.json').read_text())
        if (marker['status']!='passed' or marker['mode']!='machine-check' or marker['experiment']!=experiment
                or marker['sourceSha256']!=source_metadata()['sourceSha256'] or marker['machineId']!=machine_id()
                or marker['actions']!=actions_context() or marker['cpuset']!=pins['runner']):
            raise ValueError('Generator evidence belongs to another source, experiment, job or CPU allocation')
    except (OSError,KeyError,ValueError) as error:
        raise RuntimeError('Current machine generator verification is required: '+str(error)) from error


def run_experiment(experiment, minutes):
    if not 1 <= minutes <= 45:
        raise RuntimeError('Measurement deadline must be between 1 and 45 minutes')
    deadline = min(time.monotonic()+minutes*60, json.loads((RUNTIME/'deadline.json').read_text())['monotonic'])
    suite = json.loads((REPORTS/'suite.json').read_text())
    suite.update(mode=experiment, scope='performance', phase='measurement')
    atomic_json(REPORTS/'suite.json', suite)
    try:
        require_generator_check(experiment)
        startup = verify_clean_start()
        atomic_json(REPORTS/"startup.json", startup)
        directory = run_stage([experiment], deadline)
        suite['stages'] = [str(directory.relative_to(REPORTS))]
        value = json.loads((directory/'experiment.json').read_text())
        suite['status'] = value['status']
    except BaseException as error:
        suite.update(status='failed', error=str(error) or type(error).__name__)
        raise
    finally:
        atomic_json(REPORTS/'suite.json', suite)


def collect(public=None):
    public = public or REPORTS / 'diagnostics'
    public.mkdir(parents=True, exist_ok=True)
    for kind in ('sut', 'runner'):
        try:
            # No docker inspect/config/env dump: those contain disposable credentials.
            result = subprocess.run(compose(kind, 'logs', '--no-color', '--tail', '3000'), cwd=ROOT,
                                    capture_output=True, text=True, timeout=30)
            log = result.stdout + result.stderr
            if ENV_FILE.exists():
                for secret in initialize().values():
                    log = log.replace(secret, '[REDACTED]')
            (public / f'{kind}.log').write_text(log)
        except (OSError, subprocess.SubprocessError) as error:
            (public / f'{kind}.log').write_text(type(error).__name__)
    try:
        identifier = command(compose('sut', 'ps', '-a', '-q', 'nginx')).strip()
        if identifier:
            destination = public/'nginx-error.log'
            # Nginx errors go to stderr; retain all available errors, not an access-log tail.
            result = subprocess.run(['docker','logs',identifier],stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE,text=True,timeout=30)
            log = result.stderr
            if ENV_FILE.exists():
                for secret in initialize().values(): log = log.replace(secret, '[REDACTED]')
            destination.write_text(log)
    except (OSError, subprocess.SubprocessError):
        pass  # Service may have failed before creating the log.
    # Export the last complete snapshot even if the publisher/runner failed.
    try:
        identifier = command(compose('sut', 'ps', '-a', '-q', 'backend')).strip()
        if identifier:
            for source, destination in (('commits.jsonl', 'journal.jsonl'), ('metrics.json', 'backend-last.json')):
                try:
                    command(['docker', 'cp', f'{identifier}:/evidence/{source}', str(public / destination)])
                except subprocess.SubprocessError:
                    pass  # A failure before the first commit need not create a journal.
    except subprocess.SubprocessError:
        pass


def cleanup():
    # Only dedicated CI projects; no host-wide prune or unrelated containers.
    if ENV_FILE.exists():
        for kind in ('runner', 'sut'):
            command(compose(kind, 'down', '--volumes', '--remove-orphans', '--timeout', '15'), capture=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'build', 'check-load', 'up', 'fixture-check', 'run', 'collect', 'cleanup'])
    parser.add_argument('--experiment', choices=['redis-filtering', 'kafka-burst'], default='redis-filtering')
    parser.add_argument('--minutes', type=int, default=30)
    args = parser.parse_args()
    require_actions()
    os.environ['BENCH_ROOT'] = str(ROOT)
    started=time.monotonic()
    status='failed'
    try:
        if args.action == 'prepare':
            prepare_actions()
        elif args.action == 'build':
            initialize()
            check_engine()
            build()
        elif args.action == 'check-load':
            check_generator(args.experiment)
        elif args.action == 'up':
            start_services()
        elif args.action == 'fixture-check':
            run_fixture_check(args.experiment, args.minutes)
        elif args.action == 'run':
            run_experiment(args.experiment, args.minutes)
        elif args.action == 'collect':
            collect()
        elif args.action == 'cleanup':
            cleanup()
        status='completed'
    finally:
        path=REPORTS/'timings.json'
        rows=json.loads(path.read_text()) if path.exists() else []
        rows.append({'stage':args.action,'seconds':round(time.monotonic()-started,3),'status':status})
        atomic_json(path,rows)



if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'::error::{type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(1)
