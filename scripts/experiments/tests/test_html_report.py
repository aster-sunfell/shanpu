"""Public report rendering must preserve measurement validity and archive history."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from html_report import render, stage_report
from report_site import merge_report


class HtmlReportTest(unittest.TestCase):
    def test_failed_preparation_and_terminal_mysql_state_render_without_capacity_claims(self):
        with tempfile.TemporaryDirectory() as temp:
            stage = Path(temp)
            point = stage / '020-r1-mixed-200-B'
            point.mkdir()
            (stage / 'manifest.json').write_text(json.dumps({'status': 'failed'}))
            (point / 'preparation.json').write_text(json.dumps({'status': 'failed', 'phase': 'point-logs',
                 'batchStart': 300, 'batchEnd': 400, 'error': '<database error>'}))
            (stage / 'containers-final.json').write_text(json.dumps({'containers': [
                {'service': 'mysql', 'state': {'OOMKilled': True, 'ExitCode': 137}}]}))
            (stage / 'mysql-settings.json').write_text(json.dumps({'version': '8.0.46', 'performance_schema': 'OFF'}))
            html = stage_report(stage)
            self.assertIn('数据准备未完成', html)
            self.assertIn('point-logs', html)
            self.assertIn('结束时容器状态', html)
            self.assertIn('OOMKilled', html)
            self.assertIn('MySQL 实际配置与版本', html)
            self.assertIn('8.0.46', html)
            self.assertIn('&lt;database error&gt;', html)
            self.assertNotIn('容量增幅', html)

    def report(self, root, run, status='completed', scope='performance', error=None):
        reports = root / f'evidence-{run}'
        reports.mkdir()
        value = {'status': status, 'scope': scope, 'mode': 'full', 'error': error,
                 'actions': {'runId': str(run), 'attempt': '1', 'commit': 'test-sha',
                             'runUrl': f'https://github.com/aster-sunfell/shanpu/actions/runs/{run}'}}
        (reports / 'suite.json').write_text(json.dumps(value))
        output = root / f'html-{run}'
        render(reports, output)
        return output

    def test_offline_html_has_no_cdn_and_escapes_errors_and_redacts_credentials(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'sut.env').write_text('JWT_SECRET=disposable-secret\n')
            with patch.dict(os.environ, {'BENCH_RUNTIME': str(root)}):
                output = self.report(root, 1, 'failed', error='<script>alert(1)</script> disposable-secret')
            text = (output / 'index.html').read_text()
            self.assertNotIn('<script>alert(1)</script>', text)
            self.assertNotIn('disposable-secret', text)
            self.assertIn('&lt;script&gt;', text)
            self.assertIn('[REDACTED]', text)
            self.assertNotIn('<script src=', text)
            self.assertNotIn('<link ', text)
            self.assertIn('没有性能数据', text)

    def test_failed_report_does_not_replace_last_complete_formal_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / 'site'
            merge_report(self.report(root, 1), archive, keep=1)
            merge_report(self.report(root, 2, 'failed'), archive, keep=1)
            merge_report(self.report(root, 3, scope='validation'), archive, keep=1)
            html = (archive / 'index.html').read_text()
            self.assertIn('runs/1-1/index.html', html)
            self.assertIn('runs/3-1/index.html', html)
            self.assertTrue((archive / 'runs/1-1/index.html').is_file())
            self.assertFalse((archive / 'runs/2-1').exists())
            self.assertFalse((archive / 'runs/1-1/suite.json').exists())

    def test_unfinished_run_is_marked_failed_and_validation_is_labelled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = self.report(root, 1, status='running', scope='validation')
            value = json.loads((output / 'report.json').read_text())
            self.assertEqual('failed', value['status'])
            html = (output / 'index.html').read_text()
            self.assertIn('流程验证', html)
            self.assertIn('运行未完整完成', html)

    def test_archive_rejects_path_traversal_in_run_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = self.report(root, 1)
            (output / 'report.json').write_text(json.dumps({'id': '../../outside'}))
            with self.assertRaises(ValueError):
                merge_report(output, root / 'site')

    def test_validation_stage_never_emits_capacity_claims(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = self.report(root, 1, scope='validation')
            reports = root / 'evidence-1'
            stage = reports / 'kafka-buffering' / 'capacity'
            stage.mkdir(parents=True)
            (stage / 'manifest.json').write_text(json.dumps({'status': 'completed', 'plan': 'kafka-buffering', 'stage': 'capacity'}))
            (stage / 'capacity.json').write_text(json.dumps({'status': 'completed', 'scope': 'validation',
                'capacityGainPct': 999, 'paths': {'B': {'passingRps': 5, 'bounded': False}}}))
            render(reports, output)
            html = (output / 'index.html').read_text()
            self.assertNotIn('稳定容量区间', html)
            self.assertNotIn('至少 5 RPS', html)
            self.assertNotIn('容量增幅：', html)

    def test_measured_rows_timeline_and_resource_peaks_render_without_recomputing_claims(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = self.report(root, 1)
            reports = root / 'evidence-1'
            stage = reports / 'redis-filtering' / '1-capacity'
            point = stage / '001-r1-mixed-100-A'
            point.mkdir(parents=True)
            (stage / 'manifest.json').write_text(json.dumps({'plan': 'redis-filtering', 'stage': 'capacity', 'status': 'completed'}))
            (stage / 'capacity.json').write_text(json.dumps({'status': 'completed', 'scope': 'performance', 'paths': {
                'A': {'passingRps': 100, 'failingRps': None, 'bounded': False}}}))
            result = {'path': 'A', 'rate': 100, 'repeat': 1, 'classes': {'eligible': {'responseMs': {'p99': 12, 'samples': 1000}}},
                      'completedWithinDeadline': 99, 'actualRequestRps': 100, 'completionMs': {'p99': 15},
                      'peakPendingAccepted': 2, 'valid': True, 'sloPassed': True}
            (point / 'result.json').write_text(json.dumps(result))
            (point / 'timeline.csv').write_text('second,started,completed,outstanding,pendingAccepted\n0,10,9,1,1\n1,10,11,0,0\n')
            watch = stage / 'host-watch'
            watch.mkdir()
            (watch / 'samples.jsonl').write_text(json.dumps({'containers': [{'service': 'backend', 'resources': {
                'memory.current': str(32*1048576), 'cpu.stat': 'usage_usec 100\nnr_throttled 2'}}]}) + '\n')
            render(reports, output)
            html = (output / 'index.html').read_text()
            self.assertIn('至少 100 RPS', html)
            self.assertIn('001-r1-mixed-100-A', html)
            self.assertIn('32.00', html)
            self.assertIn('测量时间线', html)
            self.assertNotIn('容量增幅：', html)


if __name__ == '__main__':
    unittest.main()
