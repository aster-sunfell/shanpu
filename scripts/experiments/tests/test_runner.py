"""Dropped arrival evidence must survive disabled k6 metric aggregation."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from runner import MYSQL_MEMORY_SETTINGS, mysql_settings, verify_k6_load, runner_usage


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


if __name__ == '__main__':
    unittest.main()
