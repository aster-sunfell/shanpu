#!/usr/bin/env python3
"""Performance-only A/B and B/C execution in the bounded load-runner container."""
import fcntl
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.request

import transport
from analysis import Evidence, save_result, write_json
from fixtures import POINTS, PRICE, create_users, credential, prepare, snapshot, check_disk, release_redis
from protocol import (VERSION, parse_args, matrix, budgeted_matrix, policy, context_contract,
                      validate_baseline, refinement_rates, capacities, read_baseline, report, validate_deployment)
from host import check_host_health

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = Path(os.environ.get('BENCH_RUNTIME', '/bench/runtime'))
REPORTS = Path(os.environ.get('BENCH_REPORTS', '/bench/reports'))
MYSQL_MEMORY_SETTINGS = {
    'performance_schema': 'OFF', 'innodb_buffer_pool_size': str(128 * 1024 * 1024),
    'max_connections': '32', 'thread_cache_size': '4', 'table_open_cache': '128',
    'temptable_max_ram': str(16 * 1024 * 1024), 'temptable_max_mmap': '0',
    'tmp_table_size': str(4 * 1024 * 1024), 'max_heap_table_size': str(4 * 1024 * 1024),
    'innodb_log_buffer_size': str(8 * 1024 * 1024), 'innodb_flush_log_at_trx_commit': '2',
}


def sql(statement): return transport.sql(statement)
def redis(*args): return transport.redis_command(*args)
def lag(): return transport.lag()

def api(path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"http://{transport.host()}:18081/api{path}",
                                     headers=headers, data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(request, timeout=10) as response:
        value = json.load(response)
    if "code" in value:
        if value["code"] != 0:
            raise RuntimeError(f"API {path} returned code {value['code']}")
        return value.get("data")
    return value


def require_idle(token):
    started = time.time() * 1000
    metrics = api("/admin/experiments/metrics", token)
    finished = time.time() * 1000
    offset, uncertainty = metrics["timeMs"] - (started + finished) / 2, (finished - started) / 2
    metrics["runnerClock"] = {"offsetEstimateMs": round(offset, 3), "uncertaintyMs": round(uncertainty, 3)}
    if abs(offset) > 50 + uncertainty:
        raise RuntimeError("SUT/load host clocks differ by more than 50 ms; synchronize clocks before measuring")
    if not metrics["enabled"] or metrics.get("writeErrors"):
        raise RuntimeError("Experiment journal disabled or unhealthy; start the dedicated SUT stack")
    if metrics.get("settings", {}).get("sutId") != os.environ["SUT_INSTANCE_ID"]:
        raise RuntimeError("Wrong SUT instance: refusing to touch another environment")
    if metrics.get("settings", {}).get("database") != os.environ["MYSQL_DATABASE"]:
        raise RuntimeError("Backend and runner target different databases")
    if any(value != "-" for key, value in metrics.get("settings", {}).items() if key.endswith(".cron")):
        raise RuntimeError("Scheduled jobs must be disabled during measurement")
    if metrics.get("poolActive", 0) or metrics.get("poolWaitThreads", 0):
        raise RuntimeError("Database requests still active; refusing to prepare the next point")
    if any(lag().values()):
        raise RuntimeError("Kafka still has in-flight messages; preserving data")
    # Even republished DLQ entries require explicit review: lag=0 alone is insufficient.
    if int(sql("SELECT COUNT(*) FROM t_order_dlq_log;").strip()):
        raise RuntimeError("DLQ evidence exists; resolve it before starting another point")
    return metrics


def mysql_counters():
    names = "'Com_commit','Com_rollback','Com_insert','Com_update','Com_select','Innodb_row_lock_waits','Innodb_row_lock_time','Threads_running'"
    return {k: int(v) for k, v in (row.split("\t") for row in sql(f"SHOW GLOBAL STATUS WHERE Variable_name IN ({names});").splitlines())}


def mysql_settings():
    names = ','.join(f"'{name}'" for name in ('version', *MYSQL_MEMORY_SETTINGS))
    values = dict(row.split('\t', 1) for row in sql(f'SHOW GLOBAL VARIABLES WHERE Variable_name IN ({names});').splitlines())
    if values.get('version', '').split('-', 1)[0] != '8.0.46':
        raise RuntimeError(f"Expected pinned MySQL 8.0.46, got {values.get('version', 'missing')}")
    mismatches = [f"{name}={values.get(name, 'missing')} (expected {expected})"
                  for name, expected in MYSQL_MEMORY_SETTINGS.items() if values.get(name) != expected]
    if mismatches:
        raise RuntimeError('MySQL memory profile differs from the measured contract: ' + ', '.join(mismatches))
    return values


def runner_usage():
    status = Path("/proc/self/status").read_text().splitlines()
    return {"rssKiB": next(int(line.split()[1]) for line in status if line.startswith("VmRSS:")),
            "childPeakRssKiB": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss}


class Sampler:
    def __init__(self, directory, token):
        self.directory, self.token = directory, token
        self.stop = threading.Event()
        self.failure = None
        self.backend_failures = 0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        with (self.directory / "samples.jsonl").open("a") as file:
            sample_number = 0
            while not self.stop.is_set():
                sample = {"timeMs": int(time.time() * 1000)}
                for name, fn in (
                    ("backend", lambda: api("/admin/experiments/metrics", self.token)),
                    ("mysql", mysql_counters),
                    ("runner", runner_usage),
                    ("host", lambda: {"loadavg": Path("/proc/loadavg").read_text().strip(),
                                      "cpu": Path("/proc/stat").read_text().splitlines()[0],
                                      "diskstats": Path("/proc/diskstats").read_text()}),
                ):
                    try:
                        sample[name] = fn()
                    except Exception as error:
                        sample[name + "Error"] = str(error)
                if sample_number % 2 == 0:
                    try:
                        sample["kafkaLag"] = lag()
                    except Exception as error:
                        sample["kafkaLagError"] = str(error)
                self.backend_failures = self.backend_failures + 1 if "backendError" in sample else 0
                if self.backend_failures >= 3:
                    self.failure = "Backend observation failed three times consecutively"
                if sample.get("backend", {}).get("writeErrors"):
                    self.failure = "Backend commit journal failed"
                try:
                    check_disk(self.directory, check_host=False)
                except RuntimeError as error:
                    self.failure = str(error)
                file.write(json.dumps(sample, ensure_ascii=False) + "\n")
                file.flush()
                sample_number += 1
                self.stop.wait(2)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=60)
        if self.thread.is_alive():
            raise RuntimeError("Resource sampler failed to stop")


