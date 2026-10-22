"""Actual scheme names, visible adverse rounds, no raw diagnostic dump or cross-run reuse."""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from html_report import render
from report_data import Experiment, burst_recovery
from report_metrics import summarize
from protocol import comparisons, parse_args
import test_html_report


class RedesignTest(unittest.TestCase):
    fixture=test_html_report.ReportTest.fixture

    def test_scheme_headers_and_metrics_are_specific_to_selected_experiment(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.fixture(root,'redis-filtering');self.fixture(root,'kafka-burst')
            render(root,root/'html')
            html=(root/'html/index.html').read_text()
            self.assertIn('<th>Redis 同步下单</th><th>Kafka 异步下单</th>',html)
            self.assertNotIn('<th>前方案中位数',html)
            self.assertNotIn('重复请求拒绝 p99',html)
            self.assertNotIn('重复请求拒绝 p95',html)
            self.assertNotIn('数据库同步下单',html)
            self.assertIn('突发请求完成 p99',html)

    def test_duplicate_p95_uses_recorded_quantiles_and_preserves_missing_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);directory=self.fixture(root)
            for path in directory.glob('*/result.json'):
                row=json.loads(path.read_text())
                values={'A':[10,100,100],'B':[20,101,300]}
                row['classes']['duplicate']['responseMs']['p95']=values[row['path']][row['repeat']-1]
                path.write_text(json.dumps(row))
            # Existing experiment.json has no P95 summary: rerender from point evidence.
            experiment=Experiment(directory,'performance')
            metrics=comparisons(parse_args(['redis-filtering']),experiment.points)[0]['metrics']
            self.assertEqual(10,metrics['duplicateResponseP95Ms']['afterMinusBefore']['p50'])
            self.assertEqual(-100,metrics['duplicateResponseP95Ms']['pairedReductionPct']['p50'])
            render(root,root/'html');html=(root/'html/index.html').read_text()
            self.assertIn('<h3>重复请求拒绝 p95 / ms</h3>',html)
            self.assertIn('<td>重复请求拒绝 p95 / ms</td><td>100</td><td>101</td><td>10</td>',html)
            self.assertIn('第 3 轮 · Redis 过滤后同步下单: 300',html)
            path=next(directory.glob('*/result.json'));row=json.loads(path.read_text())
            del row['classes']['duplicate']['responseMs']['p95'];path.write_text(json.dumps(row))
            experiment=Experiment(directory,'performance')
            self.assertNotIn('duplicateResponseP95Ms',comparisons(parse_args(['redis-filtering']),experiment.points)[0]['metrics'])
            render(root,root/'html');html=(root/'html/index.html').read_text()
            self.assertNotIn('<td>重复请求拒绝 p95 / ms</td>',html)

    def test_missing_timeline_does_not_hide_other_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);directory=self.fixture(root,'kafka-burst')
            for file in directory.glob('*/timeline.csv'):file.write_text('')
            render(root,root/'html');html=(root/'html/index.html').read_text()
            self.assertIn('时间线缺失',html)
            self.assertIn('数据与质量检查',html)

    def test_slow_third_round_remains_visible_without_legacy_deadlines(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);directory=self.fixture(root,'kafka-burst')
            for path in directory.glob('*/result.json'):
                row=json.loads(path.read_text());row['burst']['completionMs']['p99']=5310.02 if row['path']=='C' and row['repeat']==3 else 1000
                path.write_text(json.dumps(row))
            render(root,root/'html');html=(root/'html/index.html').read_text()
            self.assertIn('第 3 轮 · Kafka 异步下单: 5,310.02',html)
            self.assertNotIn('突发请求按时受理率',html)
            self.assertNotIn('突发请求按时完成率',html)
            self.assertNotIn('期限 5,000',html)
            self.assertIn('data-round-select',html)
            for i in (1,2,3):self.assertIn(f'data-round="{i}"',html)

    def test_diagnostics_are_summarized_and_anomalies_are_not_hidden(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);directory=self.fixture(root)
            (directory/'warmup.json').write_text(json.dumps({'status':'completed','internalDetail':'private-raw-detail'}))
            (directory/'containers-final.json').write_text(json.dumps({'containers':[{'service':'backend','restartCount':1,'state':{'Running':True}}]}))
            render(root,root/'html');html=(root/'html/index.html').read_text()
            self.assertIn('容器异常：backend',html)
            self.assertIn('预热：通过',html)
            for label in ('warmup.json','containers-final.json','private-raw-detail','启动前核验','容器资源采样','实验配置与源码'):
                self.assertNotIn(label,html)

    def test_plan_mismatch_is_rejected_and_duplicate_pairs_cannot_claim_gains(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);directory=self.fixture(root)
            files=sorted(directory.glob('*/result.json'))
            first=json.loads(files[0].read_text())
            other={**first,'plan':'kafka-burst'};files[0].write_text(json.dumps(other))
            with self.assertRaisesRegex(ValueError,'another experiment'):Experiment(directory,'performance')
            files[0].write_text(json.dumps(first));files[1].write_text(json.dumps(first))
            self.assertEqual([],Experiment(directory,'performance').comparisons)

    def test_pair_difference_is_not_difference_of_medians(self):
        value=summarize([(1,10,20),(2,100,101),(3,100,300)])
        self.assertEqual(10,value['difference'])
        self.assertEqual(1,value['after']-value['before'])

    def test_recovery_rebuild_is_read_only_and_cannot_treat_missing_commits_as_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db=sqlite3.connect(root/'evidence.sqlite')
            db.executescript('''CREATE TABLE starts(rid TEXT,uid INTEGER,kind TEXT,e REAL);
                CREATE TABLE after_orders(oid TEXT,uid INTEGER); CREATE TABLE before_orders(oid TEXT);
                CREATE TABLE commits(oid TEXT,t REAL);
                INSERT INTO starts VALUES('burst:0',1,'eligible',61000),('baseline:0',2,'eligible',61000);
                INSERT INTO after_orders VALUES('1',1),('2',2);
                INSERT INTO commits VALUES('1',35000),('2',60000);''');db.close()
            row={'burst':{'requests':1,'completed':1},'cutoffMs':62000,'recoverySeconds':30}
            before=(root/'evidence.sqlite').read_bytes()
            self.assertEqual(4000,burst_recovery(root,row))
            self.assertEqual(before,(root/'evidence.sqlite').read_bytes())
            row['burst']['requests']=2
            self.assertIsNone(burst_recovery(root,row))

    def test_rerender_records_renderer_and_retains_measurement_provenance(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);directory=self.fixture(root)
            meta=json.loads((directory/'manifest.json').read_text());meta['startedAtMs']=1000000
            (directory/'manifest.json').write_text(json.dumps(meta))
            before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
            result=render(root,root/'html')
            self.assertEqual(1000000,result['measuredAtMs']);self.assertEqual(64,len(result['rendererSha256']))
            self.assertEqual(before,{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in before})

if __name__=='__main__':unittest.main()
