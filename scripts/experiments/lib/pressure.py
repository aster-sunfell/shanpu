"""Validate exact transaction buckets; sampled connection/lag evidence stays separate."""
import csv
import json
import math
from pathlib import Path


def attach_pressure(result, config, before, after, directory):
    raw = after.get('transactionPressure') or {}
    errors = []
    rows = raw.get('buckets', [])
    attempts = sum(result.get('backendDelta', {}).get(k, 0) for k in ('syncAttempts', 'asyncAttempts'))
    if raw.get('version') != 2 or raw.get('activityId') != config['activityId']:
        errors.append('Transaction pressure is missing or belongs to another activity')
    initial = before.get('transactionPressure') or {}
    if (initial.get('version') != 2 or initial.get('activityId') != config['activityId']
            or not initial.get('windowId') or initial.get('windowId') != raw.get('windowId')):
        errors.append('Transaction measurement window is missing or changed')
    if (any(initial.get(k) != 0 for k in ('started','ended','active','peakActive','errors'))
            or initial.get('buckets') != []):
        errors.append('Transaction window was not empty before measurement')
    if raw.get('errors') or raw.get('active') != 0 or raw.get('started') != raw.get('ended'):
        errors.append('Transaction pressure was truncated, clock shifted, or transactions remain active')
    fields = ('epochSecond','starts','ends','notCommitted','activePeak','activeEnd')
    well_formed = bool(rows) and all(all(isinstance(r.get(k), int) and not isinstance(r[k], bool) and r[k] >= 0 for k in fields) for r in rows)
    if not well_formed:
        errors.append('Transaction pressure buckets missing or malformed')
    timeline = []
    if well_formed:
        indexed = {r['epochSecond']:r for r in rows}
        if len(indexed) != len(rows) or sum(r['starts'] for r in rows) != raw.get('started') or sum(r['ends'] for r in rows) != raw.get('ended'):
            errors.append('Transaction bucket totals do not match counters')
        if not result['completed'] <= raw.get('started', -1) <= attempts:
            errors.append('Transaction counts do not cover committed orders or exceed writer attempts')
        first = math.floor(result['originMs']/1000)
        last = math.floor(result['cutoffMs']/1000)
        if min(indexed) < first or max(indexed) > last:
            errors.append('Transaction buckets fall outside the observed point')
        active = 0
        for second in range(first,last+1):
            row = indexed.get(second, {'starts':0,'ends':0,'notCommitted':0,'activePeak':active,'activeEnd':active})
            if (row['activeEnd'] != active+row['starts']-row['ends'] or
                    row['activePeak'] < max(active,row['activeEnd']) or row['activePeak'] > active+row['starts'] or
                    row['notCommitted'] > row['ends']):
                errors.append('Transaction concurrency buckets are inconsistent')
            active = row['activeEnd']
            timeline.append({'second':round(second-result['originMs']/1000,3), 'transactionStarts':row['starts'],
                             'transactionActivePeak':row['activePeak'], 'transactionEnds':row['ends']})
        if max((r['activePeak'] for r in rows),default=0) != raw.get('peakActive'):
            errors.append('Transaction concurrency peak does not match buckets')
    valid = not errors
    result['pressure'] = {'valid':valid, 'errors':sorted(set(errors)), 'bucketSeconds':1,
        'window':'entire-point-including-recovery-and-drain',
        'transactionScope':'first-mapper-through-commit-or-rollback-before-evidence-callbacks',
        'peakStartRps':max((r['transactionStarts'] for r in timeline),default=None) if valid else None,
        'peakActive':raw.get('peakActive') if valid else None,
        'transactionAttempts':raw.get('started'),
        'notCommitted':sum(r['notCommitted'] for r in rows) if well_formed else None,
        'poolWaitMs':result.get('backendDelta',{}).get('poolWaitMs'),
        'poolWaitCount':result.get('backendDelta',{}).get('poolWaitCount')}
    with (Path(directory)/'database-timeline.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=['second','transactionStarts','transactionActivePeak','transactionEnds'])
        writer.writeheader()
        if valid: writer.writerows(timeline)
    # These are samples, not exact maxima; retain their real timestamps and gaps.
    samples=[]
    source=Path(directory)/'samples.jsonl'
    if source.exists():
        with source.open() as stream:
            for line in stream:
                row=json.loads(line);backend=row.get('backend',{})
                stamp=backend.get('timeMs',row['timeMs'])
                if result['originMs'] <= stamp <= result['cutoffMs']:
                    samples.append({'second':round((stamp-result['originMs'])/1000,3),
                        'poolActive':backend.get('poolActive'),'poolWaitThreads':backend.get('poolWaitThreads'),
                        'kafkaLag':None})
                lag_stamp=row.get('kafkaLagTimeMs',row['timeMs'])
                if 'kafkaLag' in row and result['originMs'] <= lag_stamp <= result['cutoffMs']:
                    samples.append({'second':round((lag_stamp-result['originMs'])/1000,3), 'poolActive':None,
                                    'poolWaitThreads':None, 'kafkaLag':sum(row['kafkaLag'].values())})
    samples.sort(key=lambda s:s['second'])
    (Path(directory)/'pressure-samples.json').write_text(json.dumps(samples)+'\n')
    result['pressure']['sampledPoolActivePeak']=max((s['poolActive'] for s in samples if s['poolActive'] is not None),default=None)
    result['pressure']['sampledPoolWaitThreadsPeak']=max((s['poolWaitThreads'] for s in samples if s['poolWaitThreads'] is not None),default=None)
