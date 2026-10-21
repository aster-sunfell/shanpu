"""Offline SVG views of measured summaries; no inference of performance gains."""
import csv
import json
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
    """All rounds and both paths share scales. Missing DB evidence stays missing."""
    names=names or NAMES
    data=[]
    for directory,row in points:
        def csv_rows(filename, required):
            file=directory/filename
            if not file.exists(): return []
            try:
                with file.open() as stream: rows=list(csv.DictReader(stream))
                if rows and all(numeric(float(r[k])) for r in rows for k in required): return rows
            except (KeyError,ValueError): pass
            return []
        rows=csv_rows('timeline.csv',('second','started','completed','outstanding'))
        if not rows: continue
        database=csv_rows('database-timeline.csv',('second','transactionStarts','transactionActivePeak')) if row.get('pressure',{}).get('valid') else []
        samples=[]
        sample_file=directory/'pressure-samples.json'
        if sample_file.exists():
            try: samples=json.loads(sample_file.read_text())
            except ValueError: pass
        def values(source,key):
            return [(max(0,float(r['second'])),float(r[key])) for r in source if r.get(key) is not None and numeric(float(r[key]))]
        # label, color, values, sampled (true maxima must not be inferred from samples).
        series=[[
            ('请求到达','#6d7e96',values(rows,'started'),False),
            ('下单事务开始','#b2781f',values(database,'transactionStarts'),False),
            ('订单提交',COLORS[row['path']],values(rows,'completed'),False)], [
            ('下单事务并发峰值',COLORS[row['path']],values(database,'transactionActivePeak'),False),
            ('连接等待线程（采样）','#b2781f',values(samples,'poolWaitThreads'),True)], [
            ('未完成订单',COLORS[row['path']],values(rows,'outstanding'),False)]]
        if row['path']=='C': series[2].append(('Kafka lag（采样）','#b2781f',values(samples,'kafkaLag'),True))
        data.append((row,series))
    if not data: return empty('时间线缺失','原始证据未包含时间线。')
    duration=max(x for _,groups in data for group in groups for _,_,values,_ in group for x,_ in values) or 1
    scales=[axis(max((v for _,groups in data for _,_,values,_ in groups[i] for _,v in values),default=1)*1.1) for i in range(3)]
    repeats=sorted({r['repeat'] for r,_ in data})
    html='<div class="round-selector"><label>查看轮次 <select data-round-select>'+''.join(f'<option value="{i}">第 {i} 轮</option>' for i in repeats)+'</select></label></div>'
    for repeat in repeats:
        html+=f'<div class="timeline-variant" data-round="{repeat}"><h3>第 {repeat} 轮</h3><div class="timeline-grid">'
        for group,title in enumerate(('到达、事务开始与提交 / 每秒','事务并发与连接等待 / 个','未完成订单与消息积压 / 个')):
            top,ticks=scales[group]
            for path in paths:
                entry=next(((r,groups[group]) for r,groups in data if r['repeat']==repeat and r['path']==path),None)
                if not entry:
                    html+=empty(names[path]+'时间线缺失','不以其他轮次代替。');continue
                row,fields=entry
                if not any(values for _,_,values,_ in fields):
                    html+='<div class="panel">'+empty(names[path]+' · '+title,'缺少相应时间线证据；不补零，也不从订单提交速度推算。')+'</div>'
                    continue
                x=lambda v:58+v/duration*490
                y=lambda v:243-v/top*185
                plot=txt(58,20,names[path]+' · '+title,11)
                for tick in ticks: plot+=line(58,y(tick),552,y(tick))+txt(46,y(tick)+4,fmt(tick),10,anchor='end')
                for t,label in ((row.get('baseSeconds',0),'突发开始'),(row.get('baseSeconds',0)+row.get('seconds',0),'恢复低流量'),(row.get('baseSeconds',0)+row.get('seconds',0)+row.get('recoverySeconds',0),'停止注入')):
                    plot+=line(x(t),50,x(t),243,'#d4cde6','4 5')+txt(x(t),39,label,9,anchor='middle')
                items=[]
                for i,(label,color,values,sampled) in enumerate(fields):
                    dash=('', '6 4', '2 3')[i]
                    width=3 if i==0 else 2
                    segments=[[]]
                    for t,v in values:
                        # Gaps remain gaps: do not imply zero or a continuous sample.
                        if sampled and segments[-1] and t-segments[-1][-1][0]>6: segments.append([])
                        segments[-1].append((t,v))
                    for segment in segments:
                        plot+=polyline([(x(t),y(v)) for t,v in segment],color,dash,width)
                    if sampled:
                        for t,v in values: plot+=f'<circle cx="{x(t):g}" cy="{y(v):g}" r="2" fill="{color}"/>'
                    swatch=f'<svg viewBox="0 0 30 12" aria-hidden="true">{line(1,6,29,6,color,dash,width)}</svg>'
                    items.append('<span>'+swatch+escape(label+(' · 无曲线证据' if not values else ''))+'</span>')
                for t in range(0,int(duration)+1,10):plot+=txt(x(t),259,str(t)+'s',9,anchor='middle')
                html+='<div class="panel timeline-panel"><div class="timeline-legend" aria-label="图例">'+''.join(items)+'</div><div class="chart">'+svg(plot,names[path]+' '+title,height=275)+'</div></div>'
        html+='</div></div>'
    return html
