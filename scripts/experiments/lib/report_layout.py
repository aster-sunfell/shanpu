"""Chart-first, self-contained report layout shared by formal and partial runs."""
from html import escape
import json
from pathlib import Path

import report_charts as charts
from report_charts import COLORS, NAMES, empty, fmt, legend, numeric
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


METRICS = {
    'eligibleResponseP95Ms':'有效购买响应 p95 / ms',
    'eligibleSuccessfulResponseP99Ms':'成功购买响应 p99 / ms',
    'eligibleOnTimeSuccessPct':'有效购买按时成功率 / %',
    'eligibleCompletionPct':'有效购买最终完成率 / %',
    'burst.responseP99Ms':'突发请求受理响应 p99 / ms',
    'burst.onTimeAcceptedPct':'突发请求按时受理率 / %',
    'burst.onTimeCompletedPct':'突发请求按时完成率 / %',
    'burst.completionPct':'突发请求最终完成率 / %',
    'businessSqlPer1000Requests': '每千请求业务 SQL 调用',
    'syncAttemptsPer1000Requests': '每千请求同步事务尝试',
    'eligibleResponseP99Ms': '有效购买响应 p99 / ms',
    'duplicateResponseP99Ms': '重复拒绝响应 p99 / ms',
    'completionP99Ms': '完成 p99 / ms', 'completed': '最终完成订单数',
    'eligibleErrorRate': '有效请求错误比例',
    'burst.acceptedWithinResponseDeadline': '突发请求按响应期限受理数',
    'burst.completedWithinDeadline': '突发请求按完成期限完成数',
    'burst.completed': '突发请求最终完成数',
    'peakPendingAccepted': '已受理待完成峰值', 'drainMs': '停止注入后排空 / ms',
}


