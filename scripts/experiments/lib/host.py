"""GitHub-hosted Linux runner affinity, evidence and an independent watchdog."""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import threading
import time

MIB = 1024 * 1024
START_MEMORY_MIB = 3072
STOP_MEMORY_MIB = 1024
CRITICAL_MEMORY_MIB = 512


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False) + '\n')
    temporary.replace(path)


def memory():
    values = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    return {key: int(values[key].split()[0]) * 1024
            for key in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree')}


def cpu_list(value):
    if value == '': return []
    result = set()
    try:
        for item in value.split(','):
            ends = item.split('-')
            if len(ends) > 2: raise ValueError()
            first, last = int(ends[0]), int(ends[-1])
            if not 0 <= first <= last < 65536: raise ValueError()
            result.update(range(first, last + 1))
    except (ValueError, AttributeError):
        raise RuntimeError('CPU sets must be nonnegative CPU IDs or ranges, such as 0,2 or 0-1') from None
    return sorted(result)


def cpu_string(cpus):
    return ','.join(map(str, sorted(cpus)))


def topology():
    answer = []
    for cpu in sorted(os.sched_getaffinity(0)):
        root = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        answer.append({'cpu': cpu, 'socket': int((root/'physical_package_id').read_text()),
                       'core': int((root/'core_id').read_text()),
                       'siblings': cpu_list((root/'thread_siblings_list').read_text().strip())})
    return answer


def validate_affinity(sut, runner, rows):
    sut, runner = cpu_list(sut), cpu_list(runner)
    available = {row['cpu'] for row in rows}
    if len(available) < 4:
        raise RuntimeError('GitHub Actions measurement requires at least four available vCPUs')
    if len(sut) != 2 or len(runner) != 1 or set(sut) & set(runner):
        raise RuntimeError('Require two SUT vCPUs and one distinct load vCPU')
    if not set(sut + runner) <= available:
        raise RuntimeError('Selected CPUs are unavailable')
    shared = [row['cpu'] for row in rows if row['cpu'] in sut
              and set(row['siblings']) & set(runner)]
    return {'sut': cpu_string(sut), 'runner': cpu_string(runner),
            'reserved': cpu_string(available - set(sut + runner)),
            'crossGroupSmt': shared, 'topology': rows,
            'isolation': 'disjoint-vCPU-sets; physical host resources remain shared'}


def select_affinity():
    rows = topology()
    groups = {}
    for row in rows:
        groups.setdefault((row['socket'], row['core']), []).append(row['cpu'])
    # Keep reported SMT siblings together in the SUT when possible, rather
    # than making k6 and the SUT compete on the same reported core.
    pair = next((cpus[:2] for cpus in groups.values() if len(cpus) >= 2), None)
    sut = pair or [row['cpu'] for row in rows[:2]]
    remaining = [row['cpu'] for row in rows if row['cpu'] not in sut]
    if len(remaining) < 2:
        raise RuntimeError('GitHub Actions measurement requires at least four available vCPUs')
    return validate_affinity(cpu_string(sut), str(remaining[0]), rows)


def environment():
    model = next((line.split(':', 1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
                  if line.startswith('model name')), 'unknown')
    return {'kernel': platform.release(), 'platform': platform.platform(), 'cpuModel': model,
            'availableCpus': sorted(os.sched_getaffinity(0)), 'cpuTopology': topology(),
            'memoryBytes': memory()['MemTotal']}


def require_start_memory():
    if memory()['MemAvailable'] < START_MEMORY_MIB * MIB:
        raise RuntimeError('Available runner memory is below 3072 MiB')


def container_resources(container):
    """Read cgroup v2 counters on the Docker host without launching docker stats."""
    try:
        pid = int(container['State']['Pid'])
        entry = next(line.split(':', 2)[2] for line in Path(f'/proc/{pid}/cgroup').read_text().splitlines()
                     if line.startswith('0::'))
        root = Path('/sys/fs/cgroup')/entry.lstrip('/')
        values = {name: (root/name).read_text().strip()
                  for name in ('memory.current', 'memory.events', 'cpu.stat', 'io.stat')}
        values['memory.stat'] = '\n'.join(line for line in (root/'memory.stat').read_text().splitlines()
                                         if line.split()[0] in ('anon', 'file', 'kernel', 'inactive_file'))
        if any(int(row.split()[1]) for row in values['memory.events'].splitlines() if row.startswith('oom_kill ')):
            raise RuntimeError('Container cgroup recorded an OOM kill; preserve this run')
        return values
    except (OSError, KeyError, StopIteration):
        return {'unavailable': 'Host cgroup v2 counters are unavailable; use external container resource sampling'}


def preflight_memory(containers, reserve_mib):
    try:
        mysql = next(row for row in containers if row['service'] == 'mysql')
        resources = mysql.get('resources', {})
        current = int(resources['memory.current'])
        fields = dict(line.split() for line in resources['memory.stat'].splitlines())
        inactive = int(fields['inactive_file'])
        events = {key: int(value) for key, value in
                  (line.split() for line in resources['memory.events'].splitlines())}
        limit = mysql['memoryLimitMiB'] * MIB
        if not {'max', 'oom', 'oom_kill'} <= set(events): raise ValueError('Incomplete memory events')
    except (KeyError, ValueError, StopIteration):
        raise RuntimeError('Preflight requires readable MySQL cgroup memory counters') from None
    if not 0 <= inactive <= current <= limit:
        raise RuntimeError('Preflight MySQL memory counters are inconsistent or above the limit')
    # An estimate, not guaranteed allocatable memory: do not count inactive file
    # cache as anonymous working memory. Keep raw usage and pressure counters too.
    working = current - inactive
    sample = {'currentMiB': round(current/MIB, 3), 'inactiveFileMiB': round(inactive/MIB, 3),
              'workingMiB': round(working/MIB, 3), 'headroomMiB': round((limit-working)/MIB, 3),
              'requiredHeadroomMiB': reserve_mib, 'events': events}
    if events.get('oom', 0) or events.get('oom_kill', 0) or limit-working < reserve_mib*MIB:
        sample['error'] = (f"Preflight MySQL memory check failed: headroom={sample['headroomMiB']} MiB, "
                           f"required={reserve_mib} MiB, current={sample['currentMiB']} MiB, "
                           f"oom={events.get('oom',0)}, oom_kill={events.get('oom_kill',0)}")
    return sample


def check_host_health():
    """Read only host-produced evidence; the runner never receives Docker access."""
    name = os.environ.get('BENCH_HOST_STATUS')
    if not name:
        return  # Offline analysis and tests do not launch load.
    try:
        value = json.loads(Path(name).read_text())
        age = time.monotonic() - value['monotonicSeconds']
        if not 0 <= age <= 30: raise RuntimeError('Host watchdog evidence is stale')
        if value['failure']: raise RuntimeError(value['failure'])
        return value
    except (OSError, ValueError, KeyError) as error:
        raise RuntimeError('Host watchdog evidence is missing or unreadable; stop load') from error


def finalize_host_evidence(directory, watch_directory, failure=None):
    """Host has the final word even if the runner was killed before its finally block."""
    directory, watch_directory = Path(directory), Path(watch_directory)
    if not directory.exists(): return
    (directory/'host-watch').mkdir(exist_ok=True)
    for name in ('status.json', 'samples.jsonl'):
        if (watch_directory/name).exists():
            shutil.copyfile(watch_directory/name, directory/'host-watch'/name)
    if not failure: return
    for name in ('experiment.json', 'manifest.json'):
        path = directory/name
        if not path.exists(): continue
        if path.stat().st_size > 2*MIB:
            raise RuntimeError('Report metadata unexpectedly exceeds 2 MiB; preserve host failure evidence')
        value = json.loads(path.read_text())
        value.update(status='failed', hostFailure=failure)
        if name != 'manifest.json':
            value.update(comparisons=[])
        atomic_json(path,value)
    (directory/'report.md').write_text('# 性能测量中止\n\n状态：failed。\n\n'
                                      '**宿主机最终检查失败，本轮不可用于性能收益。**\n\n'
                                      '原因见 manifest.json 的 hostFailure 与 host-watch/。逐点结果和原始证据保留，未执行自动清理。\n')


class HostMonitor:
    """Independent of potentially blocked SQL/API calls inside the runner."""
    def __init__(self, directory, observe, emergency_stop, force_runner_stop, grace_seconds, deadline=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.observe, self.deadline = observe, deadline
        self.emergency_stop, self.force_runner_stop = emergency_stop, force_runner_stop
        self.grace_seconds, self.failure, self.failed_at = grace_seconds, None, None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.last_observation = 0
        self.targets = None
        self.emergency_stopped = False
        self.runner_stopped = False
        self.clock_origin = (time.time(), time.monotonic())

    def sample(self):
        now = time.monotonic()
        value = {'timeMs': int(time.time()*1000), 'monotonicSeconds': now, 'linuxMemory': memory(),
                 'loadavg': Path('/proc/loadavg').read_text().strip(),
                 'cpu': Path('/proc/stat').read_text().splitlines()[0],
                 'diskstats': Path('/proc/diskstats').read_text(),
                 'swapCounters': [line for line in Path('/proc/vmstat').read_text().splitlines()
                                  if line.startswith(('pswpin ', 'pswpout '))]}
        try:
            if now - self.last_observation >= 5:
                self.targets = self.observe()
                self.last_observation = time.monotonic()
            value.update(containers=self.targets)
            if self.deadline is not None and now >= self.deadline:
                raise RuntimeError('Experiment reached its internal deadline; reserve time for drain and artifacts')
            wall_origin, mono_origin = self.clock_origin
            value['clockShiftMs'] = (time.time()-wall_origin-(time.monotonic()-mono_origin))*1000
            if abs(value['clockShiftMs']) > 50:
                raise RuntimeError('Host wall clock shifted by more than 50 ms; preserve this run')
            limit = STOP_MEMORY_MIB * MIB
            if value['linuxMemory']['MemAvailable'] < limit:
                raise RuntimeError('Host available memory fell below 1024 MiB; stop injection and preserve this run')
            if shutil.disk_usage(self.directory).free < 1024 * MIB:
                raise RuntimeError('Host evidence disk has less than 1024 MiB free')
        except Exception as error:
            self.failure = self.failure or str(error)
            self.failed_at = self.failed_at or now
        value['failure'] = self.failure
        critical = value['linuxMemory']['MemAvailable'] < CRITICAL_MEMORY_MIB*MIB
        try:
            atomic_json(self.directory/'status.json', value)
            with (self.directory/'samples.jsonl').open('a') as file:
                file.write(json.dumps(value, ensure_ascii=False)+'\n')
        finally:
            if critical and not self.emergency_stopped:
                self.emergency_stopped = True
                self.emergency_stop()
            elif self.failed_at and now-self.failed_at > self.grace_seconds and not self.runner_stopped:
                self.runner_stopped = True
                self.force_runner_stop()

    def run(self):
        while not self.stop.wait(1):
            try:
                self.sample()
            except Exception as error:
                self.failure = self.failure or f'Host watchdog failed: {error}'
                self.failed_at = self.failed_at or time.monotonic()
                # A stale status also causes the runner to fail closed.
                if time.monotonic()-self.failed_at > self.grace_seconds and not self.runner_stopped:
                    self.runner_stopped = True
                    self.force_runner_stop()

    def __enter__(self):
        self.sample()
        if self.failure: raise RuntimeError(self.failure)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=30)
        if self.thread.is_alive(): raise RuntimeError('Host watchdog did not stop')
