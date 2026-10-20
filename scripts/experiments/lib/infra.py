"""Host-side lifecycle only. Heavy build, load generation and analysis have container caps."""
import argparse
import fcntl
import json
import hashlib
import tempfile
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

from host import (HostMonitor, cpu_list, cpu_string, environment, require_start_memory,
                  select_affinity, validate_affinity, topology, container_resources, finalize_host_evidence)

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / ".local/experiments"
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
    # Do not print or serialize these values into reports.
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


def pin(arguments):
    parser = argparse.ArgumentParser(description='Persist CPU affinity for same-host measurements')
    parser.add_argument('--sut-cpus')
    parser.add_argument('--runner-cpus')
    args = parser.parse_args(arguments)
    check_engine()
    for kind in ('sut', 'runner'):
        if command(compose(kind, 'ps', '-q')).strip():
            raise RuntimeError('Stop the SUT and runner before changing CPU affinity')
    result = select_affinity(args.sut_cpus, args.runner_cpus)
    (RUNTIME/'cpu-affinity.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["pin", "preflight", "config", "build", "up", "verify", "status", "stop", "run", "cleanup", "build-runner"])
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == "run" and "--dry-run" in args.arguments:
        from runner import main as preview
        return preview(args.arguments)
    if args.action == "run":
        from protocol import parse_args
        run_args = parse_args(args.arguments)
    initialize()
    if args.action in {"pin", "up", "verify", "stop", "run", "cleanup", "build", "build-runner"}:
        # Prevent a second invocation from recreating an actively running load container.
        host_lock = (RUNTIME / "load-host.lock").open("w")
        try:
            fcntl.flock(host_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another build/load operation is active on this host") from None
    if args.action == 'pin':
        return pin(args.arguments)
    config = json.loads(command(compose("sut", "config", "--format", "json")))
    validate_config(config)
    if args.action in {"preflight", "build", "build-runner", "up", "verify", "run", "cleanup"}:
        check_engine()
    if args.action == 'preflight':
        windows = require_start_memory(True)
        affinity = validate_affinity(os.environ.get('SUT_CPUSET', ''), os.environ.get('RUNNER_CPUSET', ''), topology())
        print(json.dumps({'affinity': affinity, 'environment': environment(), 'windows': windows,
                          'targetBudget': validate_config(config), 'status': 'ready-for-same-host'}, indent=2))
    elif args.action == "config":
        print(json.dumps(validate_config(config), indent=2))
    elif args.action == "build":
        build()
    elif args.action == "build-runner":
        require_memory(768)
        if command(compose('sut', 'ps', '-q')).strip():
            raise RuntimeError('Stop the local target before building the runner')
        command(compose("runner", "build"), capture=False)
        (RUNTIME / "source.json").write_text(json.dumps(source_metadata(), indent=2) + "\n")
    elif args.action == "up":
        source = json.loads((RUNTIME / "sut-source.json").read_text())
        if source["sourceSha256"] != source_metadata("sut")["sourceSha256"]:
            raise RuntimeError("Sources changed since build; rebuild before starting the target")
        require_start_memory(True) if os.environ.get('SUT_CPUSET') else require_memory(1536)
        command(compose("sut", "create"), capture=False)
        # Inspect limits before starting any target process, not only after it is healthy.
        for name in LIMITS:
            identifier = command(compose("sut", "ps", "-a", "-q", name)).strip()
            container = json.loads(command(["docker", "inspect", identifier]))[0]
            # Validation of resource allocation is independent of process state.
            container["State"] = {"Running": True, "OOMKilled": False}
            validate_container(name, container, config['services'][name].get('cpuset', ''))
        command(compose("sut", "up", "-d", "--wait", "--wait-timeout", "240"), capture=False)
        print(json.dumps(verify(), indent=2))
    elif args.action == "verify":
        print(json.dumps(verify(), indent=2))
    elif args.action == "status":
        command(compose("sut", "ps", "-a"), capture=False)
    elif args.action == "stop":
        command(compose("sut", "stop", "--timeout", "30"), capture=False)
    else:
        if not (RUNTIME / "target.json").exists():
            raise RuntimeError("Missing target.json; run sut.sh verify on the target host and copy its public result here")
        same_host = args.action == 'run' and run_args.deployment == 'same-host'
        windows = require_start_memory(same_host)
        target_ids = []
        if same_host:
            # Reinspect the local target, rather than trusting a stale copied target.json.
            target = verify()
            sut_sets = {service['cpuset'] for service in target['services']}
            if not run_args.validation_only:
                if len(sut_sets) != 1 or not next(iter(sut_sets)):
                    raise RuntimeError('Same-host performance requires sut.sh pin and restarting the SUT')
                validate_affinity(next(iter(sut_sets)), os.environ.get('RUNNER_CPUSET', ''), topology())
            target_ids = [command(compose('sut', 'ps', '-q', name)).strip() for name in LIMITS]
        source = json.loads((RUNTIME / "source.json").read_text())
        if source["sourceSha256"] != source_metadata()["sourceSha256"]:
            raise RuntimeError("Source changed since the images were built; stop and rebuild first")
        # Use a Compose override so the engine can be inspected BEFORE k6/Python starts.
        load_host = {'machineId': machine_id(), 'cpus': 1, 'memoryMiB': 512, 'environment': environment(),
                     'engine': json.loads(command(['docker', 'version', '--format', '{{json .Server}}']))}
        if windows:
            load_host['windows'] = {k: windows[k] for k in ('totalBytes', 'version')}
        arguments = list(args.arguments)
        for index, argument in enumerate(arguments):
            if argument == "--baseline-report":
                relative = Path(arguments[index+1]).resolve().relative_to(ROOT)
                arguments[index+1] = str(Path("/workspace") / relative)
            elif argument.startswith("--baseline-report="):
                relative = Path(argument.split("=",1)[1]).resolve().relative_to(ROOT)
                arguments[index] = "--baseline-report=" + str(Path("/workspace") / relative)
        overrides = {"command": arguments, "user": f"{os.getuid()}:{os.getgid()}"}
        run_id = time.strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(3)
        watch_directory = RUNTIME/'host-watch'/run_id
        container_watch = Path('/workspace/.local/experiments/host-watch')/watch_directory.name
        overrides['environment'] = {'BENCH_HOST_STATUS': str(container_watch/'status.json'), 'BENCH_RUN_ID': run_id}
        if args.action == "cleanup":
            if len(args.arguments) != 1:
                raise RuntimeError("cleanup requires exactly one report directory")
            relative = Path(args.arguments[0]).resolve().relative_to(ROOT)
            overrides.update(entrypoint=["python3", "/workspace/scripts/experiments/lib/cleanup.py"],
                             command=[str(Path("/workspace") / relative)])
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", dir=RUNTIME) as file:
            json.dump({"services": {"load-runner": overrides}}, file)
            file.flush()
            invocation = compose("runner", "--file", file.name)
            runner_config = json.loads(command(invocation + ["config", "--format", "json"]))["services"]["load-runner"]
            validate_runner_config(runner_config)
            command(invocation + ["create", "--force-recreate", "load-runner"], capture=False)
            identifier = command(invocation + ["ps", "-a", "-q", "load-runner"]).strip()
            container = json.loads(command(["docker", "inspect", identifier]))[0]
            validate_runner_container(container, runner_config.get('cpuset', ''))
            load_host['cpuset'] = container['HostConfig'].get('CpusetCpus', '')
            load_host['imageId'] = container['Image']
            (RUNTIME/'load-host.json').write_text(json.dumps(load_host)+'\n')
            def observe():
                rows = []
                containers = json.loads(command(['docker', 'inspect', *target_ids, identifier], timeout=5))
                if target_ids:
                    expected = {row['service']: row['startedAt'] for row in target['services']}
                    for name, value in zip(LIMITS, containers[:-1]):
                        row = validate_container(name, value, config['services'][name].get('cpuset', ''))
                        if row['startedAt'] != expected[name]:
                            raise RuntimeError(f'Target service {name} restarted during this run')
                        row['resources'] = container_resources(value)
                        rows.append(row)
                runner = containers[-1]
                validate_runner_container(runner, runner_config.get('cpuset', ''))
                if runner['State'].get('OOMKilled'):
                    raise RuntimeError('Load runner was OOM-killed')
                rows.append({'service': 'load-runner', 'resources': container_resources(runner)})
                return rows

            def stop_containers(identifiers):
                command(['docker', 'stop', '--timeout', '5', *identifiers], timeout=15)

            grace = run_args.drain_seconds + run_args.request_timeout + 30 if args.action == 'run' else 60
            watchdog = HostMonitor(watch_directory, same_host, observe,
                                   lambda: stop_containers([identifier, *target_ids]),
                                   lambda: stop_containers([identifier]), grace)
            failure = None
            try:
                with watchdog:
                    command(["docker", "start", "--attach", identifier], capture=False)
                    state = json.loads(command(["docker", "inspect", identifier]))[0]["State"]
                watchdog.last_observation = 0
                watchdog.sample()
                if state["ExitCode"] or state.get("OOMKilled") or watchdog.failure:
                    raise RuntimeError(f"Load runner failed (exit={state['ExitCode']}, OOM={state.get('OOMKilled')}, host={watchdog.failure}); host evidence: {watch_directory}")
            except BaseException as error:
                failure = str(error) or type(error).__name__
                raise
            finally:
                try:
                    if args.action == 'run':
                        parent = RUNTIME/'validation/performance-v2' if run_args.validation_only else ROOT/'docs/experiments'
                        finalize_host_evidence(parent/run_args.plan/run_id,watch_directory,watchdog.failure or failure)
                finally:
                    command(["docker", "stop", "--timeout", "15", identifier])
                    command(["docker", "rm", identifier])


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as error:
        print(f"实验环境操作失败：{error}", file=sys.stderr)
        sys.exit(1)
