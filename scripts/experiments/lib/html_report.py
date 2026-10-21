"""Render one independent experiment, including partial and failed evidence."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from report_data import Experiment, read_json
from report_layout import page, heading, sidebar, text, table, experiment_report, environment_report


def render(reports, output, *, summary_path=None):
    reports, output = Path(reports), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    suite = read_json(reports/'suite.json', {})
    environment = read_json(reports/'environment.json', {})
    actions = suite.get('actions') or environment.get('actions') or {}
    run_id = str(actions.get('runId') or os.environ.get('GITHUB_RUN_ID', '0'))
    attempt = str(actions.get('runAttempt') or actions.get('attempt') or os.environ.get('GITHUB_RUN_ATTEMPT', '1'))
    scope, status = suite.get('scope','validation'), suite.get('status','failed')
    if status == 'running':
        status = 'failed'
        suite['error'] = suite.get('error') or 'Workflow ended without a final experiment status'
    mode = suite.get('mode') or os.environ.get('BENCH_EXPERIMENT','unknown')
    titles = {'redis-filtering':'Redis 过滤', 'kafka-burst':'Kafka 削峰'}
    body = ['<header id="overview">'+heading(titles.get(mode,mode)+'实验报告', '一个 run，一个固定负载实验')+
            '<p>运行状态：'+text(status)+'；用途：'+text(scope)+'</p></header>']
    body.append(table(['Actions run','Attempt','Commit'], [[run_id,attempt,actions.get('commit') or '未采集']]))
    if suite.get('error'): body.append('<p class="warning">'+text(suite['error'])+'</p>')
    if scope == 'calibration': body.append('<p>负载校准报告：用于人工选定负载，不输出性能收益，也不覆盖最新正式报告。</p>')
    if scope == 'validation': body.append('<p>流程验证，不输出性能收益结论。</p>')
    if status == 'inconclusive': body.append('<p>有效配对或样本不足，保留证据，不生成完整收益比较。</p>')
    stages = [Experiment(p.parent, scope) for p in sorted(reports.glob('*/*/manifest.json')) if p.parent.parent.name == mode]
    for experiment in stages: body.append(experiment_report(experiment))
    if not stages: body.append('<p>尚无正式测量证据。</p>')
    for path in sorted(reports.glob('preflight/*/preflight.json')):
        body.append('<details><summary>启动前核验</summary><pre>'+text(json.dumps(read_json(path),ensure_ascii=False,indent=2))+'</pre></details>')
    body.append(environment_report(environment))
    body.append('<section id="methods">'+heading('方法与适用范围')+'<p>同一 runner 内交替配对，记录机器型号、镜像、配额和源码。'
                'GitHub Actions 每次机器性能可能不同，跨运行的绝对 RPS 与延迟不可直接归因于代码变化。'
                '固定负载只回答此条件下的收益，不推断最大容量。发压有效性、业务一致性与服务质量分别判定。'
                '原始数据见 performance-evidence artifact；每点 result.json、timeline.csv 和请求/提交证据可追溯。</p></section>')
    document=page('闪铺实验报告', ''.join(body), sidebar([('overview','运行概况','01'),('results','配对结果','02'),
        ('observations','逐轮证据','03'),('environment','实验环境','04'),('methods','方法说明','05')], actions.get('runUrl')))
    private=Path(os.environ.get('BENCH_RUNTIME','/bench/runtime'))/'sut.env'
    if private.exists():
        for line in private.read_text().splitlines():
            if '=' in line:
                secret=line.split('=',1)[1]
                if secret: document=document.replace(secret,'[REDACTED]').replace(text(secret),'[REDACTED]')
    (output/'index.html').write_text(document)
    summary={'id':f'{run_id}-{attempt}','runId':run_id,'attempt':attempt,'status':status,'scope':scope,'mode':mode,
             'commit':actions.get('commit'),'runUrl':actions.get('runUrl'),'createdAt':datetime.now(timezone.utc).isoformat()}
    (output/'report.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    if summary_path is not None:
        with Path(summary_path).open('a') as file:
            file.write(f'## {mode}\n\n状态：{status}；用途：{scope}。下载 performance-html 的 index.html 查看报告，performance-evidence 查看原始证据。\n')
    return summary


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--summary',type=Path,help='Explicit Actions step summary destination; library calls never write job summaries')
    args=parser.parse_args()
    render(args.reports,args.output,summary_path=args.summary)
