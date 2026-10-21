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
from report_layout import experiment_criteria
from report_site import merge_report
from protocol import parse_args, report as protocol_report
from report_data import Stage


class HtmlReportTest(unittest.TestCase):
    def capacity_fixture(self, reports, plan='redis-filtering', status='completed', scope='performance'):
        stage=reports/plan/'capacity'
        stage.mkdir(parents=True)
        args=parse_args([plan])
        paths=('A','B') if plan=='redis-filtering' else ('B','C')
        bounds={paths[0]:(100,110),paths[1]:(200,220)}
        rows=[]
        for path in paths:
            for rate in sorted({100,110,200,220}):
                if rate>bounds[path][1]: continue
                for repeat in range(1,4):
                    passed=rate<=bounds[path][0]
                    point=stage/f'{path}-{rate}-r{repeat}'
                    point.mkdir()
                    row={'path':path,'rate':rate,'repeat':repeat,'workload':'mixed' if plan=='redis-filtering' else 'steady',
                         'valid':True,'sloPassed':passed,'classes':{'eligible':{'responseMs':{'p99':100 if path==paths[0] else 60,'samples':1000},'errorRate':0}},
                         'actualRequestRps':rate,'completionMs':{'p99':120},'completionWindowRps':rate,
                         'acceptedWithinResponseDeadline':100,'completedWithinDeadline':100,'completed':100,
                         'peakPendingAccepted':0,'drainMs':0,'seconds':60,'baseSeconds':0,'eligible':100}
                    (point/'result.json').write_text(json.dumps(row))
                    rows.append((point.name,row))
        meta={'plan':plan,'stage':'capacity','status':status,'scope':scope}
        (stage/'manifest.json').write_text(json.dumps(meta))
        protocol_report(stage,meta,rows,args)
        return stage

    def burst_fixture(self, reports, scope='performance', cohort=True):
        stage=reports/'kafka-buffering'/'burst'
        stage.mkdir(parents=True)
        args=parse_args(['kafka-buffering'])
        args.stage='burst';args.q=100
        rows=[]
        for repeat in range(1,4):
            for path in ('B','C'):
                point=stage/f'{path}-200-r{repeat}'
                point.mkdir()
                row={'path':path,'rate':200,'repeat':repeat,'workload':'burst','valid':True,'sloPassed':True,
                     'classes':{'eligible':{'responseMs':{'p99':100,'samples':2000}}},'actualRequestRps':125,
                     'completionMs':{'p99':200},'completionWindowRps':125,'eligible':2500,
                     'acceptedWithinResponseDeadline':1000 if path=='B' else 1800,
                     'completedWithinDeadline':900 if path=='B' else 1500,'completed':1000 if path=='B' else 1800,
                     'peakPendingAccepted':0 if path=='B' else 10,'drainMs':100,'seconds':10,'baseSeconds':10}
                if cohort:
                    row['burst']={'scope':'burst-requests','requests':2000,'acceptedWithinResponseDeadline':800 if path=='B' else 1600,
                                  'completedWithinDeadline':700 if path=='B' else 1200,'completed':800 if path=='B' else 1600}
                (point/'result.json').write_text(json.dumps(row))
                (point/'point.json').write_text(json.dumps({'baseSeconds':10,'seconds':10}))
                (point/'timeline.csv').write_text('second,pendingAccepted\n0,0\n10,0\n20,'+('0' if path=='B' else '10')+'\n21,0\n')
                rows.append((point.name,row))
        meta={'plan':'kafka-buffering','stage':'burst','status':'completed','scope':scope}
        (stage/'manifest.json').write_text(json.dumps(meta))
        protocol_report(stage,meta,rows,args)
        return stage

    def test_preflight_and_reset_are_omitted_from_report_and_stage_count(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = self.report(root, 1, status='failed', error='Measurement did not start')
            reports = root / 'evidence-1'
            stage = reports / 'preflight' / 'run-1'
            stage.mkdir(parents=True)
            (stage/'manifest.json').write_text(json.dumps({'status': 'completed', 'stage': 'preflight',
                                                          'reset': {'status': 'completed', 'emptyRedis': True}}))
            (stage/'preflight.json').write_text(json.dumps({'scope': 'validation', 'status': 'completed', 'dataset': {'seedUsers': 339840}}))
            (stage/'capacity.json').write_text(json.dumps({'scope': 'performance', 'status': 'completed',
                                                          'capacityGainPct': 100, 'paths': {'A': {'passingRps': 200}}}))
            (stage/'memory-checks.jsonl').write_text(json.dumps({'workingMiB': 270, 'headroomMiB': 114})+'\n')
            self.assertEqual('', stage_report(stage))
            render(reports, output)
            html = (output / 'index.html').read_text()
            self.assertNotIn('真实环境预检', html)
            self.assertNotIn('339840', html)
            self.assertNotIn('重建后的环境核验', html)
            self.assertNotIn('emptyRedis', html)
            self.assertNotIn('稳定容量区间', html)
            self.assertNotIn('容量增幅：', html)
            self.assertIn('没有性能数据', html)
            self.assertIn('Measurement did not start', html)
            self.assertEqual(0, json.loads((output / 'report.json').read_text())['stages'])

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

    def test_inconclusive_capacity_preserves_results_and_never_replaces_complete_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root/'site'
            merge_report(self.report(root, 1), archive)
            output = self.report(root, 2, status='inconclusive')
            reports = root/'evidence-2'
            suite = json.loads((reports/'suite.json').read_text())
            suite['skipped'] = [{'stage': 'burst', 'reason': 'No qualified bound <example>'}]
            (reports/'suite.json').write_text(json.dumps(suite))
            render(reports, output)
            self.assertEqual('inconclusive', json.loads((output/'report.json').read_text())['status'])
            self.assertIn('容量边界尚未确定', (output/'index.html').read_text())
            self.assertIn('No qualified bound &lt;example&gt;', (output/'index.html').read_text())
            merge_report(output, archive, keep=1)
            self.assertTrue((archive/'runs/1-1/index.html').exists())
            self.assertIn('runs/1-1/index.html', (archive/'index.html').read_text())

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
            (reports / 'environment.json').write_text(json.dumps({
                'host': {'cpuModel': 'AMD EPYC 9V45 96-Core Processor', 'availableCpus': [0, 1, 2, 3],
                         'memoryBytes': 16760344576, 'kernel': '7.0.0-test', 'platform': 'Linux <test>'},
                'cpuAllocation': {'sut': '0,1', 'runner': '2'},
                'targetBudget': {'cpuLimit': 2, 'containerMemoryMiB': 1440,
                                 'machineMemoryMiB': 2048, 'hostReserveMiB': 608},
                'actions': {'runnerImage': 'ubuntu24', 'runnerImageVersion': '20261018.339.1'}}))
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
            self.assertIn('32 MiB', html)
            self.assertIn('测量时间线', html)
            self.assertNotIn('容量增幅：', html)
            self.assertIn('实验环境', html)
            self.assertIn('<dd>AMD EPYC 9V45 96-Core Processor</dd>', html)
            self.assertIn('<dd>4 vCPU</dd>', html)
            self.assertIn('<dd>15.61 GiB</dd>', html)
            self.assertIn('<dd>2 vCPU</dd>', html)
            self.assertIn('<dd>0,1</dd>', html)
            self.assertIn('<dd>1440 MiB</dd>', html)
            self.assertIn('<dd>608 MiB</dd>', html)
            self.assertIn('ubuntu24 / 20261018.339.1', html)
            self.assertIn('Linux &lt;test&gt;', html)
            self.assertNotIn('运行环境和 CPU 分配', html)
            self.assertNotIn('"cpuModel"', html)
            # Partial historical evidence must not invent missing hardware or budgets.
            (reports / 'environment.json').write_text(json.dumps({'host': {'kernel': '7.0.0-test'}}))
            render(reports, output)
            html = (output / 'index.html').read_text()
            self.assertIn('<dt>CPU 型号</dt><dd>未采集</dd>', html)
            self.assertIn('<dt>被测端 CPU 配额合计</dt><dd>未采集</dd>', html)

    def test_formal_layout_uses_qualified_claims_and_never_imports_preview_constants(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            output=self.report(root,1)
            reports=root/'evidence-1'
            stage=self.capacity_fixture(reports)
            self.capacity_fixture(reports,'kafka-buffering')
            self.burst_fixture(reports)
            render(reports,output)
            html=(output/'index.html').read_text()
            self.assertIn('<h1>压测报告</h1>',html)
            for name in ('数据库同步','Redis 同步','Kafka 异步','方案说明'):
                self.assertIn(name,html)
            self.assertIn('-40.00<small>%</small>',html)
            self.assertIn('双方均达标的最高共同压力 100 RPS',html)
            self.assertIn('从 100 ms 降至 60 ms',html)
            self.assertIn('3 轮降幅中位数为 40%',html)
            self.assertNotIn('配对',html)
            self.assertNotIn('class="context-line"',html)
            self.assertIn('+100.00<small>%</small>',html)
            self.assertIn('+500<small> 单</small>',html)
            self.assertIn('突发段请求',html)
            self.assertNotIn('模拟',html)
            self.assertNotIn('77.1',html)
            self.assertIn('data-timeline-select',html)
            self.assertIn('aria-label="报告导航"',html)
            self.assertNotIn('独立归一化',html)
            self.assertNotIn('<script src=',html)
            self.assertNotIn('<link ',html)
            # A non-monotonic report must never inherit a stale numeric gain.
            artifact=json.loads((stage/'capacity.json').read_text())
            artifact['paths']['B']['nonMonotonic']=True
            (stage/'capacity.json').write_text(json.dumps(artifact))
            self.assertIsNone(Stage(stage).gain)

    def test_partial_and_validation_reports_suppress_claims_without_hiding_valid_lower_bounds(self):
        for status,scope in (('inconclusive','performance'),('failed','performance'),('completed','validation')):
            with self.subTest(status=status,scope=scope),tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                output=self.report(root,1,status=status,scope=scope)
                reports=root/'evidence-1'
                stage=self.capacity_fixture(reports,status=status)
                # Deliberately stale comparison metadata must not bypass stage/suite gates.
                render(reports,output)
                html=(output/'index.html').read_text()
                self.assertNotIn('-40.00<small>%</small>',html)
                self.assertNotIn('+100.00<small>%</small>',html)
                self.assertIn('未生成',html)
                if scope=='performance': self.assertIn('通过 100 RPS',html)
                else:self.assertNotIn('通过 100 RPS',html)

    def test_legacy_burst_totals_are_labelled_and_missing_pairs_never_become_zero_gains(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            output=self.report(root,1)
            reports=root/'evidence-1'
            stage=self.burst_fixture(reports,cohort=False)
            render(reports,output)
            html=(output/'index.html').read_text()
            self.assertIn('整场计数（含低负载段）',html)
            self.assertIn('+600<small> 单</small>',html)
            missing=stage/'C-200-r3'/'result.json'
            value=json.loads(missing.read_text());value['valid']=False
            missing.write_text(json.dumps(value))
            render(reports,output)
            html=(output/'index.html').read_text()
            self.assertNotIn('+600<small> 单</small>',html)
            self.assertIn('暂无完整突发对照',html)

    def test_service_objectives_are_experiment_specific_and_use_actual_recorded_thresholds(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            output=self.report(root,1)
            reports=root/'evidence-1'
            redis=self.capacity_fixture(reports)
            kafka=self.capacity_fixture(reports,'kafka-buffering')
            for stage,response,completion,error in ((redis,350,4000,.002),(kafka,750,6500,.003)):
                file=stage/'capacity.json'
                artifact=json.loads(file.read_text())
                artifact['policy'].update(responseP99Ms=response,completionP99Ms=completion,maxErrorRate=error)
                file.write_text(json.dumps(artifact))
            render(reports,output)
            html=(output/'index.html').read_text()
            redis_section=html.split('<section id="ab">',1)[1].split('<section id="bc">',1)[0]
            kafka_section=html.split('<section id="bc">',1)[1].split('<section id="environment">',1)[0]
            self.assertIn('p99 ≤ 350 ms',redis_section)
            self.assertIn('p99 ≤ 4 秒',redis_section)
            self.assertIn('错误率 ≤ 0.2 %',redis_section)
            self.assertNotIn('p99 ≤ 750 ms',redis_section)
            self.assertIn('重复购买按预期拒绝不算错误',redis_section)
            self.assertIn('p99 ≤ 750 ms',kafka_section)
            self.assertIn('p99 ≤ 6.5 秒',kafka_section)
            self.assertIn('错误率 ≤ 0.3 %',kafka_section)
            self.assertIn('Kafka 异步在消息确认后返回 PENDING',kafka_section)
            self.assertIn('超出服务目标不等于测量无效',kafka_section)
            self.assertNotIn('重复购买按预期拒绝不算错误',kafka_section)
            # Missing metadata is not replaced by the default policy.
            missing=experiment_criteria('redis-filtering',[])
            self.assertIn('未采集',missing)
            self.assertNotIn('500',missing)
            # A changed burst policy remains visible separately from capacity policy.
            burst=self.burst_fixture(reports)
            criteria=experiment_criteria('kafka-buffering',[Stage(kafka),Stage(burst)])
            self.assertIn('稳定容量的服务目标',criteria)
            self.assertIn('突发对照的服务目标',criteria)
            self.assertIn('p99 ≤ 750 ms',criteria)
            self.assertIn('p99 ≤ 500 ms',criteria)

    def test_completed_stage_can_keep_its_claims_when_a_later_stage_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            output=self.report(root,1,status='failed')
            reports=root/'evidence-1'
            self.capacity_fixture(reports)
            render(reports,output)
            html=(output/'index.html').read_text()
            self.assertIn('运行未完整完成',html)
            self.assertIn('-40.00<small>%</small>',html)
            self.assertIn('+100.00<small>%</small>',html)


if __name__ == '__main__':
    unittest.main()
