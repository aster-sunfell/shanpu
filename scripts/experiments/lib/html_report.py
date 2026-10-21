"""Render a public offline report from protocol-qualified disk evidence."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from report_data import Stage, read_json
from report_layout import (STYLE, environment_report, heading, methods, overview, page,
                           resources, schemes, sidebar, stage_report, text, panel, experiment_criteria)
from report_charts import empty, fmt as number


def render(reports, output):
    reports, output = Path(reports), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    suite = read_json(reports/'suite.json', {})
    environment = read_json(reports/'environment.json', {})
    actions = suite.get('actions') or environment.get('actions') or {}
    status = suite.get('status', 'failed')
    if status not in ('completed', 'inconclusive'): status = 'failed'
    scope = suite.get('scope', 'validation' if os.environ.get('BENCH_MODE') == 'smoke' else 'performance')
    run_id = str(actions.get('runId') or os.environ.get('GITHUB_RUN_ID', 'unknown'))
    attempt = str(actions.get('attempt') or os.environ.get('GITHUB_RUN_ATTEMPT', '1'))
    stages = []
    for manifest in sorted(reports.glob('*/*/manifest.json')):
        if read_json(manifest, {}).get('stage') != 'preflight':
            stages.append(Stage(manifest.parent, scope))
    body = [overview(suite, stages, actions, status, scope)]
    navigation = [('overview', '本次结论', '◈'), ('schemes', '方案说明', '◇')]
    plans = ['redis-filtering', 'kafka-buffering']
    selected_plan = {'redis': 'redis-filtering', 'kafka': 'kafka-buffering'}.get(suite.get('mode'), suite.get('mode'))
    if selected_plan in plans: plans = [selected_plan]
    paths = ['B', 'C'] if plans == ['kafka-buffering'] else ['A', 'B'] if plans == ['redis-filtering'] else ['A', 'B', 'C']
    body.append(schemes(paths))
    if not stages:
        body.append('<p class="summary-note">环境准备阶段未产生测量点，没有性能数据。请查看 Actions 日志和 diagnostics。</p>')
    known = []
    for plan in plans:
        group = [s for s in stages if s.plan == plan]
        known.extend(group)
        key, title, condition, index = (
            ('ab', 'Redis 过滤收益', '10% 有效购买 / 90% 已购重复请求 · 数据库同步 → Redis 同步 · 成功响应均为 PAID', '01')
            if plan == 'redis-filtering' else
            ('bc', 'Kafka 异步化收益与代价', '100% 有效购买 · Redis 同步 → Kafka 异步 · PAID / PENDING 分别统计', '02'))
        navigation.append((key, 'Redis 过滤收益' if key == 'ab' else 'Kafka 异步化收益', index))
        body.append(f'<section id="{key}">'+heading(title, condition, index))
        body.append(experiment_criteria(plan,group))
        capacity = next((s for s in group if s.kind == 'capacity'), None)
        if capacity:
            body.append(stage_report(capacity))
        else:
            body.append(panel('稳定完成容量' if key == 'bc' else '延迟与混合流量容量', '未产生本阶段的正式比较结果',
                              empty('本阶段未执行或证据缺失', '已有阶段的状态不替代本阶段的性能测量。'), '未生成提升比例。'))
        if key == 'bc':
            burst = next((s for s in group if s.kind == 'burst'), None)
            body.append(stage_report(burst) if burst else panel('突发按时完成量与积压', '等待本次合格的 Redis 同步容量基线',
                        empty('突发实验未执行或证据缺失', '完整测量后，分别展示按时完成量、积压峰值与排空时间。'), '缺失数据不显示为零订单或零积压。'))
        body.append('</section>')
    for index, stage in enumerate(s for s in stages if s not in known):
        key = f'other-{index}'
        navigation.append((key, stage.plan, '·'))
        body.append(f'<section id="{key}">'+heading(stage.plan)+stage_report(stage)+'</section>')
    navigation.extend([('environment', '实验环境', '03'), ('methods', '方法与说明', '↳')])
    body.append(environment_report(environment))
    body.append(methods(stages))
    document = page('闪铺压测报告', ''.join(body), sidebar(navigation, actions.get('runUrl')))
    private = Path(os.environ.get('BENCH_RUNTIME', '/bench/runtime'))/'sut.env'
    if private.exists():
        for line in private.read_text().splitlines():
            if '=' in line:
                secret = line.split('=', 1)[1]
                if secret: document = document.replace(secret, '[REDACTED]').replace(text(secret), '[REDACTED]')
    (output/'index.html').write_text(document)
    summary = {'id': f'{run_id}-{attempt}', 'runId': run_id, 'attempt': attempt, 'status': status, 'scope': scope,
               'mode': suite.get('mode', 'unknown'), 'commit': actions.get('commit'), 'runUrl': actions.get('runUrl'),
               'createdAt': datetime.now(timezone.utc).isoformat(), 'stages': len(stages)}
    (output/'report.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    summary_path = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary_path:
        with Path(summary_path).open('a') as file:
            file.write(f'## 压测报告\n\n状态：**{status}**；用途：**{scope}**。\n\n下载本次 `performance-html` artifact，打开 `index.html`。完整逐笔证据位于 `performance-evidence` artifact。\n')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reports', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    render(args.reports, args.output)
