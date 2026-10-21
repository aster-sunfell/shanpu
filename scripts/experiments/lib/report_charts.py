"""Offline SVG views of measured summaries; no inference of performance gains."""
import csv
from html import escape
import math
from pathlib import Path
from statistics import median

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
    return f'{value:,.2f}'.rstrip('0').rstrip('.') if numeric(value) else '—'


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


def latency(rows, paths, observations, repeats, limit):
    rows = [row for row in rows if row.get('valid') and row.get('path') in paths
            and numeric(row.get('rate')) and numeric(row.get('classes', {}).get('eligible', {}).get('responseMs', {}).get('p99'))]
    if not rows:
        return empty('暂无有效延迟观测', '测量结果产生后，在这里比较同压下的有效购买响应 p99。')
    rates = sorted({r['rate'] for r in rows})
    lo, hi = min(rates), max(rates)
    if lo == hi: lo, hi = max(0, lo * .8), hi * 1.2 or 1
    top,ticks = axis(max([r['classes']['eligible']['responseMs']['p99'] for r in rows] + ([limit] if numeric(limit) else [1])) * 1.15)
    x = lambda v: 58+(v-lo)/(hi-lo)*490
    y = lambda v: 244-v/top*190
    body = txt(53, 25, '响应 p99 / ms', 10)
    for tick in ticks:
        body += line(58,y(tick),552,y(tick))+txt(47,y(tick)+4,fmt(tick),10,anchor='end')
    if numeric(limit):
        body += line(58,y(limit),552,y(limit),'#d9b37e','5 5')+txt(551,y(limit)-7,f'响应目标 {fmt(limit)} ms',9,'#b18a54','end')
    for path in paths:
        connected = []
        for rate in rates:
            group = [r for r in rows if r['path']==path and r['rate']==rate]
            values = [r['classes']['eligible']['responseMs']['p99'] for r in group]
            state = observations.get((path,rate))
            complete = state in ('pass','fail') and len(group)==repeats and len({r.get('repeat') for r in group})==repeats
            if complete:
                value = median(values)
                connected.append((x(rate),y(value)))
                body += line(x(rate),y(min(values)),x(rate),y(max(values)),COLORS[path],width=5)
                body += dot(x(rate),y(value),COLORS[path],f'{NAMES[path]} · {fmt(rate)} RPS · p99 中位数 {fmt(value)} ms',state=='fail')
            else:
                if len(connected)>1:
                    body += polyline(connected,COLORS[path])
                connected=[]
                for row in group:
                    value=row['classes']['eligible']['responseMs']['p99']
                    body += dot(x(rate),y(value),COLORS[path],f'{NAMES[path]} · 第 {row.get("repeat","?")} 轮 · {fmt(value)} ms',True)
        if len(connected)>1: body+=polyline(connected,COLORS[path])
    for rate in rates[::max(1,math.ceil(len(rates)/7))]: body+=txt(x(rate),267,fmt(rate),10,anchor='middle')
    return svg(body+txt(552,290,'输入 RPS',10,anchor='end'),'有效购买响应 p99')


def polyline(points, color):
    return '<polyline points="'+' '.join(f'{x:g},{y:g}' for x,y in points)+f'" fill="none" stroke="{color}" stroke-width="2.2"/>'


def capacity(bounds, paths, formal):
    if not formal:
        return empty('尚无正式容量结论', '流程验证或缺失的测量证据不用于推导容量。')
    maximum,ticks = axis(max([v.get('failingRps') or v.get('passingRps') or 0 for v in bounds.values()] + [1])*1.18)
    x=lambda v: 134+v/maximum*382
    body=txt(134,28,'最高通过档位 → 最近有效失败档位',10)
    for value in ticks:
        body+=line(x(value),48,x(value),228)+txt(x(value),250,fmt(value),10,anchor='middle')
    for i,path in enumerate(paths):
        value=bounds.get(path,{})
        yy=91+i*86
        body+=txt(17,yy+5,NAMES[path],13,COLORS[path])
        passed=value.get('passingRps')
        failed=value.get('failingRps') if value.get('bounded') else None
        if value.get('nonMonotonic'):
            body+=txt(134,yy,'结果非单调，需要重测',11,'#b2781f')
            continue
        if not numeric(passed) or passed<=0:
            body+=line(x(0),yy,x(maximum),yy,'#dce3eb','3 6')+txt(134,yy+27,'缺少样本充分且全部轮次通过的档位',10)
            continue
        body+=line(x(0),yy,x(passed),yy,COLORS[path],width=7)
        body+=dot(x(passed),yy,COLORS[path],f'{NAMES[path]} 已证实通过 {fmt(passed)} RPS')
        if numeric(failed) and failed>passed:
            body+=line(x(passed),yy,x(failed),yy,COLORS[path],'4 4',2)
            body+=dot(x(failed),yy,COLORS[path],f'有效测量中未达标档位 {fmt(failed)} RPS',True)
            body+=txt(x(passed)-8,yy-14,fmt(passed),11,COLORS[path],'end')+txt(x(failed)+7,yy-14,fmt(failed),10,COLORS[path])
            description=f'通过 {fmt(passed)} RPS；有效未达标上界 {fmt(failed)} RPS'
        else:
            body+=line(x(passed),yy,x(passed)+25,yy,COLORS[path],'4 4',2)+txt(x(passed)+25,yy+5,'›',18,COLORS[path])
            body+=txt(x(passed),yy-15,f'至少 {fmt(passed)}',11,COLORS[path],'middle')
            description=f'至少 {fmt(passed)} RPS，未找到有效失败上界'
        body+=txt(134,yy+28,description,10)
        observation=next((row for row in value.get('observations',[]) if row.get('rate')==passed and row.get('state')=='pass'),{})
        throughput=(observation.get('completionRps') or {}).get('p50')
        if numeric(throughput): body+=txt(134,yy+43,f'有效订单完成 {fmt(throughput)} 单/s · 各轮中位数',9)
    return svg(body+txt(552,281,'输入 RPS',10,anchor='end'),'稳定容量区间',294)


