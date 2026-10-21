"""Real-runner fixture growth and short request probes, never comparison evidence."""
import copy
import json
import math
import os
import secrets
import sys
import time

from analysis import Evidence, write_json
from fixtures import POINTS, PRICE, create_users, credential, prepare, release_redis
from host import check_host_health
from protocol import budgeted_matrix, parse_args, validate_deployment, warmup_matrix
from runner import RUNTIME, REPORTS, api, measure, mysql_settings, require_idle, sql


def dataset_plan(args):
    points = budgeted_matrix(args)
    total = sum(p['scheduled'] for p in points)
    warmup = sum(p['scheduled'] for p in warmup_matrix(args))
    return {'formalRequests': total, 'warmupRequests':warmup, 'seedUsers': math.ceil((total+warmup)*1.2),
            'batchUsers': max(p['scheduled'] for p in points),
            'probeRate': 5, 'warmupSeconds': 5, 'probeSeconds': 10,
            'coverage': 'Fixed experiment plus fixed equal warmup request count plus 20%'}


def verify_fixture(directory, config):
    """Validate streamed snapshots in bounded SQLite memory before retiring Redis."""
    evidence = Evidence(directory)
    errors = []
    try:
        evidence.load_snapshot('before')
        planned = config['scheduled']
        expected_orders = planned-planned//10+1 if config['workload'] == 'mixed' else 1
        counts = {kind: evidence.scalar(f'SELECT COUNT(*) FROM before_{kind}')
                  for kind in ('users', 'orders', 'logs')}
        if counts != {'users': planned+1, 'orders': expected_orders, 'logs': expected_orders}:
            errors.append('Fixture row counts do not match the duplicate mix and warmup user')
        duplicate_predicate = 'u.idx%10<>9' if config['workload'] == 'mixed' else '0'
        checks = {
            'Fixture ownership, mix or balance mismatch': f'''SELECT COUNT(*) FROM before_users u
                LEFT JOIN before_orders o ON o.uid=u.uid WHERE u.idx<0 OR u.idx>?
                OR ((u.idx=? OR {duplicate_predicate})<>(o.oid IS NOT NULL))
                OR u.points<>?-CASE WHEN o.oid IS NOT NULL THEN ? ELSE 0 END''',
            'Fixture order or log mismatch': '''SELECT COUNT(*) FROM before_orders o
                LEFT JOIN before_users u ON u.uid=o.uid LEFT JOIN
                (SELECT oid,COUNT(*) n,MIN(uid) uid,MIN(delta) delta,MIN(kind) kind FROM before_logs GROUP BY oid) l ON l.oid=o.oid
                WHERE u.uid IS NULL OR o.price<>? OR o.status<>2 OR l.n IS NULL OR l.n<>1
                OR l.uid<>o.uid OR l.delta<>-o.price OR l.kind<>2''',
        }
        parameters = ((planned, planned, POINTS, PRICE), (PRICE,))
        for (message, query), params in zip(checks.items(), parameters):
            if evidence.scalar(query, params): errors.append(message)
        metadata = json.loads((directory/'before.json').read_text())
        if metadata['stock'] != planned+99:
            errors.append('Fixture stock conservation failed')
        if config['path'] != 'A':
            if metadata['redisStock'] != metadata['stock'] or evidence.scalar('''SELECT COUNT(*) FROM
                (SELECT uid FROM before_bought EXCEPT SELECT uid FROM before_orders)''') or evidence.scalar('''SELECT COUNT(*) FROM
                (SELECT uid FROM before_orders EXCEPT SELECT uid FROM before_bought)'''):
                errors.append('Fixture Redis stock or bought set differs from DB')
        result = {'status': 'failed' if errors else 'completed', 'counts': counts, 'errors': errors}
        write_json(directory/'fixture-check.json', result)
        if errors: raise RuntimeError('; '.join(errors))
        return counts
    finally:
        evidence.close()


