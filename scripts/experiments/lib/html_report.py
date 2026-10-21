"""Render one independent experiment, including partial and failed evidence."""
import argparse
from datetime import datetime, timezone
import json
import os
import hashlib
import re
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
    commit=actions.get('commit') or ''
    repository=actions.get('repository') or ''
    commit_label=text(commit[:12] or '未采集')
    if re.fullmatch(r'[0-9a-f]{40}',commit) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):
        commit_label=f'<a href="https://github.com/{repository}/commit/{commit}">{commit_label}</a>'
    body.append('<p class="site-meta">Actions '+text(run_id)+' · Attempt '+text(attempt)+' · 源码 '+commit_label+'</p>')
    if suite.get('error'): body.append('<p class="warning">'+text(suite['error'])+'</p>')
    if scope == 'validation': body.append('<p>流程验证，不输出性能收益结论。</p>')
    if status == 'inconclusive': body.append('<p>本次未形成完整有效对照，请查看预热或测量诊断；不输出完整收益结论。</p>')
    stages = [Experiment(p.parent, scope) for p in sorted(reports.glob('*/*/manifest.json')) if p.parent.parent.name == mode]
    for experiment in stages: body.append(experiment_report(experiment))
    if not stages: body.append('<p>尚无正式测量证据。</p>')
    for path in sorted(reports.glob('preflight/*/preflight.json')):
        value=read_json(path,{})
        if value.get('status')!='completed': body.append('<p class="warning">启动检查未通过：'+text(value.get('error','请查看证据包'))+'</p>')
    generator=read_json(reports/'generator-check/check.json',{})
    if generator and generator.get('status')!='passed':
        body.append('<p class="warning">当前机器发压验证失败：'+text(generator.get('error','输入未通过核验'))+'</p>')
    timing=read_json(reports/'timings.json',[])
    if timing:
        body.append('<details class="observations"><summary>本次运行耗时</summary>'+table(['阶段','秒','状态'],[[r['stage'],r['seconds'],r['status']] for r in timing])+'</details>')
    body.append(environment_report(environment))
    measured_at=next((e.meta.get('startedAtMs') for e in stages if e.meta.get('startedAtMs')),None)
    renderer_hash=hashlib.sha256(b''.join(p.read_bytes() for p in sorted(Path(__file__).parent.glob('*')) if p.is_file() and p.suffix in ('.py','.css','.js'))).hexdigest()
    body.append('<p class="site-meta">原始测量时间：'+text(datetime.fromtimestamp(measured_at/1000,timezone.utc).isoformat() if measured_at else '未采集')+' · 报告生成器版本：'+renderer_hash[:12]+'。重新生成页面不会重跑或改写原始实验。</p>')
    sections=[('performance','数据库削峰' if mode=='kafka-burst' else '性能对照')]+([('cost','完成代价'),('timeline','突发过程')] if mode=='kafka-burst' else [])+[('validity','数据检查'),('observations','详细数据'),('environment','实验环境')]
    document=page('闪铺实验报告', ''.join(body), sidebar([(key,label,f'{i:02d}') for i,(key,label) in enumerate(sections,1)], actions.get('runUrl')))
    private=Path(os.environ.get('BENCH_RUNTIME','/bench/runtime'))/'sut.env'
    if private.exists():
        for line in private.read_text().splitlines():
            if '=' in line:
                secret=line.split('=',1)[1]
                if secret: document=document.replace(secret,'[REDACTED]').replace(text(secret),'[REDACTED]')
    (output/'index.html').write_text(document)
    summary={'id':f'{run_id}-{attempt}','runId':run_id,'attempt':attempt,'status':status,'scope':scope,'mode':mode,
             'measuredAtMs':measured_at,'rendererSha256':renderer_hash,'commit':actions.get('commit'),'runUrl':actions.get('runUrl'),'createdAt':datetime.now(timezone.utc).isoformat()}
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
