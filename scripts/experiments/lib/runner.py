#!/usr/bin/env python3
"""Performance-only A/B and B/C execution in the bounded load-runner container."""
import copy
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
import observation
from readiness import assess
from analysis import Evidence, save_result, write_json
from fixtures import POINTS, PRICE, create_users, credential, prepare, snapshot, check_disk, release_redis
from protocol import (VERSION, parse_args, matrix, budgeted_matrix, policy, context_contract,
                      report, validate_deployment, comparisons)
from host import check_host_health, container_resources

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
    if path == '/admin/experiments/metrics':
        return observation.metrics()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"http://{transport.host()}:18080/api{path}",
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
    metrics = observation.fresh_metrics()
    finished = time.time() * 1000
    metrics["runnerClock"] = observation.clock_evidence(metrics, started, finished)
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
            "containerResources": container_resources({'State': {'Pid': os.getpid()}}),
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
                    self.failure = "Backend observation failed three times consecutively: "+sample['backendError']
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
        self.partial_tail = False

    def poll(self):
        # Fixed-size API pages go straight to disk. No list of historical commits.
        with (self.directory / 'commits.jsonl').open('a') as file:
            for _ in range(64):
                page = observation.commits(self.offset, self.activity)
                self.partial_tail = page.get('partialTail', False)
                if page['nextOffset'] < self.offset:
                    raise RuntimeError('Commit cursor was truncated or did not advance')
                if page['nextOffset'] == self.offset and page['hasMore']:
                    self.has_more = True
                    break  # A concurrently appended final line may still be incomplete.
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


