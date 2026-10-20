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
    complete = next((value for value in reports if value['status'] == 'completed' and value['scope'] == 'performance'), None)
    # Preserve the latest complete formal report even after many failed/validation attempts.
    retained = reports[:keep]
    if complete and complete not in retained:
        retained.append(complete)
    retained_ids = {value['id'] for value in retained}
    for value in reports:
        if value['id'] not in retained_ids:
            shutil.rmtree(runs / value['id'])
    body = ['<h1>闪铺性能实验</h1><p class="muted">GitHub Actions · A/B 延迟与容量 · B/C 稳定容量与突发收益</p>']
    if complete:
        body.append(f'<section><h2>最近一次完整正式实验</h2><p><a href="runs/{complete["id"]}/index.html">打开报告 {complete["id"]}</a></p></section>')
    else:
        body.append('<p class="warning">尚无完整正式实验。流程验证和失败记录均有明确标记。</p>')
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
