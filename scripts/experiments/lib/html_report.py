"""Render public, dependency-free HTML from disk evidence; never recompute claims."""
import argparse
import csv
from datetime import datetime, timezone
from html import escape
import json
import math
import os
from pathlib import Path

STYLE = '''
:root{color-scheme:light;--ink:#172d3c;--muted:#526a79;--line:#d5e0e6;--accent:#096b65}
*{box-sizing:border-box}body{margin:0;background:#f3f6f8;color:var(--ink);font:15px/1.65 system-ui,sans-serif}
main{max-width:1240px;margin:auto;padding:36px 24px}h1{font-size:32px;line-height:1.25}h2{margin-top:32px}
a{color:var(--accent)}section,.card,details{background:white;border:1px solid var(--line);border-radius:10px;padding:20px;margin:16px 0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.card{margin:0}.big{font-size:25px;font-weight:650}
.muted{color:var(--muted)}.warning{background:#fff4df;border-left:5px solid #b97600;padding:14px}.failed{color:#a32e2e}
.scroll{overflow:auto}table{border-collapse:collapse;min-width:760px;width:100%;font-size:14px}td,th{text-align:left;padding:9px;border-bottom:1px solid var(--line)}
th{background:#edf3f6;white-space:nowrap}code{overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6f8;padding:12px}
summary{cursor:pointer;font-weight:600}svg{max-width:100%;height:auto}input{padding:10px;border:1px solid var(--line);border-radius:6px;width:100%;max-width:380px}
footer{margin-top:30px;color:var(--muted)}.chart-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
'''


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Unexpectedly large report metadata: ' + path.name)
    return json.loads(path.read_text())


def text(value):
    return escape(str(value), quote=True)


def number(value):
    return '—' if value is None else f'{value:,.2f}' if isinstance(value, (int, float)) else text(value)


def page(title, body):
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{text(title)}</title><style>{STYLE}</style></head><body><main>{body}'
            '<footer>闪铺 · GitHub Actions 单机受限资源测量。vCPU 配额不能等同于独占物理核或线上容量。</footer>'
            '</main></body></html>')


def chart(values, title, unit):
    values = [(str(label), value) for label, value in values if isinstance(value, (int, float)) and math.isfinite(value)]
    if not values:
        return f'<p class="muted">{text(title)}：暂无数据。</p>'
    ceiling = max((value for _, value in values), default=1) or 1
    height = 55 + len(values) * 28
    rows = []
    for index, (label, value) in enumerate(values):
        y = 36 + index * 28
        width = max(0, value / ceiling * 280)
        rows.append(f'<text x="8" y="{y + 13}" font-size="12">{text(label)}</text>'
                    f'<rect x="132" y="{y}" width="{width:.2f}" height="18" rx="3" fill="#14867c"/>'
                    f'<text x="{140 + width:.2f}" y="{y + 13}" font-size="12">{value:.2f}</text>')
    return (f'<svg role="img" aria-label="{text(title)}" viewBox="0 0 520 {height}">'
            f'<text x="8" y="19" font-size="14" font-weight="600">{text(title)} · {text(unit)}</text>'
            + ''.join(rows) + '</svg>')


def timeline(path):
    path = Path(path)
    if not path.exists():
        return ''
    # Stream and bucket timeline data so a large experiment cannot create an unbounded page.
    with path.open() as file:
        count = sum(1 for _ in file) - 1
    stride = max(1, math.ceil(count / 300))
    rows = []
    with path.open() as file:
        for index, row in enumerate(csv.DictReader(file)):
            if index % stride == 0:
                rows.append(row)
    if not rows:
        return ''
    # Actual timeline column names are preserved, rather than inferred from missing values.
    keys = [key for key in ('started', 'completed', 'outstanding', 'pendingAccepted') if key in rows[0]]
    if not keys:
        return ''
    colors = ['#096b65', '#2478b3', '#c06a18', '#9d4d91']
    parts = ['<svg role="img" aria-label="测量时间线" viewBox="0 0 760 260">']
    for n, key in enumerate(keys):
        values = [float(row.get(key) or 0) for row in rows]
        top = max(values) or 1
        points = ' '.join(f'{40 + i * 680 / max(1, len(values)-1):.2f},{210 - v / top * 150:.2f}' for i, v in enumerate(values))
        parts += [f'<polyline points="{points}" fill="none" stroke="{colors[n]}" stroke-width="2"/>',
                  f'<text x="{40+n*175}" y="240" fill="{colors[n]}" font-size="12">{text(key)} max={top:.0f}</text>']
    parts.append('<text x="40" y="22" font-size="13">时间线 · 各序列独立归一化，用于观察趋势</text></svg>')
    return ''.join(parts)


