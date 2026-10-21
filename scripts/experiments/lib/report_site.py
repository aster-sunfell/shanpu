"""Merge a new HTML report into a bounded static archive for GitHub Pages."""
import argparse
from html import escape
import json
from pathlib import Path
import re
import shutil

from html_report import page, read_json


def merge_report(current, archive, keep=30):
    current, archive = Path(current), Path(archive)
    report = read_json(current / 'report.json')
    if not report or not re.fullmatch(r'[0-9]+-[0-9]+', report['id']):
        raise ValueError('A report must identify an actual Actions run and attempt')
    runs = archive / 'runs'
    runs.mkdir(parents=True, exist_ok=True)
    target = runs / report['id']
    target.mkdir(exist_ok=True)
    # Only rendered, public files are published. Never copy raw runtime/evidence trees.
    for name in ('index.html', 'report.json'):
        shutil.copyfile(current / name, target / name)
    reports = []
    for path in runs.glob('*/report.json'):
        value = read_json(path)
        if value and re.fullmatch(r'[0-9]+-[0-9]+', path.parent.name) and value.get('id') == path.parent.name:
            reports.append(value)
    reports.sort(key=lambda value: (int(value['runId']), int(value['attempt'])), reverse=True)
    complete = {plan: next((v for v in reports if v.get('mode') == plan and v['status'] == 'completed'
                           and v['scope'] == 'performance'), None) for plan in ('redis-filtering','kafka-burst')}
    retained = reports[:keep]
    for value in complete.values():
        if value and value not in retained: retained.append(value)
    retained_ids = {value['id'] for value in retained}
    for value in reports:
        if value['id'] not in retained_ids:
            shutil.rmtree(runs / value['id'])
    body = ['<h1>闪铺压测报告</h1><p class="muted">GitHub Actions · Redis 过滤收益 · Kafka 削峰收益</p>']
    for plan, value in complete.items():
        body.append('<section><h2>'+escape(plan)+' · 最近一次完整正式实验</h2>')
        body.append(f'<a href="runs/{value["id"]}/index.html">打开报告 {value["id"]}</a>' if value else '<p>尚无完整正式实验。</p>')
        body.append('</section>')
    body.append('<section><h2>运行历史</h2><div class="scroll"><table><thead><tr><th>运行</th><th>模式</th><th>用途</th><th>状态</th><th>Commit</th></tr></thead><tbody>')
    for value in retained:
        body.append(f'<tr><td><a href="runs/{value["id"]}/index.html">{value["id"]}</a></td>'
                    + ''.join(f'<td>{escape(str(value.get(key, "")))}</td>' for key in ('mode', 'scope', 'status', 'commit')) + '</tr>')
    body.append('</tbody></table></div></section>')
    (archive / 'index.html').write_text(page('闪铺性能实验', ''.join(body)))
    (archive / 'reports.json').write_text(json.dumps(retained, ensure_ascii=False, indent=2) + '\n')
    (archive / '.nojekyll').touch()
    return retained


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    args = parser.parse_args()
    merge_report(args.current, args.archive)
