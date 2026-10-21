import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from html_report import render
from report_site import merge_report
from protocol import report, policy
from test_protocol import rows


class ReportTest(unittest.TestCase):
    def fixture(self, root, plan='redis-filtering', status='completed', scope='performance'):
        args, results=rows(plan)
        directory=root/plan/'run'
        directory.mkdir(parents=True)
        meta={'status':status,'scope':scope,'plan':plan}
        (directory/'manifest.json').write_text(json.dumps(meta))
        report(directory,meta,results,args)
        for name,value in results:
            point=directory/name
            point.mkdir()
            (point/'result.json').write_text(json.dumps(value))
            (point/'timeline.csv').write_text('second,started,accepted,completed,pendingAccepted,outstanding\n0,100,100,90,10,10\n1,100,100,110,0,0\n')
        (root/'suite.json').write_text(json.dumps({'mode':plan,'status':status,'scope':scope,'actions':{'runId':'123','attempt':'1'}}))
        return directory

    def test_one_experiment_offline_charts_show_real_paired_metrics_and_negative_results(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.fixture(root,'kafka-burst')
            render(root,root/'html')
            html=(root/'html/index.html').read_text()
            self.assertIn('Kafka 削峰实验报告',html)
            self.assertIn('后方案减前方案：-100',html)
            self.assertIn('订单完成',html)
            self.assertNotIn('每千请求业务 SQL 调用',html)
            self.assertNotIn('<script src=',html)
            self.assertNotIn('容量基线',html)

    def test_partial_and_validation_hide_claims_but_keep_measurements(self):
        for status,scope in [('inconclusive','performance'),('failed','performance'),('completed','validation')]:
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                self.fixture(root,status=status,scope=scope)
                render(root,root/'html')
                html=(root/'html/index.html').read_text()
                self.assertNotIn('后方案减前方案：-900',html)
                self.assertIn('尚无完整有效比较',html)
                self.assertIn('错误数',html)

    def test_failure_diagnostics_escape_html_and_redact_runtime_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            d=self.fixture(root,status='failed')
            (d/'measurement-error.json').write_text(json.dumps({'error':'<script>bad</script> private-password'}))
            (root/'sut.env').write_text('PASSWORD=private-password\n')
            with patch.dict(os.environ,{'BENCH_RUNTIME':str(root)}): render(root,root/'html')
            html=(root/'html/index.html').read_text()
            self.assertIn('&lt;script&gt;bad',html)
            self.assertNotIn('private-password',html)
            self.assertIn('[REDACTED]',html)

    def test_archive_keeps_latest_complete_per_experiment_despite_new_failures(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); current=root/'current'; current.mkdir()
            (current/'index.html').write_text('public')
            for run,plan,status in [(1,'redis-filtering','completed'),(2,'kafka-burst','completed'),(3,'redis-filtering','failed'),(4,'kafka-burst','inconclusive')]:
                (current/'report.json').write_text(json.dumps({'id':f'{run}-1','runId':str(run),'attempt':'1','mode':plan,'scope':'performance','status':status}))
                retained=merge_report(current,root/'archive',keep=1)
            self.assertEqual({'1-1','2-1','4-1'},{r['id'] for r in retained})
            html=(root/'archive/index.html').read_text()
            self.assertIn('runs/1-1/index.html',html)
            self.assertIn('runs/2-1/index.html',html)
            (current/'report.json').write_text(json.dumps({'id':'../bad'}))
            with self.assertRaises(ValueError): merge_report(current,root/'archive')

if __name__ == '__main__': unittest.main()
