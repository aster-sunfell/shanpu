"""GitHub Actions only: isolated ephemeral services, bounded load and evidence."""
import argparse
import json
import hashlib
import tempfile
import signal
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from host import (atomic_json, HostMonitor, cpu_list, cpu_string, environment, require_start_memory,
                  select_affinity, container_resources, finalize_host_evidence)


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
LIMITS = {"backend": (.8, 512), "mysql": (.5, 384), "redis": (.1, 96), "kafka": (.6, 448)}


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
    if budget != {"cpus": 2, "memory_mib": 2048, "host_reserve_mib": 608}:
        raise RuntimeError("Target-machine budget must remain exactly 2 CPUs / 2048 MiB")
    if set(config["services"]) != set(LIMITS):
        raise RuntimeError("Unexpected services in the target-machine budget")
    total_cpu, total_memory = 0, 0
    for name, (cpus, mib) in LIMITS.items():
        service = config["services"][name]
        if (float(service.get("cpus", 0)) != cpus or int(service.get("mem_limit", 0)) != mib * MIB
                or int(service.get("memswap_limit", 0)) != mib * MIB
                or service.get("restart") != "no" or not 0 < service.get("pids_limit", 0) <= 192):
            raise RuntimeError(f"Invalid hard resource limits for {name}")
        total_cpu += cpus
        total_memory += mib
    if abs(total_cpu - 2) > 1e-9 or total_memory + budget["host_reserve_mib"] != 2048:
        raise RuntimeError("Target budget sum is inconsistent")
    return {"cpuLimit": total_cpu, "containerMemoryMiB": total_memory,
            "hostReserveMiB": 608, "machineMemoryMiB": 2048}


def validate_container(name, container, expected_cpuset=None):
    cpus, mib = LIMITS[name]
    actual = container["HostConfig"]
    if (actual.get("NanoCpus") != round(cpus * 1e9) or actual.get("Memory") != mib * MIB
            or actual.get("MemorySwap") != mib * MIB or not 0 < actual.get("PidsLimit", 0) <= 192
            or actual.get("Privileged") or actual.get("NetworkMode") == "host"
            or actual.get("RestartPolicy", {}).get("Name") != "no"):
        raise RuntimeError(f"Docker did not apply the required CPU/memory/swap limits to {name}")
    actual_cpuset = actual.get('CpusetCpus', '')
    if expected_cpuset is not None and cpu_list(actual_cpuset or '') != cpu_list(expected_cpuset or ''):
        raise RuntimeError(f'Docker did not apply the required CPU affinity to {name}')
    if (container["State"].get("OOMKilled") or not container["State"].get("Running")
            or container["State"].get("Health", {}).get("Status", "healthy") != "healthy"):
        raise RuntimeError(f"Target service {name} has stopped or was OOM-killed")
    return {"service": name, "imageId": container["Image"], "cpuLimit": cpus,
            "memoryLimitMiB": mib, "memorySwapMiB": mib, "oomKilled": False,
            'startedAt': container['State'].get('StartedAt'),
            'cpuset': cpu_string(cpu_list(actual_cpuset)) if actual_cpuset else ''}


def machine_id():
    return hashlib.sha256(Path("/etc/machine-id").read_bytes()).hexdigest()


