"""Chart-first, self-contained report layout shared by formal and partial runs."""
from html import escape
import json
from pathlib import Path
from statistics import median

import report_charts as charts
from report_charts import COLORS, NAMES, empty, fmt, legend, numeric
from report_data import Stage, read_json

ASSETS=Path(__file__).resolve().parent
STYLE=(ASSETS/'html_report.css').read_text()
SCRIPT=(ASSETS/'html_report.js').read_text()


def text(value): return escape(str(value),quote=True)


def page(title,body,sidebar=None):
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{text(title)}</title><style>{STYLE}</style></head>'
            f'<body class="{"report" if sidebar else "archive"}">{sidebar or ""}<main>{body}'
            '<footer><span>闪铺 · GitHub Actions 单机受限资源测量</span>'
            '<span>vCPU 配额不等同于独占物理核或线上容量。</span></footer>'
            f'</main><script>{SCRIPT}</script></body></html>')


def heading(title,subtitle='',number=''):
    prefix=f'<span class="section-number">{text(number)}</span>' if number else ''
    return f'<div class="section-heading"><div><h2>{prefix}{text(title)}</h2><p>{text(subtitle)}</p></div></div>'


def panel(title,subtitle,graphic,note,paths=()):
    return (f'<article class="panel"><div class="panel-head"><div><h3>{text(title)}</h3>'
            f'<div class="panel-sub">{text(subtitle)}</div></div>{legend(paths) if paths else ""}</div>'
            f'<div class="chart">{graphic}</div><div class="takeaway">{note}</div></article>')


def table(headers,rows):
    return ('<div class="table-scroll"><table><thead><tr>'+''.join(f'<th>{text(v)}</th>' for v in headers)
            +'</tr></thead><tbody>'+''.join('<tr>'+''.join(f'<td>{text(v)}</td>' for v in row)+'</tr>' for row in rows)
            +'</tbody></table></div>')


def sidebar(sections,run_url=None):
    links=''.join(f'<a href="#{key}" {"class=active aria-current=location" if i==0 else ""}><span>{marker}</span>{text(label)}</a>'
                  for i,(key,label,marker) in enumerate(sections))
    evidence=f'<a class="switch" href="{text(run_url)}">Actions 与完整证据 ↗</a>' if valid_run_url(run_url) else ''
    return ('<aside><a href="../../index.html" class="brand"><i></i>闪铺</a><div class="brand-sub">PERFORMANCE LAB</div>'
            f'<nav aria-label="报告导航">{links}</nav><div class="aside-bottom"><strong>性能实验报告</strong>'
            f'本次测量 · 独立资源配额{evidence}</div></aside>')


def valid_run_url(url):
    return isinstance(url,str) and url.startswith('https://github.com/')


def schemes(paths):
    values={'A':('数据库同步下单','直接执行数据库事务，完成扣库存、扣积分与创建订单。','PAID · 订单已完成'),
            'B':('Redis 预扣＋同步下单','Redis 预扣和过滤重复购买，再同步执行数据库事务。','PAID · 订单已完成'),
            'C':('Redis 预扣＋Kafka 异步下单','Redis 预扣，Kafka 确认后先返回，由消费者完成数据库事务。','PENDING · 已受理，等待完成')}
    cards=[]
    for p in paths:
        title,description,state=values[p]
        cards.append(f'<article class="scheme" style="--scheme-color:{COLORS[p]}"><h3><small>{p}</small>{title}</h3><p>{description}</p><div class="scheme-state">{state}</div></article>')
    return '<section id="schemes">'+heading('方案说明','独立对照方案；图表使用具体名称，字母仅用于对应实验记录。')+'<div class="scheme-grid">'+''.join(cards)+'</div></section>'


