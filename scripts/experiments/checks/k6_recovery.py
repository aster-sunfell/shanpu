"""Real k6 + controlled HTTP queue + production workload/evidence code; no performance claims.

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
from analysis import Evidence, LATE_START_ERROR, save_result, write_json
from protocol import parse_args, policy, matrix
from runner import verify_k6_load, safe_load_failure, runner_usage


class HttpQueue:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.active, self.ledger, self.config = 0, {}, {}
        self.gate = asyncio.Semaphore(2048)
        self.server = asyncio.run_coroutine_threadsafe(
            asyncio.start_server(self.handle, '127.0.0.1', 0, backlog=2048), self.loop).result()
        self.port = self.server.sockets[0].getsockname()[1]

    async def handle(self, reader, writer):
        self.active += 1
        try:
            headers = (await reader.readuntil(b'\r\n\r\n')).decode().split('\r\n')
            headers = dict(line.split(': ', 1) for line in headers[1:] if ': ' in line)
            await reader.readexactly(int(headers.get('Content-Length', 0)))
            token = headers['Authorization'].split()[1].split('.')[1]
            uid = json.loads(base64.urlsafe_b64decode(token+'='*(-len(token) % 4)))['uid']
            duplicate = self.config['workload'] == 'mixed' and (uid-1)%10 != 9
            async with self.gate:
                await asyncio.sleep(self.config.get('fixtureDelaySeconds',.001))
                oid = str(uid)
                if not duplicate: self.ledger[uid] = {'orderId': oid, 'userId': uid, 'activityId': self.config['activityId'],
                                    'commitObservedAtMs': int(time.time()*1000)}
            data = json.dumps({'code': 40900} if duplicate else {'code': 0, 'data': {'orderId': oid,
                               'status': 'PENDING' if self.config['path'] == 'C' else 'PAID'}}).encode()
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


def check(output, experiment):
    output.mkdir(parents=True, exist_ok=True)
    args = parse_args(['kafka-burst'])
    server = HttpQueue()
    observed, results, meta = [], [], {}

    def execute(point, phase):
        assert server.active == 0, 'Previous HTTP work did not drain'
        directory = output/f'{len(observed)+1:02d}-{phase}-{point["path"]}-{point["rate"]}'
        directory.mkdir()
        config = {**point, 'activityId': len(observed)+1, 'points': 1000, 'requestTimeout': 10}
        server.config, server.ledger = config, {}
        pool = {'count': config['scheduled']+1, 'prefix': 'recovery', 'issuedAt': int(time.time()),
                'segments': [{'index': 0, 'userId': 1, 'count': config['scheduled']+1}]}
        write_json(directory/'point.json', config)
        write_json(directory/'users.json', pool)
        wrapper = LIB.parent/'k6'/f"{config['plan']}.js"
        with (directory/'k6.out').open('w') as log:
            completed = subprocess.run(['k6', 'run', '--no-summary', '--no-thresholds', '--log-format', 'raw',
                '--console-output', str(directory/'requests.jsonl'), str(wrapper)],
                env={**os.environ, 'POINT_CONFIG': str(directory/'point.json'), 'USER_FILE': str(directory/'users.json'),
                     'JWT_SECRET': 'local-recovery-fixture-only',
                     'API_BASE': f'http://127.0.0.1:{server.port}/api'}, stdout=log, stderr=subprocess.STDOUT, timeout=100)
        until = time.monotonic()+5
        while server.active and time.monotonic() < until: time.sleep(.01)
        assert server.active == 0, 'HTTP queue did not drain'
        for snapshot in ('before', 'after'):
            duplicates = {i+1: {} for i in range(config['scheduled']) if config['workload'] == 'mixed' and i%10 != 9}
            paid = {**duplicates, **server.ledger} if snapshot == 'after' else duplicates
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
        assert value['valid'] or safe_load_failure(value), value['loadErrors']
        observed.append(value)
        value['runnerUsage'] = runner_usage()
        save_result(directory, value)
        results.append((directory.name, value))
        print(json.dumps({'point': directory.name, 'valid': value['valid'], 'vuExhausted': value['vuExhausted'],
                          'started': value['started'], 'scheduled': value['scheduled'],
                          'late': value['injectionTiming']['lateStarts']['count'], 'loadErrors': value['loadErrors']}), flush=True)
        return value

    try:
        # Actual default counts, durations and fixed VU allocation; only HTTP business
        # behavior is controlled. This validates the generator, not service performance.
        selected = list(matrix(parse_args([experiment])))[-1]
        measured = execute(selected, 'fixed-profile')
        assert measured['valid'], measured['loadErrors']
        if experiment == 'kafka-burst': assert measured['burst']['requests'] == selected['rate']*selected['seconds']
        delayed = execute({**selected,'workload':'steady','baseSeconds':0,'recoverySeconds':0,
                           'seconds':15,'rate':800,'scheduled':12000,'fixtureDelaySeconds':1}, 'delayed-800rps')
        assert delayed['valid'], delayed['loadErrors']
        burst = next(p for p in matrix(args) if p['path'] == 'C')
        limited = execute({'plan': 'kafka-burst', 'path': 'B', 'rate': 800, 'seconds': 3,
                           'baseSeconds': 0, 'baseRate': 0, 'recoverySeconds': 0,
                           'workload': 'steady', 'repeat': 1, 'vus': 20, 'scheduled': 2400, 'fixtureDelaySeconds':.1}, 'overload')
        assert limited['vuExhausted'] and safe_load_failure(limited), limited['loadErrors']
        recovered = execute({**burst, 'workload': 'steady', 'baseSeconds': 0, 'seconds': 2,
                             'recoverySeconds': 0, 'rate': 50, 'scheduled': 100}, 'after-overload')
        assert recovered['valid'], recovered['loadErrors']
        write_json(output/'check.json', {'status': 'passed', 'scope': 'validation',
                   'description': 'Real default k6 profiles and production evidence; controlled HTTP queue, no business performance claims',
                   'experiment': experiment, 'verifiedEnvelope':{'rps':800,'responseDelayMs':1000,'vus':1200}, 'observedPoints': len(observed), 'resources': runner_usage()})
    finally: server.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--experiment', choices=['redis-filtering','kafka-burst'], default='redis-filtering')
    options = parser.parse_args()
    try:
        check(options.output.resolve(), options.experiment)
    except Exception as error:
        options.output.mkdir(parents=True, exist_ok=True)
        write_json(options.output/'check.json', {'status': 'failed', 'scope': 'validation', 'error': str(error)})
        raise
