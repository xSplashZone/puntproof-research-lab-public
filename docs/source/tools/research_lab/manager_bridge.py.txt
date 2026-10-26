"""Exact existing-manager invocation; no transport implementation or credential access."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

SOURCES=('player_stats','pbp','rosters')
def validate_request(request):
    if set(request)!={'source','season','weeks'} or request['source'] not in SOURCES or request['season']!=2026 or not request['weeks'] or any(type(w) is not int or not 1<=w<=18 for w in request['weeks']):
        raise ValueError('EXACT_2026_MANAGER_SCOPE_REQUIRED')
    return request

def run_request(request, manager_root, lab_root):
    validate_request(request)
    registry=json.loads((lab_root/'config/source_registry.json').read_bytes())
    permission=registry.get('research_lab_manager_requests',{})
    if permission.get('enabled') is not True or permission.get('sources')!=list(SOURCES) or permission.get('seasons')!=[2026] or permission.get('cost_cents')!=0:
        raise PermissionError('MANAGER_REQUEST_AUTHORITY_NOT_REGISTERED')
    import subprocess
    committed=subprocess.run(['git','-C',str(lab_root),'show','HEAD:config/source_registry.json'],capture_output=True,check=True,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    if json.loads(committed.stdout).get('research_lab_manager_requests')!=permission:raise PermissionError('MANAGER_REQUEST_NOT_COMMITTED')
    from datetime import datetime,timezone
    if datetime.now(timezone.utc)>=datetime.fromisoformat(permission['expires_at'].replace('Z','+00:00')):raise PermissionError('MANAGER_REQUEST_AUTHORITY_EXPIRED')
    for item in permission['implementation']:
        path=lab_root/item['path']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise PermissionError('MANAGER_REQUEST_CODE_CHANGED')
    if (lab_root/'TASK_CHARTER.json').exists():raise PermissionError('LIVE_TASK_REQUIRES_ITS_OWN_ACQUISITION_GATE')
    # All collection/reuse/custody remains owned by the installed manager.
    sys.path.insert(0,str(manager_root/'tools/puntproof_manual_odds'))
    from manager_gate import verify
    from manager_store import Store
    from manager_service import Service
    verify(request['source'],root=manager_root)
    store=Store(manager_root/'data/private/puntproof/data-manager')
    service=Service(store)
    try:
        if not service.enabled(request['source']):return {'status':'DISABLED_BY_OWNER','requests':0}
        futures=service.run([request['source']],[2026],trigger='research_lab',required_by_source={request['source']:request['weeks']})
        for future in futures:future.result(timeout=300)
        decision=store.get('needs:last:'+request['source'],{})
        runs=store.rows('SELECT id,status,started,ended,error,snapshot FROM runs WHERE source=? AND season=2026 ORDER BY started DESC LIMIT 1',(request['source'],))
        return {'status':'MANAGER_COMPLETED','decision':decision,'last_run':dict(runs[0]) if runs else None,'source':request['source'],'weeks':request['weeks']}
    finally:service.close()

def bounded_child(argv,root,logbase,*,seconds=300,memory_mb=1024):
    import subprocess,os
    from v3_runtime import process_memory_mb
    logbase=Path(logbase);logbase.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();unknown=None
    with logbase.with_suffix('.out.log').open('xb') as out,logbase.with_suffix('.err.log').open('xb') as err:
        p=subprocess.Popen(argv,cwd=root,stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
        try:
            while p.poll() is None:
                memory=process_memory_mb(p.pid)
                unknown=(unknown or time.monotonic()) if memory is None else None
                if time.monotonic()-started>seconds or memory is not None and memory>memory_mb or unknown and time.monotonic()-unknown>5:
                    raise RuntimeError('BOUNDED_CHILD_RESOURCE_STOP')
                time.sleep(.2)
            if p.returncode:raise RuntimeError('BOUNDED_CHILD_FAILED: inspect '+logbase.name+' logs')
        finally:
            if p.poll() is None:p.kill();p.wait()
    return logbase.with_suffix('.out.log')


def dispatch_one(operations,period):
    from core import now
    store=operations.store;root=operations.root
    permission=json.loads((root/'config/source_registry.json').read_bytes()).get('research_lab_manager_requests',{})
    if not permission.get('enabled'):return {'status':'PERMISSION_REQUIRED','provider_calls':0}
    if (root/'TASK_CHARTER.json').exists():return {'status':'WAITING_FOR_ENGINEERING_CLOSEOUT','provider_calls':0}
    imports=operations._latest_imports();last=store.get_setting('manager_request_last',{})
    chosen=None
    for source in SOURCES:
        if source=='rosters' and period[1] in imports.get(source,{}).get('weeks',[]):continue
        previous=last.get(source,{})
        if time.time()-previous.get('epoch',0)>=21600:chosen=source;break
    if chosen is None:return {'status':'COOLDOWN','next_at':min((x.get('epoch',0)+21600 for x in last.values()),default=None),'last':last}
    request=dict(source=chosen,season=2026,weeks=[period[1]])
    identity=hashlib.sha256(json.dumps([request,time.time_ns()]).encode()).hexdigest()
    directory=store.root/'executions'/('manager-request-'+identity[:24]);directory.mkdir(parents=True)
    claim=dict(request=request,at=now(),epoch=time.time(),status='CLAIMED_NO_AUTOMATIC_RETRY',id=identity)
    from v3_runtime import write_once
    write_once(directory/'REQUEST.json',claim)
    last[chosen]=claim;store.set_setting('manager_request_last',last)
    try:
        output=bounded_child([str(root/'.venv/Scripts/python.exe'),'-I','-B',str(root/'tools/research_lab/manager_bridge.py'),'--source',chosen,'--week',str(period[1])],root,directory/'manager',seconds=300)
        result=json.loads(output.read_bytes());result.update(request_id=identity,completed_at=now())
    except (OSError,ValueError,RuntimeError) as e:
        result=dict(status='FAILED',reason=str(e),request_id=identity,completed_at=now(),next_action='Inspect exact manager request logs; next automatic demand is no earlier than six hours.')
    write_once(directory/'RESULT.json',result);store.append('manager_refresh',identity,{**claim,**result},immutable=True)
    store.set_setting('manager_refresh_status',result)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',choices=SOURCES,required=True);p.add_argument('--week',type=int,required=True);a=p.parse_args()
    root=Path(__file__).resolve().parents[2]
    print(json.dumps(run_request(dict(source=a.source,season=2026,weeks=[a.week]),Path('C:/Dev/nfl-model'),root)))