def grouped_burst(groups, limit):
    if not groups:
        return empty('暂无完整突发对照', '需要同一突发档位、同一统计范围下的完整有效轮次。')
    # Each panel holds at most the protocol's five configured burst strengths.
    top,ticks=axis(max(max(g[p]['max'] for p in ('B','C')) for g in groups)*1.2)
    y=lambda v:238-v/top*180
    body=txt(50,26,f'{fmt(limit)} ms 内完成 / 订单' if numeric(limit) else '按完成期限完成 / 订单',10)
    for value in ticks:
        body+=line(57,y(value),556,y(value))+txt(46,y(value)+4,fmt(value),10,anchor='end')
    for index,group in enumerate(groups):
        center=57+(index+.5)*490/len(groups)
        for offset,path in ((-34,'B'),(6,'C')):
            stats=group[path]
            left=center+offset
            body+=f'<rect x="{left:g}" y="{y(stats["median"]):g}" width="28" height="{238-y(stats["median"]):g}" rx="3" fill="{COLORS[path]}"><title>{NAMES[path]} · {fmt(stats["median"])} 单</title></rect>'
            body+=line(left+14,y(stats['min']),left+14,y(stats['max']),'#46566c')
            body+=line(left+9,y(stats['max']),left+19,y(stats['max']),'#46566c')
            body+=txt(left+14,y(stats['max'])-9,fmt(stats['median']),11,COLORS[path],'middle')
        body+=txt(center,261,group['label'],10,anchor='middle')
        body+=txt(center,282,f'{fmt(group["rate"])} RPS',9,anchor='middle')
    return svg(body,'突发按时完成量')


def read_timeline(path):
    path=Path(path)
    if not path.exists(): return []
    with path.open() as file: count=sum(1 for _ in file)-1
    stride=max(1,math.ceil(count/300))
    rows=[]
    with path.open() as file:
        bucket=[]
        for row in csv.DictReader(file):
            if not numeric_string(row.get('second')) or not numeric_string(row.get('pendingAccepted')): continue
            bucket.append(row)
            if len(bucket)==stride:
                rows.append((float(bucket[-1]['second']),max(float(r['pendingAccepted']) for r in bucket)))
                bucket=[]
        if bucket: rows.append((float(bucket[-1]['second']),max(float(r['pendingAccepted']) for r in bucket)))
    return rows


def numeric_string(value):
    try: return math.isfinite(float(value))
    except (TypeError,ValueError): return False


def timelines(series, base_seconds=None, end_seconds=None):
    if not series or any(not values for values in series.values()):
        return empty('暂无完整积压曲线', '时间线证据缺失，不将缺失曲线绘制为零积压。')
    top,ticks=axis(max(v for values in series.values() for _,v in values)*1.15)
    duration,time_ticks=axis(max(t for values in series.values() for t,_ in values),5)
    x=lambda v:58+v/duration*490
    y=lambda v:243-v/top*185
    body=txt(52,25,'受理后待完成 / 订单',10)
    for value in ticks:
        body+=line(58,y(value),552,y(value))+txt(46,y(value)+4,fmt(value),10,anchor='end')
    for seconds,label in ((base_seconds,'突发开始'),(end_seconds,'停止注入')):
        if numeric(seconds) and seconds<=duration:
            body+=line(x(seconds),47,x(seconds),243,'#d4cde6','4 5')+txt(x(seconds),39,label,9,'#978ba9','middle')
    for path,values in series.items(): body+=polyline([(x(t),y(v)) for t,v in values],COLORS[path])
    for tick in time_ticks: body+=txt(x(tick),265,fmt(tick),10,anchor='middle')
    return svg(body+txt(552,290,'时间 / s',10,anchor='end'),'测量时间线 · 受理后待完成订单')