def run(args):
    if os.environ.get('BENCH_CONTAINER') != '1' or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise RuntimeError('Preflight must run inside the bounded Actions load container')
    target = json.loads((RUNTIME/'target.json').read_text())
    load_host = json.loads((RUNTIME/'load-host.json').read_text())
    validate_deployment(args, target, load_host)
    directory = REPORTS/'preflight'/os.environ['BENCH_RUN_ID']
    meta = json.loads((directory/'manifest.json').read_text())
    summary = {'status': 'running', 'scope': 'validation', 'seeds': [], 'probes': []}
    started = time.monotonic()
    try:
        check_host_health()
        settings = mysql_settings()
        write_json(directory/'mysql-settings.json', settings)
        summary['dataset'] = dataset_plan(args)
        write_json(directory/'preflight.json', summary)
        private = RUNTIME/'preflight'/os.environ['BENCH_RUN_ID']
        private.mkdir(parents=True, mode=0o700)
        pool = create_users('x'+secrets.token_hex(6), 1, private)
        admin = credential(pool, 0)['token']
        require_idle(admin)
        product = int(sql("INSERT INTO t_product(title,price,stock,status) VALUES ('preflight',100,0,1); SELECT LAST_INSERT_ID();"))
        seeded, batch = 0, 0
        while seeded < summary['dataset']['seedUsers']:
            check_host_health()
            batch += 1
            size = min(summary['dataset']['batchUsers'], summary['dataset']['seedUsers']-seeded)
            paths = ('A', 'B') if args.plan == 'redis-filtering' else ('B', 'C')
            path = paths[(batch-1)%2]
            point = {'plan': args.plan, 'repeat': 1, 'workload': 'mixed' if args.plan == 'redis-filtering' else 'steady', 'path': path,
                     'rate': summary['dataset']['probeRate'], 'seconds': args.seconds, 'baseSeconds': 0,
                     'baseRate': 1, 'vus': args.vus, 'requestTimeout': args.request_timeout,
                     'points': POINTS, 'scheduled': size, 'userPrefix': 'x'+secrets.token_hex(6)}
            name = f'seed-{batch:03d}-{path}'
            point_dir = directory/name
            point_dir.mkdir()
            print(f'Preflight fixture {name}: {size} users, cumulative target {summary["dataset"]["seedUsers"]}', flush=True)
            prepare(point, product, private/name, point_dir, admin, api, require_idle)
            counts = verify_fixture(point_dir, point)
            release_redis(private/name, point)
            seeded += size
            summary['seeds'].append({'point': name, 'counts': counts})
            write_json(directory/'preflight.json', summary)
        # Warm each path at low rate, then validate with fresh users and activity.
        # Both phases retain evidence and validity checks; neither gates on SLO
        # or requires passing the measured workload's SLO.
        probe_args = copy.copy(args)
        probe_args.validation_only = True
        probe_args.drain_seconds = min(args.drain_seconds, 30)
        for path in (('A', 'B') if args.plan == 'redis-filtering' else ('B', 'C')):
            for phase, seconds in (('warmup', summary['dataset']['warmupSeconds']),
                                   ('probe', summary['dataset']['probeSeconds'])):
                check_host_health()
                name = f'{phase}-{path}'
                point_dir = directory/name
                point_dir.mkdir()
                point = {'plan': args.plan, 'repeat': 1,
                         'workload': 'mixed' if args.plan == 'redis-filtering' else 'steady', 'path': path,
                         'rate': summary['dataset']['probeRate'], 'seconds': seconds,
                         'baseSeconds': 0, 'baseRate': 1, 'vus': args.vus, 'requestTimeout': args.request_timeout,
                         'points': POINTS, 'scheduled': summary['dataset']['probeRate']*seconds,
                         'userPrefix': 'x'+secrets.token_hex(6)}
                print(f'Preflight {name}: {point["rate"]} RPS, {seconds}s, '
                      f'{point["scheduled"]} requests (readiness only)', flush=True)
                prepare(point, product, private/name, point_dir, admin, api, require_idle)
                value = measure(point, private/name, point_dir, admin, probe_args)
                errors = value['loadErrors']+value['correctnessErrors']
                for kind, metrics in value['classes'].items():
                    if metrics['failed']:
                        errors.append(f'Unexpected {kind} responses: {metrics["failed"]}')
                if not value['eligible'] or value['completed'] != value['eligible']:
                    errors.append(f'Eligible orders incomplete: {value["completed"]}/{value["eligible"]}')
                if value['unresolvedCount']:
                    errors = errors+[f'Unresolved accepted orders: {value["unresolvedCount"]}']
                passed = value['valid'] and not errors
                summary['probes'].append({'phase': phase, 'path': path, 'rate': point['rate'], 'seconds': seconds,
                                         'scheduled': point['scheduled'], 'started': value['started'],
                                         'completed': value['completed'], 'status': 'completed' if passed else 'failed',
                                         'errors': errors})
                write_json(directory/'preflight.json', summary)
                print(f'Preflight {name}: started={value["started"]}/{point["scheduled"]}, '
                      f'completed={value["completed"]}, status={summary["probes"][-1]["status"]}', flush=True)
                if not passed:
                    raise RuntimeError(f'Preflight {phase} {path} failed: ' + '; '.join(errors))
                release_redis(private/name, point)
        check_host_health()
        summary['status'] = meta['status'] = 'completed'
    except BaseException as error:
        summary.update(status='failed', error=str(error))
        meta.update(status='failed', error=str(error))
        raise
    finally:
        summary['elapsedSeconds'] = round(time.monotonic()-started, 3)
        meta['finishedAtMs'] = int(time.time()*1000)
        write_json(directory/'preflight.json', summary)
        write_json(directory/'manifest.json', meta)


if __name__ == '__main__':
    try:
        run(parse_args())
    except (RuntimeError, OSError, ValueError) as error:
        print(f'Preflight failed: {error}', file=sys.stderr)
        sys.exit(1)
