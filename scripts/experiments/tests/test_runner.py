"""Dropped arrival evidence must survive disabled k6 metric aggregation."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from runner import verify_k6_load


class LoadEvidenceTest(unittest.TestCase):
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