def experiment_report(experiment):
    e = experiment
    body = [schemes(e.paths), '<section id="results">'+heading('负载校准' if e.artifact.get('calibration') else '固定负载配对结果', '仅用于人工选择负载，不计算方案收益。' if e.artifact.get('calibration') else '各轮交替对照；保留零收益和负收益，不要求双方 SLO 均达标。')]
    parameters=e.parameters
    profile=(f"{parameters.get('rate', '—')} RPS × {parameters.get('seconds', '—')} 秒；90% 重复 / 10% 有效购买"
             if e.plan == 'redis-filtering' else
             f"{parameters.get('base_rate', '—')} RPS × {parameters.get('base_seconds', '—')} 秒 → "
             f"{parameters.get('burst_rate', '—')} RPS × {parameters.get('burst_seconds', '—')} 秒 → "
             f"{parameters.get('recovery_rate', '—')} RPS × {parameters.get('recovery_seconds', '—')} 秒；全部有效购买")
    calibration = e.artifact.get('calibration')
    if calibration:
        body.append('<p>负载校准：只测基线路径的三个预设档位，不计算方案收益。</p>')
        body.append(table(['基线 RPS','输入有效','响应 p99 ms','网络/超时/内部错误','非预期响应','池等待次数','候选'],[
            [r['rate'],r['valid'],fmt(r['p99Ms']),r['networkErrors'],r['responseErrors'],r['poolWaitCount'],r['eligibleCandidate']]
            for r in calibration['candidates']]))
        body.append('<p>建议负载：'+text(calibration['recommendedRate'] or '暂无合适候选')+
                    '。规则：选择输入有效、无非预期响应、p99 在 300–1000ms 且有数据库等待或积压的最低档位。'
                    '人工审查后在源码中冻结负载，再发起新的 measurement；不会自动采用建议，也不读取历史结果。</p>')
    else:
        body.append('<p>'+text(profile)+'</p>')
    if not calibration: body.append(table(['每路径重复次数','固定 VU 数','排空上限 / 秒'], [[parameters.get('repeats'),parameters.get('vus'),parameters.get('drain_seconds')]]))
    body.append('<p>服务质量目标：响应 p99 ≤ '+fmt(e.policy.get('responseP99Ms'))+' ms，完成 p99 ≤ '
                +fmt(e.policy.get('completionP99Ms'))+' ms，分类错误比例 ≤ '+fmt(e.policy.get('maxErrorRate'))+'。</p>')
    metrics = e.comparisons[0]['metrics'] if e.comparisons else {}
    primary = ('eligibleResponseP99Ms', 'eligibleOnTimeSuccessPct') if e.plan == 'redis-filtering' else (
        'burst.onTimeAcceptedPct', 'burst.onTimeCompletedPct')
    for key in (() if calibration else primary):
        value = metrics.get(key)
        body.append(panel(METRICS[key], '柱为每轮值的中位数，线为最小至最大值',
                          charts.paired([value['before'], value['after']] if value else [], e.paths, METRICS[key]),
                          ('后方案减前方案：'+fmt(value['afterMinusBefore']['p50'])+(' 个百分点' if key.endswith('Pct') else '')+
                           ('；配对降幅中位数 '+fmt(value['pairedReductionPct']['p50'])+'%' if key.endswith('Ms') and value.get('pairedReductionPct') else '')) if value else '尚无完整有效比较。', e.paths))
    if not calibration: body.append(table(['指标','前方案中位数','后方案中位数','配对差值中位数','配对差值范围'], [
        [METRICS.get(k,k), fmt(v['before']['p50']),fmt(v['after']['p50']),fmt(v['afterMinusBefore']['p50']),
         fmt(v['afterMinusBefore']['min'])+' ～ '+fmt(v['afterMinusBefore']['max'])] for k,v in metrics.items()]))
    body.append('<p>按时成功率以所有有效购买请求为分母，同时要求响应成功且满足响应期限、订单满足完成期限。百分比指标差值用百分点表示。SQL/事务尝试只用于解释机制。每轮 p99 单独计算；不把多个 p99 平均成总体分位数。业务 SQL 来自 mapper 计数，事务尝试不等于提交。'
                'Kafka 的 PENDING 仅为受理；完成量以提交证据为准。突发指标只计算突发段请求。</p></section>')
    body.append('<section id="observations">'+heading('逐轮观测与时间线','无效点仅供诊断，不参与收益比较。'))
    for directory,r in e.points:
        state = '测量无效' if not r.get('valid') else '有效 / 达标' if r.get('sloPassed') else '有效 / 未达标'
        body.append('<details class="observations"><summary>'+text(directory.name)+' · '+state+'</summary>')
        body.append(table(['发出/计划','完成/有效请求','错误数','HTTP 在途峰值','待完成峰值','排空 ms'], [[
            f"{r.get('started')}/{r.get('scheduled')}",f"{r.get('completed')}/{r.get('eligible')}",
            sum(c.get('failed',0) for c in r.get('classes',{}).values()),r.get('peakHttpInFlight'),r.get('peakPendingAccepted'),fmt(r.get('drainMs'))]]))
        body.append(table(['请求分类','响应 p95 ms','响应 p99 ms','成功响应 p99 ms','错误比例'], [[k,fmt(c.get('responseMs',{}).get('p95')),fmt(c.get('responseMs',{}).get('p99')),fmt(c.get('successfulResponseMs',{}).get('p99')),fmt(c.get('errorRate'))] for k,c in r.get('classes',{}).items()]))
        body.append('<p>'+text('; '.join(r.get('loadErrors',[])+r.get('correctnessErrors',[])))+'</p>')
        if r.get('networkErrors',{}).get('groups'):
            body.append('<pre>'+text(json.dumps(r['networkErrors'],ensure_ascii=False,indent=2))+'</pre>')
        base = r.get('baseSeconds',0)
        body.append(charts.timeline(directory/'timeline.csv', base, base+r.get('seconds',0) if base else 0,
                                   base+r.get('seconds',0)+r.get('recoverySeconds',0)))
        body.append('</details>')
    for pattern in ('**/measurement-error.json','**/preparation.json','warmup.json','containers-final.json'):
        for path in e.directory.glob(pattern):
            value=read_json(path,{})
            if pattern.endswith('preparation.json') and value.get('status') == 'completed': continue
            body.append('<details><summary>'+text(str(path.relative_to(e.directory)))+'</summary><pre>'+text(json.dumps(value,ensure_ascii=False,indent=2))+'</pre></details>')
    if e.meta.get('error'): body.append('<p>'+text(e.meta['error'])+'</p>')
    body.append('<details><summary>实验配置与源码</summary><pre>'+text(json.dumps({'parameters':e.parameters,'policy':e.policy,'source':e.meta.get('source'),'contract':e.meta.get('contract')},ensure_ascii=False,indent=2))+'</pre></details>')
    body.append(resources(e.directory/'host-watch/samples.jsonl'))
    body.append('</section>')
    return ''.join(body)
