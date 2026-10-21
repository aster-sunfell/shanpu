"""Disk-backed evidence joins and exact quantiles; memory does not grow with request count."""
import csv
import json
import math
from pathlib import Path
import sqlite3

LATE_START_ERROR = 'Requests started after the injection window'


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def records(path):
    with Path(path).open() as file:
        for line in file:
            if line.strip(): yield json.loads(line)


def percentiles(values):
    values = sorted(values)
    def one(q):
        if not values: return None
        pos = (len(values) - 1) * q
        lo, hi = math.floor(pos), math.ceil(pos)
        return round(values[lo] + (values[hi] - values[lo]) * (pos - lo), 3)
    return {'samples': len(values), 'p50': one(.5), 'p95': one(.95), 'p99': one(.99)}


class Evidence:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.db = sqlite3.connect(self.directory / 'evidence.sqlite')
        self.db.executescript('''
            PRAGMA cache_size=-8192;
            PRAGMA temp_store=FILE;
            PRAGMA journal_mode=DELETE;
            CREATE TABLE IF NOT EXISTS starts(rid TEXT PRIMARY KEY,idx INTEGER UNIQUE,uid INTEGER,t REAL,e REAL,kind TEXT,due REAL);
            CREATE TABLE IF NOT EXISTS finishes(rid TEXT PRIMARY KEY,idx INTEGER,t REAL,f REAL,outcome TEXT,ms REAL,oid TEXT);
            CREATE TABLE IF NOT EXISTS commits(oid TEXT PRIMARY KEY,aid INTEGER,uid INTEGER,t REAL);
            CREATE INDEX IF NOT EXISTS commit_time ON commits(t);
            CREATE INDEX IF NOT EXISTS start_time ON starts(kind,t);
            CREATE INDEX IF NOT EXISTS finish_time ON finishes(f);
            CREATE INDEX IF NOT EXISTS finish_order ON finishes(oid);
        ''')
        for phase in ('before', 'after'):
            self.db.executescript(f'''
                CREATE TABLE IF NOT EXISTS {phase}_users(idx INTEGER PRIMARY KEY,uid INTEGER UNIQUE,points INTEGER);
                CREATE TABLE IF NOT EXISTS {phase}_orders(oid TEXT PRIMARY KEY,uid INTEGER,price INTEGER,status INTEGER);
                CREATE INDEX IF NOT EXISTS {phase}_order_user ON {phase}_orders(uid);
                CREATE TABLE IF NOT EXISTS {phase}_logs(uid INTEGER,oid TEXT,delta INTEGER,kind INTEGER);
                CREATE INDEX IF NOT EXISTS {phase}_log_order ON {phase}_logs(oid);
                CREATE TABLE IF NOT EXISTS {phase}_bought(uid INTEGER PRIMARY KEY);
            ''')
        self.record_errors = []

    def close(self): self.db.close()

    def scalar(self, query, args=()):
        return self.db.execute(query, args).fetchone()[0]

    def load_snapshot(self, phase):
        columns = {'users': 3, 'orders': 4, 'logs': 4, 'bought': 1}
        with self.db:
            for kind, count in columns.items():
                path = self.directory / f'{phase}-{kind}.jsonl'
                if not path.exists(): continue
                values = ((value,) if kind == 'bought' else value for value in records(path))
                # SSCAN is allowed to return a member twice; deduplicate only the bought set.
                insert = 'INSERT OR IGNORE' if kind == 'bought' else 'INSERT'
                self.db.executemany(f'{insert} INTO {phase}_{kind} VALUES ({",".join("?" for _ in range(count))})', values)

    def load_requests(self):
        with self.db:
            for row in records(self.directory / 'requests.jsonl'):
                try:
                    if row['type'] == 'start':
                        times = [row['startedAtMs'], row['injectionEndMs'], row.get('scheduledAtMs', row['startedAtMs'])]
                        if any(not isinstance(t, (int, float)) or isinstance(t, bool) or not math.isfinite(t) for t in times):
                            self.record_errors.append('Non-finite or non-numeric injection timestamp')
                            continue
                        self.db.execute('INSERT INTO starts VALUES (?,?,?,?,?,?,?)',
                                        (row['requestId'], row['index'], row['userId'], row['startedAtMs'], row['injectionEndMs'], row['trafficClass'], row.get('scheduledAtMs',row['startedAtMs'])))
                    elif row['type'] == 'finish':
                        self.db.execute('INSERT INTO finishes VALUES (?,?,?,?,?,?,?)',
                                        (row['requestId'], row['index'], row['startedAtMs'], row['finishedAtMs'], row['outcome'], row['responseMs'], row['orderId']))
                    else: raise ValueError('Unknown request record')
                except sqlite3.IntegrityError:
                    if 'Duplicate request/index records' not in self.record_errors: self.record_errors.append('Duplicate request/index records')

    def add_commits(self, events):
        with self.db:
            self.db.executemany('INSERT INTO commits VALUES (?,?,?,?)',
                                ((e['orderId'], e['activityId'], e['userId'], e['commitObservedAtMs']) for e in events))

    def pending_accepted(self):
        return self.scalar("SELECT COUNT(*) FROM finishes f LEFT JOIN commits c ON c.oid=f.oid WHERE f.outcome='accepted' AND c.oid IS NULL")

    def quantiles(self, expression, source, condition='1'):
        query = f'SELECT {expression} value FROM {source} WHERE {condition} AND {expression} IS NOT NULL'
        count = self.scalar(f'SELECT COUNT(*) FROM ({query})')
        def one(q):
            if not count: return None
            pos = (count - 1) * q
            lo, hi = math.floor(pos), math.ceil(pos)
            a = self.scalar(f'{query} ORDER BY value LIMIT 1 OFFSET {lo}')
            b = self.scalar(f'{query} ORDER BY value LIMIT 1 OFFSET {hi}')
            return round(a + (b - a) * (pos - lo), 3)
        return {'samples': count, 'p50': one(.5), 'p95': one(.95), 'p99': one(.99)}

    def injection_timing(self, origin, end):
        """Keep scheduling delay separate from inconsistent clocks/window evidence."""
        def anomaly(condition, deviation, parameters):
            count, maximum = self.db.execute(
                f'SELECT COUNT(*),MAX({deviation}) FROM starts WHERE {condition}', parameters).fetchone()
            # Substitution order is SELECT deviation, then WHERE condition.
            examples = self.db.execute(
                f'SELECT rid,t,due,e,{deviation} deviation FROM starts WHERE {condition} '
                'ORDER BY deviation DESC,rid LIMIT 5', parameters).fetchall()
            return {'count': count, 'maxDeviationMs': round(maximum, 3) if maximum is not None else None,
                    'examples': [dict(zip(('requestId', 'startedAtMs', 'scheduledAtMs', 'injectionEndMs', 'deviationMs'), row))
                                 for row in examples]}
        return {'windowStartMs': origin, 'windowEndMs': end, 'timestampToleranceMs': 1,
                'inconsistentEnds': anomaly('ABS(e-?)>1', 'ABS(e-?)', (end, end)),
                'earlyStarts': anomaly('t<?', '?-t', (origin, origin)),
                'lateStarts': anomaly('t>=?', 't-?', (end, end+1)),
                'invalidSchedule': anomaly('due<? OR due>=?', 'MAX(?-due,due-?,0)', (origin, end, origin, end)),
                'aheadOfSchedule': anomaly('t-due < -50', 'due-t', ())}

    def analyze(self, config, k6, cutoff_ms, policy):
        self.db.executescript('''
            DROP VIEW IF EXISTS new_orders;
            CREATE TEMP VIEW new_orders AS SELECT a.* FROM after_orders a LEFT JOIN before_orders b ON b.oid=a.oid WHERE b.oid IS NULL;
            DROP VIEW IF EXISTS joined;
            CREATE TEMP VIEW joined AS SELECT s.*,f.f,f.outcome,f.ms,f.oid response_oid,o.oid,o.status,c.t ct,c.aid,c.uid commit_uid
              FROM starts s LEFT JOIN finishes f ON f.rid=s.rid LEFT JOIN new_orders o ON o.uid=s.uid LEFT JOIN commits c ON c.oid=o.oid;
        ''')
        errors = list(self.record_errors)
        checks = {
            'Request/user ownership mismatch': 'SELECT COUNT(*) FROM starts s LEFT JOIN after_users u ON u.idx=s.idx WHERE u.uid IS NULL OR u.uid<>s.uid',
            'Unmatched finish or request identity changed': 'SELECT COUNT(*) FROM finishes f LEFT JOIN starts s ON s.rid=f.rid WHERE s.rid IS NULL OR f.idx<>s.idx OR f.t<>s.t',
            'Unexpected workload classification': "SELECT COUNT(*) FROM starts WHERE kind <> CASE WHEN idx%10<>9 AND ?='mixed' THEN 'duplicate' ELSE 'eligible' END",
            'Workload changed unexpectedly': "SELECT COUNT(*) FROM joined WHERE outcome IN ('duplicate','sold_out') AND NOT (kind='duplicate' AND outcome='duplicate') OR kind='duplicate' AND outcome='accepted'",
            'Accepted order owner/status mismatch': "SELECT COUNT(*) FROM joined WHERE outcome='accepted' AND (oid IS NULL OR response_oid<>oid OR status<>2)",
            'Commit evidence missing': 'SELECT COUNT(*) FROM new_orders o LEFT JOIN commits c ON c.oid=o.oid WHERE c.oid IS NULL',
            'Commit event without a DB order': 'SELECT COUNT(*) FROM commits c LEFT JOIN new_orders o ON o.oid=c.oid WHERE o.oid IS NULL',
            'Commit event owner/activity mismatch': 'SELECT COUNT(*) FROM commits c JOIN new_orders o ON o.oid=c.oid WHERE c.uid<>o.uid OR c.aid<>?',
            'Unpaid order at snapshot': 'SELECT COUNT(*) FROM after_orders WHERE status<>2',
            'Multiple orders per user/activity': 'SELECT COUNT(*) FROM (SELECT uid FROM after_orders GROUP BY uid HAVING COUNT(*)<>1)',
            'Point log mismatch': '''SELECT COUNT(*) FROM after_orders o LEFT JOIN
                 (SELECT oid,COUNT(*) n,MIN(uid) uid,MIN(delta) delta,MIN(kind) kind FROM after_logs GROUP BY oid) l ON l.oid=o.oid
                 WHERE l.n IS NULL OR l.n<>1 OR l.uid<>o.uid OR l.delta<>-o.price OR l.kind<>2''',
            'Point balance mismatch': '''SELECT COUNT(*) FROM after_users u LEFT JOIN
                 (SELECT uid,SUM(delta) delta FROM after_logs GROUP BY uid) l ON l.uid=u.uid WHERE u.points<>?+COALESCE(l.delta,0)''',
            'Orphan point log': 'SELECT COUNT(*) FROM after_logs l LEFT JOIN after_orders o ON o.oid=l.oid WHERE o.oid IS NULL',
            'Order from an unscheduled user': 'SELECT COUNT(*) FROM new_orders o LEFT JOIN starts s ON s.uid=o.uid WHERE s.rid IS NULL',
            'Negative completion latency': 'SELECT COUNT(*) FROM joined WHERE ct<t',
        }
        parameters = {'Unexpected workload classification': (config['workload'],),
                      'Commit event owner/activity mismatch': (config['activityId'],), 'Point balance mismatch': (config['points'],)}
        for message, query in checks.items():
            count = self.scalar(query, parameters.get(message, ()))
            if count: errors.append(f'{message}: {count}')
        before = json.loads((self.directory / 'before.json').read_text())
        after = json.loads((self.directory / 'after.json').read_text())
        count_new = self.scalar('SELECT COUNT(*) FROM new_orders')
        if after['stock'] < 0 or before['stock'] - count_new != after['stock']: errors.append('DB stock conservation failed')
        if config['path'] != 'A':
            if after['redisStock'] != after['stock']: errors.append('Redis/DB stock mismatch at cutoff')
            if self.scalar('SELECT COUNT(*) FROM (SELECT uid FROM after_bought EXCEPT SELECT uid FROM after_orders)') or self.scalar('SELECT COUNT(*) FROM (SELECT uid FROM after_orders EXCEPT SELECT uid FROM after_bought)'):
                errors.append('Redis bought set/DB orders mismatch')
        started = self.scalar('SELECT COUNT(*) FROM starts')
        unmatched = self.scalar('SELECT COUNT(*) FROM starts s LEFT JOIN finishes f ON f.rid=s.rid WHERE f.rid IS NULL')
        dropped = k6.get('metrics', {}).get('dropped_iterations', {}).get('values', {}).get('count')
        load_errors = []
        if dropped or started != config['scheduled']: load_errors.append('Offered load not achieved')
        if unmatched: load_errors.append('Interrupted requests')
        if not started: load_errors.append('No requests recorded')
        generator_lag = self.quantiles('t-due','starts')
        if (generator_lag['p99'] or 0)>policy.get('generatorLagP99Ms',100):
            load_errors.append('Injection timing does not follow the arrival schedule')
        origin = self.scalar('SELECT MIN(e) FROM starts') or cutoff_ms
        origin -= (config['seconds'] + config['baseSeconds']) * 1000
        end = origin + (config['seconds'] + config['baseSeconds']) * 1000
        injection_timing = self.injection_timing(origin, end)
        for category, message in (
                ('inconsistentEnds', 'Injection window endpoints are inconsistent'),
                ('earlyStarts', 'Requests started before the injection window'),
                ('invalidSchedule', 'Scheduled arrivals fall outside the injection window'),
                ('aheadOfSchedule', 'Requests started before their scheduled arrival'),
                ('lateStarts', LATE_START_ERROR)):
            if injection_timing[category]['count']: load_errors.append(message)
        seconds = config['seconds'] + config['baseSeconds']
        outcomes = dict(self.db.execute('SELECT outcome,COUNT(*) FROM finishes GROUP BY outcome'))
        if unmatched: outcomes['interrupted'] = unmatched
        response = {outcome: self.quantiles('ms', 'finishes', f"outcome='{outcome}'") for outcome in outcomes if outcome != 'interrupted'}
        classes = {}
        completion_limit = policy['completionP99Ms']
        response_limit = policy['responseP99Ms']
        for kind in ('eligible', 'duplicate'):
            count = self.scalar('SELECT COUNT(*) FROM starts WHERE kind=?', (kind,))
            if not count: continue
            expected = 'accepted' if kind == 'eligible' else 'duplicate'
            failures = self.scalar('SELECT COUNT(*) FROM joined WHERE kind=? AND (outcome IS NULL OR outcome<>?)', (kind, expected))
            response_q = self.quantiles('ms', 'joined', f"kind='{kind}'")
            classes[kind] = {'requests': count, 'failed': failures, 'errorRate': failures / count,
                             'responseMs': response_q,
                             'successfulResponseMs': self.quantiles('ms', 'joined', f"kind='{kind}' AND outcome='{expected}'")}
        completed = self.scalar('SELECT COUNT(*) FROM joined WHERE ct<=?', (cutoff_ms,))
        unresolved = self.scalar("SELECT COUNT(*) FROM joined WHERE outcome='accepted' AND (ct IS NULL OR ct>?)", (cutoff_ms,))
        with (self.directory / 'unresolved.jsonl').open('w') as file:
            for row in self.db.execute("SELECT rid FROM joined WHERE outcome='accepted' AND (ct IS NULL OR ct>?)", (cutoff_ms,)):
                file.write(json.dumps({'requestId': row[0]}) + '\n')
        eligible = classes.get('eligible', {}).get('requests', 0)
        completion_q = self.quantiles('ct-t', 'joined', f"kind='eligible' AND ct<={cutoff_ms}")
        accepted = self.scalar("SELECT COUNT(*) FROM finishes WHERE outcome='accepted'")
        timely_accepted = self.scalar("SELECT COUNT(*) FROM finishes WHERE outcome='accepted' AND f-t<=?", (response_limit,))
        on_time = self.scalar('SELECT COUNT(*) FROM joined WHERE ct<=? AND ct-t<=?', (cutoff_ms, completion_limit))
        burst = None
        if config['workload'] == 'burst':
            # Scenario identity keeps a delayed baseline request out of the burst cohort.
            cohort = "kind='eligible' AND rid LIKE 'burst:%'"
            burst = {
                'scope': 'burst-requests',
                'requests': self.scalar(f'SELECT COUNT(*) FROM joined WHERE {cohort}'),
                'acceptedWithinResponseDeadline': self.scalar(
                    f"SELECT COUNT(*) FROM joined WHERE {cohort} AND outcome='accepted' AND f-t<=?", (response_limit,)),
                'completedWithinDeadline': self.scalar(
                    f'SELECT COUNT(*) FROM joined WHERE {cohort} AND ct<=? AND ct-t<=?', (cutoff_ms, completion_limit)),
                'completed': self.scalar(f'SELECT COUNT(*) FROM joined WHERE {cohort} AND ct<=?', (cutoff_ms,)),
            }
            if burst['requests'] != config['rate'] * config['seconds']:
                load_errors.append('Burst request cohort does not match the scheduled phase')
        buckets = [{'second': i, 'started': 0, 'accepted': 0, 'completed': 0, 'pendingAccepted': 0, 'outstanding': 0}
                   for i in range(max(1, math.ceil(max(end, cutoff_ms) - origin) // 1000 + 1))]
        # Group on disk; only the bounded time axis is materialized in Python.
        for expression, condition, field in [('t', '1', 'started'), ('f', "outcome='accepted'", 'accepted'), ('ct', f'ct<={cutoff_ms}', 'completed')]:
            for second, count in self.db.execute(f'SELECT CAST(({expression}-?)/1000 AS INTEGER),COUNT(*) FROM joined WHERE {condition} AND {expression} IS NOT NULL GROUP BY 1', (origin,)):
                if 0 <= second < len(buckets): buckets[second][field] += count
        peak, pending = 0, 0
        # Complete-before-response contributes no false pending order.
        intervals = "SELECT f.f t,1 delta FROM finishes f JOIN commits c ON c.oid=f.oid WHERE f.outcome='accepted' AND c.t>f.f UNION ALL SELECT c.t,-1 FROM finishes f JOIN commits c ON c.oid=f.oid WHERE f.outcome='accepted' AND c.t>f.f UNION ALL SELECT f.f,1 FROM finishes f LEFT JOIN commits c ON c.oid=f.oid WHERE f.outcome='accepted' AND c.oid IS NULL"
        for stamp, change in self.db.execute(f'SELECT t,SUM(delta) FROM ({intervals}) WHERE t<=? GROUP BY t ORDER BY t', (cutoff_ms,)):
            pending += change
            peak = max(peak, pending)
        arrivals = dict(self.db.execute("SELECT CAST((t-?)/1000 AS INTEGER),COUNT(*) FROM starts WHERE kind='eligible' GROUP BY 1", (origin,)))
        changes = iter(self.db.execute(f'SELECT t,SUM(delta) FROM ({intervals}) WHERE t<=? GROUP BY t ORDER BY t', (cutoff_ms,)))
        change = next(changes, None)
        eligible_started = eligible_completed = pending = 0
        for row in buckets:
            boundary = origin + (row['second'] + 1) * 1000
            eligible_started += arrivals.get(row['second'], 0)
            eligible_completed += row['completed']
            row['outstanding'] = max(0, eligible_started - eligible_completed)
            while change is not None and change[0] < boundary:
                pending += change[1]
                change = next(changes, None)
            row['pendingAccepted'] = max(0, pending)
        steady_rows = buckets[:config['seconds']] if config['workload'] != 'burst' else []
        quarter = max(1, len(steady_rows) // 4)
        growth = (sum(r['outstanding'] for r in steady_rows[-quarter:]) - sum(r['outstanding'] for r in steady_rows[:quarter])) / quarter if steady_rows else 0
        tail = steady_rows[len(steady_rows)//2:]
        n = len(tail)
        sx, sy = sum(range(n)), sum(r['outstanding'] for r in tail)
        denominator = n * sum(i*i for i in range(n)) - sx*sx
        slope = (n * sum(i*r['outstanding'] for i,r in enumerate(tail)) - sx*sy) / denominator if denominator else 0
        offered_eligible = eligible / seconds
        growth_limit, slope_limit = max(2, offered_eligible * policy.get('backlogGrowthSeconds', .25)), max(.05, offered_eligible * policy.get('backlogSlopeFraction', .005))
        stable = growth <= growth_limit and slope <= slope_limit
        slo = bool(classes) and all(c['errorRate'] <= policy['maxErrorRate'] and c['responseMs']['p99'] is not None
                                    and c['responseMs']['p99'] <= response_limit for c in classes.values())
        slo = slo and completion_q['p99'] is not None and completion_q['p99'] <= completion_limit and completed == eligible
        if config['workload'] != 'burst': slo = slo and stable
        last_commit = self.scalar('SELECT MAX(ct) FROM joined WHERE ct<=?', (cutoff_ms,)) or end
        window_complete = self.scalar('SELECT COUNT(*) FROM joined WHERE ct>=? AND ct<?', (origin, end))
        health = bool(not errors and not unresolved)
        peak_http = self.scalar('''SELECT COALESCE(MAX(n),0) FROM
            (SELECT SUM(delta) OVER (ORDER BY t) n FROM
                (SELECT t,SUM(delta) delta FROM
                    (SELECT t,1 delta FROM starts UNION ALL SELECT f,-1 FROM finishes) GROUP BY t))''')
        return {'path': config['path'], 'workload': config['workload'], 'rate': config['rate'],
                'peakHttpInFlight': peak_http,
                'scheduled': config['scheduled'], 'started': started, 'generatorLagMs': generator_lag,
                'injectionTiming': injection_timing, 'outcomes': outcomes, 'classes': classes,
                'accepted': accepted, 'completed': completed, 'eligible': eligible, 'unresolvedCount': unresolved,
                'committedAfterResponseFailure': self.scalar("SELECT COUNT(*) FROM joined WHERE ct<=? AND (outcome IS NULL OR outcome<>'accepted')", (cutoff_ms,)),
                'acceptedWithinResponseDeadline': timely_accepted, 'completedWithinDeadline': on_time,
                'burst': burst,
                'actualRequestRps': started / seconds, 'acceptedRps': accepted / seconds, 'completionWindowRps': window_complete / seconds,
                'responseMs': response, 'completionMs': completion_q, 'completionCoverage': completed / eligible if eligible else None,
                'peakPendingAccepted': peak, 'drainMs': max(0, last_commit-end) if not unresolved else None,
                'backlog': {'growth': growth, 'tailSlopePerSecond': slope, 'growthLimit': growth_limit, 'slopeLimit': slope_limit, 'stable': stable},
                'correctnessErrors': errors, 'loadErrors': load_errors, 'dropped': dropped, 'missingRequests': max(0,config['scheduled']-started), 'cutoffMs': cutoff_ms,
                'valid': health and not load_errors, 'sloPassed': bool(slo and health and not load_errors), 'timeline': buckets}


def save_result(directory, result):
    directory = Path(directory)
    write_json(directory / 'result.json', result)
    if 'injectionTiming' in result:
        write_json(directory / 'injection-timing.json', result['injectionTiming'])
    fields = ['second', 'started', 'accepted', 'completed', 'pendingAccepted', 'outstanding']
    with (directory / 'timeline.csv').open('w') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(result['timeline'])
    save_curves(directory, result['timeline'])
    lines = [f"# {result['path']} / {result['workload']} / {result['rate']} RPS", '',
             f"数据与输入有效：{result['valid']}；SLO 通过：{result['sloPassed']}。", '',
             f"受理 {result['accepted']}，按响应期限受理 {result['acceptedWithinResponseDeadline']}，完成 {result['completed']}，按完成期限完成 {result['completedWithinDeadline']}。", '',
             f"完成覆盖率：{result['completionCoverage']}；截止未完成受理数：{result['unresolvedCount']}。", '',
             '![请求、完成与积压曲线](curves.svg)', '',
             '分类延迟、错误率及积压判定见 [result.json](result.json)，每秒曲线数据见 [timeline.csv](timeline.csv)。', '',
             '注入时间异常的分类、数量、最大偏差与示例见 [injection-timing.json](injection-timing.json)。', '',
             '原始逐笔记录保存在 JSONL，关联和精确分位数在 evidence.sqlite 中通过磁盘计算。']
    (directory / 'report.md').write_text('\n'.join(lines) + '\n')


def save_curves(directory, rows):
    # Bounded by experiment duration, not request count; standalone SVG artifact.
    colors = {'started':'#64748b','accepted':'#2563eb','completed':'#16a34a','outstanding':'#dc2626','pendingAccepted':'#d97706'}
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="880" height="510" viewBox="0 0 880 510">',
           '<rect width="880" height="510" fill="white"/><g font-family="sans-serif" font-size="12">']
    for top, title, fields in [(40,'Requests, acceptance and commit per second',('started','accepted','completed')),
                               (285,'Outstanding requests / accepted orders',('outstanding','pendingAccepted'))]:
        ceiling = max((r[f] for r in rows for f in fields),default=1) or 1
        svg += [f'<text x="55" y="{top-15}">{title}; max scale {ceiling}</text>',
                f'<path d="M55 {top} V{top+150} H840" fill="none" stroke="#aaa"/>']
        for i,field in enumerate(fields):
            points = ' '.join(f"{55+785*n/max(1,len(rows)-1):.1f},{top+150-150*r[field]/ceiling:.1f}" for n,r in enumerate(rows))
            svg += [f'<polyline points="{points}" fill="none" stroke="{colors[field]}" stroke-width="2"/>',
                    f'<text x="{55+i*230}" y="{top+175}" fill="{colors[field]}">{field}</text>']
        svg.append(f'<text x="750" y="{top+195}">{max(0,len(rows)-1)} seconds</text>')
    svg.append('</g></svg>')
    (Path(directory)/'curves.svg').write_text('\n'.join(svg)+'\n')