def experiment_criteria(plan,stages):
    """Explain each experiment's actual policy, without inventing absent thresholds."""
    policies={}
    for stage in stages:
        if stage.policy:
            policies.setdefault(json.dumps(stage.policy,sort_keys=True),[]).append(stage.kind)
    if not policies: policies={'{}':[]}
    kafka=plan=='kafka-buffering'
    body=[f'<div class="criteria" data-experiment="{text(plan)}"><div class="criteria-heading"><h3>本实验达标条件（SLO）</h3>'
          '<p>SLO 是预先设定的服务质量目标，用于判断某个压力档位是否达标。</p></div>']
    for encoded,kinds in policies.items():
        policy=json.loads(encoded)
        def target(key,unit,scale=1,prefix='p99 ≤ '):
            value=policy.get(key)
            return prefix+fmt(value*scale)+' '+unit if numeric(value) else '未采集'
        if len(policies)>1:
            label=' / '.join('稳定容量' if kind=='capacity' else '突发对照' if kind=='burst' else kind for kind in kinds)
            body.append('<p class="criteria-policy-label">'+text(label)+'的服务目标</p>')
        response=('Redis 同步返回 PAID；Kafka 异步在消息确认后返回 PENDING，表示已受理。' if kafka else
                  '两种方案成功购买均在数据库事务完成后返回 PAID；有效购买与重复拒绝分别检查响应延迟。')
        completion=('两种方案均以数据库订单完成为准，从每笔请求发起时计时。' if kafka else
                    '有效购买对应的数据库订单完成，从每笔请求发起时计时。')
        reliability=('全部为有效购买，检查购买请求错误率。' if kafka else
                     '有效购买和重复购买分别检查；重复购买按预期拒绝不算错误。')
        fields=[('响应速度',target('responseP99Ms','ms'),response),
                ('订单完成速度',target('completionP99Ms','秒',.001),completion),
                ('请求可靠性',target('maxErrorRate','%',100,'错误率 ≤ '),reliability)]
        body.append('<div class="criteria-grid">'+''.join(f'<div><span>{text(label)}</span><strong>{text(value)}</strong><p>{description}</p></div>' for label,value,description in fields)+'</div>')
    body.append('<p class="criteria-note"><b>稳定容量：</b>须同时满足上述目标，订单最终全部完成、积压不持续增长，并通过样本与轮次检查。</p>')
    if kafka:
        body.append('<p class="criteria-note"><b>突发实验：</b>主动施加超载，比较期限内完成量、积压和排空时间。超出服务目标不等于测量无效；测量有效性另按发压、业务核对与证据检查判断。</p>')
    if not any(stage.policy for stage in stages):
        body.append('<p class="criteria-note">尚无本实验的目标记录，缺失阈值标为“未采集”。</p>')
    return ''.join(body)+'</div>'


def capacity_note(stage):
    if not stage.formal: return '流程验证或证据缺失，未生成正式容量结论。'
    if any(stage.bounds.get(p,{}).get('nonMonotonic') for p in stage.paths): return '通过 / 失败结果非单调，需要重测，未生成容量增幅。'
    if stage.gain is None: return '保留已证实通过的档位和下限；缺少完整合格边界，未生成容量增幅。'
    value=stage.gain
    spread=stage.artifact.get('capacityGainRangePct')
    result=f'<b>已测通过档位变化 {value:+.2f}%。</b>'
    if isinstance(spread,list) and len(spread)==2 and all(numeric(v) for v in spread):
        result+=f'边界推导范围 {fmt(spread[0])}%–{fmt(spread[1])}%。'
        if spread[0]<=0<=spread[1]: result+='区间包含零，尚不能明确断言容量提高。'
    return result+'保留搜索档位误差，不代表精确最大容量。'