def preflight_report(directory, meta):
    body = ['<section><h2>真实环境预检</h2><p class="warning">用途：validation。'
            '预检验证造数、低速预热与请求核对及运行资源，不输出容量或性能提升结论。'
            '最高档压力由正式容量实验逐级探索。</p>',
            '<p>状态：<strong>' + text(meta.get('status', 'incomplete')) + '</strong></p>']
    if meta.get('error') or meta.get('hostFailure'):
        body.append('<p class="failed">' + text(meta.get('error') or meta['hostFailure']) + '</p>')
    for title, value in [('预检数据规模与业务核对', read_json(directory/'preflight.json')),
                         ('清理并重建后的环境核验', meta.get('reset')),
                         ('MySQL 实际配置与版本', read_json(directory/'mysql-settings.json')),
                         ('结束时容器状态（清理前）', read_json(directory/'containers-final.json'))]:
        if value:
            body.append('<details><summary>' + text(title) + '</summary><pre>'
                        + text(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre></details>')
    checks = directory/'memory-checks.jsonl'
    if checks.exists():
        values = []
        with checks.open() as log:
            for line in log:
                try: values.append(json.loads(line))
                except ValueError: continue
        if values:
            body.append('<p>MySQL 工作集估算峰值：' + number(max(v['workingMiB'] for v in values))
                        + ' MiB；估算最小余量：' + number(min(v['headroomMiB'] for v in values))
                        + ' MiB。扣除 inactive_file 的估算不保证实际可分配内存。</p>')
    for path in sorted(directory.glob('*/preparation.json')):
        value = read_json(path, {})
        if value.get('status') != 'completed':
            body.append('<pre>' + text(path.parent.name) + '\n' + text(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre>')
    body.append(resources(directory/'host-watch/samples.jsonl'))
    return ''.join(body) + '</section>'


def stage_report(directory):
    meta = read_json(directory / 'manifest.json', {})
    if meta.get('stage') == 'preflight':
        return preflight_report(directory, meta)
    artifact = read_json(directory / 'capacity.json') or read_json(directory / 'burst.json') or {}
    completed = meta.get('status') == 'completed' and artifact.get('status') == 'completed'
    title = f"{meta.get('plan', directory.parent.name)} / {meta.get('stage', 'unknown')}"
    body = [f'<section><h2>{text(title)}</h2><p>状态：<strong>{text(meta.get("status", "incomplete"))}</strong></p>']
    if not completed:
        body.append('<p class="warning">本阶段未完整完成，不能用于容量提升或突发基线。</p>')
    for key in ('error', 'hostFailure'):
        if meta.get(key):
            body.append(f'<p class="failed">{text(meta[key])}</p>')
    if artifact.get('search'):
        body.append('<details><summary>容量搜索预算与停止原因</summary><pre>'
                    + text(json.dumps(artifact['search'], ensure_ascii=False, indent=2)) + '</pre></details>')
    paths = artifact.get('paths', {})
    if paths and artifact.get('scope') == 'performance':
        body.append('<h3>稳定容量区间</h3><ul>')
        for name, value in paths.items():
            if not completed:
                meaning = '阶段未完成，仅保留观测点'
            elif value.get('nonMonotonic'):
                meaning = '结果非单调，需要重测'
            elif value.get('bounded'):
                meaning = f"通过 {value['passingRps']} RPS；有效失败上界 {value['failingRps']} RPS"
            elif value.get('passingRps'):
                meaning = f"至少 {value['passingRps']} RPS，未找到有效失败上界"
            else:
                meaning = '没有样本充分且全部轮次通过的档位'
            body.append(f'<li><strong>{text(name)}</strong>：{text(meaning)}</li>')
        body.append('</ul>')
        if completed and artifact.get('scope') == 'performance':
            body.append(chart([(name, value.get('passingRps')) for name, value in paths.items()], '最高已证实通过档位', 'RPS'))
        if completed and artifact.get('scope') == 'performance' and artifact.get('capacityGainPct') is not None:
            body.append(f'<p>已测通过档位容量增幅：{number(artifact["capacityGainPct"])}%；区间推导范围：'
                        f'{text(artifact.get("capacityGainRangePct"))}%。这不是精确最大容量。</p>')
    if completed and artifact.get('scope') == 'performance' and artifact.get('comparisons'):
        body.append('<details><summary>配对比较与范围</summary><pre>' + text(json.dumps(artifact['comparisons'], ensure_ascii=False, indent=2)) + '</pre></details>')
    results = []
    for path in sorted(directory.glob('*/result.json')):
        value = read_json(path)
        if value:
            results.append((path.parent, value))
    if results:
        body.append('<div class="chart-grid">' + chart([(f'{r["path"]} {r["rate"]} r{r["repeat"]}', r['classes'].get('eligible', {}).get('responseMs', {}).get('p99')) for _, r in results], '有效请求响应 p99', 'ms')
                    + chart([(f'{r["path"]} {r["rate"]} r{r["repeat"]}', r['completedWithinDeadline']) for _, r in results], '按期限完成', '订单') + '</div>')
        body.append('<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{label}</th>' for label in ['测量点', '实际 RPS', '有效样本', '响应 p99 ms', '完成 p99 ms', '按时完成', '峰值积压', 'HTTP 在途峰值', '有效 / SLO']) + '</tr></thead><tbody>')
        for path, value in results:
            eligible = value['classes'].get('eligible', {}).get('responseMs', {})
            columns = [text(path.name), number(value['actualRequestRps']), number(eligible.get('samples')),
                       number(eligible.get('p99')), number(value['completionMs'].get('p99')),
                       number(value['completedWithinDeadline']), number(value['peakPendingAccepted']), number(value.get('peakHttpInFlight')),
                       text(f'{value["valid"]} / {value["sloPassed"]}')]
            body.append('<tr>' + ''.join(f'<td>{column}</td>' for column in columns) + '</tr>')
        body.append('</tbody></table></div>')
        for path, value in results:
            errors = value.get('correctnessErrors', []) + value.get('loadErrors', [])
            body.append(f'<details><summary>{text(path.name)} · 时间线和核对</summary>' + timeline(path / 'timeline.csv')
                        + ('<pre>' + text('\n'.join(errors)) + '</pre>' if errors else '<p>未报告数据或发压核对错误。</p>') + '</details>')
    if artifact.get('policy'):
        body.append('<details><summary>SLO 和样本要求</summary><pre>' + text(json.dumps(artifact['policy'], ensure_ascii=False, indent=2)) + '</pre></details>')
    for path in sorted(directory.glob('*/preparation.json')):
        value = read_json(path, {})
        if value.get('status') != 'completed':
            body.append(f'<details open><summary>{text(path.parent.name)} · 数据准备未完成</summary><pre>'
                        + text(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre></details>')
    terminal = read_json(directory / 'containers-final.json')
    if terminal:
        body.append('<details><summary>结束时容器状态（清理前）</summary><pre>'
                    + text(json.dumps(terminal, ensure_ascii=False, indent=2)) + '</pre></details>')
    database = read_json(directory / 'mysql-settings.json')
    if database:
        body.append('<details><summary>MySQL 实际配置与版本</summary><pre>'
                    + text(json.dumps(database, ensure_ascii=False, indent=2)) + '</pre></details>')
    body.append(resources(directory / 'host-watch/samples.jsonl'))
    return ''.join(body) + '</section>'


def resources(path):
    if not path.exists():
        return '<p class="muted">本阶段没有宿主资源采样。</p>'
    peaks, counters, samples = {}, {}, 0
    with path.open() as file:
        for line in file:
            try:
                sample = json.loads(line)
            except ValueError:
                continue  # An interrupted final JSONL line is not a measurement sample.
            samples += 1
            for container in sample.get('containers') or []:
                name, values = container['service'], container.get('resources', {})
                if 'memory.current' in values:
                    peaks[name] = max(peaks.get(name, 0), int(values['memory.current']))
                if 'cpu.stat' in values:
                    fields = dict(row.split() for row in values['cpu.stat'].splitlines())
                    if 'nr_throttled' in fields:
                        value = int(fields['nr_throttled'])
                        first, _ = counters.get(name, (value, value))
                        counters[name] = (first, value)
    if not peaks:
        return f'<p class="muted">宿主采样 {samples} 条；cgroup 计数不可用，无法绘制容器内存。</p>'
    body = '<details><summary>容器资源采样</summary><p>下图为采样峰值，可能遗漏采样间隔内的瞬时峰值。CPU throttle 是计数差，不代表完整 CPU 利用率。</p>'
    body += chart([(name, value / 1048576) for name, value in peaks.items()], '采样内存峰值', 'MiB')
    body += '<ul>'
    for name, (first, last) in counters.items():
        body += f'<li>{text(name)}：CPU nr_throttled 增量 {max(0, last-first)}</li>'
    return body + '</ul></details>'


def render(reports, output):
    reports, output = Path(reports), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    suite = read_json(reports / 'suite.json', {})
    environment = read_json(reports / 'environment.json', {})
    actions = suite.get('actions') or environment.get('actions') or {}
    status = suite.get('status', 'failed')
    if status not in ('completed', 'inconclusive'):
        status = 'failed'
    scope = suite.get('scope', 'validation' if os.environ.get('BENCH_MODE') == 'smoke' else 'performance')
    run_id = str(actions.get('runId') or os.environ.get('GITHUB_RUN_ID', 'unknown'))
    attempt = str(actions.get('attempt') or os.environ.get('GITHUB_RUN_ATTEMPT', '1'))
    title = '闪铺压测报告'
    body = [f'<h1>{title}</h1><p class="muted">GitHub Actions · {text(suite.get("mode", os.environ.get("BENCH_MODE", "unknown")))} · run {text(run_id)} / attempt {text(attempt)}</p>',
            '<div class="cards">' + ''.join(f'<div class="card"><div class="muted">{text(k)}</div><div class="big">{text(v)}</div></div>' for k, v in [('执行状态', status), ('用途', scope), ('被测端配额', '2 vCPU'), ('发压端配额', '1 vCPU')]) + '</div>']
    if scope != 'performance':
        body.append('<p class="warning">流程验证：仅核对执行、采集和业务一致性，不输出正式性能提升结论，不能作为正式突发基线。</p>')
    if status != 'completed':
        body.append('<p class="warning">运行未完整完成。已有证据供排查，不能以部分结果声称完整实验成功。</p>')
    if status == 'inconclusive':
        body.append('<p class="warning">容量边界尚未确定：保留已完成的有效测量，缺少合格基线时跳过突发。</p>')
    for title, values in [('未确定的容量边界', suite.get('inconclusive')), ('未执行的阶段与原因', suite.get('skipped'))]:
        if values:
            body.append('<details open><summary>'+title+'</summary><pre>'
                        +text(json.dumps(values, ensure_ascii=False, indent=2))+'</pre></details>')
    if suite.get('error'):
        body.append('<p class="failed">' + text(suite['error']) + '</p>')
    body.append('<p>Commit：<code>' + text(actions.get('commit', os.environ.get('GITHUB_SHA', 'unknown'))) + '</code></p>')
    run_url = actions.get('runUrl')
    if isinstance(run_url, str) and run_url.startswith('https://github.com/'):
        body.append(f'<p><a href="{text(run_url)}">打开 Actions 运行及完整证据下载</a>（artifacts 有保留期限，下载可能需要登录 GitHub）</p>')
    directories = sorted(reports.glob('*/*/manifest.json'))
    for manifest in directories:
        body.append(stage_report(manifest.parent))
    if not directories:
        body.append('<section><h2>环境准备阶段未产生测量点</h2><p>请查看 Actions 日志和 diagnostics。没有性能数据。</p></section>')
    if environment:
        body.append('<details><summary>运行环境和 CPU 分配</summary><pre>' + text(json.dumps(environment, ensure_ascii=False, indent=2)) + '</pre></details>')
    document = page(title, ''.join(body))
    private = Path(os.environ.get('BENCH_RUNTIME', '/bench/runtime')) / 'sut.env'
    if private.exists():
        for line in private.read_text().splitlines():
            if '=' in line:
                secret = line.split('=', 1)[1]
                if secret:
                    document = document.replace(secret, '[REDACTED]').replace(text(secret), '[REDACTED]')
    (output / 'index.html').write_text(document)
    summary = {'id': f'{run_id}-{attempt}', 'runId': run_id, 'attempt': attempt, 'status': status, 'scope': scope,
               'mode': suite.get('mode', 'unknown'), 'commit': actions.get('commit'), 'runUrl': run_url,
               'createdAt': datetime.now(timezone.utc).isoformat(), 'stages': len(directories)}
    (output / 'report.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
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
