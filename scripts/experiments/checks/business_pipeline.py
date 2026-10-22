"""Short real-service regression using the production fixture/measurement/report path.

Run in the bounded runner against a dedicated disposable SUT, with a host watchdog.
This is validation evidence only; it never estimates performance gains.
"""
import json
import os
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
from analysis import write_json
from fixtures import PRICE, create_users, credential, prepare, release_redis
from host import check_host_health
from protocol import parse_args, matrix, report
from runner import RUNTIME, REPORTS, api, sql, require_idle, measure, require_valid_point


def main():
    if os.environ.get('BENCH_CONTAINER') != '1' or not os.environ.get('BENCH_HOST_STATUS'):
        raise RuntimeError('Use a bounded runner and a host watchdog with a dedicated disposable SUT')
    check_host_health()
    run_id = 'business-pipeline-'+secrets.token_hex(4)
    private = RUNTIME/run_id
    private.mkdir(parents=True, mode=0o700)
    require_idle(None)  # Verify instance/database, disabled jobs and idle work before mutation.
    admin = credential(create_users('x'+secrets.token_hex(6), 1, private), 0)['token']
    product = int(sql(f"INSERT INTO t_product(title,price,stock,status) VALUES ('pipeline-regression',{PRICE},0,1); SELECT LAST_INSERT_ID();"))
    for plan in ('redis-filtering', 'kafka-burst'):
        args = parse_args([plan, '--validation-only', '--repeats', '1', '--rate', '50', '--seconds', '2',
                           '--base-rate', '10', '--base-seconds', '2', '--burst-rate', '50', '--burst-seconds', '2',
                           '--recovery-rate', '10', '--recovery-seconds', '2', '--drain-seconds', '30', '--min-samples', '1', '--vus', '200'])
        args.deployment = 'development-validation'
        directory = REPORTS/plan/run_id
        directory.mkdir(parents=True)
        meta, results = {'status':'running','scope':'validation','plan':plan,'points':[]}, []
        try:
            for index, point in enumerate(matrix(args), 1):
                name = f'{index:03d}-{point["path"]}'
                output, secret = directory/name, private/plan/name
                output.mkdir()
                point['userPrefix'] = 'x'+secrets.token_hex(6)
                prepare(point, product, secret, output, admin, api, require_idle)
                value = measure(point, secret, output, admin, args)
                results.append((name,value))
                meta['points'].append({'directory':name,'activityId':point['activityId']})
                require_valid_point(value, point)
                if value['completed'] != value['eligible'] or any(c['failed'] for c in value['classes'].values()):
                    raise RuntimeError('Business pipeline returned unhealthy responses or incomplete orders')
                if plan == 'kafka-burst':
                    after = json.loads((output/'backend-after.json').read_text())['transactionPressure']
                    assert after['started'] == value['completed'], 'Prepared order leaked into formal transaction count'
                require_idle(admin)
                release_redis(secret, point)
                print(json.dumps({'plan':plan,'path':point['path'],'valid':value['valid'],
                                  'started':value['started'],'completed':value['completed'],
                                  'pressure':value.get('pressure')}),flush=True)
            meta['status'] = 'completed'
        except BaseException as error:
            meta.update(status='failed',error=str(error))
            raise
        finally:
            write_json(directory/'manifest.json',meta)
            report(directory,meta,results,args)


if __name__ == '__main__':
    main()