def latency_note(stage):
    comparison=stage.latency_comparison
    if not comparison: return '保留有效观测；完整对照、达标条件、样本或阶段状态不满足要求，未生成延迟降幅。'
    pct=comparison['latencyReductionPct']
    rate=comparison['rate']
    result=f'<b>在双方均达标的最高共同压力 {fmt(rate)} RPS 下</b>，'
    values=[[r.get('classes',{}).get('eligible',{}).get('responseMs',{}).get('p99')
             for r in stage.rows if r.get('valid') and r.get('path')==path and r.get('rate')==rate] for path in stage.paths]
    if all(len(group)==stage.repeats and all(numeric(v) for v in group) for group in values):
        before,after=(median(group) for group in values)
        verb='降至' if after<before else '升至' if after>before else '保持为'
        result+=f'有效购买响应 p99 从 {fmt(before)} ms {verb} {fmt(after)} ms，'
    result+=f'{stage.repeats} 轮降幅中位数为 {fmt(pct["p50"])}%'
    if numeric(pct.get('min')) and numeric(pct.get('max')):
        result+=f'，范围 {fmt(pct["min"])}%–{fmt(pct["max"])}%'
        if pct['min']<=0<=pct['max']: result+='，各轮方向不一致'
    if pct['p50']<0: result+='；负降幅表示响应变慢'
    return result+'。'


def stage_report(directory,scope=None):
    stage=directory if isinstance(directory,Stage) else Stage(directory,scope)
    if stage.kind=='preflight': return ''
    if stage.kind=='burst': return burst_report(stage)
    graphic=charts.latency(stage.rows,stage.paths,stage.observations,stage.repeats,stage.policy.get('responseP99Ms'))
    label='同压下，有效购买是否更快？' if stage.plan=='redis-filtering' else '响应延迟观测'
    left=panel(label,'完整档位汇总；轮次未齐时保留独立散点',graphic,latency_note(stage),stage.paths)
    right=panel('满足服务质量要求，能承受多少流量？','通过点和有效未达标点限定容量边界',
                charts.capacity(stage.bounds,stage.paths,stage.formal),capacity_note(stage))
    if stage.plan=='kafka-buffering':
        stable=panel('稳定完成容量','双方采用相同服务目标 · 订单全部完成，积压不持续增长',
                     charts.capacity(stage.bounds,stage.paths,stage.formal),capacity_note(stage))
        stable=stable.replace('<div class="chart">','<div class="chart" style="max-width:740px;margin:auto">',1)
        return stable+diagnostics(stage)
    return '<div class="two-col">'+left+right+'</div>'+diagnostics(stage)


def burst_report(stage):
    groups=stage.burst_groups()
    scopes={g['cohort'] for g in groups}
    # Historical whole-run totals remain distinguishable from burst-cohort totals.
    subtitle='突发段请求 · 各轮中位数与范围' if scopes=={True} else '整场计数（含低负载段） · 各轮中位数与范围'
    if scopes=={False,True}: subtitle='各档位统计范围见下方明细，不合并不同口径'
    notes=[]
    for group in groups:
        delta=group['delta']
        notes.append(f'{text(group["label"])}：按时完成变化 {fmt(delta["p50"])} 单，范围 {fmt(delta.get("min"))}–{fmt(delta.get("max"))} 单。')
    left=panel('同一波突发，多按时完成多少单？',subtitle,
               charts.grouped_burst(groups,stage.policy.get('completionP99Ms')),
               '<br>'.join(notes) or '缺少完整有效对照，未生成订单增量。',('B','C'))
    right=timeline_panel(stage)
    details=[]
    for group in groups:
        for path in ('B','C'):
            rows=[r for r in group['rows'] if r['path']==path]
            source=[r['burst'] if group['cohort'] else r for r in rows]
            def mid(key):
                values=[r.get(key) for r in source]
                return fmt(median(values)) if all(numeric(v) for v in values) else '—'
            requests=[r.get('requests') if group['cohort'] else r.get('eligible') for r in source]
            counts=[r.get('completedWithinDeadline') for r in source]
            rates=[v/n*100 for v,n in zip(counts,requests) if numeric(v) and numeric(n) and n>0]
            fraction=fmt(median(rates))+'%' if len(rates)==len(rows) else '—'
            details.append([group['label'],NAMES[path],'突发段' if group['cohort'] else '整场（含低负载）',mid('acceptedWithinResponseDeadline'),mid('completedWithinDeadline'),fraction,mid('completed')])
    body='<div class="burst-grid">'+left+right+'</div>'
    if details:
        body+='<details class="observations"><summary>受理、按时完成与最终完成 · 各轮中位数</summary>'+table(['档位','方案','统计范围','按响应期限受理','按完成期限完成','按时完成率','最终完成'],details)+'</details>'
    return body+diagnostics(stage)


