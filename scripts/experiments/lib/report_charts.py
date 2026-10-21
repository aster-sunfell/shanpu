"""Offline SVG views of measured summaries; no inference of performance gains."""
import csv
from html import escape
import math
from pathlib import Path

NAMES = {'A': '数据库同步', 'B': 'Redis 同步', 'C': 'Kafka 异步'}
COLORS = {'A': '#6d7e96', 'B': '#168b80', 'C': '#7961cc'}


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def axis(maximum, intervals=4):
    maximum=max(maximum,1)
    raw=maximum/intervals
    magnitude=10**math.floor(math.log10(raw))
    step=next(v*magnitude for v in (1,2,2.5,5,10) if v*magnitude>=raw)
    top=math.ceil(maximum/step)*step
    return top,[i*step for i in range(round(top/step)+1)]


def fmt(value):
    if not numeric(value): return '—'
    return f'{value:.3g}' if 0 < abs(value) < .01 else f'{value:,.2f}'.rstrip('0').rstrip('.')


def txt(x, y, value, size=11, color='#8c98a8', anchor='start'):
    return f'<text x="{x:g}" y="{y:g}" font-size="{size}" fill="{color}" text-anchor="{anchor}">{escape(str(value))}</text>'


def line(x1, y1, x2, y2, color='#edf0f4', dash='', width=1):
    return f'<line x1="{x1:g}" y1="{y1:g}" x2="{x2:g}" y2="{y2:g}" stroke="{color}" stroke-width="{width}" stroke-dasharray="{dash}"/>'


def dot(x, y, color, label, hollow=False):
    return f'<circle cx="{x:g}" cy="{y:g}" r="4" fill="{"white" if hollow else color}" stroke="{color}" stroke-width="2"><title>{escape(label)}</title></circle>'


def svg(body, label, height=300):
    return f'<svg viewBox="0 0 600 {height}" role="img" aria-label="{escape(label)}">{body}</svg>'


def legend(paths):
    return '<div class="legend">'+''.join(f'<span style="--c:{COLORS[p]}">{NAMES[p]}</span>' for p in paths)+'</div>'


def empty(title, reason):
    return f'<div class="empty-state"><div><div class="empty-ring">—</div><strong>{escape(title)}</strong><p>{escape(reason)}</p></div></div>'


def paired(values, paths, label):
    if not values: return empty('尚无完整有效对照', '保留每点观测；不从无效或不完整配对计算收益。')
    top, ticks = axis(max(v['max'] for v in values)*1.15)
    y = lambda v: 244-v/top*190
    body = txt(53,25,label,10)
    for tick in ticks: body += line(58,y(tick),552,y(tick))+txt(46,y(tick)+4,fmt(tick),10,anchor='end')
    for x, path, value in zip((210,400), paths, values):
        body += f'<rect x="{x-40}" y="{y(value["p50"])}" width="80" height="{244-y(value["p50"])}" fill="{COLORS[path]}" opacity=".8"/>'
        body += line(x,y(value['min']),x,y(value['max']),COLORS[path],width=2)
        body += txt(x,268,NAMES[path],11,anchor='middle')+txt(x,y(value['max'])-12,fmt(value['p50']),11,anchor='middle')
    return svg(body,label+' · 中位数与范围')


def polyline(points, color):
    return '<polyline fill="none" stroke="'+color+'" stroke-width="2" points="'+' '.join(f'{x:g},{y:g}' for x,y in points)+'"/>'


def timeline(path, base=0, burst_end=0, end=0):
    if not path.exists(): return empty('时间线缺失', '缺失数据不显示为零。')
    with path.open() as file: rows = list(csv.DictReader(file))
    if not rows: return empty('时间线为空', '缺失数据不显示为零。')
    fields = [('started','请求发出','#6d7e96'), ('completed','订单完成','#168b80'), ('pendingAccepted','已受理待完成','#7961cc')]
    body = ''
    # Rates and backlog use separate axes/charts to avoid equating orders with orders/s.
    for selected, title in ((fields[:2], '请求 / 完成数每秒'), (fields[2:], '已受理待完成订单数')):
        top,ticks = axis(max(float(row[key]) for row in rows for key,_,_ in selected)*1.15)
        duration = max(float(r['second']) for r in rows) or 1
        x = lambda v: 58+v/duration*490
        y = lambda v: 243-v/top*185
        plot = txt(52,25,title,10)
        for tick in ticks: plot += line(58,y(tick),552,y(tick))+txt(46,y(tick)+4,fmt(tick),10,anchor='end')
        for t,label in ((base,'突发开始'),(burst_end,'恢复低流量'),(end,'停止注入')):
            if t > 0: plot += line(x(t),47,x(t),243,'#d4cde6','4 5')+txt(x(t),39,label,9,anchor='middle')
        for key,label,color in selected:
            plot += polyline([(x(float(r['second'])),y(float(r[key]))) for r in rows],color)
        plot += txt(58,280,' / '.join(label for _,label,_ in selected),10)+txt(550,280,fmt(duration)+' s',10,anchor='end')
        body += svg(plot,title)
    return body
