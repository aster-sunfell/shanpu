"""Chart-first, self-contained report layout shared by formal and partial runs."""
from html import escape
from pathlib import Path

import report_charts as charts
from report_charts import fmt, legend, numeric
from report_data import read_json

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
            '<span>CPU 资源设置不等同于独占物理核或线上容量。</span></footer>'
            f'</main><script>{SCRIPT}</script></body></html>')


def heading(title,subtitle='',number=''):
    prefix=f'<span class="section-number">{text(number)}</span>' if number else ''
    return f'<div class="section-heading"><div><h2>{prefix}{text(title)}</h2><p>{text(subtitle)}</p></div></div>'


def panel(title,subtitle,graphic,note,paths=(),names=None):
    return (f'<article class="panel"><div class="panel-head"><div><h3>{text(title)}</h3>'
            f'<div class="panel-sub">{text(subtitle)}</div></div>{legend(paths,names) if paths else ""}</div>'
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
            f'本次测量 · 固定资源预算{evidence}</div></aside>')


def valid_run_url(url):
    return isinstance(url,str) and url.startswith('https://github.com/')


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
            ('被测端共享逻辑 CPU 数' if budget.get('cpuModel')=='shared-cpuset' else '被测端 CPU 配额合计',quantity(budget.get('cpuLimit'),'vCPU')),
            ('CPU 分配模式',budget.get('cpuModel') or '历史逐服务配额'),
            ('被测端 / 发压端 CPU 集合',str(allocation.get('sut','—'))+' / '+str(allocation.get('runner','—'))),
            ('被测容器内存合计',quantity(budget.get('containerMemoryMiB'),'MiB')),
            ('被测部署内存预算',quantity(budget.get('machineMemoryMiB'),'MiB')),
            ('Runner 镜像 / 版本',image or '未采集'),('内核',host.get('kernel') or '未采集')]
    return '<section id="environment">'+heading('实验环境','本次 Runner 采集值与被测端资源预算')+'<div class="environment"><dl class="environment-grid">'+''.join(f'<div><dt>{text(k)}</dt><dd>{text(v)}</dd></div>' for k,v in fields)+'</dl><p class="env-note">逻辑 CPU 可能是同一核心的 SMT 线程，不代表独占物理核。CPU 型号中的核心数不代表实验分配的核心数；Runner 可见内存不等于被测容器配额。宿主余量为预算，不是硬预留。</p></div></section>'


from report_metrics import EXPERIMENTS, LABELS, paired_rows, summarize


def metric_panel(e,key):
    rows=paired_rows(e.points,e.paths,key)
    summary=summarize(rows) if e.comparisons else None
    threshold=(e.policy.get('responseP99Ms') if key=='burst.responseP99Ms' else
               e.policy.get('completionP99Ms') if key=='burst.completionP99Ms' else None)
    note='尚无完整有效比较；逐轮观测仅供诊断。'
    if summary:
        unit=' 个百分点' if key.endswith('Pct') else ' ms'
        note=f"{e.names[e.paths[1]]} − {e.names[e.paths[0]]}：配对差值中位数 {fmt(summary['difference'])}{unit}，范围 {fmt(summary['min'])} ～ {fmt(summary['max'])}{unit}。"
        if key.endswith('Ms') and summary['reduction'] is not None:
            note+=f"逐轮配对降幅中位数 {fmt(summary['reduction'])}%。"
    subtitle='每个点代表一轮观测，连线对应同一轮两种方案。'
    if key in ('burst.onTimeAcceptedPct','burst.onTimeCompletedPct'):
        limit=e.policy.get('responseP99Ms' if key.endswith('AcceptedPct') else 'completionP99Ms')
        subtitle=f'期限 {fmt(limit)} ms；以全部突发请求为分母，不隐藏未达期限的轮次。'
    if key in ('burst.onTimeAcceptedPct','burst.onTimeCompletedPct'):
        below=[f'第 {i} 轮 {e.names[path]} {fmt(v)}%' for i,a,b in rows for path,v in zip(e.paths,(a,b)) if numeric(v) and v<100]
        if below: note+=' 未全部按时'+('受理' if key.endswith('AcceptedPct') else '完成')+'：'+'；'.join(below)+'。'
    return panel(LABELS[key],subtitle,charts.rounds(rows,e.paths,LABELS[key],threshold,key.endswith('Pct'),e.names),text(note),e.paths,e.names)


