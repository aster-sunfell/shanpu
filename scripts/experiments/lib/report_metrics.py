"""Experiment-specific presentation contract; no cross-run or cross-plan comparisons."""
from statistics import median

NAMES = {'A':'数据库同步下单', 'B':'Redis 过滤后同步下单', 'C':'Kafka 异步下单'}
EXPERIMENTS = {
    'redis-filtering': {'paths':('A','B'), 'names':{'A':NAMES['A'],'B':NAMES['B']}, 'primary':('eligibleResponseP99Ms','eligibleResponseP95Ms','duplicateResponseP99Ms'),
                        'support':('businessSqlPer1000Requests','syncAttemptsPer1000Requests')},
    'kafka-burst': {'paths':('B','C'), 'names':{'B':'Redis 同步下单','C':NAMES['C']}, 'primary':('burst.responseP99Ms','burst.onTimeAcceptedPct'),
                    'cost':('burst.completionP99Ms','burst.onTimeCompletedPct','burst.recoveryMs')},
}
LABELS = {
    'eligibleResponseP99Ms':'有效购买响应 p99 / ms', 'eligibleResponseP95Ms':'有效购买响应 p95 / ms',
    'duplicateResponseP99Ms':'重复请求拒绝 p99 / ms',
    'businessSqlPer1000Requests':'每千请求业务 SQL 调用', 'syncAttemptsPer1000Requests':'每千请求同步事务尝试',
    'burst.responseP99Ms':'突发请求响应 p99 / ms', 'burst.onTimeAcceptedPct':'突发请求按时受理率 / %',
    'burst.completionP99Ms':'突发请求完成 p99 / ms', 'burst.onTimeCompletedPct':'突发请求按时完成率 / %',
    'burst.recoveryMs':'突发结束至突发批次全部完成 / ms',
}

def value(row, key):
    if key == 'eligibleResponseP99Ms': return row.get('classes',{}).get('eligible',{}).get('responseMs',{}).get('p99')
    if key == 'eligibleResponseP95Ms': return row.get('classes',{}).get('eligible',{}).get('responseMs',{}).get('p95')
    if key == 'duplicateResponseP99Ms': return row.get('classes',{}).get('duplicate',{}).get('responseMs',{}).get('p99')
    if key.startswith('burst.'):
        burst=row.get('burst') or {}
        field=key.split('.',1)[1]
        if field in ('responseP99Ms','completionP99Ms'):
            return burst.get('responseMs' if field=='responseP99Ms' else 'completionMs',{}).get('p99')
        return burst.get(field)
    delta=row.get('backendDelta',{})
    if not row.get('started'): return None
    if key=='businessSqlPer1000Requests' and 'sqlCalls' in delta:
        return sum(delta['sqlCalls'].values())*1000/row['started']
    if key=='syncAttemptsPer1000Requests' and 'syncAttempts' in delta:
        return delta['syncAttempts']*1000/row['started']
    return None


def paired_rows(points, paths, key):
    indexed={(r['repeat'],r['path']):r for _,r in points}
    return [(repeat, *(value(indexed.get((repeat,p),{}),key) for p in paths))
            for repeat in sorted({r['repeat'] for _,r in points})]


def summarize(rows):
    if not rows or any(a is None or b is None for _,a,b in rows): return None
    a=[a for _,a,_ in rows];b=[b for _,_,b in rows];diff=[y-x for x,y in zip(a,b)]
    return {'before':median(a),'after':median(b),'difference':median(diff),'min':min(diff),'max':max(diff),
            'reduction':median([(x-y)*100/x for x,y in zip(a,b)]) if all(x>0 for x in a) else None}