def delta(before, after):
    result = {}
    for key in ("syncAttempts", "asyncAttempts", "events", "writeErrors", "poolWaitCount", "poolWaitMs", "gcTimeMs", "processCpuTimeNs"):
        if key in before and key in after:
            result[key] = after[key] - before[key]
    result["sqlCalls"] = {k: value - before["sqlCalls"].get(k, 0) for k, value in after["sqlCalls"].items()}
    return result


class JournalReader:
    def __init__(self, offset, config, token, evidence):
        self.offset, self.activity, self.token = offset, config['activityId'], token
        self.evidence, self.directory = evidence, evidence.directory
        self.has_more = False

    def poll(self):
        # Fixed-size API pages go straight to disk. No list of historical commits.
        with (self.directory / 'commits.jsonl').open('a') as file:
            for _ in range(64):
                page = api(f'/admin/experiments/commits?activityId={self.activity}&after={self.offset}', self.token)
                if page['nextOffset'] < self.offset or page['hasMore'] and page['nextOffset'] == self.offset:
                    raise RuntimeError('Commit cursor was truncated or did not advance')
                self.offset, self.has_more = page['nextOffset'], page['hasMore']
                self.evidence.add_commits(page['events'])
                for event in page['events']: file.write(json.dumps(event) + '\n')
                if not self.has_more: break
        check_disk(self.directory, check_host=False)


def verify_k6_load(result, log_path, return_code):
    # With aggregation disabled, the warning is independent evidence of dropped
    # arrivals. An extra duration-boundary iteration can otherwise mask the loss
    # when only the planned and recorded request totals are compared.
    with Path(log_path).open() as log:
        result['vuExhausted'] = any('Insufficient VUs, reached ' in line for line in log)
    if result['vuExhausted']:
        result['loadErrors'].append('k6 exhausted its fixed VU pool (Insufficient VUs); arrivals were dropped')
    if return_code:
        result['loadErrors'].append(f'k6 exit {return_code}')
    result['valid'] &= not result['loadErrors']
    result['sloPassed'] &= result['valid']


