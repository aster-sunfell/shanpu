"""Real k6 + controlled HTTP queue + production evidence/search code; no capacity claims.

Run inside the load-runner image. The HTTP fixture replaces the business backend;
its ledger provides deterministic snapshot/commit evidence, not a database benchmark.
"""
import argparse
import asyncio
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

LIB = Path(__file__).resolve().parents[1]/'lib'
sys.path.insert(0, str(LIB))
from adaptive_search import recoverable_load_limit, run
from analysis import Evidence, LATE_START_ERROR, save_result, write_json
from protocol import capacities, parse_args, policy
from runner import verify_k6_load


class HttpQueue:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.active, self.ledger, self.config = 0, {}, {}
        self.gate = asyncio.Semaphore(16)
        self.server = asyncio.run_coroutine_threadsafe(
            asyncio.start_server(self.handle, '127.0.0.1', 0), self.loop).result()
        self.port = self.server.sockets[0].getsockname()[1]

    async def handle(self, reader, writer):
        self.active += 1
        try:
            headers = (await reader.readuntil(b'\r\n\r\n')).decode().split('\r\n')
            headers = dict(line.split(': ', 1) for line in headers[1:] if ': ' in line)
            await reader.readexactly(int(headers.get('Content-Length', 0)))
            token = headers['Authorization'].split()[1].split('.')[1]
            uid = json.loads(base64.urlsafe_b64decode(token+'='*(-len(token) % 4)))['uid']
            async with self.gate:
                await asyncio.sleep(.04 if self.config['path'] == 'B' else .001)
                oid = str(uid)
                self.ledger[uid] = {'orderId': oid, 'userId': uid, 'activityId': self.config['activityId'],
                                    'commitObservedAtMs': int(time.time()*1000)}
            data = json.dumps({'code': 0, 'data': {'orderId': oid,
                               'status': 'PAID' if self.config['path'] == 'B' else 'PENDING'}}).encode()
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: '
                         + str(len(data)).encode() + b'\r\n\r\n' + data)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            self.active -= 1

    def close(self):
        async def shutdown():
            self.server.close()
            await self.server.wait_closed()
        asyncio.run_coroutine_threadsafe(shutdown(), self.loop).result()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()
        self.loop.close()


