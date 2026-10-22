"""Real cgroup + production Nginx slow-upstream regression. Never a performance report.

Host entrypoint; requires Docker and passwordless sudo for isolated runtime slices.
--prepared allows a developer to provision the named slices separately as root.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'deploy'))
sys.path.insert(0, str(ROOT/'scripts/experiments/lib'))
import resource_group
from host import select_affinity, HostMonitor, MIB
from runtime_alignment import render_configs


def run(*args, **kwargs):
    return subprocess.check_output(list(args), text=True, **kwargs).strip()


def inspect(name): return json.loads(run('docker','inspect',name))[0]


def check(output, group, image, prepared=False, experiment=None):
    output.mkdir(parents=True, exist_ok=True)
    pins = select_affinity()
    groups = (group, group.removesuffix('.slice')+'oom.slice')
    resource_group.require_engine()
    for name in groups:
        if prepared: resource_group.verify(name)
        else: resource_group.ensure(name)
    config = render_configs()[0]['services']['nginx']
    prefix = group.removesuffix('.slice')
    network = prefix+'net'
    owned=[]
    try:
        run('docker','network','create',network)
        for plan in ([experiment] if experiment else ['redis-filtering','kafka-burst']):
            directory=output/plan;directory.mkdir()
            load=prefix+'load';gateway=prefix+'gateway'
            owned.extend([load,gateway])
            run('docker','create','--name',load,'--network',network,'--network-alias','backend','--network-alias','frontend',
                '--cpus','2','--cpuset-cpus',pins['runner'],'--memory','4g','--memory-swap','4g','--pids-limit','128',
                '--ulimit','nofile=65536:65536','--user',f'{os.getuid()}:{os.getgid()}', '-e','GOMAXPROCS=2',
                '-v',f'{ROOT}:/workspace:ro','-v',f'{directory}:/check','--entrypoint','python3',image,
                '/workspace/scripts/experiments/checks/k6_recovery.py','--output','/check','--experiment',plan,
                '--short','--gateway','http://nginx:8080')
            mounts=[]
            for mount in config['volumes']:
                mounts += ['-v',f"{mount['source']}:{mount['target']}:ro"]
            run('docker','create','--name',gateway,'--network',network,'--network-alias','nginx',
                '--cgroup-parent',group,'--cpuset-cpus',pins['sut'],'--pids-limit','32','--ulimit','nofile=65536:65536',
                *mounts,config['image'])
            run('docker','start',load)
            run('docker','start',gateway)
            resource_group.verify_member(inspect(gateway),group)
            resource_group.verify_member(inspect(load),group,inside=False)
            deadline=time.monotonic()+120
            samples=[]
            while inspect(load)['State']['Running']:
                sample=resource_group.snapshot(group)
                samples.append(sample)
                resource_group.validate_snapshot(sample)
                if time.monotonic()>deadline: raise RuntimeError('Gateway regression timed out')
                time.sleep(.25)
            (directory/'group-samples.json').write_text(json.dumps(samples))
            (directory/'nginx.log').write_text(run('docker','logs',gateway,stderr=subprocess.STDOUT))
            if inspect(load)['State']['ExitCode']:
                raise RuntimeError(run('docker','logs',load,stderr=subprocess.STDOUT))
            result=json.loads((directory/'check.json').read_text())
            if result['status']!='passed':raise RuntimeError(str(result))
            peak=max(int(s['resources']['memory.current']) for s in samples)
            if peak<=64*MIB: raise RuntimeError('Regression did not exercise memory above old gateway cap')
            print(json.dumps({'experiment':plan,'status':'passed','gatewayPeakMiB':round(peak/MIB,2),
                              'generator':result['verifiedEnvelope']}),flush=True)
            run('docker','rm','-f',load,gateway);owned=[]
        # Kernel OOM must remain visible when the container's PID 1 survives a child kill.
        name=prefix+'oom';owned.append(name)
        payload="import subprocess,time; p=subprocess.run(['python3','-c','a=[]\\nwhile True:a.append(bytearray(16*1024*1024))']); print(p.returncode,flush=True); time.sleep(60)"
        run('docker','run','-d','--name',name,'--network','none','--cgroup-parent',groups[1],
            '--cpus','1','--pids-limit','32','--entrypoint','python3',image,'-c',payload)
        deadline=time.monotonic()+60
        while True:
            sample=resource_group.snapshot(groups[1])
            events=dict(line.split() for line in sample['resources']['memory.events'].splitlines())
            if int(events['oom_kill']):break
            if time.monotonic()>deadline:raise RuntimeError('Deliberate OOM was not recorded')
            time.sleep(.2)
        (output/'oom-group.json').write_text(json.dumps(sample))
        def observe_memory():
            value=resource_group.snapshot(groups[1])
            try:resource_group.validate_snapshot(value)
            except RuntimeError as error:return value,str(error)
            return value,None
        monitor=HostMonitor(output/'oom-watch',lambda:[],lambda:None,lambda:None,10,observe_memory=observe_memory)
        monitor.sample()
        if not monitor.failure or 'OOM' not in monitor.failure:raise RuntimeError('Watchdog missed parent OOM')
        state=inspect(name)['State']
        print(json.dumps({'oomDetected':True,'containerStillRunning':state['Running'],'events':events}),flush=True)
        (output/'check.json').write_text(json.dumps({'status':'passed','scope':'validation','memoryModel':'shared-cgroup'}))
    finally:
        for name in owned:subprocess.run(['docker','rm','-f',name],check=False,stdout=subprocess.DEVNULL)
        subprocess.run(['docker','network','rm',network],check=False,stdout=subprocess.DEVNULL)
        # Do not erase counters before writing evidence above.
        if not prepared:
            for name in groups:resource_group.remove(name)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--group',default='shanpumemorycheck.slice')
    parser.add_argument('--image',default='shanpu-experiment-runner:ci')
    parser.add_argument('--prepared',action='store_true')
    parser.add_argument('--experiment',choices=['redis-filtering','kafka-burst'])
    args=parser.parse_args()
    check(args.output.resolve(),args.group,args.image,args.prepared,args.experiment)
