"""Read report metadata and expose only comparisons qualified by the protocol."""
import json
import sqlite3
from contextlib import closing
from report_metrics import EXPERIMENTS
from pathlib import Path



def read_json(path, default=None):
    path = Path(path)
    if not path.exists(): return default
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError('Unexpectedly large report metadata: ' + path.name)
    return json.loads(path.read_text())


class Experiment:
    def __init__(self, directory, scope=None):
        self.directory = Path(directory)
        self.meta = read_json(self.directory/'manifest.json', {})
        self.artifact = read_json(self.directory/'experiment.json', {})
        self.plan = self.meta.get('plan', self.directory.parent.name)
        if self.plan not in EXPERIMENTS: raise ValueError('Unknown experiment: '+self.plan)
        if self.artifact.get('plan',self.plan) != self.plan: raise ValueError('Experiment metadata mismatch')
        self.paths = EXPERIMENTS[self.plan]['paths']
        self.names = EXPERIMENTS[self.plan]['names']
        self.parameters = self.artifact.get('parameters', {})
        self.policy = self.artifact.get('policy', {})
        self.points = [(p.parent, read_json(p)) for p in sorted(self.directory.glob('*/result.json'))]
        for _, row in self.points:
            if (row.get('path') not in self.paths or row.get('plan',self.plan) != self.plan
                    or row.get('workload') != ('mixed' if self.plan=='redis-filtering' else 'burst')):
                raise ValueError('Point belongs to another experiment')
        formal = scope in (None, 'performance') and self.artifact.get('scope') == 'performance'
        self.comparisons = [c for c in self.artifact.get('comparisons', []) if c.get('qualified')] if (
            formal and self.meta.get('status') == self.artifact.get('status') == 'completed') else []

        # Re-rendering never writes to original evidence. Older result.json already
        # contains burst completion quantiles; derive only recovery from read-only SQL.
        for directory,row in self.points:
            burst=row.get('burst')
            if burst and 'recoveryMs' not in burst:
                burst['recoveryMs']=burst_recovery(directory,row)
        expected=self.parameters.get('repeats',0)
        keys=[(r.get('repeat'),r.get('path')) for _,r in self.points]
        if (not expected or len(keys)!=2*expected or len(set(keys))!=len(keys)
                or set(keys)!={(i,p) for i in range(1,expected+1) for p in self.paths}
                or any(not r.get('valid') for _,r in self.points)):
            self.comparisons=[]


def burst_recovery(directory,row):
    burst=row.get('burst') or {}
    if not burst.get('requests') or burst.get('completed')!=burst['requests']: return None
    path=directory/'evidence.sqlite'
    if not path.exists(): return None
    try:
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            end=db.execute('SELECT MIN(e) FROM starts').fetchone()[0]
            count,last=db.execute("SELECT COUNT(*),MAX(c.t) FROM starts s JOIN after_orders o ON o.uid=s.uid LEFT JOIN before_orders b ON b.oid=o.oid JOIN commits c ON c.oid=o.oid WHERE b.oid IS NULL AND s.kind='eligible' AND s.rid LIKE 'burst:%' AND c.t<=?",(row['cutoffMs'],)).fetchone()
            if count!=burst['requests'] or last is None or end is None: return None
            return max(0,last-(end-row.get('recoverySeconds',0)*1000))
    except (sqlite3.Error,KeyError): return None