def safe_load_failure(value):
    allowed = {'Offered load not achieved', 'Injection timing does not follow the arrival schedule',
               'k6 exhausted its fixed VU pool (Insufficient VUs); arrivals were dropped',
               'Requests started after the injection window', 'Burst request cohort does not match the scheduled phase'}
    return (bool(value.get('loadErrors')) and not value.get('correctnessErrors')
            and not value.get('unresolvedCount') and set(value['loadErrors']) <= allowed)


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
                     'RESULT_DIR': str(directory), 'API_BASE': f'http://{transport.host()}:18080/api'}
        clock_wall = time.time()*1000
        started = time.monotonic()
        interruption = None
        with Sampler(directory, token) as sampler, (directory / 'k6.out').open('w') as output:
            process = subprocess.Popen(invocation, stdout=output, stderr=subprocess.STDOUT, cwd=ROOT, env=child_env)
            try:
                deadline = time.monotonic() + config['seconds'] + config['baseSeconds'] + config.get('recoverySeconds', 0) + config['requestTimeout'] + 120
                while process.poll() is None:
                    try:
                        check_host_health()
                    except RuntimeError as error:
                        interruption = str(error)
                        break
                    if sampler.failure:
                        interruption = sampler.failure
                        break
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
            interruption = interruption or sampler.failure
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
                interruption = interruption or sampler.failure
                journal.poll()
                metrics = observation.fresh_metrics()
                if not journal.has_more and not journal.partial_tail and evidence.pending_accepted() == 0 and not any(lag().values()) and not metrics.get('poolActive', 0) and (not ambiguous or time.time()*1000 >= deadline_ms): break
                if time.time()*1000 >= deadline_ms: break
                if time.monotonic() >= drain_deadline: break
                time.sleep(.5)
            cutoff = min(int(time.time()*1000), deadline_ms)
            snapshot(config, directory, 'after', check_host=False)
            evidence.load_snapshot('after')
            clock_start = time.time()*1000
            after_metrics = observation.fresh_metrics()
            clock_end = time.time()*1000
            # Collect all already-written evidence, even if processing takes past cutoff.
            # Classification still uses commit timestamps <= cutoff.
            while True:
                journal.poll()
                if not journal.has_more: break
            if journal.partial_tail:
                raise RuntimeError('Commit journal contains an incomplete final record; preserving invalid evidence')
            after_metrics['runnerClock'] = observation.clock_evidence(after_metrics, clock_start, clock_end)
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
            result['correctnessErrors'].append('Measurement interrupted this point: ' + interruption)
        write_json(directory / 'load.json', {'planned':config['scheduled'], 'started':result['started'],
                   'missing':max(0,config['scheduled']-result['started']), 'vus':config['vus'],
                   'vuExhausted':result['vuExhausted'], 'metricsAggregation':'disabled; disk evidence and k6 warnings are authoritative'})
        result.update(phase=config.get('phase', 'confirmation'), scope='validation' if args.validation_only else 'calibration' if args.run_type == 'calibration' else 'performance',
                      repeat=config['repeat'], seconds=config['seconds'], baseRate=config['baseRate'], baseSeconds=config['baseSeconds'], recoveryRate=config.get('recoveryRate', 0), recoverySeconds=config.get('recoverySeconds', 0), k6ExitCode=process.returncode,
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
    except BaseException as error:
        write_json(directory/'measurement-error.json', {'type': type(error).__name__, 'error': str(error),
                   'observationChannel': 'read-only-evidence-volume', 'timeMs': int(time.time()*1000)})
        raise
    finally:
        evidence.close()


def manifest(args, target, load_host):
    source = json.loads((RUNTIME / 'source.json').read_text())
    parameters = {k: str(v) if isinstance(v, Path) else v for k,v in vars(args).items()}
    return {'schemaVersion': VERSION, 'plan': args.plan, 'stage': 'experiment',
            'scope': 'validation' if args.validation_only else 'calibration' if args.run_type == 'calibration' else 'performance', 'status': 'running', 'deployment': args.deployment,
            'startedAtMs': int(time.time()*1000), 'parameters': parameters, 'policy': policy(args),
            'target': target, 'loadHost': load_host, 'source': source,
            'database': os.environ['MYSQL_DATABASE'], 'platform': platform.platform(), 'points': [],
            'hostWatch': str(Path(os.environ['BENCH_HOST_STATUS']).parent.relative_to(RUNTIME)),
            'observationChannel': 'read-only-evidence-volume',
            'entrypoint': 'nginx -> backend; frontend running without browser load',
            'runtimeProfile': 'prod -> 2c2g (shared deployment configuration)',
            'experimentDifferences': ['Commit/SQL observation is enabled within the backend resource quota',
                                     'Compensation, timeout, reconciliation and scheduled warmup jobs are disabled',
                                     'Frontend is running without browser workload'],
            'limitations': ['GitHub-hosted vCPUs share storage, caches, kernel and host scheduling; quotas do not emulate dedicated physical cores'] + ['Single-broker results have no availability/durability claim',
                           'After-commit observation includes callback delay and residual clock error',
                           'Resource quotas are per service; idle quota is not borrowed by other services',
                           'SUT Redis memory bounds fixture working sets; fixture failures are not service performance results',
                           'After-commit journal flushes, mapper/AOP counters and sampling add measurement overhead',
                           'Global MySQL counters include observer queries; results describe this instrumented deployment']}


class WarmupInconclusive(RuntimeError):
    pass


def warm_runtime(args, product_id, private, directory, token):
    """Both paths receive equal 60–90s warmup; only the last 30s determine stability."""
    selected = copy.copy(args)
    selected.validation_only = True
    rate = min(args.rate if args.plan == 'redis-filtering' else args.burst_rate, 200)
    paths = ('A', 'B') if args.plan == 'redis-filtering' else ('B', 'C')
    summary = {'scope':'validation','status':'running','points':[]}
    try:
        for block in range(1,4):
            stable = []
            for path in paths if block%2 else paths[::-1]:
                check_host_health()
                if time.monotonic()+30+args.request_timeout+selected.drain_seconds+30 >= float(os.environ.get('BENCH_STAGE_DEADLINE','inf')):
                    require_idle(token)
                    raise WarmupInconclusive('Insufficient budget for warmup; no formal comparison')
                name = f'{path}-{rate}-block{block}'
                output, secret = directory/'warmup'/name, private/'warmup'/name
                output.mkdir(parents=True)
                point = {'plan':args.plan,'path':path,'workload':'mixed' if args.plan=='redis-filtering' else 'steady',
                         'repeat':block,'rate':rate,'seconds':30,'baseSeconds':0,'baseRate':1,
                         'vus':args.vus,'requestTimeout':args.request_timeout,'points':POINTS,
                         'scheduled':rate*30,'userPrefix':'x'+secrets.token_hex(6),'phase':'warmup'}
                print(f'Runtime warmup {name}: {rate} RPS, 30s (validation; excluded from comparisons)',flush=True)
                prepare(point,product_id,secret,output,token,api,require_idle)
                value = measure(point,secret,output,token,selected)
                readiness = assess(value,output/'samples.jsonl')
                summary['points'].append({'path':path,'block':block,**readiness})
                write_json(directory/'warmup.json',summary)
                if value['correctnessErrors'] or value['unresolvedCount']:
                    raise RuntimeError('Warmup data invalid or unresolved; preserve evidence')
                if value['loadErrors'] and not safe_load_failure(value):
                    raise RuntimeError('Warmup evidence invalid: '+'; '.join(value['loadErrors']))
                require_idle(token)
                release_redis(secret,point)
                stable.append(readiness['stable'])
            if block >= 2 and all(stable):
                summary['status']='completed'
                return
        raise WarmupInconclusive('Warmup did not stabilize within equal 90s per path; see warmup.json')
    except WarmupInconclusive as error:
        summary.update(status='inconclusive',error=str(error))
        raise
    except BaseException as error:
        summary.update(status='failed',error=str(error))
        raise
    finally:
        write_json(directory/'warmup.json',summary)


def main(argv=None):
    args = parse_args(argv)
    points = budgeted_matrix(args)
    if args.dry_run:
        print(json.dumps({'scope': 'validation' if args.validation_only else 'calibration' if args.run_type == 'calibration' else 'performance', 'policy': policy(args), 'measurements': points}, ensure_ascii=False, indent=2))
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
            product_id = int(sql(f"INSERT INTO t_product(title,price,stock,status) VALUES ('experiment-{name}',{PRICE},0,1); SELECT LAST_INSERT_ID();").strip())
            meta['productId'] = product_id
            warm_runtime(args, product_id, private, directory, admin['token'])

            def checkpoint():
                write_json(directory/'manifest.json', meta)
                report(directory, meta, results, args)

            def execute_point(config, phase):
                point = dict(config)
                check_host_health()
                check_disk(directory)
                entries = meta['points']
                point_name = f"{len(entries)+1:03d}-r{point['repeat']}-{point['workload']}-{point['rate']}-{point['path']}"
                relative = Path(point_name)
                point_dir = directory/relative
                point_dir.mkdir(parents=True)
                point['userPrefix'] = 'x'+secrets.token_hex(6)
                entries.append({'directory': str(relative), 'userPrefix': point['userPrefix'],
                                'phase': phase, 'scope': meta['scope']})
                checkpoint()
                current = require_idle(admin['token'])
                if (current['settings'] != metrics['settings'] or current['localCacheEnabled'] != metrics['localCacheEnabled']
                        or current.get('processStartedAtMs') != metrics.get('processStartedAtMs')):
                    raise RuntimeError('Backend settings/process changed between points')
                print(f"测量 {point_name}: {point['scheduled']} 请求", flush=True)
                prepare(point, product_id, private/relative, point_dir, admin['token'], api, require_idle)
                entries[-1]['activityId'] = point['activityId']
                checkpoint()
                value = measure(point, private/relative, point_dir, admin['token'], args)
                value.pop('timeline', None)
                value['phase'] = phase
                value['scope'] = meta['scope']
                value['evidenceDirectory'] = str(relative)
                results.append((point_name, value))
                write_json(point_dir/'result.json', value)
                print(f"结果 {point_name}: phase={phase}, valid={value['valid']}, SLO={value['sloPassed']}, "
                      f"started={value['started']}/{value['scheduled']}, "
                      f"peakHttpInFlight={value.get('peakHttpInFlight')}, VUs={point['vus']}, "
                      f"responseP99Ms={value['classes'].get('eligible', {}).get('responseMs', {}).get('p99')}", flush=True)
                timing = value.get('injectionTiming', {})
                for category in ('inconsistentEnds', 'earlyStarts', 'lateStarts', 'invalidSchedule', 'aheadOfSchedule'):
                    detail = timing.get(category, {})
                    if detail.get('count'):
                        print(f"注入时间异常 {category}: count={detail['count']}, maxDeviationMs={detail['maxDeviationMs']}, "
                              f"examples={json.dumps(detail.get('examples', []), ensure_ascii=False)}", flush=True)
                checkpoint()
                check_host_health()
                if value['correctnessErrors'] or value['unresolvedCount']:
                    raise RuntimeError('Data/evidence invalid or accepted orders unresolved; preserve this run')
                limited = safe_load_failure(value)
                if value['loadErrors'] and not limited:
                    raise RuntimeError(
                        f"Load generation invalid: {'; '.join(value['loadErrors'])} "
                        f"(planned={value['scheduled']}, started={value['started']}, "
                        f"missing={value['missingRequests']}, VUs={point['vus']}, "
                        f"lagP99Ms={value['generatorLagMs']['p99']}). "
                        'See point/result.json and k6.out; no service performance conclusion can be drawn')
                if not value['valid'] and not limited:
                    raise RuntimeError('Measurement is invalid; preserve this point')
                if limited:
                    # Recovery requires the same full conservation/commit checks plus an idle SUT.
                    # Disk evidence is retained; only verified closed-point hot data is retired.
                    require_idle(admin['token'])
                    value['recoveryVerified'] = True
                    write_json(point_dir/'result.json', value)
                    print('发压受限：'+ '; '.join(value['loadErrors']) + '；该点不参与收益比较；证据已保留，核对空闲后继续固定对照。', flush=True)
                release_redis(private/relative, point)
                entries[-1]['redisReleased'] = True
                checkpoint()
                return value

            for point in points:
                execute_point(point, args.run_type)
            check_host_health()
            qualified = args.validation_only or all(c['qualified'] for c in comparisons(args, results))
            meta['status'] = 'completed' if len(results) == len(points) and all(r['valid'] for _, r in results) and qualified else 'inconclusive'
        except WarmupInconclusive as error:
            meta.update(status='inconclusive',error=str(error))
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
        if meta['status'] == 'failed':
            raise RuntimeError(meta.get('error', 'Run was interrupted'))
        return 0


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'测量中止：{error}', file=sys.stderr)
        sys.exit(1)
