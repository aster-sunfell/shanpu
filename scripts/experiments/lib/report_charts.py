"""Offline SVG views of measured summaries; no inference of performance gains."""
import csv
from html import escape
import math
from pathlib import Path

from report_metrics import NAMES
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


def legend(paths, names=None):
    names=names or NAMES
    return '<div class="legend">'+''.join(f'<span style="--c:{COLORS[p]}">{names[p]}</span>' for p in paths)+'</div>'


def empty(title, reason):
    return f'<div class="empty-state"><div><div class="empty-ring">—</div><strong>{escape(title)}</strong><p>{escape(reason)}</p></div></div>'


def polyline(points, color, dash='', width=2):
    return '<polyline fill="none" stroke="'+color+'" stroke-width="'+str(width)+'" stroke-dasharray="'+dash+'" points="'+' '.join(f'{x:g},{y:g}' for x,y in points)+'"/>'


def rounds(rows, paths, label, threshold=None, percentage=False, names=None):
    names=names or NAMES
    values=[v for _,a,b in rows for v in (a,b) if numeric(v)]
    if not values: return empty('缺少逐轮数据','缺失指标不补零，也不从全程指标替代突发指标。')
    top,ticks=(100,[0,25,50,75,100]) if percentage else axis(max(values+[threshold or 0])*1.12)
    y=lambda v:244-v/top*190
    body=txt(54,20,label,10)
    for tick in ticks: body+=line(58,y(tick),552,y(tick))+txt(46,y(tick)+4,fmt(tick),10,anchor='end')
    if threshold is not None:
        body+=line(58,y(threshold),552,y(threshold),'#b2781f','5 4')+txt(550,y(threshold)-8,'期限 '+fmt(threshold)+' ms',9,anchor='end')
    for index,(repeat,a,b) in enumerate(rows):
        x=110+index*390/max(len(rows)-1,1)
        if numeric(a) and numeric(b): body+=line(x-20,y(a),x+20,y(b),'#b8c3ce',width=2)
        for offset,path,v in zip((-20,20),paths,(a,b)):
            if numeric(v):
                body+=dot(x+offset,y(v),COLORS[path],f'第 {repeat} 轮 · {names[path]}: {fmt(v)}')
                body+=txt(x+offset,y(v)+(-10 if offset<0 else 17),fmt(v),10,COLORS[path],anchor='middle')
        body+=txt(x,282,f'第 {repeat} 轮',11,anchor='middle')
    return svg(body,label+' · 全部配对轮次')


def comparison_timeline(points, paths, names=None):
    """Same scales across all rounds and paths; never select the best-looking run."""
    names=names or NAMES
    data=[]
    for directory,row in points:
        file=directory/'timeline.csv'
        if file.exists():
            with file.open() as stream: rows=list(csv.DictReader(stream))
            required=('second','started','completed','outstanding','pendingAccepted')
            try:
                if rows and all(numeric(float(r[key])) and float(r[key])>=0 for r in rows for key in required):
                    data.append((row,rows))
            except (KeyError,ValueError):
                pass  # Missing/corrupt time series is shown as unavailable, never zero.

    if not data: return empty('时间线缺失','原始证据未包含时间线。')
    duration=max(float(r['second']) for _,rows in data for r in rows) or 1
    rate_top,rate_ticks=axis(max(float(r[k]) for _,rows in data for r in rows for k in ('started','completed'))*1.1)
    pending_top,pending_ticks=axis(max(float(r['outstanding']) for _,rows in data for r in rows)*1.1)
    repeats=sorted({r['repeat'] for r,_ in data})
    html='<div class="round-selector"><label>查看轮次 <select data-round-select>' + ''.join(f'<option value="{i}">第 {i} 轮</option>' for i in repeats)+'</select></label></div>'
    for repeat in repeats:
        html+=f'<div class="timeline-variant" data-round="{repeat}"><h3>第 {repeat} 轮</h3><div class="timeline-grid">'
        selected=[(r,rows) for r,rows in data if r['repeat']==repeat]
        for pending in (False,True):
            top,ticks=(pending_top,pending_ticks) if pending else (rate_top,rate_ticks)
            for path in paths:
                entry=next(((r,rows) for r,rows in selected if r['path']==path),None)
                if not entry:
                    html+=empty(names[path]+'时间线缺失','不以其他轮次代替。');continue
                row,rows=entry
                x=lambda v:58+v/duration*490
                y=lambda v:243-v/top*185
                label=names[path]+(' · 未完成请求 / 个' if pending else ' · 到达与完成 / 每秒')
                plot=txt(58,20,label,11)
                for tick in ticks: plot+=line(58,y(tick),552,y(tick))+txt(46,y(tick)+4,fmt(tick),10,anchor='end')
                for t,label in ((row.get('baseSeconds',0),'突发开始'),(row.get('baseSeconds',0)+row.get('seconds',0),'恢复低流量'),(row.get('baseSeconds',0)+row.get('seconds',0)+row.get('recoverySeconds',0),'停止注入')):
                    plot+=line(x(t),50,x(t),243,'#d4cde6','4 5')+txt(x(t),39,label,9,anchor='middle')
                fields=[('outstanding','未完成请求',COLORS[path]),('pendingAccepted','已受理待完成','#b2781f')] if pending else [('started','请求到达','#6d7e96'),('completed','订单完成',COLORS[path])]
                legend_items=[]
                for i,(key,label,color) in enumerate(fields):
                    dash='6 4' if i else ''
                    width=2 if i else 3
                    plot+=polyline([(x(float(r['second'])),y(float(r[key]))) for r in rows],color,dash,width)
                    swatch=f'<svg viewBox="0 0 30 12" aria-hidden="true">{line(1,6,29,6,color,dash,width)}</svg>'
                    legend_items.append('<span>'+swatch+escape(label)+'</span>')
                chart_legend='<div class="timeline-legend" aria-label="图例">'+''.join(legend_items)+'</div>'
                for t in range(0,int(duration)+1,10):plot+=txt(x(t),259,str(t)+'s',9,anchor='middle')
                html+='<div class="panel timeline-panel">'+chart_legend+'<div class="chart">'+svg(plot,names[path]+(' 未完成请求' if pending else ' 到达与完成'),height=275)+'</div></div>'
        html+='</div></div>'
    return html
