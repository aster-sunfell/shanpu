"""Independent observation must remain bounded and reject stale or replaced evidence."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
import observation
import runner


class ObservationTest(unittest.TestCase):
    def test_idle_and_final_collection_wait_for_a_snapshot_after_their_work(self):
        previous, current = {'timeMs': 1999}, {'timeMs': 2000}
        with patch('observation.time.time', return_value=2), \
                patch('observation.time.monotonic', return_value=0), patch('observation.time.sleep') as sleep, \
                patch('observation.metrics', side_effect=[previous, current]):
            self.assertEqual(current, observation.fresh_metrics())
            sleep.assert_called_once()

    def test_publisher_that_stops_updating_is_rejected_before_using_old_counters(self):
        with patch('observation.time.time', return_value=2), \
                patch('observation.time.monotonic', side_effect=[0, 0, 6]), patch('observation.time.sleep'), \
                patch('observation.metrics', return_value={'timeMs': 1999}), \
                self.assertRaisesRegex(RuntimeError, 'fresh snapshot'):
            observation.fresh_metrics(timeout=5)

    def test_metrics_bypass_saturated_http_and_reject_stale_wrong_or_partial_snapshots(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'BENCH_EVIDENCE': temp, 'SUT_INSTANCE_ID': 'new'}), \
                patch('runner.urllib.request.urlopen', side_effect=TimeoutError('business connector full')) as http:
            path = Path(temp)/'metrics.json'
            value = {'timeMs': int(time.time()*1000), 'settings': {'sutId': 'new'}}
            path.write_text(json.dumps(value))
            self.assertEqual(value, runner.api('/admin/experiments/metrics'))
            http.assert_not_called()
            for changed in ({**value, 'timeMs': 0}, {**value, 'settings': {'sutId': 'old'}}, {}):
                path.write_text(json.dumps(changed))
                with self.assertRaises(RuntimeError): observation.metrics()
            path.write_text('{"timeMs":')
            with self.assertRaises(RuntimeError): observation.metrics()

    def test_commit_cursor_filters_activities_bounds_pages_and_waits_for_complete_lines(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'BENCH_EVIDENCE': temp}):
            path = Path(temp)/'commits.jsonl'
            self.assertEqual([], observation.commits(0, 1)['events'])
            rows = [{'activityId': 1 if i%2 else 2, 'orderId': str(i)} for i in range(2500)]
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
            first = observation.commits(0, 1)
            self.assertEqual(1000, len(first['events']))
            self.assertTrue(first['hasMore'])
            second = observation.commits(first['nextOffset'], 1)
            self.assertEqual(250, len(second['events']))
            self.assertFalse(second['hasMore'])
            with path.open('a') as file: file.write('{"activityId":1,"orderId":"last"}')
            partial = observation.commits(second['nextOffset'], 1)
            self.assertEqual(second['nextOffset'], partial['nextOffset'])
            self.assertTrue(partial['partialTail'])
            self.assertFalse(partial['hasMore'])  # Final collection cannot spin forever.
            with path.open('a') as file: file.write('\n')
            self.assertEqual('last', observation.commits(partial['nextOffset'], 1)['events'][0]['orderId'])
            path.write_text('')
            with self.assertRaisesRegex(RuntimeError, 'truncated'): observation.commits(second['nextOffset'], 1)

    def test_metrics_and_journal_metadata_remain_bounded(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'BENCH_EVIDENCE': temp}):
            path = Path(temp)/'metrics.json'
            path.write_text(' '*(observation.MAX_METADATA_BYTES+1))
            with self.assertRaisesRegex(RuntimeError, '2 MiB'): observation.metrics()
            path = Path(temp)/'commits.jsonl'
            path.write_text(' '*(observation.MAX_METADATA_BYTES//2+1))
            with self.assertRaisesRegex(RuntimeError, '1 MiB'): observation.commits(0, 1)


if __name__ == '__main__': unittest.main()