def measure(config, private, directory, token, args):
    check_host_health()
    before_metrics = require_idle(token)
    write_json(directory / 'backend-before.json', before_metrics)
    write_json(directory / 'mysql-before.json', mysql_counters())
    evidence = Evidence(directory)
    try:
        evidence.load_snapshot('before')
        journal = JournalReader(before_metrics['journalOffset'], config, token, evidence)
        invocation = ['k6', 'run', '--no-summary', '--no-thresholds', '--log-format', 'raw', '--console-output', str(directory / 'requests.jsonl'),
                      str(ROOT / 'scripts/experiments/k6' / f"{config['plan']}.js")]
        child_env = {**os.environ, 'POINT_CONFIG': str(private / 'point.json'), 'USER_FILE': str(private / 'users.json'),
                     'RESULT_DIR': str(directory), 'API_BASE': f'http://{transport.host()}:18081/api'}
        clock_wall = time.time()*1000
        started = time.monotonic()
        interruption = None
        with Sampler(directory, token) as sampler, (directory / 'k6.out').open('w') as output:
            process = subprocess.Popen(invocation, stdout=output, stderr=subprocess.STDOUT, cwd=ROOT, env=child_env)
            try:
                deadline = time.monotonic() + config['seconds'] + config['baseSeconds'] + config['requestTimeout'] + 120
                while process.poll() is None:
                    try:
                        check_host_health()
                    except RuntimeError as error:
                        interruption = str(error)
                        break
                    if sampler.failure: raise RuntimeError(sampler.failure)
                    if time.monotonic() > deadline: raise RuntimeError('Load generation exceeded its time budget')
                    time.sleep(.5)
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    try: process.wait(timeout=config['requestTimeout'] + 5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            try:
                check_host_health()
            except RuntimeError as error:
                interruption = interruption or str(error)
            if sampler.failure and not interruption: raise RuntimeError(sampler.failure)
            evidence.load_requests()
            injection_end = evidence.scalar('SELECT MIN(e) FROM starts') or int(time.time()*1000)
            deadline_ms = injection_end + args.drain_seconds * 1000
            drain_deadline = time.monotonic() + args.drain_seconds
            ambiguous = evidence.scalar("SELECT COUNT(*) FROM finishes WHERE outcome IN ('timeout','network_error','internal_error')")
            while True:
                try:
                    check_host_health()
                except RuntimeError as error:
                    interruption = interruption or str(error)
                if sampler.failure and not interruption: raise RuntimeError(sampler.failure)
                journal.poll()
                metrics = api('/admin/experiments/metrics', token)
                if not journal.has_more and evidence.pending_accepted() == 0 and not any(lag().values()) and not metrics.get('poolActive', 0) and (not ambiguous or time.time()*1000 >= deadline_ms): break
                if time.time()*1000 >= deadline_ms: break
                if time.monotonic() >= drain_deadline: break
                time.sleep(.5)
            cutoff = min(int(time.time()*1000), deadline_ms)
            snapshot(config, directory, 'after', check_host=False)
            evidence.load_snapshot('after')
            # Collect all already-written evidence, even if processing takes past cutoff.
            # Classification still uses commit timestamps <= cutoff.
            while True:
                journal.poll()
                if not journal.has_more: break
            clock_start = time.time()*1000
            after_metrics = api('/admin/experiments/metrics', token)
            clock_end = time.time()*1000
            after_metrics['runnerClock'] = {'offsetEstimateMs': after_metrics['timeMs']-(clock_start+clock_end)/2, 'uncertaintyMs': (clock_end-clock_start)/2}
            write_json(directory / 'backend-after.json', after_metrics)
            write_json(directory / 'mysql-after.json', mysql_counters())
            final_lag = lag()
            write_json(directory / 'kafka-final.json', final_lag)
        drift = time.time()*1000-clock_wall-(time.monotonic()-started)*1000
        result = evidence.analyze(config, {}, cutoff, policy(args))
        verify_k6_load(result, directory / 'k6.out', process.returncode)
        result['clockShiftMs'] = round(drift,3)
        result['ambiguousResponseCount'] = ambiguous
        if interruption:
            result['correctnessErrors'].append('Host safety interrupted this point: ' + interruption)
        write_json(directory / 'load.json', {'planned':config['scheduled'], 'started':result['started'],
                   'missing':max(0,config['scheduled']-result['started']), 'vus':config['vus'],
                   'vuExhausted':result['vuExhausted'], 'metricsAggregation':'disabled; disk evidence and k6 warnings are authoritative'})
        result.update(repeat=config['repeat'], seconds=config['seconds'], baseRate=config['baseRate'], baseSeconds=config['baseSeconds'], k6ExitCode=process.returncode,
                      wallSeconds=round(time.monotonic()-started, 3), backendDelta=delta(before_metrics, after_metrics))
        if abs(drift)>50 or abs(after_metrics['runnerClock']['offsetEstimateMs'])>50+after_metrics['runnerClock']['uncertaintyMs']:
            result['correctnessErrors'].append('Clock shifted during the point or target/load clocks diverged')
        unproven = evidence.scalar("SELECT COUNT(*) FROM joined WHERE outcome IN ('timeout','network_error','internal_error') AND ct IS NULL")
        if unproven and config['path']=='C':
            result['correctnessErrors'].append(f'Uncertain asynchronous submission outcome: {unproven}; preserve this point')
        if (result['backendDelta'].get('writeErrors', 0) or after_metrics['events'] < before_metrics['events']
                or before_metrics.get('processStartedAtMs') != after_metrics.get('processStartedAtMs')):
            result['correctnessErrors'].append('Backend journal failed or backend restarted')
        if before_metrics['settings'] != after_metrics['settings'] or before_metrics['localCacheEnabled'] != after_metrics['localCacheEnabled']:
            result['correctnessErrors'].append('Backend settings changed')
        if any(final_lag.values()): result['correctnessErrors'].append('Kafka did not drain by cutoff')
        if int(sql('SELECT COUNT(*) FROM t_order_dlq_log;').strip()): result['correctnessErrors'].append('DLQ evidence exists')
        result['valid'] &= not result['correctnessErrors'] and not result['loadErrors']
        result['sloPassed'] &= result['valid']
        save_result(directory, result)
        return result
    finally:
        evidence.close()


def manifest(args, target, load_host):
    source = json.loads((RUNTIME / 'source.json').read_text())
    parameters = {k: str(v) if isinstance(v, Path) else v for k,v in vars(args).items()}
    return {'schemaVersion': VERSION, 'plan': args.plan, 'stage': args.stage,
            'scope': 'validation' if args.validation_only else 'performance', 'status': 'running', 'deployment': args.deployment,
            'startedAtMs': int(time.time()*1000), 'parameters': parameters, 'policy': policy(args),
            'target': target, 'loadHost': load_host, 'source': source,
            'database': os.environ['MYSQL_DATABASE'], 'platform': platform.platform(), 'points': [],
            'hostWatch': str(Path(os.environ['BENCH_HOST_STATUS']).parent.relative_to(RUNTIME)),
            'limitations': ['GitHub-hosted vCPUs share storage, caches, kernel and host scheduling; quotas do not emulate dedicated physical cores'] + ['Single-broker results have no availability/durability claim',
                           'After-commit observation includes callback delay and residual clock error',
                           'Resource quotas are per service; idle quota is not borrowed by other services',
                           'SUT Redis memory bounds fixture working sets; fixture failures are not capacity results',
                           'After-commit journal flushes, mapper/AOP counters and sampling add measurement overhead',
                           'Global MySQL counters include observer queries; results describe this instrumented deployment']}


def main(argv=None):
    args = parse_args(argv)
    points = budgeted_matrix(args)
    if args.dry_run:
        print(json.dumps({'scope': 'validation' if args.validation_only else 'performance',
                          'deployment': args.deployment,
                          'policy': policy(args), 'initialMeasurements': points,
                          'refinementRounds': args.refine_steps if args.stage == 'capacity' else 0}, ensure_ascii=False, indent=2))
        return 0
    if os.environ.get('BENCH_CONTAINER') != '1' or os.environ.get('GITHUB_ACTIONS') != 'true': raise RuntimeError('Use the resource-limited load-runner container')
    target = json.loads((RUNTIME / 'target.json').read_text())
    load_host = json.loads((RUNTIME / 'load-host.json').read_text())
    if target['instanceId'] != os.environ['SUT_INSTANCE_ID'] or target['machineMemoryMiB'] != 2048 or target['cpuLimit'] != 2:
        raise RuntimeError('Target resource verification is missing or belongs to another SUT')
    validate_deployment(args, target, load_host)
    if not os.environ.get('BENCH_HOST_STATUS'):
        raise RuntimeError('Missing Actions watchdog; launch through the performance workflow')
    check_host_health()
    with (RUNTIME / 'experiment.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        health = api('/health')
        if any(health.get(key) != 'UP' for key in ('status', 'mysql', 'redis', 'kafka')):
            raise RuntimeError('All SUT dependencies must be healthy')
        name = os.environ.get('BENCH_RUN_ID') or time.strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3)
        parent = REPORTS
        directory = parent / args.plan / name
        directory.mkdir(parents=True, exist_ok=True)
        private = RUNTIME / name
        private.mkdir(mode=0o700)
        meta, results = manifest(args, target, load_host), []
        write_json(directory / 'manifest.json', meta)
        print(f'报告目录：{directory}', flush=True)
        try:
            meta['mysqlSettings'] = mysql_settings()
            write_json(directory / 'mysql-settings.json', meta['mysqlSettings'])
            write_json(directory / 'manifest.json', meta)
            meta['adminPrefix'] = 'x' + secrets.token_hex(6)
            write_json(directory / 'manifest.json', meta)
            admin_pool = create_users(meta['adminPrefix'], 1, private)
            write_json(private / 'admin.json', admin_pool)
            admin = credential(admin_pool, 0)
            metrics = require_idle(admin['token'])
            meta['contract'] = context_contract(target, {**metrics['settings'], 'localCacheEnabled': metrics['localCacheEnabled']},
                                                meta['source']['sourceSha256'], load_host, args, meta['mysqlSettings'])
            write_json(directory / 'backend-settings.json', metrics)
            if args.baseline_report:
                baseline = read_baseline(args.baseline_report)
                validate_baseline(baseline, args, meta['contract'])
                write_json(directory / 'baseline.json', baseline)
            product_id = int(sql(f"INSERT INTO t_product(title,price,stock,status) VALUES ('experiment-{name}',{PRICE},0,1); SELECT LAST_INSERT_ID();").strip())
            meta['productId'] = product_id
            refinements = 0
            while True:
                for point in points:
                    check_host_health()
                    if args.stage == 'capacity':
                        observations = capacities(args, results)[point['path']]['observations']
                        failures = [item['rate'] for item in observations if item['state']=='fail']
                        if failures and point['rate'] > min(failures):
                            continue
                    check_disk(directory)
                    point_name = f"{len(meta['points'])+1:03d}-r{point['repeat']}-{point['workload']}-{point['rate']}-{point['path']}"
                    point_dir = directory / point_name
                    point_dir.mkdir()
                    point['userPrefix'] = 'x' + secrets.token_hex(6)
                    meta['points'].append({'directory': point_name, 'userPrefix': point['userPrefix']})
                    write_json(directory / 'manifest.json', meta)
                    current = require_idle(admin['token'])
                    if (current['settings'] != metrics['settings'] or current['localCacheEnabled'] != metrics['localCacheEnabled']
                            or current.get('processStartedAtMs') != metrics.get('processStartedAtMs')):
                        raise RuntimeError('Backend settings/process changed between points')
                    print(f"测量 {point_name}: {point['scheduled']} 请求", flush=True)
                    prepare(point, product_id, private / point_name, point_dir, admin['token'], api, require_idle)
                    meta['points'][-1]['activityId'] = point['activityId']
                    write_json(directory / 'manifest.json', meta)
                    value = measure(point, private / point_name, point_dir, admin['token'], args)
                    value.pop("timeline", None)
                    results.append((point_name, value))
                    report(directory, meta, results, args)
                    check_host_health()
                    if value['correctnessErrors'] or value['unresolvedCount']:
                        raise RuntimeError('Data/evidence invalid or accepted orders unresolved; preserve this run')
                    if value['loadErrors']:
                        raise RuntimeError(
                            f"Load generation invalid: {'; '.join(value['loadErrors'])} "
                            f"(planned={value['scheduled']}, started={value['started']}, "
                            f"missing={value['missingRequests']}, VUs={point['vus']}, "
                            f"lagP99Ms={value['generatorLagMs']['p99']}). "
                            'See point/result.json and k6.out; this is not a service capacity failure')
                    # Keep DB rows and all evidence; retire only this closed point's hot Redis data.
                    release_redis(private / point_name, point)
                    meta['points'][-1]['redisReleased'] = True
                    write_json(directory / 'manifest.json', meta)
                if args.stage != 'capacity' or refinements >= args.refine_steps: break
                rates = refinement_rates(args, results)
                if not rates: break
                points = list(matrix(args, rates))
                refinements += 1
            check_host_health()
            meta['status'] = 'completed'
        except BaseException as error:
            meta.update(status='failed', error=str(error))
            raise
        finally:
            meta['finishedAtMs'] = int(time.time()*1000)
            try:
                check_host_health()
                watch = Path(os.environ['BENCH_HOST_STATUS']).parent
                (directory/'host-watch').mkdir(exist_ok=True)
                for name in ('status.json', 'samples.jsonl'):
                    shutil.copyfile(watch/name, directory/'host-watch'/name)
                if json.loads((watch/'status.json').read_text()).get('failure'):
                    meta.update(status='failed', error='Host safety interrupted the run; see host-watch/status.json')
            except (OSError, ValueError, RuntimeError) as error:
                meta.update(status='failed', error=f'Host safety/evidence check failed: {error}')
            report(directory, meta, results, args)
            write_json(directory / 'manifest.json', meta)
            print(f'现场与证据已保留：{directory}', flush=True)
        if meta['status'] != 'completed':
            raise RuntimeError(meta.get('error', 'Run was interrupted'))
        return 0


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'测量中止：{error}', file=sys.stderr)
        sys.exit(1)