def verify():
    config = json.loads(command(compose("sut", "config", "--format", "json")))
    result = validate_config(config)
    services = []
    for name in LIMITS:
        identifier = command(compose("sut", "ps", "-a", "-q", name)).strip()
        if not identifier:
            raise RuntimeError(f"Missing target container: {name}")
        container = json.loads(command(["docker", "inspect", identifier]))[0]
        services.append(validate_container(name, container, config['services'][name].get('cpuset', '')))
    result.update(instanceId=initialize()["SUT_INSTANCE_ID"], services=services, machineId=machine_id(),
                  actions=actions_context(),
                  source=json.loads((RUNTIME / "sut-source.json").read_text()),
                  environment=environment(), verifiedAtMs=int(time.time() * 1000))
    (RUNTIME / "target.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def source_metadata(scope="runner"):
    digest = hashlib.sha256()
    if scope == "sut":
        names = ("pom.xml", "backend", "experiments/observer", "experiments/application",
                 "experiments/docker/Sut.Dockerfile", "experiments/compose.sut.yml")
    else:
        names = ("scripts/experiments", "experiments/docker/Runner.Dockerfile",
                 "experiments/requirements.txt", "experiments/compose.runner.yml")
    paths = []
    for name in names:
        root = ROOT / name
        paths.extend([root] if root.is_file() else (p for p in root.rglob("*") if p.is_file()
                     and not {"target", "__pycache__"}.intersection(p.parts)))
    for path in sorted(paths):
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0")
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(65536), b""):
                digest.update(chunk)
    return {"scope": scope, "gitCommit": command(["git", "rev-parse", "HEAD"]).strip(),
            "gitStatus": command(["git", "status", "--short"]), "sourceSha256": digest.hexdigest()}


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
                    "cp experiments/application/target/shanpu-experiment-app-1.0.0.jar /output/app.jar")
    command(["docker", "run", "--rm", "--name", "shanpu-experiment-build", "--cpus", "2",
             "--memory", "768m", "--memory-swap", "768m", "--pids-limit", "128",
             "-e", "MAVEN_OPTS=-Xms64m -Xmx384m -XX:ActiveProcessorCount=2",
             "-v", f"{ROOT}:/source:ro", "-v", f"{output}:/output",
             "-v", "shanpu-experiment-m2:/root/.m2", "maven:3.9-eclipse-temurin-17",
             "sh", "-c", build_script], capture=False)
    command(compose("sut", "build", "backend"), capture=False)
    command(compose("runner", "build"), capture=False)
    if source["sourceSha256"] != source_metadata()["sourceSha256"] or sut_source["sourceSha256"] != source_metadata("sut")["sourceSha256"]:
        raise RuntimeError("Sources changed during build; build again to capture a consistent image")
    (RUNTIME / "source.json").write_text(json.dumps(source, indent=2) + "\n")
    (RUNTIME / "sut-source.json").write_text(json.dumps(sut_source, indent=2) + "\n")


def validate_runner_config(service):
    if (float(service.get("cpus", 0)) != 1 or int(service.get("mem_limit", 0)) != 512 * MIB
            or int(service.get("memswap_limit", 0)) != 512 * MIB or service.get("pids_limit") != 128
            or service.get("restart") != "no"):
        raise RuntimeError("Load runner must have fixed 1 CPU / 512 MiB limits")
    for mount in service.get("volumes", []):
        if "docker.sock" in mount.get("source", ""):
            raise RuntimeError("The load runner must not access the Docker socket")


def validate_runner_container(container, expected_cpuset=None):
    actual = container["HostConfig"]
    if (actual.get("NanoCpus") != 1000000000 or actual.get("Memory") != 512 * MIB
            or actual.get("MemorySwap") != 512 * MIB or actual.get("PidsLimit") != 128
            or actual.get("Privileged") or actual.get("RestartPolicy", {}).get("Name") != "no"):
        raise RuntimeError("Docker did not apply the load runner limits")
    if expected_cpuset is not None and cpu_list(actual.get('CpusetCpus', '') or '') != cpu_list(expected_cpuset or ''):
        raise RuntimeError('Docker did not apply the load runner CPU affinity')
    if any("docker.sock" in mount.get("Source", "") for mount in container.get("Mounts", [])):
        raise RuntimeError("Load runner has an unexpected Docker socket mount")



def prepare_actions():
    require_start_memory()
    check_engine()
    REPORTS.mkdir(parents=True, exist_ok=True)
    for name, value in initialize().items():
        # Mask disposable secrets before Docker emits any build/startup output.
        print(f'::add-mask::{value}', flush=True)
    pins = select_affinity()
    atomic_json(RUNTIME / 'cpu-affinity.json', pins)
    atomic_json(RUNTIME / 'actions.json', actions_context())
    atomic_json(RUNTIME / 'deadline.json', {'monotonic': time.monotonic() + 160 * 60})
    atomic_json(REPORTS / 'environment.json', {'actions': actions_context(), 'cpuAllocation': pins,
                                               'host': environment(), 'targetBudget': validate_config(
        json.loads(command(compose('sut', 'config', '--format', 'json'))))})
    atomic_json(REPORTS / 'suite.json', {'status': 'running', 'actions': actions_context(), 'stages': []})
    print(json.dumps({'cpuAllocation': pins, 'environment': environment()}, ensure_ascii=False), flush=True)