def timeline_panel(stage):
    groups={}
    for path,row in stage.points:
        if row.get('valid') and row.get('path') in ('B','C'):
            key=(row.get('rate'),row.get('repeat'))
            groups.setdefault(key,{})[row['path']]=(path,row)
    options=[]
    variants=[]
    for rate,repeat in sorted(groups,key=lambda key:(key[0] or 0,key[1] or 0)):
        pair=groups[(rate,repeat)]
        if set(pair)!=set(('B','C')): continue
        configs={p:read_json(path/'point.json',{}) for p,(path,_) in pair.items()}
        starts=[config.get('baseSeconds',pair[p][1].get('baseSeconds')) for p,config in configs.items()]
        seconds=[config.get('seconds',pair[p][1].get('seconds')) for p,config in configs.items()]
        same=starts[0]==starts[1] and seconds[0]==seconds[1] and all(numeric(v) for v in starts+seconds)
        base=starts[0] if same else None
        end=base+seconds[0] if same else None
        series={p:charts.read_timeline(path/'timeline.csv') for p,(path,_) in pair.items()}
        if not all(series.values()): continue
        key=f'curve-{len(options)}'
        options.append(f'<option value="{key}">{fmt(rate)} RPS · 第 {text(repeat)} 轮</option>')
        note=[]
        for p,(_,row) in pair.items():
            drain=row.get('drainMs')
            note.append(f'{NAMES[p]}：待完成峰值 {fmt(row.get("peakPendingAccepted"))} 单，排空 {fmt(drain/1000 if numeric(drain) else None)} 秒')
        variants.append(f'<div class="timeline-variant" data-key="{key}" {"hidden" if len(options)>1 else ""}>{charts.timelines(series,base,end)}<div class="chart-caption">{text("；".join(note))}</div></div>')
    if not options:
        return panel('受理收益背后的积压与排空','共享真实数量与时间坐标',empty('暂无完整积压曲线','没有同档位、同轮次的有效对照时间线。'),'缺失证据不显示为零积压。')
    selector='<div class="timeline-controls"><select data-timeline-select aria-label="选择突发档位和轮次">'+''.join(options)+'</select></div>'
    return '<article class="panel" data-timeline-group><div class="panel-head"><div><h3>受理收益背后的积压与排空</h3><div class="panel-sub">共享真实坐标 · 默认最低档第一个完整对照轮次</div></div>'+selector+'</div>'+legend(('B','C'))+'<div class="chart">'+''.join(variants)+'</div><div class="takeaway">受理与最终完成分别评价；停止注入后排空，不代表持续发压时无积压增长。</div></article>'


