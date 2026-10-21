"""Validate prepared business fixtures before sending requests."""
import json
from analysis import Evidence, write_json
from fixtures import POINTS, PRICE


def verify_fixture(directory, config):
    """Validate streamed snapshots in bounded SQLite memory before retiring Redis."""
    evidence = Evidence(directory, database_name="fixture-check.sqlite")
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
        (directory/"fixture-check.sqlite").unlink(missing_ok=True)