def start_services():
    require_start_memory()
    config = json.loads(command(compose('sut', 'config', '--format', 'json')))
    validate_config(config)
    command(compose('sut', 'create'), capture=False)
    for name in LIMITS:
        identifier = command(compose('sut', 'ps', '-a', '-q', name)).strip()
        value = json.loads(command(['docker', 'inspect', identifier]))[0]
        value['State'] = {'Running': True, 'OOMKilled': False}
        validate_container(name, value, config['services'][name]['cpuset'])
    command(compose('sut', 'up', '-d', '--wait', '--wait-timeout', '300'), capture=False)
    verify()


def run_stage(arguments, deadline):
    from protocol import parse_args
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
    identifier_list = [command(compose('sut', 'ps', '-q', name)).strip() for name in LIMITS]
    run_id = f"{os.environ['GITHUB_RUN_ID']}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}-{args.stage}-{secrets.token_hex(3)}"
    watch = RUNTIME / 'host-watch' / run_id
    converted = list(arguments)
    for index, argument in enumerate(converted):
        if argument == '--baseline-report':
            converted[index + 1] = str(Path('/bench/reports') / Path(converted[index + 1]).relative_to(REPORTS))
    overrides = {'command': converted, 'user': f'{os.getuid()}:{os.getgid()}',
                 'environment': {'BENCH_RUN_ID': run_id,
                                 'BENCH_HOST_STATUS': str(Path('/bench/runtime/host-watch') / run_id / 'status.json')}}
    result_dir = REPORTS / args.plan / run_id
    # Create evidence location before startup, so even a failed container launch is reported.
    result_dir.mkdir(parents=True)
    atomic_json(result_dir / 'manifest.json', {'schemaVersion': 3, 'plan': args.plan, 'stage': args.stage,
                'scope': 'validation' if args.validation_only else 'performance', 'status': 'running',
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
            load_host = {'machineId': machine_id(), 'cpus': 1, 'memoryMiB': 512, 'environment': environment(),
                         'actions': actions_context(), 'cpuset': service['cpuset'], 'imageId': container['Image'],
                         'engine': json.loads(command(['docker', 'version', '--format', '{{json .Server}}']))}
            atomic_json(RUNTIME / 'load-host.json', load_host)
            expected = {row['service']: row['startedAt'] for row in target['services']}

            def observe():
                values = json.loads(command(['docker', 'inspect', *identifier_list, identifier], timeout=5))
                rows = []
                for name, value in zip(LIMITS, values[:-1]):
                    row = validate_container(name, value, config['services'][name]['cpuset'])
                    if row['startedAt'] != expected[name]:
                        raise RuntimeError('SUT container restarted: ' + name)
                    row['resources'] = container_resources(value)
                    rows.append(row)
                validate_runner_container(values[-1], service['cpuset'])
                if values[-1]['State'].get('OOMKilled'):
                    raise RuntimeError('Load runner was OOM-killed')
                rows.append({'service': 'load-runner', 'resources': container_resources(values[-1])})
                return rows

            def stop(ids):
                command(['docker', 'stop', '--timeout', '5', *ids], timeout=20)

            watchdog = HostMonitor(watch, observe, lambda: stop([identifier, *identifier_list]),
                                   lambda: stop([identifier]), args.drain_seconds + args.request_timeout + 30,
                                   deadline=deadline)
            with watchdog:
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
        finalize_host_evidence(result_dir, watch, failure or (watchdog.failure if watchdog else None))
        if identifier:
            command(['docker', 'stop', '--timeout', '15', identifier])
            command(['docker', 'rm', identifier])
    return result_dir


def run_suite(mode, rates, seconds, repeats, vus, refine_steps, minutes):
    if mode not in ('full', 'redis', 'kafka', 'smoke'):
        raise RuntimeError('Unsupported experiment selection')
    if not 1 <= minutes <= 150:
        raise RuntimeError('Internal deadline must be between 1 and 150 minutes')
    from protocol import parse_args, read_baseline, validate_baseline
    # Parse all numeric inputs before starting services or creating fixtures.
    common = ['--rates', rates, '--seconds', str(seconds), '--repeats', str(repeats),
              '--vus', str(vus), '--refine-steps', str(refine_steps)]
    if mode == 'smoke':
        common = ['--rates', '5', '--seconds', '2', '--repeats', '1', '--vus', '5',
                  '--refine-steps', '0', '--validation-only']
    parse_args(['redis-filtering', *common])
    deadline = min(time.monotonic() + minutes * 60,
                   json.loads((RUNTIME / 'deadline.json').read_text())['monotonic'])
    suite = json.loads((REPORTS / 'suite.json').read_text())
    suite.update(mode=mode, scope='validation' if mode == 'smoke' else 'performance')
    atomic_json(REPORTS / 'suite.json', suite)
    try:
        plans = ['redis-filtering', 'kafka-buffering'] if mode in ('full', 'smoke') else [
            'redis-filtering' if mode == 'redis' else 'kafka-buffering']
        for plan in plans:
            if time.monotonic() >= deadline:
                raise RuntimeError('Experiment deadline reached before next stage')
            directory = run_stage([plan, *common], deadline)
            suite['stages'].append(str(directory.relative_to(REPORTS)))
            atomic_json(REPORTS / 'suite.json', suite)
            if plan == 'kafka-buffering':
                baseline = directory / 'capacity.json'
                # Fails closed if B is unbounded or insufficient. No manually supplied Q.
                burst_args = [plan, '--stage', 'burst', '--baseline-report', str(baseline), *common]
                validate_baseline(read_baseline(baseline), parse_args(burst_args))
                directory = run_stage(burst_args, deadline)
                suite['stages'].append(str(directory.relative_to(REPORTS)))
                atomic_json(REPORTS / 'suite.json', suite)
        suite['status'] = 'completed'
    except BaseException as error:
        suite.update(status='failed', error=str(error) or type(error).__name__)
        raise
    finally:
        atomic_json(REPORTS / 'suite.json', suite)


def collect():
    public = REPORTS / 'diagnostics'
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
    # Export commit journal before the ephemeral volume is destroyed.
    try:
        identifier = command(compose('sut', 'ps', '-a', '-q', 'backend')).strip()
        if identifier:
            command(['docker', 'cp', f'{identifier}:/evidence/commits.jsonl', str(public / 'journal.jsonl')])
    except subprocess.SubprocessError:
        pass


def cleanup():
    # Only dedicated CI projects; no host-wide prune or unrelated containers.
    if ENV_FILE.exists():
        for kind in ('runner', 'sut'):
            command(compose(kind, 'down', '--volumes', '--remove-orphans', '--timeout', '15'), capture=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'build', 'up', 'run', 'collect', 'cleanup'])
    parser.add_argument('--mode', choices=['full', 'redis', 'kafka', 'smoke'], default='full')
    parser.add_argument('--rates', default='25,50,100,200')
    parser.add_argument('--seconds', type=int, default=60)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--vus', type=int, default=20)
    parser.add_argument('--refine-steps', type=int, default=4)
    parser.add_argument('--minutes', type=int, default=150)
    args = parser.parse_args()
    require_actions()
    os.environ['BENCH_ROOT'] = str(ROOT)
    if args.action == 'prepare':
        prepare_actions()
    elif args.action == 'build':
        initialize()
        check_engine()
        build()
    elif args.action == 'up':
        start_services()
    elif args.action == 'run':
        run_suite(args.mode, args.rates, args.seconds, args.repeats, args.vus, args.refine_steps, args.minutes)
    elif args.action == 'collect':
        collect()
    elif args.action == 'cleanup':
        cleanup()


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'::error::{type(error).__name__}: {error}', file=sys.stderr)
        sys.exit(1)
