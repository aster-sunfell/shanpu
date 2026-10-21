"""Predeclared warmup stability bands. Missing evidence is never interpreted as stability."""
import statistics
from analysis import records

BANDS = {'eligibleP95Ms':30, 'cpuCores':.1, 'poolWaitMsPerRequest':2}


def assess(result, samples_path):
    windows = result.get('warmupWindows', [])[-3:]
    samples = list(records(samples_path)) if samples_path.exists() else []
    rows, errors = [], []
    for window in windows:
        q = window['eligibleResponseMs']
        row = {'startMs':window['startMs'], 'eligibleP95Ms':q.get('p95')}
        points = [s for s in samples if window['startMs'] <= s['timeMs'] < window['endMs'] and 'backend' in s]
        if q.get('samples',0) < 100 or len(points) < 2 or not window['requests']:
            errors.append('Insufficient eligible responses or backend samples in a warmup window')
        else:
            first,last = points[0],points[-1]
            span = last['timeMs']-first['timeMs']
            for key,counter,scale in (('cpuCores','processCpuTimeNs',1e6), ('poolWaitMsPerRequest','poolWaitMs',None)):
                a,b = first['backend'].get(counter),last['backend'].get(counter)
                if a is None or b is None or b<a or span<5000:
                    errors.append('Missing or reset warmup counter: '+counter)
                else:
                    row[key] = (b-a)/span/scale if scale else (b-a)*10000/span/window['requests']
        rows.append(row)
    if len(rows)!=3: errors.append('Three complete 10-second warmup windows required')
    for key,floor in BANDS.items():
        values = [r[key] for r in rows if r.get(key) is not None]
        if len(values)!=3: errors.append('Incomplete warmup stability metric: '+key)
        elif max(values)-min(values) > max(floor, .5*statistics.median(values)):
            errors.append('Warmup metric is still drifting: '+key)
    if not result['valid'] or any(c['failed'] for c in result['classes'].values()) or result['completed']!=result['eligible']:
        errors.append('Warmup load, responses or completed orders are not healthy')
    return {'stable':not errors,'windows':rows,'errors':errors,
            'bands':{'relativeRange':.5,'absoluteRange':BANDS,'minimumEligibleSamplesPerWindow':100}}