def check(output):
    output.mkdir(parents=True, exist_ok=True)
    workload = (LIB.parent/'k6/lib/workload.js').as_uri()
    wrapper = output/'workload.js'
    # Exercise a real late start without rewriting timestamps or evidence. Delay
    # the final 100 ms of iterations until 25 ms after the actual scenario end.
    wrapper.write_text("import exec from 'k6/execution';\nimport { sleep } from 'k6';\n"
                       f"import {{ optionsFor, request }} from '{workload}';\n"
                       "export const options = optionsFor('kafka-buffering');\n"
                       "const config = JSON.parse(open(__ENV.POINT_CONFIG));\n"
                       "export default function() {\n"
                       "  const left = exec.scenario.startTime + config.seconds*1000 - Date.now();\n"
                       "  if (__ENV.STOP_TAIL === '1' && left < 100) return;\n"
                       "  if (__ENV.DELAY_TAIL === '1' && left > 0 && left < 100) sleep((left+25)/1000);\n"
                       "  request();\n}\n")
    args = parse_args(['kafka-buffering'])
    # Short functional checks only; these deliberately cannot qualify as formal evidence.
    args.rates, args.probe_seconds, args.probe_steps = [500], 3, 5
    args.seconds, args.repeats, args.min_samples, args.refine_steps = 2, 1, 1, 2
    server = HttpQueue()
    observed, results, meta = [], [], {}

    def execute(point, phase, tail=False, stop_tail=False):
        assert server.active == 0, 'Previous HTTP work did not drain'
        directory = output/f'{len(observed)+1:02d}-{phase}-{point["path"]}-{point["rate"]}'
        directory.mkdir()
        config = {**point, 'activityId': len(observed)+1, 'points': 1000, 'requestTimeout': 5}
        server.config, server.ledger = config, {}
        pool = {'count': config['scheduled']+1, 'prefix': 'recovery', 'issuedAt': int(time.time()),
                'segments': [{'index': 0, 'userId': 1, 'count': config['scheduled']+1}]}
        write_json(directory/'point.json', config)
        write_json(directory/'users.json', pool)
        with (directory/'k6.out').open('w') as log:
            completed = subprocess.run(['k6', 'run', '--no-summary', '--no-thresholds', '--log-format', 'raw',
                '--console-output', str(directory/'requests.jsonl'), str(wrapper)],
                env={**os.environ, 'POINT_CONFIG': str(directory/'point.json'), 'USER_FILE': str(directory/'users.json'),
                     'JWT_SECRET': 'local-recovery-fixture-only', 'DELAY_TAIL': '1' if tail else '0',
                     'STOP_TAIL': '1' if stop_tail else '0',
                     'API_BASE': f'http://127.0.0.1:{server.port}/api'}, stdout=log, stderr=subprocess.STDOUT, timeout=30)
        until = time.monotonic()+5
        while server.active and time.monotonic() < until: time.sleep(.01)
        assert server.active == 0, 'HTTP queue did not drain'
        for snapshot in ('before', 'after'):
            paid = server.ledger if snapshot == 'after' else {}
            rows = {'users': [[i, i+1, 900 if i+1 in paid else 1000] for i in range(pool['count'])],
                    'orders': [[str(uid), uid, 100, 2] for uid in paid],
                    'logs': [[uid, str(uid), -100, 2] for uid in paid], 'bought': list(paid)}
            for kind, values in rows.items():
                (directory/f'{snapshot}-{kind}.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in values))
            write_json(directory/f'{snapshot}.json', {'stock': pool['count']-len(paid), 'redisStock': pool['count']-len(paid)})
        evidence = Evidence(directory)
        try:
            evidence.load_snapshot('before'); evidence.load_snapshot('after'); evidence.load_requests()
            evidence.add_commits(server.ledger.values())
            value = evidence.analyze(config, {}, int(time.time()*1000), policy(args))
        finally: evidence.close()
        verify_k6_load(value, directory/'k6.out', completed.returncode)
        value.update(phase=phase, scope='validation', repeat=config['repeat'], evidenceDirectory=directory.name)
        save_result(directory, value)
        assert not value['correctnessErrors'] and not value['unresolvedCount'], value['correctnessErrors']
        assert value['valid'] or recoverable_load_limit(value), value['loadErrors']
        observed.append(value)
        if phase == 'confirmation': results.append((directory.name, value))
        print(json.dumps({'point': directory.name, 'valid': value['valid'], 'vuExhausted': value['vuExhausted'],
                          'started': value['started'], 'scheduled': value['scheduled'],
                          'late': value['injectionTiming']['lateStarts']['count'], 'loadErrors': value['loadErrors']}), flush=True)
        return value

    try:
        # Lateness alone must remain invalid, even when no VU is exhausted.
        late = execute({'plan': 'kafka-buffering', 'path': 'C', 'rate': 50, 'seconds': 3, 'baseSeconds': 0,
                        'baseRate': 0, 'workload': 'steady', 'repeat': 1, 'vus': 200, 'scheduled': 150}, 'probe', tail=True)
        assert LATE_START_ERROR in late['loadErrors'] and not late['vuExhausted']
        # Stop the last 100 ms deliberately in this fixture to isolate pool
        # exhaustion from the independently checked duration-boundary race.
        limited = execute({'plan': 'kafka-buffering', 'path': 'B', 'rate': 800, 'seconds': 3, 'baseSeconds': 0,
                           'baseRate': 0, 'workload': 'steady', 'repeat': 1, 'vus': 200, 'scheduled': 2400},
                          'probe', stop_tail=True)
        assert limited['vuExhausted'] and LATE_START_ERROR not in limited['loadErrors']
        search_start = len(observed)
        run(args, lambda point, phase: execute(point, phase, tail=point['path'] == 'B' and point['rate'] == 500),
            results, meta, lambda: 100000, lambda: write_json(output/'search.json', meta))
        slow = [r for r in observed[search_start:] if r['path'] == 'B']
        assert slow[0]['vuExhausted'] and LATE_START_ERROR in slow[0]['loadErrors'], 'Combined overload not reproduced'
        bounds = capacities(args, results)
        assert all(b['passingRps'] is not None for b in bounds.values()), bounds
        for path, bound in bounds.items():
            limited_rates = {r['rate'] for r in observed[search_start:] if r['path'] == path and not r['valid']}
            assert bound['failingRps'] not in limited_rates, 'Invalid injection became a service failure bound'
        assert any(r['path'] == 'C' and r['valid'] for _, r in results), 'Other path did not continue'
        write_json(output/'check.json', {'status': 'passed', 'scope': 'validation', 'formalCapacityEvidence': False,
                   'description': 'Real k6 and production workload/evidence/search; controlled HTTP queue replaces business services',
                   'observedPoints': len(observed), 'search': meta['search']})
    finally: server.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    try:
        check(options.output.resolve())
    except Exception as error:
        options.output.mkdir(parents=True, exist_ok=True)
        write_json(options.output/'check.json', {'status': 'failed', 'scope': 'validation', 'error': str(error)})
        raise