def diagnostics(stage):
    body=['<details class="diagnostics"><summary>测量明细与诊断证据</summary>']
    for key in ('error','hostFailure'):
        if stage.meta.get(key): body.append(f'<p class="failed">{text(stage.meta[key])}</p>')
    if stage.artifact.get('search'):
        search=stage.artifact['search']
        body.append('<p>容量搜索停止原因：'+text(search.get('stopReason') or '未记录')+'</p>')
        if search.get('strategy') == 'fixed-start-probe-confirm':
            body.append('<p>固定起点 '+text(search.get('fixedStartRps'))+' RPS；短探路为 validation，'
                        '只选择本次正式候选，不计入容量、样本或配对收益，也不读取历史结果。</p>')
            probes = [[NAMES.get(path, path), text(row['rate']), text(row['state']), 'validation / 不计容量']
                      for path, state in search.get('paths', {}).items() for row in state.get('probes', [])]
            if probes:
                body.append(table(['探路方案', 'RPS', '短时结果', '用途'], probes))
            body.append('<p>各路径停止原因：'+text('；'.join(path+': '+str(state.get('stopReason') or state.get('probeStopReason'))
                        for path, state in search.get('paths', {}).items()))+'</p>')
    rows=[]
    for path,r in stage.points:
        eligible=r.get('classes',{}).get('eligible',{})
        status='测量无效' if not r.get('valid') else '达标' if r.get('sloPassed') else '有效测量 / 未达标'
        rows.append([path.name,NAMES.get(r.get('path'),r.get('path','—')),fmt(r.get('actualRequestRps')),
                     fmt(eligible.get('responseMs',{}).get('samples')),fmt(eligible.get('responseMs',{}).get('p99')),
                     fmt((r.get('completionMs') or {}).get('p99')),fmt(eligible.get('errorRate')*100) if numeric(eligible.get('errorRate')) else '—',
                     fmt(r.get('completedWithinDeadline')),fmt(r.get('completionWindowRps')),fmt(r.get('peakPendingAccepted')),
                     fmt(r.get('peakHttpInFlight')),status])
    if rows: body.append(table(['测量点','方案','实际 RPS','有效样本','响应 p99 ms','完成 p99 ms','分类错误率 %','全程按时完成','窗口完成/s','待完成峰值','HTTP 在途峰值','状态'],rows))
    for path,r in stage.points:
        errors=r.get('correctnessErrors',[])+r.get('loadErrors',[])
        if errors: body.append('<p>'+text(path.name)+'：'+text('；'.join(errors))+'</p>')
        if stage.kind!='burst':
            timeline=charts.read_timeline(path/'timeline.csv')
            if timeline and r.get('path') in COLORS:
                body.append('<details><summary>'+text(path.name)+' · 测量时间线</summary>'+charts.timelines({r['path']:timeline})+'</details>')
    for file in sorted(stage.directory.glob('*/preparation.json')):
        value=read_json(file,{})
        if value.get('status')!='completed':
            body.append('<h3>数据准备未完成</h3>'+table(['测量点','步骤','错误'],[[file.parent.name,value.get('phase','—'),value.get('error','未记录')]]))
    for file in sorted(stage.directory.glob('**/measurement-error.json')):
        value=read_json(file,{})
        body.append('<h3>测量或观测中断 · '+text(file.parent.name)+'</h3><pre>'+text(json.dumps(value,ensure_ascii=False,indent=2))+'</pre>')
    terminal=read_json(stage.directory/'containers-final.json',{})
    if terminal:
        rows=[[v.get('service'),v.get('restartCount','—'),v.get('state',{}).get('Status','—'),v.get('state',{}).get('ExitCode','—'),v.get('state',{}).get('OOMKilled','—')] for v in terminal.get('containers',[])]
        body.append('<h3>结束时容器状态（清理前）</h3>'+table(['服务','重启次数','状态','退出码','OOMKilled'],rows))
    database=read_json(stage.directory/'mysql-settings.json')
    if database: body.append('<details><summary>MySQL 实际配置与版本</summary>'+table(['参数','实际值'],database.items())+'</details>')
    body.append(resources(stage.directory/'host-watch/samples.jsonl'))
    return ''.join(body)+'</details>'


def resources(path):
    if not path.exists(): return ''
    peaks={}
    throttled={}
    with path.open() as file:
        for line in file:
            try: sample=json.loads(line)
            except ValueError: continue
            for container in sample.get('containers') or []:
                memory=container.get('resources',{}).get('memory.current')
                if memory is not None: peaks[container['service']]=max(peaks.get(container['service'],0),int(memory))
                fields=dict(row.split() for row in container.get('resources',{}).get('cpu.stat','').splitlines())
                if 'nr_throttled' in fields:
                    value=int(fields['nr_throttled'])
                    first,_=throttled.get(container['service'],(value,value))
                    throttled[container['service']]=(first,value)
    if not peaks: return ''
    return '<details><summary>容器资源采样</summary>'+table(['服务','采样内存峰值','CPU nr_throttled 增量'],[(p,fmt(v/1048576)+' MiB',fmt(max(0,throttled[p][1]-throttled[p][0])) if p in throttled else '—') for p,v in peaks.items()])+'<p class="muted">采样峰值可能遗漏瞬时峰值；throttle 计数不代表完整 CPU 利用率，资源相关性不等于性能变化的唯一原因。</p></details>'


