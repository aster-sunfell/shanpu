"""Dropped arrival evidence must survive disabled k6 metric aggregation."""
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from analysis import Evidence
from runner import MYSQL_MEMORY_SETTINGS, mysql_settings, verify_k6_load, runner_usage, orders_drained


class LoadEvidenceTest(unittest.TestCase):
    def test_load_container_records_its_own_resource_counters(self):
        counters = {'memory.current': '123456', 'cpu.stat': 'usage_usec 123'}
        with patch('runner.container_resources', return_value=counters):
            sample = runner_usage()
            self.assertEqual(counters, sample['containerResources'])
            self.assertGreater(sample['rssKiB'], 0)

    def test_database_profile_records_only_expected_server_variables(self):
        values = {'version': '8.0.46', **MYSQL_MEMORY_SETTINGS}
        with patch('runner.sql', return_value='\n'.join(f'{k}\t{v}' for k, v in values.items())):
            self.assertEqual(values, mysql_settings())

    def test_database_profile_rejects_missing_or_different_memory_limits_before_measurement(self):
        values = {'version': '8.0.46', **MYSQL_MEMORY_SETTINGS}
        for change in ({'performance_schema': 'ON'}, {'temptable_max_ram': '1073741824'},
                       {'innodb_buffer_pool_size': '67108864'}, {'version': '8.0.45'},
                       {'thread_cache_size': None}):
            modified = {**values, **change}
            with self.subTest(change=change), \
                    patch('runner.sql', return_value='\n'.join(f'{k}\t{v}' for k, v in modified.items() if v is not None)), \
                    self.assertRaises(RuntimeError):
                mysql_settings()

    def verify(self, output, exit_code=0, **changes):
        result = {'scheduled': 3350, 'started': 3350, 'loadErrors': [],
                  'valid': True, 'sloPassed': True, **changes}
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / 'k6.out'
            log.write_text(output)
            verify_k6_load(result, log, exit_code)
        return result

    def test_vu_drops_are_invalid_even_if_boundary_iteration_restores_total(self):
        result = self.verify('Insufficient VUs, reached 20 active VUs and cannot initialize more\n')
        self.assertEqual(result['scheduled'], result['started'])
        self.assertTrue(result['vuExhausted'])
        self.assertTrue(result['loadErrors'])
        self.assertFalse(result['valid'])
        self.assertFalse(result['sloPassed'])

    def test_process_failure_is_invalid_without_a_vu_warning(self):
        result = self.verify('script interrupted\n', exit_code=1)
        self.assertFalse(result['vuExhausted'])
        self.assertIn('k6 exit 1', result['loadErrors'])
        self.assertFalse(result['valid'])

    def test_clean_exit_preserves_existing_load_and_slo_failures(self):
        result = self.verify('', loadErrors=['Offered load not achieved'], valid=False, sloPassed=False)
        self.assertEqual(['Offered load not achieved'], result['loadErrors'])
        self.assertFalse(result['valid'])
        self.assertFalse(result['sloPassed'])
        result = self.verify('', sloPassed=False)
        self.assertTrue(result['valid'])
        self.assertFalse(result['sloPassed'])


class DrainEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.evidence = Evidence(self.temp.name)
        self.evidence.db.execute("INSERT INTO starts VALUES ('burst:0',0,7,1000,2000,'eligible',1000)")
        self.evidence.db.execute("INSERT INTO finishes VALUES ('burst:0',0,1000,1100,'timeout',100,NULL)")
        self.journal = SimpleNamespace(has_more=False, partial_tail=False)
        self.metrics = {'poolActive':0, 'poolWaitThreads':0, 'transactionPressure':{'active':0}, 'writeErrors':0}

    def tearDown(self):
        self.evidence.close()
        self.temp.cleanup()

    def commit(self, oid='900', aid=12, uid=7, stamp=2100):
        self.evidence.add_commits([{'orderId':oid, 'activityId':aid, 'userId':uid, 'commitObservedAtMs':stamp}])

    def drained(self):
        return orders_drained(self.evidence, self.journal, self.metrics, 12)

    def test_failed_response_stops_blocking_as_soon_as_its_commit_is_observed(self):
        for outcome in ('timeout','network_error','internal_error'):
            with self.subTest(outcome=outcome):
                self.evidence.db.execute('DELETE FROM commits')
                self.evidence.db.execute('UPDATE finishes SET outcome=?', (outcome,))
                self.assertEqual(0, self.evidence.pending_accepted())
                self.assertEqual(1, self.evidence.pending_ambiguous(12))
                self.assertFalse(self.drained())
                self.commit()
                self.assertEqual(0, self.evidence.pending_ambiguous(12))
                self.assertTrue(self.drained())
                self.assertEqual(outcome, self.evidence.scalar('SELECT outcome FROM finishes'))

    def test_unconfirmed_timeout_is_not_resolved_by_another_activity_user_or_old_commit(self):
        self.commit(oid='other-activity', aid=13)
        self.commit(oid='other-user', uid=8)
        self.commit(oid='old-commit', stamp=900)
        self.assertEqual(1, self.evidence.pending_ambiguous(12))
        self.assertFalse(self.drained())
        self.commit()
        self.assertTrue(self.drained())

    def test_resolved_timeouts_do_not_hide_pending_acceptance_or_busy_backend(self):
        self.commit()
        self.evidence.db.execute("INSERT INTO starts VALUES ('recovery:0',1,8,2000,3000,'eligible',2000)")
        self.evidence.db.execute("INSERT INTO finishes VALUES ('recovery:0',1,2000,2010,'accepted',10,'901')")
        self.assertFalse(self.drained())
        self.commit(oid='901', uid=8)
        self.assertTrue(self.drained())
        for key in ('poolActive','poolWaitThreads','writeErrors'):
            with self.subTest(key=key):
                self.metrics[key] = 1
                self.assertFalse(self.drained())
                self.metrics[key] = 0
        self.metrics['transactionPressure']['active'] = 1
        self.assertFalse(self.drained())
        self.metrics['transactionPressure']['active'] = 0
        for key in ('has_more','partial_tail'):
            with self.subTest(key=key):
                setattr(self.journal, key, True)
                self.assertFalse(self.drained())
                setattr(self.journal, key, False)


if __name__ == '__main__':
    unittest.main()
