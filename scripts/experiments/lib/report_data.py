"""Read report metadata and expose only comparisons qualified by the protocol."""
import json
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
        self.paths = ('A','B') if self.plan == 'redis-filtering' else ('B','C')
        self.parameters = self.artifact.get('parameters', {})
        self.policy = self.artifact.get('policy', {})
        self.points = [(p.parent, read_json(p)) for p in sorted(self.directory.glob('*/result.json'))]
        formal = scope in (None, 'performance') and self.artifact.get('scope') == 'performance'
        self.comparisons = [c for c in self.artifact.get('comparisons', []) if c.get('qualified')] if (
            formal and self.meta.get('status') == self.artifact.get('status') == 'completed') else []
