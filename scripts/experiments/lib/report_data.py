"""Read report metadata and expose only comparisons qualified by the protocol."""
import json
from pathlib import Path
from statistics import median

from report_charts import numeric


def read_json(path, default=None):
    path = Path(path)
    if not path.exists(): return default
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Unexpectedly large report metadata: ' + path.name)
    return json.loads(path.read_text())


class Stage:
    def __init__(self, directory, scope=None):
        self.directory=Path(directory)
        self.meta=read_json(self.directory/'manifest.json', {})
        self.artifact=read_json(self.directory/'capacity.json') or read_json(self.directory/'burst.json') or {}
        self.plan=self.meta.get('plan') or self.artifact.get('plan') or self.directory.parent.name
        self.kind=self.meta.get('stage') or self.artifact.get('stage') or 'unknown'
        self.paths=('B','C') if self.plan=='kafka-buffering' else ('A','B')
        self.parameters=self.artifact.get('parameters') or {}
        self.policy=self.artifact.get('policy') or {}
        self.repeats=self.parameters.get('repeats',3)
        self.formal=(scope in (None,'performance') and self.artifact.get('scope')=='performance')
        self.completed=self.meta.get('status')==self.artifact.get('status')=='completed'
        self.qualified=self.formal and self.completed and self.repeats>=3
        self.bounds=self.artifact.get('paths') or {}
        self.observations={(p,r['rate']):r.get('state') for p,v in self.bounds.items() for r in v.get('observations',[]) if numeric(r.get('rate'))}
        self.points=[]
        for file in sorted(self.directory.glob('*/result.json')):
            value=read_json(file)
            if value and value.get('phase') != 'probe': self.points.append((file.parent,value))

    @property
    def rows(self): return [value for _,value in self.points]

    @property
    def comparisons(self):
        if not self.qualified: return []
        return [item for item in self.artifact.get('comparisons',[])
                if item.get('validPairs')==item.get('expectedPairs') and item.get('expectedPairs',0)>=3]

    @property
    def latency_comparison(self):
        eligible=[item for item in self.comparisons if numeric((item.get('latencyReductionPct') or {}).get('p50'))]
        # Use the highest common qualified load, never the largest observed improvement.
        return max(eligible,key=lambda item:item['rate'],default=None)

    @property
    def gain(self):
        if not self.qualified or not all(self.bounds.get(p,{}).get('bounded') and not self.bounds[p].get('nonMonotonic') for p in self.paths): return None
        value=self.artifact.get('capacityGainPct')
        return value if numeric(value) else None

    def burst_groups(self):
        result=[]
        for comparison in self.comparisons:
            rate=comparison['rate']
            rows=[r for r in self.rows if r.get('rate')==rate and r.get('valid') and r.get('path') in ('B','C')]
            if any(len([r for r in rows if r['path']==p])!=self.repeats
                   or {r.get('repeat') for r in rows if r['path']==p}!=set(range(1,self.repeats+1)) for p in ('B','C')): continue
            cohort=all((r.get('burst') or {}).get('scope')=='burst-requests' for r in rows)
            key='burstExtraCompletedOnTime' if cohort else 'extraCompletedOnTime'
            if not numeric((comparison.get(key) or {}).get('p50')): continue
            q=self.parameters.get('q')
            if not numeric(q) or q<=0:
                q=(read_json(self.directory/'baseline.json',{}).get('paths',{}).get('B') or {}).get('passingRps')
            label=f'{rate/q:g} 倍同步基线' if numeric(q) and q>0 else f'{rate:g} RPS'
            group={'rate':rate,'label':label,'cohort':cohort,'comparison':comparison,'delta':comparison[key],'rows':rows}
            for path in ('B','C'):
                values=[(r['burst'] if cohort else r).get('completedWithinDeadline') for r in rows if r['path']==path]
                if not all(numeric(v) for v in values): break
                group[path]={'median':median(values),'min':min(values),'max':max(values)}
            if all(p in group for p in ('B','C')): result.append(group)
        return sorted(result,key=lambda g:g['rate'])
