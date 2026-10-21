"""Read-only observation channel; business HTTP saturation must not hide evidence."""
import json
import os
from pathlib import Path
import time

MAX_METADATA_BYTES = 2*1024*1024


def root():
    return Path(os.environ.get('BENCH_EVIDENCE', '/bench/evidence'))


def metrics():
    path = root()/'metrics.json'
    try:
        if path.stat().st_size > MAX_METADATA_BYTES:
            raise RuntimeError('Observation metadata exceeds 2 MiB')
        value = json.loads(path.read_text())
        age = time.time()*1000-value['timeMs']
        if not -50 <= age <= 10000:
            raise RuntimeError(f'Observation snapshot is stale or clock shifted: ageMs={age:.0f}')
        if value['settings']['sutId'] != os.environ['SUT_INSTANCE_ID']:
            raise RuntimeError('Observation belongs to a different SUT instance')
        return value
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError('Observation snapshot unavailable or invalid: '+type(error).__name__) from error


def fresh_metrics(timeout=5):
    """Wait for a publication taken after the caller finished its preceding work."""
    after_ms = time.time()*1000
    deadline = time.monotonic()+timeout
    while True:
        value = metrics()
        if value['timeMs'] >= after_ms:
            return value
        if time.monotonic() >= deadline:
            raise RuntimeError('Observation publisher did not produce a fresh snapshot within the deadline')
        time.sleep(.05)


def commits(offset, activity):
    if offset < 0 or activity <= 0:
        raise RuntimeError('Invalid commit cursor or activity')
    path = root()/'commits.jsonl'
    if not path.exists():
        if offset: raise RuntimeError('Commit journal disappeared')
        return {'nextOffset': 0, 'events': [], 'hasMore': False}
    events = []
    with path.open('rb') as file:
        size = os.fstat(file.fileno()).st_size
        if offset > size: raise RuntimeError('Commit journal was truncated')
        file.seek(offset)
        next_offset = offset
        partial_tail = False
        while len(events) < 1000 and file.tell()-offset < MAX_METADATA_BYTES//2:
            start = file.tell()
            line = file.readline(MAX_METADATA_BYTES//2+1)
            if not line: break
            if len(line) > MAX_METADATA_BYTES//2: raise RuntimeError('Commit record exceeds 1 MiB')
            if not line.endswith(b'\n'):
                file.seek(start)
                partial_tail = True
                break
            next_offset = file.tell()
            value = json.loads(line)
            if value['activityId'] == activity: events.append(value)
        return {'nextOffset': next_offset, 'events': events, 'hasMore': next_offset < size and not partial_tail,
                'partialTail': partial_tail}


def clock_evidence(value, started_ms, finished_ms):
    # Deployment validation requires both containers to share this Linux kernel.
    # A periodic snapshot's age is publication latency, not an NTP clock offset.
    age = finished_ms-value['timeMs']
    if not -50 <= age <= 10000:
        raise RuntimeError(f'Observation snapshot is stale or clock shifted: ageMs={age:.0f}')
    return {'offsetEstimateMs': 0, 'uncertaintyMs': 0, 'source': 'shared-host-kernel-clock',
            'snapshotAgeMs': round(age, 3), 'readDurationMs': round(finished_ms-started_ms, 3)}
