"""Independent, fixed-load paired experiments. No capacity or historical baseline search."""
import argparse
import hashlib
import json
from pathlib import Path
from analysis import percentiles, write_json
from host import validate_affinity

VERSION = 10
# Cover requests waiting through the full HTTP timeout, plus scheduling headroom.
MAX_RATE = 1000
MAX_REQUEST_TIMEOUT = 10
DEFAULT_VUS = MAX_RATE * MAX_REQUEST_TIMEOUT + 400
# Fixed experimental conditions; never adapted to observed results.
REDIS_RATE = 600
KAFKA_BURST_RATE = 1000
PLANS = ('redis-filtering', 'kafka-burst')


def spread(values):
    values = list(values)
    return {**percentiles(values), 'min': min(values, default=None), 'max': max(values, default=None)}


def positive(value):
    value = int(value)
    if value <= 0: raise argparse.ArgumentTypeError('must be positive')
    return value


def policy(args):
    return {'responseP99Ms': args.response_p99_ms if args.plan=='redis-filtering' else None, 'completionP99Ms': args.completion_p99_ms if args.plan=='redis-filtering' else None,
            'maxErrorRate': args.max_error_rate, 'minSamples': args.min_samples,
            'generatorLagP99Ms': 100, 'tailToleranceMs': 20, 'tailFraction': .0001,
            'backlogGrowthSeconds': .25, 'backlogSlopeFraction': .005}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('plan', choices=PLANS)
    for name, default in (('rate', REDIS_RATE), ('seconds', 60), ('repeats', 3), ('vus', None),
                          ('base-rate', 100), ('base-seconds', 20), ('burst-rate', KAFKA_BURST_RATE), ('burst-seconds', 10),
                          ('recovery-rate', 100), ('recovery-seconds', 30), ('request-timeout', 10),
                          ('drain-seconds', 120), ('response-p99-ms', 500), ('completion-p99-ms', 5000),
                          ('min-samples', 1000)):
        parser.add_argument('--'+name, type=positive, default=default)
    parser.add_argument('--max-error-rate', type=float, default=.001)
    parser.add_argument('--validation-only', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if args.vus is None:
        args.vus = (KAFKA_BURST_RATE if args.plan == 'kafka-burst' else REDIS_RATE) * MAX_REQUEST_TIMEOUT + 400
    if args.plan=='kafka-burst':
        args.response_p99_ms = args.completion_p99_ms = None
    args.deployment = 'github-actions'
    if not 0 <= args.max_error_rate <= 1: parser.error('--max-error-rate must be in [0,1]')
    if max(args.rate, args.burst_rate, args.base_rate, args.recovery_rate) > MAX_RATE:
        parser.error('Rates above the 1000 RPS generator verification envelope require new generator verification')
    if args.vus > DEFAULT_VUS: parser.error('At most 10400 VUs in the verified load-runner budget')
    if args.request_timeout > MAX_REQUEST_TIMEOUT: parser.error('Request timeout exceeds the verified 10 second occupancy budget')
    if args.repeats > 10: parser.error('At most 10 repetitions')
    if max(args.seconds, args.base_seconds, args.burst_seconds, args.recovery_seconds,
           args.drain_seconds, args.request_timeout) > 600: parser.error('Durations limited to 600 seconds')
    if not args.validation_only:
        if args.repeats < 3 or args.min_samples < 1000: parser.error('Formal comparisons require >=3 pairs and >=1000 eligible samples per path')
        if args.plan == 'redis-filtering' and args.seconds < 60: parser.error('Redis measurement requires >=60 seconds')
        if args.plan == 'kafka-burst' and min(args.base_seconds, args.burst_seconds, args.recovery_seconds) < 5:
            parser.error('Each burst phase requires >=5 seconds')
    return args


def matrix(args):
    burst = args.plan == 'kafka-burst'
    paths = ('B', 'C') if burst else ('A', 'B')
    for repeat in range(1, args.repeats+1):
        for path in paths if repeat % 2 else paths[::-1]:
            rate, seconds = (args.burst_rate, args.burst_seconds) if burst else (args.rate, args.seconds)
            base, recovery = (args.base_seconds, args.recovery_seconds) if burst else (0, 0)
            scheduled = rate*seconds + args.base_rate*base + args.recovery_rate*recovery
            if scheduled > 2_000_000: raise RuntimeError('Point exceeds bounded fixture budget')
            yield {'plan': args.plan, 'repeat': repeat, 'workload': 'burst' if burst else 'mixed',
                   'path': path, 'rate': rate, 'seconds': seconds, 'baseRate': args.base_rate, 'baseSeconds': base,
                   'recoveryRate': args.recovery_rate, 'recoverySeconds': recovery, 'vus': args.vus,
                   'requestTimeout': args.request_timeout, 'points': 10_000_000, 'scheduled': scheduled}


def warmup_matrix(args):
    """Fixed equal warmup: 30s at 100 RPS, then two 30s blocks at 200 RPS."""
    paths = ('A','B') if args.plan == 'redis-filtering' else ('B','C')
    ceiling = args.rate if args.plan == 'redis-filtering' else args.burst_rate
    for block, nominal_rate in enumerate((100,200,200),1):
        for path in paths if block%2 else paths[::-1]:
            rate = min(ceiling,nominal_rate)
            yield {'plan':args.plan,'path':path,'workload':'mixed' if args.plan=='redis-filtering' else 'steady',
                   'repeat':block,'rate':rate,'seconds':30,'baseSeconds':0,'baseRate':1,
                   'vus':args.vus,'requestTimeout':args.request_timeout,'points':10_000_000,
                   'scheduled':rate*30,'phase':'warmup'}


def budgeted_matrix(args):
    return list(matrix(args))


def validate_deployment(args, target, load_host):
    if not target.get('machineId') or target['machineId'] != load_host.get('machineId'):
        raise RuntimeError('SUT and load must use the same GitHub Actions runner')
    for value in (target, load_host):
        if not value.get('environment') or not value.get('actions', {}).get('runId'):
            raise RuntimeError('Missing GitHub Actions environment fingerprints')
    if target['actions'] != load_host['actions']:
        raise RuntimeError('SUT and load belong to different Actions jobs')
    sets = {service.get('cpuset', '') for service in target['services']}
    if len(sets) != 1 or not next(iter(sets)):
        raise RuntimeError('Bind all SUT services to the same two vCPUs')
    validate_affinity(next(iter(sets)), load_host.get('cpuset', ''),
                      load_host['environment'].get('cpuTopology', []))


def context_contract(target, settings, generator_hash=None, load_host=None, args=None, database_settings=None):
    fields = ('service', 'imageId', 'cpuLimit', 'cpuModel', 'memoryLimitMiB', 'memorySwapMiB', 'cpuset', 'pidsLimit', 'restartPolicy')
    context = {'instanceId': target['instanceId'], 'services': sorted([{k: s.get(k) for k in fields} for s in target['services']], key=lambda s: s['service']),
               'settings': settings, 'protocol': VERSION, 'generatorSource': generator_hash,
               'targetHost': {k: target.get(k) for k in ('machineId', 'environment', 'actions')}, 'loadHost': load_host}
    if args is not None:
        context['deployment'] = args.deployment
        context['generator'] = {'vus': args.vus, 'requestTimeout': args.request_timeout,
                                'drainSeconds': args.drain_seconds, 'warmup': list(warmup_matrix(args)),
                                'experiment': args.plan, 'matrix': list(matrix(args))}
    if database_settings is not None:
        context['databaseSettings'] = database_settings
    return hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()


def comparisons(args, results):
    if args.validation_only: return []
    paths = ('A', 'B') if args.plan == 'redis-filtering' else ('B', 'C')
    indexed = {(r['path'], r['repeat']): r for _, r in results}
    pairs = [(indexed.get((paths[0], i)), indexed.get((paths[1], i))) for i in range(1, args.repeats+1)]
    valid = [(a,b) for a,b in pairs if a and b and a['valid'] and b['valid']]
    enough = len(valid) == args.repeats and all(sum(r['classes'].get('eligible', {}).get('requests', 0)
                      for pair in valid for r in [pair[n]]) >= args.min_samples for n in (0,1))
    item = {'validPairs': len(valid), 'expectedPairs': args.repeats, 'qualified': enough,
            'metrics': {}, 'reason': None if enough else 'Incomplete valid pairs or insufficient eligible samples'}
    if enough:
        # Paired differences can be negative. A measured SLO failure is still evidence.
        metrics = item['metrics']
        def difference(name, fn):
            values = [(fn(a), fn(b)) for a,b in valid]
            if all(a is not None and b is not None for a,b in values):
                metrics[name] = {'before': spread(a for a,b in values), 'after': spread(b for a,b in values),
                                 'afterMinusBefore': spread(b-a for a,b in values),
                                 'pairedReductionPct': spread((1-b/a)*100 for a,b in values) if all(a > 0 for a,b in values) else None}
        if args.plan == 'redis-filtering':
            difference('eligibleResponseP95Ms', lambda r: r['classes']['eligible']['responseMs'].get('p95'))
            difference('eligibleSuccessfulResponseP99Ms', lambda r: r['classes']['eligible'].get('successfulResponseMs',{}).get('p99'))
            difference('eligibleOnTimeSuccessPct', lambda r: r.get('eligibleOnTimeSuccessPct'))
            difference('eligibleCompletionPct', lambda r: r.get('completionCoverage')*100 if r.get('completionCoverage') is not None else None)
            difference('eligibleResponseP99Ms', lambda r: r['classes']['eligible']['responseMs']['p99'])
            difference('completionP99Ms', lambda r: r['completionMs']['p99'])
            difference('completed', lambda r: r['completed'])
            difference('eligibleErrorRate', lambda r: r['classes']['eligible']['errorRate'])
            difference('duplicateResponseP99Ms', lambda r: r['classes'].get('duplicate', {}).get('responseMs', {}).get('p99'))
            difference('businessSqlPer1000Requests', lambda r: sum(r['backendDelta']['sqlCalls'].values())*1000/r['started']
                       if 'sqlCalls' in r.get('backendDelta', {}) and r['started'] else None)
            difference('syncAttemptsPer1000Requests', lambda r: r['backendDelta']['syncAttempts']*1000/r['started']
                       if 'syncAttempts' in r.get('backendDelta', {}) and r['started'] else None)
        else:
            for key, field in (('completionP50Ms','p50'),('completionP95Ms','p95'),('completionP99Ms','p99')):
                difference('burst.'+key, lambda r, f=field: (r.get('burst') or {}).get('completionMs',{}).get(f))
            for field in ('recoveryMs','completionPct','completed'):
                difference('burst.'+field, lambda r, f=field: (r.get('burst') or {}).get(f))
            difference('peakOutstanding', lambda r: r.get('peakOutstanding'))
            item['pressureQualified'] = all(r.get('pressure',{}).get('valid') for pair in valid for r in pair)
            if item['pressureQualified']:
                for field in ('peakStartRps','peakActive','poolWaitMs','sampledPoolWaitThreadsPeak'):
                    difference('pressure.'+field, lambda r, f=field: r['pressure'].get(f))
    return [item]


def report(directory, meta, results, args):
    paired = comparisons(args, results) if meta['status'] not in ('failed', 'running') else []
    artifact = {'schemaVersion': VERSION, 'plan': args.plan, 'status': meta['status'], 'scope': meta['scope'],
                'deployment': args.deployment, 'policy': policy(args), 'contract': meta.get('contract'),
                'comparisons': paired, 'parameters': vars(args), 'points': [name for name, _ in results],
                'environment': {'target': meta.get('target'), 'loadHost': meta.get('loadHost')}}
    write_json(directory/'experiment.json', artifact)
    kafka=args.plan=='kafka-burst'
    lines = [f"# {args.plan}", '', f"状态：{meta['status']}；用途：{meta['scope']}。", '',
             '固定同压配对；无削峰收益不影响实验完成，发压或事务证据无效不生成削峰比较。' if kafka else '固定同压配对；SLO 不达标也是测量结果，发压无效不生成收益结论。', '',
             '| 点 | 发出/计划 | 输入有效 | 完成/有效请求 | 事务开始峰值 次/秒 | 事务并发峰值 |' if kafka else '| 点 | 发出/计划 | 有效 | SLO | 完成/有效请求 | 有效响应 p99 ms |',
             '|---|---|---|---|---|---|']
    for name,r in results:
        if kafka:
            pressure=r.get('pressure',{})
            lines.append(f"| [{name}]({name}/report.md) | {r['started']}/{r['scheduled']} | {r['valid']} | {r['completed']}/{r['eligible']} | {pressure.get('peakStartRps','—')} | {pressure.get('peakActive','—')} |")
        else:
            lines.append(f"| [{name}]({name}/report.md) | {r['started']}/{r['scheduled']} | {r['valid']} | {r['sloPassed']} | {r['completed']}/{r['eligible']} | {r['classes'].get('eligible',{}).get('responseMs',{}).get('p99')} |")
    lines += ['', '配对指标的 before/after 为每轮值的分布，afterMinusBefore 为每轮差值的分布；不将多个 p99 合并成总体 p99。',
              'Redis 主指标：有效购买响应 p95/p99、按时成功比例、最终完成比例及错误率；SQL/事务尝试仅解释机制。',
              'Kafka 主指标：覆盖突发及恢复全程的下单事务速率/并发峰值；代价为订单完成分位数、最终完成率、最大积压和恢复时间。不设置任意的完成时限，不比较 PAID/PENDING 返回耗时。',
              '完整比较见 experiment.json；每点保留逐笔请求、提交、快照、资源采样及 timeline.csv。',
              'GitHub-hosted runner 共享宿主机资源；结论限于本次机器、配额和负载，不推断最大容量。']
    (directory/'report.md').write_text('\n'.join(lines)+'\n')