def experiment_report(e):
    specification=EXPERIMENTS[e.plan]
    p=e.parameters
    profile=(f"{p.get('rate','—')} RPS × {p.get('seconds','—')} 秒；90% 重复 / 10% 有效购买"
             if e.plan=='redis-filtering' else
             f"{p.get('base_rate','—')} RPS × {p.get('base_seconds','—')} 秒 → {p.get('burst_rate','—')} RPS × {p.get('burst_seconds','—')} 秒 → {p.get('recovery_rate','—')} RPS × {p.get('recovery_seconds','—')} 秒；全部有效购买")
    body=['<section id="performance">'+heading('性能对照',profile)]
    body.append(f'<p>{text(e.names[e.paths[0]])} 与 {text(e.names[e.paths[1]])}，每路径 {text(p.get("repeats","—"))} 轮交替配对。</p>')
    if not e.comparisons:
        body.append('<p class="warning">尚无完整有效比较。保留观测，不输出收益结论。</p>')
    body.append('<div class="metrics-grid">')
    for key in specification['primary']: body.append(metric_panel(e,key))
    body.append('</div></section>')
    if e.plan=='kafka-burst':
        body.append('<section id="cost">'+heading('完成等待与恢复','只计算突发批次；限时完成不等于最终完成，快速受理不等于吞吐提升。')+'<div class="metrics-grid">')
        for key in specification['cost']:body.append(metric_panel(e,key))
        body.append('</div><p>恢复时间从突发结束算到突发批次最后一个订单提交，早于突发结束则为 0；未全部完成或证据缺失显示为 —。它不等于停止全部注入后的排空时间。</p></section>')
        body.append('<section id="timeline">'+heading('突发过程','所有轮次、两种方案使用相同时间轴与纵轴尺度；默认第 1 轮。'))
        body.append(charts.comparison_timeline(e.points,e.paths,e.names))
        body.append('<p>未完成请求包含已发出但尚未提交的请求，可比较同步与异步方案。已受理待完成仅表示回复后仍未提交的订单，不代表 Kafka 消息队列长度。实线为未完成请求，虚线为已受理待完成；数值接近时两条线会重合。速率单位为每秒订单数，积压单位为订单数。</p></section>')
    body.append('<section id="validity">'+heading('数据与质量检查','有效性与服务达标分开判定；未达服务目标仍是实验结果。'))
    for path in e.paths:
        rows=[r for _,r in e.points if r['path']==path]
        if rows:
            complete=sum(r.get('completed',0) for r in rows);eligible=sum(r.get('eligible',0) for r in rows)
            errors=sum(c.get('failed',0) for r in rows for c in r.get('classes',{}).values())
            body.append('<p>'+text(e.names[path])+f'：{sum(bool(r.get("valid")) for r in rows)}/{len(rows)} 个测量点输入与数据有效；最终完成 {complete}/{eligible} 个有效购买；错误数 {errors}。</p>')
    warm=read_json(e.directory/'warmup.json',{})
    if warm:body.append('<p>预热：'+text({'completed':'通过','failed':'失败','inconclusive':'未通过'}.get(warm.get('status'),'未完成'))+'。</p>')
    if warm.get('error'):body.append('<p class="warning">'+text(warm['error'])+'</p>')
    final=read_json(e.directory/'containers-final.json',{})
    bad=[r.get('service','未知服务') for r in final.get('containers',[]) if r.get('state',{}).get('OOMKilled') or r.get('restartCount',0) or r.get('state',{}).get('ExitCode',0) or (r.get('service')!='load-runner' and not r.get('state',{}).get('Running',False))]
    if final:body.append('<p'+(' class="warning"' if bad else '')+'>'+('容器异常：'+text('、'.join(bad)) if bad else '容器检查：未发现重启、OOM 或异常退出。')+'</p>')
    for pattern in ('**/measurement-error.json','**/preparation.json'):
        for path in e.directory.glob(pattern):
            value=read_json(path,{})
            if pattern.endswith('preparation.json') and value.get('status')=='completed':continue
            body.append('<p class="warning">'+text(value.get('error') or value.get('errors') or '准备或测量失败，请查看证据包。')+'</p>')
    if e.meta.get('error'):body.append('<p class="warning">'+text(e.meta['error'])+'</p>')
    body.append('</section><section id="observations">'+heading('详细数据与统计口径'))
    body.append('<p>方案列为各轮指标的中位数；配对差值先逐轮相减再取中位数，可能不等于两列中位数相减。比例差值用百分点。缺失数据不补零；无效点不计算收益。</p>')
    if e.comparisons:
        keys=specification['primary']+specification.get('cost',())
        summaries=[(key,summarize(paired_rows(e.points,e.paths,key))) for key in keys]
        body.append(table(['指标',e.names[e.paths[0]],e.names[e.paths[1]],'配对差值中位数','配对差值范围'],[
            [LABELS[key],fmt(v['before']),fmt(v['after']),fmt(v['difference']),fmt(v['min'])+' ～ '+fmt(v['max'])] for key,v in summaries if v]))
    body.append(table(['轮次','方案','输入有效','服务目标','发出 / 计划','最终完成 / 有效购买','错误数'],[
        [r.get('repeat'),e.names[r['path']],'是' if r.get('valid') else '否','达标' if r.get('sloPassed') else '未达标',f"{r.get('started')}/{r.get('scheduled')}",f"{r.get('completed')}/{r.get('eligible')}",sum(c.get('failed',0) for c in r.get('classes',{}).values())] for _,r in e.points]))
    for _,r in e.points:
        errors=r.get('loadErrors',[])+r.get('correctnessErrors',[])
        if errors:body.append('<p class="warning">'+text(f"第 {r.get('repeat')} 轮 {e.names[r['path']]}："+'; '.join(errors))+'</p>')
    durations=[]
    if warm.get('elapsedSeconds') is not None:durations.append(['预热（含准备与核对）',warm['elapsedSeconds']])
    if e.meta.get('phaseTimings',{}).get('measurementSeconds') is not None:durations.append(['正式测量（含准备与核对）',e.meta['phaseTimings']['measurementSeconds']])
    if durations:body.append(table(['实验阶段','秒'],durations))
    body.append('<p>完整请求、提交日志、预热、容器状态、配置和资源采样保存在 performance-evidence；这些检查仍执行，不占用成功报告正文。</p></section>')
    return ''.join(body)
