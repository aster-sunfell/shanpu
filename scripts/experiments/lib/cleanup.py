"""Explicit bounded cleanup after complete evidence and ownership checks."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from analysis import write_json
from fixtures import credentials, credential, release_redis
from runner import ROOT, RUNTIME, require_idle, redis, sql
import transport



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report_directory', type=Path)
    directory = parser.parse_args().report_directory.resolve()
    allowed = False
    for root in (ROOT/'docs/experiments', RUNTIME/'validation/performance-v2'):
        try:
            relative = directory.relative_to(root)
            allowed |= len(relative.parts)==2 and relative.parts[0] in ('redis-filtering','kafka-buffering')
        except ValueError: pass
    if not allowed: raise RuntimeError('Not a current performance/validation report directory')
    meta = json.loads((directory/'manifest.json').read_text())
    if meta.get('schemaVersion') != 2: raise RuntimeError('Legacy experiment is archived; preserve or reconcile it manually')
    if meta['target']['instanceId'] != os.environ['SUT_INSTANCE_ID'] or meta['database'] != os.environ['MYSQL_DATABASE']:
        raise RuntimeError('Report belongs to another target/database')
    if meta.get('dataState') == 'cleaned': raise RuntimeError('Already cleaned')
    if meta['status'] != 'completed': raise RuntimeError('Incomplete/failed run: preserve evidence and reconcile manually')
    private = RUNTIME/directory.name
    with (RUNTIME/'experiment.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        admin_pool = json.loads((private/'admin.json').read_text())
        require_idle(credential(admin_pool,0)['token'])
        product_id = int(meta['productId'])
        if sql(f'SELECT title FROM t_product WHERE id={product_id};').strip() != f'experiment-{directory.name}':
            raise RuntimeError('Product ownership mismatch')
        ids, prefixes = [], []
        for point in meta['points']:
            prefix, aid = point['userPrefix'], int(point['activityId'])
            if Path(point['directory']).name != point['directory'] or not re.fullmatch(r'x[0-9a-f]{12}',prefix):
                raise RuntimeError('Invalid point identity')
            value = json.loads((directory/point['directory']/'result.json').read_text())
            if value['correctnessErrors'] or value['unresolvedCount']: raise RuntimeError('Unresolved data cannot be deleted')
            if sql(f'SELECT title FROM t_seckill_activity WHERE id={aid} AND product_id={product_id};').strip() != prefix:
                raise RuntimeError('Activity ownership mismatch')
            if sql(f"SELECT COUNT(*) FROM t_order o JOIN t_user u ON u.id=o.user_id WHERE "
                   f"(o.activity_id={aid} AND u.username NOT REGEXP '^{prefix}_[0-9]+$') OR "
                   f"(u.username REGEXP '^{prefix}_[0-9]+$' AND (o.activity_id IS NULL OR o.activity_id<>{aid}));").strip() != '0':
                raise RuntimeError('Foreign orders reference this fixture')
            ids.append(aid); prefixes.append(prefix)
        prefix = meta['adminPrefix']
        if not re.fullmatch(r'x[0-9a-f]{12}',prefix): raise RuntimeError('Invalid admin prefix')
        if sql(f"SELECT COUNT(*) FROM t_order o JOIN t_user u ON u.id=o.user_id WHERE u.username REGEXP '^{prefix}_[0-9]+$';").strip() != '0':
            raise RuntimeError('Admin has unexpected orders')
        prefixes.append(prefix)
        id_list = ','.join(map(str,ids))
        if sql(f'SELECT COUNT(*) FROM t_seckill_activity WHERE product_id={product_id} AND id NOT IN ({id_list});').strip() != '0':
            raise RuntimeError('Foreign activity references this product')
        if sql(f'SELECT COUNT(*) FROM t_order WHERE product_id={product_id} AND (activity_id IS NULL OR activity_id NOT IN ({id_list}));').strip() != '0':
            raise RuntimeError('Foreign order references this product')
        # All ownership checks complete before the first deletion. Batches bound undo and client RAM.
        for point in meta['points']:
            config = json.loads((directory/point['directory']/'point.json').read_text())
            release_redis(private/point['directory'],config)
        for prefix in prefixes:
            last = 0
            while True:
                batch = list(transport.sql_rows(f"SELECT id FROM t_user WHERE id>{last} AND username REGEXP '^{prefix}_[0-9]+$' ORDER BY id LIMIT 500;"))
                if not batch: break
                users = ','.join(str(int(row[0])) for row in batch)
                last = int(batch[-1][0])
                sql(f'START TRANSACTION; DELETE FROM t_point_log WHERE user_id IN ({users}); '
                    f'DELETE FROM t_order WHERE user_id IN ({users}); DELETE FROM t_user WHERE id IN ({users}); COMMIT;')
        sql(f'START TRANSACTION; DELETE FROM t_seckill_activity WHERE id IN ({id_list}); DELETE FROM t_product WHERE id={product_id}; COMMIT;')
        admin_keys = [f"shanpu:user:session:{hashlib.sha256(u['token'].encode()).hexdigest()[:32]}" for u in credentials(admin_pool)]
        if admin_keys: redis('DEL',*admin_keys)
        for file in private.glob('*/users.json'): file.unlink()
        (private/'admin.json').unlink()
        meta['dataState']='cleaned'
        write_json(directory/'manifest.json',meta)
        print(f'已分批清理独立实验数据，证据保留：{directory}')


if __name__ == '__main__':
    try: main()
    except (RuntimeError, ValueError, OSError) as error:
        print(f'拒绝/中止清理，保留剩余现场：{error}',file=sys.stderr)
        sys.exit(1)
