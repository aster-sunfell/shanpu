"""Bounded fixture batches and compact credentials; no token array proportional to load."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import time

import transport
from host import atomic_json, check_host_health

POINTS = 10_000_000
PRICE = 100
BATCH = 500
ORDER_BATCH = 100


def check_disk(path, *, check_host=True):
    if check_host: check_host_health()
    if shutil.disk_usage(path).free < 1024 * 1024 * 1024:
        raise RuntimeError('Less than 1024 MiB disk space remains; preserving evidence')


def token_for(uid, username, issued_at=None):
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b'=').decode()
    now = int(time.time()) if issued_at is None else issued_at
    head = encode(b'{"alg":"HS256","typ":"JWT"}')
    body = encode(json.dumps({'uid': uid, 'uname': username, 'iat': now, 'exp': now + 86400}, separators=(',', ':')).encode())
    payload = f'{head}.{body}'
    return f'{payload}.{encode(hmac.new(os.environ["JWT_SECRET"].encode(), payload.encode(), hashlib.sha256).digest())}'


def user_id(pool, index):
    segment = next(s for s in pool['segments'] if s['index'] <= index < s['index'] + s['count'])
    return segment['userId'] + index - segment['index']


def credential(pool, index):
    uid = user_id(pool, index)
    username = f'{pool["prefix"]}_{index}'
    return {'index': index, 'userId': uid, 'token': token_for(uid, username, pool['issuedAt'])}


def credentials(pool):
    for segment in pool['segments']:
        for index in range(segment['index'], segment['index'] + segment['count']):
            uid = segment['userId'] + index - segment['index']
            yield {'index': index, 'userId': uid,
                   'token': token_for(uid, f'{pool["prefix"]}_{index}', pool['issuedAt'])}


def create_users(prefix, count, disk_path):
    pool = {'prefix': prefix, 'count': count, 'issuedAt': int(time.time()), 'segments': []}
    (Path(disk_path)/'users.json').write_text(json.dumps(pool))
    for start in range(0, count, BATCH):
        check_disk(disk_path)
        end = min(start + BATCH, count)
        values = ','.join(f"('{prefix}_{i}','disabled-experiment-login','实验用户',{POINTS})" for i in range(start, end))
        first_id = int(transport.sql(f'INSERT INTO t_user(username,password,nickname,points) VALUES {values}; SELECT LAST_INSERT_ID();').strip())
        rows = list(transport.sql_rows(f'SELECT id,username FROM t_user WHERE id>={first_id} AND id<{first_id + end-start} ORDER BY id;'))
        if len(rows) != end - start or any(int(uid) != int(rows[0][0]) + i or username != f'{prefix}_{start+i}'
                                           for i, (uid, username) in enumerate(rows)):
            raise RuntimeError('Fixture IDs are not contiguous within a batch; another writer may be active')
        segment = {'index': start, 'userId': int(rows[0][0]), 'count': len(rows)}
        if pool['segments'] and pool['segments'][-1]['userId'] + pool['segments'][-1]['count'] == segment['userId']:
            pool['segments'][-1]['count'] += len(rows)
        else:
            pool['segments'].append(segment)
        if len(pool['segments']) > 1024:
            raise RuntimeError('Too many fixture ID gaps; require an exclusive target database')
        (Path(disk_path)/'users.json').write_text(json.dumps(pool))
        transport.sessions({'userId': int(uid), 'token': token_for(int(uid), name, pool['issuedAt'])} for uid, name in rows)
    return pool


def snapshot(config, directory, phase, *, check_host=True):
    aid, prefix = config['activityId'], config['userPrefix']
    metadata = {'stock': int(transport.sql(f'SELECT total_stock FROM t_seckill_activity WHERE id={aid};').strip()),
                'redisStock': int(transport.redis_command('GET', f'shanpu:seckill:stock:{aid}')) if config['path'] != 'A' else None}
    queries = {
        'users': f"SELECT CAST(SUBSTRING_INDEX(username,'_',-1) AS UNSIGNED),id,points FROM t_user WHERE username REGEXP '^{prefix}_[0-9]+$';",
        'orders': f'SELECT order_id,user_id,price,status FROM t_order WHERE activity_id={aid};',
        'logs': f"SELECT l.user_id,l.biz_ref,l.delta,l.biz_type FROM t_point_log l JOIN t_user u ON u.id=l.user_id WHERE u.username REGEXP '^{prefix}_[0-9]+$';"}
    for kind, query in queries.items():
        with (directory / f'{phase}-{kind}.jsonl').open('w') as file:
            for index, row in enumerate(transport.sql_rows(query)):
                file.write(json.dumps(row, default=str) + '\n')
                if index % 5000 == 0: check_disk(directory, check_host=check_host)
    if config['path'] != 'A':
        with (directory / f'{phase}-bought.jsonl').open('w') as file:
            for uid in transport.redis_members(f'shanpu:seckill:bought:{aid}'):
                file.write(json.dumps(int(uid)) + '\n')
    (directory / f'{phase}.json').write_text(json.dumps(metadata) + '\n')
    return metadata


def prepare_mixed_batch(config, pool, product_id, tier, start, end, progress):
    aid = config['activityId']
    users = [user_id(pool, i) for i in range(start, end) if i % 10 != 9]
    if not users:
        return
    orders = [(f'prep{uid}x{aid}', uid, product_id, aid, tier + 1, PRICE, 2) for uid in users]
    logs = [(uid, -PRICE, POINTS - PRICE, 2, f'prep{uid}x{aid}') for uid in users]
    # IDs come from the validated fixture pool, never from historical orders.
    # Every statement is bounded by this batch and uses primary-key updates.
    operations = [
        ('orders', 'INSERT INTO t_order(order_id,user_id,product_id,activity_id,source,price,status,paid_at) VALUES '
         + ','.join(['(%s,%s,%s,%s,%s,%s,%s,NOW())'] * len(users)),
         tuple(value for row in orders for value in row), len(users)),
        ('balances', 'UPDATE t_user SET points=points-%s WHERE id IN (' + ','.join(['%s'] * len(users)) + ')',
         (PRICE, *users), len(users)),
        ('point-logs', 'INSERT INTO t_point_log(user_id,delta,balance_after,biz_type,biz_ref) VALUES '
         + ','.join(['(%s,%s,%s,%s,%s)'] * len(users)),
         tuple(value for row in logs for value in row), len(users)),
        ('stock', 'UPDATE t_seckill_activity SET total_stock=total_stock-%s WHERE id=%s',
         (len(users), aid), 1),
    ]
    transport.fixture_transaction(operations, progress)
    if config['path'] != 'A':
        progress('redis-bought')
        transport.redis_command('SADD', f'shanpu:seckill:bought:{aid}', *users)


def prepare(config, product_id, private, directory, token, api, require_idle):
    started = time.monotonic()
    state = {'status': 'running', 'phase': 'users', 'batchStart': None, 'batchEnd': None}

    def progress(phase, **details):
        state.update(phase=phase, elapsedSeconds=round(time.monotonic()-started, 3), **details)
        atomic_json(directory / 'preparation.json', state)

    try:
        progress('users')
        value = _prepare(config, product_id, private, directory, token, api, require_idle, progress)
        progress('complete', status='completed', batchStart=None, batchEnd=None)
        print(f"数据准备完成 {directory.name}: {state['elapsedSeconds']:.3f}s", flush=True)
        return value
    except Exception as error:
        progress(state['phase'], status='failed', error=str(error))
        raise RuntimeError(f"Fixture preparation failed: point={directory.name}, phase={state['phase']}, "
                           f"batch=[{state['batchStart']},{state['batchEnd']}), "
                           f"elapsed={state['elapsedSeconds']:.3f}s; {error}") from error


def _prepare(config, product_id, private, directory, token, api, require_idle, progress):
    private.mkdir(parents=True, exist_ok=True)
    private.chmod(0o700)
    pool = create_users(config['userPrefix'], config['scheduled'] + 1, private)
    (private / 'users.json').write_text(json.dumps(pool))
    stock = config['scheduled'] + 100
    prepared = config['scheduled'] - config['scheduled'] // 10 if config['workload'] == 'mixed' else 0
    tier = {'A': 1, 'B': 2, 'C': 3}[config['path']]
    progress('activity')
    aid = int(transport.sql(f"INSERT INTO t_seckill_activity(product_id,title,tier,seckill_price,total_stock,start_at,end_at,status) "
                            f"VALUES ({product_id},'{config['userPrefix']}',{tier},{PRICE},{stock+prepared},NOW()-INTERVAL 1 HOUR,NOW()+INTERVAL 1 DAY,1); SELECT LAST_INSERT_ID();").strip())
    config['activityId'] = aid
    for path in (directory / 'point.json', private / 'point.json'): path.write_text(json.dumps(config))
    if config['workload'] == 'mixed':
        for start in range(0, config['scheduled'], ORDER_BATCH):
            check_disk(directory)
            end = min(start + ORDER_BATCH, config['scheduled'])
            progress('duplicate-batch', batchStart=start, batchEnd=end)
            prepare_mixed_batch(config, pool, product_id, tier, start, end, progress)
    progress('warmup', batchStart=None, batchEnd=None)
    if config['path'] != 'A': transport.redis_command('SET', f'shanpu:seckill:stock:{aid}', stock)
    warm = api(f'/seckills/{aid}/grab', credential(pool, config['scheduled'])['token'], {})
    deadline = time.monotonic() + 60
    while transport.sql(f"SELECT COUNT(*) FROM t_order WHERE order_id='{warm['orderId']}' AND status=2;").strip() != '1':
        check_disk(directory)
        if time.monotonic() > deadline: raise RuntimeError('Warmup order did not complete')
        time.sleep(.2)
    require_idle(token)
    progress('snapshot')
    result = snapshot(config, directory, 'before')
    from fixture_check import verify_fixture
    verify_fixture(directory, config)
    return result


def release_redis(private, config):
    """Verified point only: retire ephemeral sessions/cache, keep DB and disk evidence."""
    pool = json.loads((private / 'users.json').read_text())
    batch = []
    for user in credentials(pool):
        digest = hashlib.sha256(user['token'].encode()).hexdigest()[:32]
        batch.append(f'shanpu:user:session:{digest}')
        if len(batch) == 500:
            transport.redis_command('DEL', *batch)
            batch.clear()
    if batch: transport.redis_command('DEL', *batch)
    aid = config['activityId']
    transport.redis_command('DEL', *(f'shanpu:seckill:{part}:{aid}' for part in ('stock','bought','activity','activity:lock','activity:null')))
    batch = []
    for uid, oid in transport.sql_rows(f'SELECT user_id,order_id FROM t_order WHERE activity_id={aid};'):
        batch.append(f'shanpu:order:detail:user:{uid}:{oid}')
        if len(batch) == 500:
            transport.redis_command('DEL', *batch); batch.clear()
    if batch: transport.redis_command('DEL', *batch)