def environment_report(environment):
    host=environment.get('host') or {}
    allocation=environment.get('cpuAllocation') or {}
    budget=environment.get('targetBudget') or {}
    actions=environment.get('actions') or {}
    def quantity(value,unit): return f'{value:g} {unit}' if numeric(value) else '未采集'
    available=host.get('availableCpus')
    memory=host.get('memoryBytes')
    image=' / '.join(str(actions[key]) for key in ('runnerImage','runnerImageVersion') if actions.get(key))
    fields=[('CPU 型号',host.get('cpuModel') or '未采集'),
            ('Runner 可用逻辑 CPU',quantity(len(available) if isinstance(available,list) else None,'vCPU')),
            ('Runner 可见内存',quantity(round(memory/1024**3,2) if numeric(memory) else None,'GiB')),
            ('被测端 CPU 配额合计',quantity(budget.get('cpuLimit'),'vCPU')),
            ('被测端 CPU 集合',allocation.get('sut') or '未采集'),('发压端 CPU 集合',allocation.get('runner') or '未采集'),
            ('被测容器内存配额合计',quantity(budget.get('containerMemoryMiB'),'MiB')),
            ('被测端总内存预算',quantity(budget.get('machineMemoryMiB'),'MiB')),
            ('宿主余量预算',quantity(budget.get('hostReserveMiB'),'MiB')),
            ('Runner 镜像 / 版本',image or '未采集'),('运行平台',host.get('platform') or '未采集'),('内核版本',host.get('kernel') or '未采集')]
    return '<section id="environment">'+heading('实验环境','本次 Runner 采集值与被测端资源预算','03')+'<div class="environment"><dl class="environment-grid">'+''.join(f'<div><dt>{text(k)}</dt><dd>{text(v)}</dd></div>' for k,v in fields)+'</dl><p class="env-note">CPU 型号中的核心数不代表实验分配的核心数；Runner 可见内存不等于被测容器配额。宿主余量为预算，不是硬预留。</p></div></section>'


def overview(suite,stages,actions,status,scope):
    cards=[]
    for plan,label in (('redis-filtering','Redis 过滤'),('kafka-buffering','Kafka 异步化')):
        stage=next((s for s in stages if s.plan==plan and s.kind=='capacity'),None)
        if stage is None and suite.get('mode') not in (None,'full','smoke',plan): continue
        if plan=='redis-filtering':
            comparison=stage.latency_comparison if stage else None
            value=f'{-comparison["latencyReductionPct"]["p50"]:+.2f}<small>%</small>' if comparison else '尚无结论'
            note=f'{fmt(comparison["rate"])} RPS · 双方均达标的最高共同档位' if comparison else '缺少完整合格的延迟对照'
            cards.append(metric(label,'有效购买 p99',value,note,comparison is not None))
        gain=stage.gain if stage else None
        cards.append(metric(label,'已测通过档位容量',f'{gain:+.2f}<small>%</small>' if gain is not None else '边界未确定' if stage else '未执行',
                            '容量增幅保留上下界误差' if gain is not None else '保留已有观测，不把缺失数据记为零',gain is not None))
        if plan=='kafka-buffering':
            burst=next((s for s in stages if s.plan==plan and s.kind=='burst'),None)
            groups=burst.burst_groups() if burst else []
            group=groups[0] if groups else None
            value=f'{group["delta"]["p50"]:+,.0f}<small> 单</small>' if group else '尚无结论' if burst else '未执行'
            note=(group['label']+' · '+('突发段请求' if group['cohort'] else '整场含低负载段')) if group else '缺少本次完整有效突发对照'
            cards.append(metric(label,'按时完成变化',value,note,group is not None))
    badges={'completed':'本次执行完成','inconclusive':'容量边界尚未确定','failed':'测量中断 · 证据不足'}
    badge=badges[status] if scope=='performance' else '流程验证 · 不输出性能结论'
    body=f'<header id="overview"><div class="hero"><div><div class="eyebrow">PERFORMANCE REPORT</div><h1>压测报告</h1><p>对照不同方案，分别评价响应延迟、稳定容量与突发业务收益。</p></div><span class="badge {"warn" if status!="completed" or scope!="performance" else ""}">{badge}</span></div>'
    body+='<div class="summary">'+''.join(cards)+'</div>'
    if scope!='performance': body+='<p class="warning">流程验证：仅核对执行、采集和业务一致性，不输出正式性能提升结论，不能作为正式突发基线。</p>'
    if status!='completed': body+='<p class="warning">运行未完整完成。保留已有观测，不以部分结果声称完整实验成功。</p>'
    if status=='inconclusive': body+='<p class="summary-note">容量边界尚未确定；已有有效下限保留，缺少合格基线时不执行突发。</p>'
    if suite.get('error'): body+='<p class="failed">'+text(suite['error'])+'</p>'
    for label,key in (('未执行的阶段与原因','skipped'),('未确定的容量边界','inconclusive')):
        if suite.get(key):
            body+='<details class="observations"><summary>'+label+'</summary>'+table(['实验','阶段 / 方案','原因'],[[v.get('plan','—'),NAMES.get(v.get('path'),v.get('stage','—')),v.get('reason','—')] for v in suite[key]])+'</details>'
    run_url=actions.get('runUrl')
    body+='<p class="site-meta">Run '+text(actions.get('runId','未记录'))+' / attempt '+text(actions.get('attempt','未记录'))+' · Commit '+text(actions.get('commit','未记录'))
    if valid_run_url(run_url): body+=f' · <a href="{text(run_url)}">Actions 与完整证据 ↗</a>'
    return body+'</p></header>'


