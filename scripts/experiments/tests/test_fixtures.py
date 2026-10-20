"""Fixture correctness and transaction failures without starting measured services."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from fixtures import ORDER_BATCH, POINTS, PRICE, prepare, prepare_mixed_batch, user_id
from transport import fixture_transaction


class Cursor:
    def __init__(self, db):
        self.cursor = db.cursor()

    def __enter__(self): return self
    def __exit__(self, *_): self.cursor.close()

    def execute(self, statement, parameters):
        self.cursor.execute(statement.replace('%s', '?'), parameters)
        self.rowcount = self.cursor.rowcount


class Connection:
    def __init__(self, db):
        self.db, self.closed = db, False

    def cursor(self): return Cursor(self.db)
    def commit(self): self.db.commit()
    def rollback(self): self.db.rollback()
    def close(self): self.closed = True


class FixturesTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.create_function('NOW', 0, lambda: '2026-10-21 00:00:00')
        self.db.executescript('''
            CREATE TABLE t_user(id INTEGER PRIMARY KEY, points INTEGER);
            CREATE TABLE t_order(order_id TEXT PRIMARY KEY, user_id INTEGER, product_id INTEGER,
                activity_id INTEGER, source INTEGER, price INTEGER, status INTEGER, paid_at TEXT,
                UNIQUE(user_id,activity_id));
            CREATE TABLE t_point_log(user_id INTEGER, delta INTEGER, balance_after INTEGER,
                biz_type INTEGER, biz_ref TEXT);
            CREATE TABLE t_seckill_activity(id INTEGER PRIMARY KEY, total_stock INTEGER);
        ''')
        self.pool = {'count': 206, 'segments': [{'index': 0, 'userId': 1000, 'count': 60},
                                             {'index': 60, 'userId': 10000, 'count': 146}]}
        self.config = {'scheduled': 205, 'activityId': 20, 'path': 'B'}
        self.db.executemany('INSERT INTO t_user VALUES (?,?)',
                            [(user_id(self.pool, i), POINTS) for i in range(self.pool['count'])])
        self.db.execute('INSERT INTO t_user VALUES (99,77)')
        self.db.execute("INSERT INTO t_order VALUES ('history',99,7,1,2,100,2,NOW())")
        self.db.execute('INSERT INTO t_seckill_activity VALUES (20,490)')
        self.db.commit()
        self.connections = []

    def tearDown(self): self.db.close()

    def connect(self, **options):
        self.assertEqual({'autocommit': False}, options)
        value = Connection(self.db)
        self.connections.append(value)
        return value

    def scalar(self, query): return self.db.execute(query).fetchone()[0]

    def test_batches_preserve_mix_balances_stock_history_and_warmup_user_across_id_gaps(self):
        with patch('transport.mysql_connection', side_effect=self.connect), \
                patch('transport.redis_command') as redis:
            for start in range(0, 205, ORDER_BATCH):
                prepare_mixed_batch(self.config, self.pool, 7, 2, start, min(start+ORDER_BATCH, 205), Mock())
        self.assertEqual(185, self.scalar('SELECT COUNT(*) FROM t_order WHERE activity_id=20'))
        self.assertEqual(185, self.scalar('SELECT COUNT(*) FROM t_point_log'))
        self.assertEqual(305, self.scalar('SELECT total_stock FROM t_seckill_activity WHERE id=20'))
        for i in range(206):
            expected = POINTS - PRICE if i < 205 and i % 10 != 9 else POINTS
            self.assertEqual(expected, self.scalar(f'SELECT points FROM t_user WHERE id={user_id(self.pool,i)}'))
        self.assertEqual(185, self.scalar(f'SELECT COUNT(*) FROM t_point_log WHERE delta={-PRICE} '
                                         f'AND balance_after={POINTS-PRICE} AND biz_type=2'))
        self.assertEqual(1, self.scalar("SELECT COUNT(*) FROM t_order WHERE order_id='history'"))
        self.assertEqual(77, self.scalar('SELECT points FROM t_user WHERE id=99'))
        bought = [uid for call in redis.call_args_list for uid in call.args[2:]]
        self.assertEqual([user_id(self.pool,i) for i in range(205) if i%10!=9], bought)
        self.assertTrue(all(c.closed for c in self.connections))

    def test_ledger_failure_rolls_back_orders_balances_stock_and_never_updates_redis(self):
        self.db.execute("CREATE TRIGGER fail_ledger BEFORE INSERT ON t_point_log BEGIN SELECT RAISE(ABORT,'ledger failed'); END")
        with patch('transport.mysql_connection', side_effect=self.connect), \
                patch('transport.redis_command') as redis, self.assertRaisesRegex(RuntimeError, 'point-logs'):
            prepare_mixed_batch(self.config, self.pool, 7, 2, 0, 100, Mock())
        self.assertEqual(0, self.scalar('SELECT COUNT(*) FROM t_order WHERE activity_id=20'))
        self.assertEqual(0, self.scalar('SELECT COUNT(*) FROM t_user WHERE id<>99 AND points<>10000000'))
        self.assertEqual(490, self.scalar('SELECT total_stock FROM t_seckill_activity WHERE id=20'))
        redis.assert_not_called()

    def test_missing_fixture_user_rejects_and_rolls_back_the_batch(self):
        self.db.execute('DELETE FROM t_user WHERE id=1000')
        self.db.commit()
        with patch('transport.mysql_connection', side_effect=self.connect), \
                patch('transport.redis_command') as redis, self.assertRaisesRegex(RuntimeError, 'balances'):
            prepare_mixed_batch(self.config, self.pool, 7, 2, 0, 100, Mock())
        self.assertEqual(0, self.scalar('SELECT COUNT(*) FROM t_order WHERE activity_id=20'))
        redis.assert_not_called()

    def test_failed_preparation_records_point_step_and_batch_without_a_measurement_result(self):
        def fail(*args):
            args[-1]('point-logs', batchStart=300, batchEnd=400)
            raise RuntimeError('databaseError=2013')
        with tempfile.TemporaryDirectory() as temp, patch('fixtures._prepare', side_effect=fail):
            directory = Path(temp)
            with self.assertRaisesRegex(RuntimeError, r'phase=point-logs, batch=\[300,400\)'):
                prepare({}, 7, directory, directory, 'private-token', Mock(), Mock())
            value = json.loads((directory / 'preparation.json').read_text())
            self.assertEqual('failed', value['status'])
            self.assertEqual('point-logs', value['phase'])
            self.assertGreaterEqual(value['elapsedSeconds'], 0)
            self.assertNotIn('private-token', (directory / 'preparation.json').read_text())
            self.assertFalse((directory / 'result.json').exists())

    def test_commit_connection_loss_never_replays_the_batch_or_masks_original_failure(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.rowcount = 1
        connection.commit.side_effect = OSError(2013, 'Lost connection')
        connection.rollback.side_effect = OSError(2013, 'Connection already closed')
        with patch('transport.mysql_connection', return_value=connection) as connect:
            with self.assertRaisesRegex(RuntimeError, 'at commit.*databaseError=2013.*no automatic retry'):
                fixture_transaction([('orders', 'INSERT INTO t_order VALUES (%s)', (7,), 1)], Mock())
        connect.assert_called_once_with(autocommit=False)
        cursor.execute.assert_called_once()
        connection.commit.assert_called_once()
        connection.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