def metric(label,title,value,note,qualified):
    return f'<div class="metric"><div class="metric-top"><span class="tag {"purple" if label=="Kafka 异步化" else ""}">{label}</span>{title}</div><div class="metric-value {"purple-text" if label=="Kafka 异步化" and qualified else "teal" if qualified else "quiet"}">{value}</div><div class="metric-note">{text(note)}</div></div>'


def methods(stages):
    rows=[]
    for stage in stages:
        if stage.policy:
            rows.append([stage.plan+' / '+stage.kind,fmt(stage.policy.get('responseP99Ms')),fmt(stage.policy.get('completionP99Ms')),fmt(stage.parameters.get('repeats')),fmt(stage.parameters.get('seconds')),fmt(stage.policy.get('minSamples'))])
    differences=any(s.meta.get('experimentDifferences') for s in stages)
    body='<details class="methods" id="methods"><summary>实验方法与阅读说明</summary><div class="methods-body"><div><b>延迟与降幅</b>p99 表示约 99% 的响应不超过该时长。先计算相同压力、同一轮两种方案的降幅，再汇总中位数与范围。图中 p99 为各轮 p99 的中位数，不是合并请求的 p99；范围不是置信区间。</div><div><b>代表档位</b>延迟摘要选择双方均达标的最高已测共同压力，不挑降幅最大的点。其他档位保留在曲线和测量明细中；对照未完成时不生成降幅。</div><div><b>容量边界</b>实心点是已证实通过档位，空心点是有效测量中未达标的档位。已测档位增幅比较两个最高通过点；增幅范围由通过 / 未达标边界推导，不是置信区间。发压无效、样本不足与观测中断不构成失败上界。</div><div><b>适用范围</b>Redis 过滤实验使用固定混合流量，异步化实验使用全有效购买；两组收益不能直接相乘。完成期限从每笔请求发起时计算；突发段和整场计数按标注区分。</div></div>'
    if rows: body+=table(['实验','响应期限 ms','完成期限 ms','计划轮次','容量窗口秒','最少有效样本'],rows)
    if differences: body+='<p class="muted">实验关闭定时补偿、超时处理、对账和定时预热任务，启用提交 / SQL 观测；前端计入预算但不施加浏览器负载。结果仅代表本次实验条件。</p>'
    return body+'</details>'
