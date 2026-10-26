"""Durable V3 orchestration. All real calls enter exact committed repository gates.

A failed scientific lease is never retried automatically. Existing immutable output
folders remain forensic evidence. The service never changes production or providers.
"""
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from nfl_model import research_lab_gate as gate
from core import now
from engine_bridge import validate_outcomes
from monitoring import distribution,score_rows,monitor

CHANNELS=('receiving','rushing')

def validate_execution_clock(manifest):
    current=dt.datetime.now(dt.timezone.utc)
    if not gate.timestamp(manifest['cutoff'])<=gate.timestamp(manifest['as_of'])<=current:
        raise ValueError('FUTURE_EXECUTION_OBSERVATION_DENIED')



def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def write_once(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);raw=gate.encoded(value)
    if path.exists():
        if path.read_bytes()!=raw:raise ValueError('IMMUTABLE_RUNTIME_RECORD_CONFLICT:'+path.name)
        return path
    with path.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    return path


def ref(root,path):
    path=Path(path).resolve();root=Path(root).resolve()
    if not path.is_relative_to(root):raise ValueError('RUNTIME_REFERENCE_ESCAPE')
    return {'path':path.relative_to(root).as_posix(),'sha256':sha(path)}


class LifecycleJournal:
    def __init__(self,root,manifest):
        self.root=Path(root).resolve();self.manifest=manifest
        self.directory=gate.lifecycle_directory(self.root,manifest)
        if not self.directory.resolve().is_relative_to(self.root/'data/private/research-lab/executions'):
            raise ValueError('EXECUTION_DIRECTORY_ESCAPE')
    def records(self):
        rows=[json.loads(p.read_bytes()) for p in sorted(self.directory.glob('*.json'))] if self.directory.exists() else []
        gate.validate_lifecycle_events(rows,self.manifest['operation']);return rows
    def append(self,state,**evidence):
        rows=self.records();states=gate.FORWARD_STATES if self.manifest['operation']==gate.FORWARD else gate.MODEL_STATES
        if len(rows)>=len(states) or states[len(rows)]!=state:raise ValueError('LIFECYCLE_TRANSITION_REFUSED')
        event={'sequence':len(rows),'state':state,'previous_hash':rows[-1]['sha256'] if rows else None,'at':now(),**evidence}
        event['sha256']=hashlib.sha256(gate.encoded(event)).hexdigest()
        p=self.directory/f'{len(rows):03}.json';write_once(p,event);return ref(self.root,p)
    def phase(self):
        rows=self.records();return rows[-1]['state'] if rows else None


def process_memory_mb(pid):
    if os.name!='nt':
        try:
            lines=Path(f'/proc/{pid}/status').read_text().splitlines()
            return float(next(x.split()[1] for x in lines if x.startswith('VmRSS:')))/1024
        except (OSError,StopIteration):return None
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD),('peak',ctypes.c_size_t),('working',ctypes.c_size_t),('peak_page_pool',ctypes.c_size_t),('page_pool',ctypes.c_size_t),('peak_nonpage_pool',ctypes.c_size_t),('nonpage_pool',ctypes.c_size_t),('pagefile',ctypes.c_size_t),('peak_pagefile',ctypes.c_size_t)]
    open_process=ctypes.windll.kernel32.OpenProcess;open_process.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];open_process.restype=wintypes.HANDLE
    handle=open_process(0x0400|0x0010,False,pid)
    if not handle:return None
    try:
        c=Counters();c.cb=ctypes.sizeof(c)
        fn=ctypes.windll.psapi.GetProcessMemoryInfo;fn.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
        return c.working/1024**2 if fn(handle,ctypes.byref(c),c.cb) else None
    finally:
        close=ctypes.windll.kernel32.CloseHandle;close.argtypes=[wintypes.HANDLE];close(handle)


class ScientificRuntime:
    def __init__(self,store,root=ROOT):
        self.store=store;self.root=Path(root).resolve()
        self.engine=self.root/'scratch/research-lab-starter/engine/PuntProof_Adaptive_V3'
        self.python=self.root/'scratch/v3-env/Scripts/python.exe'
        self._status_cache=None
    def status(self,refresh=False):
        if not refresh and self._status_cache and time.monotonic()-self._status_cache[0]<30:return dict(self._status_cache[1])
        result=self._status_uncached()
        result['checked_at']=now();self._status_cache=(time.monotonic(),dict(result));return result
    def _status_uncached(self):
        result={'installed':self.python.is_file(),'registered':False,'ready':False,'status':'BLOCKED_PENDING_READINESS','provider_calls':0,'genuine_roi':'ROI_NOT_ESTABLISHED'}
        try:
            consumer,_=gate.consumer_for(self.root,gate.FORWARD)
            result.update(registered=True,expires_at=consumer['expires_at'])
        except (ValueError,KeyError,OSError,TypeError) as e:
            result['reason']=str(e)[:300]
            return result
        active=self.store.get_setting('active_forecast_plan')
        if active:
            try:
                m=gate.value(self.root,active['manifest'])
                phase=LifecycleJournal(self.root,m).phase()
                operation=self.store.get_setting('operation_status',{})
                result.update(plan=active,phase=phase,forecasts=len(self.store.records('forecast')),
                              learning_updates=len(self.store.records('model_head')))
                if phase=='AUTHORITY_CLOSED':
                    result.update(status='COMPLETED_WEEK_LEARNED',reason='The sealed week was settled and its versioned model state saved.')
                elif phase=='ATTEMPT_CONSUMED':
                    result.update(status='FORECAST_SEALED_AWAITING_OUTCOMES',reason=operation.get('settlement',{}).get('reason') or 'Real forecasts are saved. Complete verified target-week outcomes are required for settlement and learning.')
                else:
                    result.update(status='CURRENT_PLAN_IN_PROGRESS',reason='Inspect the preserved execution lifecycle; automatic scientific retries are disabled.')
            except (ValueError,KeyError,OSError,TypeError) as e:
                result.update(status='EXECUTION_EVIDENCE_NEEDS_REPAIR',reason=str(e)[:400])
            return result
        plan=self.store.root/'admission/current.json'
        if not plan.is_file():
            result['reason']='No exact execution manifest and READY certificate. Week7 completed stats/PBP/snaps and an authenticated full fixture census are not established.'
            return result
        try:
            p=json.loads(plan.read_bytes())
            if p['operation']!=gate.FORWARD:raise ValueError('UNATTENDED_OPERATION_DENIED')
            with gate.execution_authority(self.root,p['operation'],p['manifest'],p['readiness']) as manifest:
                validate_execution_clock(manifest)
                if p['operation']==gate.FORWARD:
                    phase=LifecycleJournal(self.root,manifest).phase()
                    if p.get('action')=='forecast' and phase is not None or p.get('action')=='settle' and phase!='ATTEMPT_CONSUMED':
                        result.update(status='CURRENT_PLAN_ALREADY_STARTED_OR_COMPLETE',reason='Inspect durable execution receipt; automatic retry of a scientific attempt is disabled.');return result
            result.update(ready=True,status='READY_CONDITIONAL',plan=p)
        except (ValueError,KeyError,OSError,TypeError,PermissionError) as e:result['reason']=str(e)[:400]
        return result
    def _lease(self,manifest,action):
        path=self.root/manifest['execution_directory']/(action.upper()+'_LEASE.json')
        # A different directory, classification or revised evidence hash must not
        # reopen the same period. The SQLite unique key survives partial writes.
        with self.store.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS lab_scientific_leases(season INTEGER,week INTEGER,action TEXT,operation TEXT,execution_directory TEXT,created_at TEXT,PRIMARY KEY(season,week,action))')
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM lab_scientific_leases WHERE season=2026 AND week=? AND action=?',(manifest['week'],action)).fetchone():
                raise ValueError('SCIENTIFIC_LEASE_ALREADY_CONSUMED:'+action)
            db.execute('INSERT INTO lab_scientific_leases VALUES(?,?,?,?,?,?)',(2026,manifest['week'],action,manifest['operation'],manifest['execution_directory'],now()))
        if path.exists():raise ValueError('SCIENTIFIC_LEASE_ALREADY_CONSUMED:'+action)
        write_once(path,{'action':action,'at':now(),'operation':manifest['operation'],'season':2026,'week':manifest['week'],'automatic_retry':False})
        return path
    def _invoke(self,request):
        if not self.python.is_file():raise ValueError('V3_ENVIRONMENT_MISSING')
        request=dict(request)
        out=Path(request['output_dir'])
        if out.exists() and not (out/'RESULT.json').exists() and request['action'] in ('inspect_seed','build_features'):
            # Non-scoring engineering actions may retry in a new immutable directory.
            base=out
            for attempt in range(1,4):
                candidate=base.parent/(base.name+'-retry'+str(attempt))
                if not candidate.exists() or (candidate/'RESULT.json').exists():
                    out=candidate;request['output_dir']=str(out);break
            else:raise RuntimeError('ENGINEERING_RETRY_BUDGET_EXHAUSTED')
        request_file=out.parent/(out.name+'-request.json')
        write_once(request_file,request)
        result_file=out/'RESULT.json'
        if result_file.is_file():
            result=json.loads(result_file.read_bytes())
            self._validate_bridge_result(request,result,request_file,out)
            result['_receipt_ref']=ref(self.root,result_file)
            return result
        logs=out.parent;logs.mkdir(parents=True,exist_ok=True)
        env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','TEMP','TMP','SYSTEMDRIVE','COMSPEC')}
        git_exe=shutil.which('git')
        if not git_exe:raise ValueError('GIT_CUSTODY_RUNTIME_UNAVAILABLE')
        env.update(PATH=os.pathsep.join([str(self.python.parent),str(Path(git_exe).resolve().parent),str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32')]),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='2',MKL_NUM_THREADS='2',NUMEXPR_NUM_THREADS='2')
        budget=max(1,min(300,float(self.store.get_setting('scientific_max_seconds',300))))
        memory=max(128,min(16384,float(self.store.get_setting('worker_memory_limit_mb',1024))))
        started=time.monotonic();unknown_since=None
        with (logs/(out.name+'.out.log')).open('xb') as stdout,(logs/(out.name+'.err.log')).open('xb') as stderr:
            p=subprocess.Popen([str(self.python),'-I','-B',str(self.root/'tools/research_lab/engine_bridge.py'),'--request',str(request_file),'--result',str(result_file)],cwd=self.root,env=env,stdout=stdout,stderr=stderr,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
            try:
                while p.poll() is None:
                    used=process_memory_mb(p.pid)
                    if used is None:unknown_since=unknown_since or time.monotonic()
                    else:unknown_since=None
                    if time.monotonic()-started>budget or used is not None and used>memory or unknown_since and time.monotonic()-unknown_since>5:
                        p.kill();p.wait();raise RuntimeError('ENGINE_RESOURCE_BUDGET_STOP_NO_AUTOMATIC_RETRY')
                    time.sleep(.2)
                if p.returncode:
                    stderr.flush()
                    tail=(logs/(out.name+'.err.log')).read_text(encoding='utf-8',errors='replace').splitlines()[-1:]
                    raise RuntimeError('ENGINE_FAILED_NO_AUTOMATIC_RETRY: '+(' '.join(tail)[-500:] or str(p.returncode)))
            finally:
                if p.poll() is None:p.kill();p.wait()
        result=json.loads(result_file.read_bytes())
        self._validate_bridge_result(request,result,request_file,out)
        result['_receipt_ref']=ref(self.root,result_file)
        return result
    def _validate_bridge_result(self,request,result,request_file,out):
        if result.get('schema_version')!='puntproof-engine-bridge-result-v1' or result.get('status')!='PASS':
            raise ValueError('ENGINE_RESULT_NOT_SUCCESS')
        if any(result.get(key)!=request[key] for key in ('action','channel','period')):
            raise ValueError('ENGINE_RESULT_REQUEST_MISMATCH')
        if result.get('request_sha256')!=sha(request_file) or result.get('engine_manifest_sha256')!=request['package_manifest_sha256']:
            raise ValueError('ENGINE_RESULT_REQUEST_BINDING')
        if type(result.get('provider_calls')) is not int or result['provider_calls']!=0 or result.get('production') is not False:
            raise ValueError('ENGINE_RESULT_CAPABILITY_MISMATCH')
        required={'forecast':{'session':'session.joblib','forecast':'forecast.npz','predictions':'predictions.json'},
                  'recover_forecast':{'session':'session.joblib','forecast':'forecast.npz','predictions':'predictions.json'},
                  'settle':{'session':'session.joblib'},'build_features':{'features':'features.npz'},'inspect_seed':{}}[request['action']]
        artifacts=result.get('artifacts')
        if not isinstance(artifacts,dict) or set(artifacts)!=set(required):
            raise ValueError('ENGINE_RESULT_ARTIFACT_SET')
        for key,name in required.items():
            artifact=artifacts[key]
            if not isinstance(artifact,dict) or set(artifact)!={'path','sha256'}:
                raise ValueError('ENGINE_OUTPUT_CUSTODY')
            path=Path(artifact['path']).resolve()
            if path!=(Path(out)/name).resolve() or not path.is_relative_to(Path(out).resolve()) or sha(path)!=artifact['sha256']:
                raise ValueError('ENGINE_OUTPUT_CUSTODY')
        if request['action']=='inspect_seed':
            if result.get('fits')!=0 or result.get('numerical_forecasts')!=0 or result.get('training_rows_added')!=0:
                raise ValueError('INSPECTION_MUTATED_MODEL')
            if result.get('feature_count')!=228 or not isinstance(result.get('through_period'),list):
                raise ValueError('INSPECTION_SCHEMA')
        if request['action'] in ('forecast','recover_forecast','settle'):
            is_forecast=request['action'] in ('forecast','recover_forecast')
            expected_period=request['parent_session']['through_period'] if is_forecast else request['period']
            expected_pending=request['period'] if is_forecast else None
            if result.get('through_period')!=expected_period or result.get('pending_period')!=expected_pending:
                raise ValueError('ENGINE_RESULT_SESSION_PERIOD')
            expected_action='FORECAST_SEALED' if is_forecast else 'SETTLED_AND_LEARNED'
            if result.get('receipt',{}).get('action')!=expected_action or result['receipt'].get('period')!=request['period']:
                raise ValueError('ENGINE_RESULT_RECEIPT')
    def _request(self,manifest,manifest_ref,readiness_ref,channel,action,parent,output,**extra):
        prepared=manifest['channel_inputs'][channel]
        return {'schema_version':1,'action':action,'engine_root':str(self.engine),'package_manifest_sha256':sha(self.engine/'PACKAGE_MANIFEST.json'),'operation':manifest['operation'],'authority':{'execution_manifest':manifest_ref,'readiness':readiness_ref},'period':[2026,manifest['week']],'channel':channel,'output_dir':str(output),'parent_session':{'path':str(gate.reference(self.root,parent)),'sha256':parent['sha256'],'through_period':[2026,manifest['week']-1]},'input_root':prepared['input_root'],'fixtures':prepared['fixtures'],'thresholds':[0,25,25.5,50,75,100],**extra}
    def forecast(self,manifest_ref,readiness_ref):
        with gate.execution_authority(self.root,gate.value(self.root,manifest_ref)['operation'],manifest_ref,readiness_ref) as m:
            validate_execution_clock(m)
            channels=tuple(m.get('channels',CHANNELS))
            journal=LifecycleJournal(self.root,m)
            if journal.phase() is not None:raise ValueError('FORECAST_ALREADY_STARTED')
            self._lease(m,'forecast');journal.append('PRETASK_VALID');journal.append('INPUTS_VERIFIED')
            gate.require_phase('forecast');results={}
            try:
                for channel in channels:
                    req=self._request(m,manifest_ref,readiness_ref,channel,'forecast',m['model_artifacts'][channel],self.root/m['execution_directory']/channel/'forecast')
                    results[channel]=self._invoke(req)
                return self._complete_forecast(m,manifest_ref,results,journal)
            except Exception as e:
                self.store.alert('V3_EXECUTION_QUARANTINED','P0','V3 execution stopped; prior state preserved',{'actual':str(e),'next_action':'Inspect immutable execution logs and lease. Never retry a consumed fit or overwrite outputs.'},manifest_ref['sha256']);raise

    def recover_forecast(self,manifest_ref,readiness_ref):
        with gate.execution_authority(self.root,gate.REPLAY,manifest_ref,readiness_ref) as m:
            validate_execution_clock(m)
            contract=gate.value(self.root,m['engineering_recovery'],committed=True)
            origin=gate.value(self.root,contract['refs']['original_manifest'])
            journal=LifecycleJournal(self.root,m)
            if journal.phase()!='INPUTS_VERIFIED':raise ValueError('RECOVERY_REQUIRES_UNCONSUMED_ORIGINAL_PHASE')
            with self.store.connect() as db:
                row=db.execute("SELECT operation,execution_directory FROM lab_scientific_leases WHERE season=? AND week=? AND action='forecast'",(m['season'],m['week'])).fetchone()
            if not row or row['operation']!=origin['operation'] or row['execution_directory']!=origin['execution_directory']:
                raise ValueError('RECOVERY_ORIGINAL_GLOBAL_LEASE_REQUIRED')
            claim=self.root/origin['execution_directory']/contract.get('claim_filename','RECOVERY_CLAIM.json')
            body={'schema_version':'puntproof-lab-recovery-claim-v1','manifest':manifest_ref,
                  'contract':m['engineering_recovery'],'origin_manifest':contract['refs']['original_manifest'],
                  'original_lease':contract['original_lease'],'created_at':dt.datetime.now(dt.timezone.utc).isoformat()}
            try:
                with claim.open('xb') as stream:
                    stream.write(gate.encoded(body));stream.flush();os.fsync(stream.fileno())
            except FileExistsError:
                raise ValueError('RECOVERY_ALREADY_CLAIMED_NO_AUTOMATIC_RETRY') from None
            try:
                recovery_refs=gate.require_forecast_recovery(self.root,'receiving')
                results={}
                request=self._request(m,manifest_ref,readiness_ref,'receiving','recover_forecast',
                    m['model_artifacts']['receiving'],self.root/m['execution_directory']/'receiving/forecast',
                    recovery=recovery_refs)
                results['receiving']=self._invoke(request)
                request=self._request(m,manifest_ref,readiness_ref,'rushing','forecast',
                    m['model_artifacts']['rushing'],self.root/m['execution_directory']/'rushing/forecast')
                results['rushing']=self._invoke(request)
                result=self._complete_forecast(m,manifest_ref,results,journal)
                recovery_receipt={'status':'RECOVERED_AND_SEALED','manifest':manifest_ref,
                    'original_manifest':contract['refs']['original_manifest'],'claim':ref(self.root,claim),
                    'repeated_deterministic_expert_inference':True,'receiving_forecast_week_calls':0,
                    'rushing_first_execution':True,'original_lease_preserved':True,'fits':0,'updates':0,
                    'target_outcomes_opened':0,'bridge_receipts':{c:r['_receipt_ref'] for c,r in results.items()}}
                rp=write_once(self.root/m['execution_directory']/'RECOVERY_RESULT.json',recovery_receipt)
                self.store.audit('V3_FORECAST_PERSISTENCE_RECOVERED',dict(recovery_receipt,receipt=ref(self.root,rp)))
                return dict(result,recovery_receipt=ref(self.root,rp))
            except Exception as e:
                self.store.alert('V3_EXECUTION_QUARANTINED','P0','Forecast recovery stopped; original evidence preserved',
                    {'actual':str(e),'next_action':'Inspect the preserved original and recovery evidence. Do not reset either claim or lease.'},manifest_ref['sha256'])
                raise

    def _complete_forecast(self,m,manifest_ref,results,journal):
        channels=tuple(m.get('channels',CHANNELS))
        if m['operation']==gate.FORWARD and dt.datetime.now(dt.timezone.utc)>=gate.timestamp(m['first_kickoff']):raise ValueError('FORECAST_COMPLETED_AFTER_KICKOFF_NONISSUED')
        pending={c:ref(self.root,results[c]['artifacts']['session']['path']) for c in channels}
        evidence=[ref(self.root,a['path']) for c in channels for a in results[c]['artifacts'].values()]
        journal.append('FORECAST_PACKAGES_SEALED',pending_sessions=pending,evidence=evidence)
        journal.append('GLOBAL_EXECUTION_SEAL_VERIFIED',evidence=evidence)
        journal.append('ATTEMPT_CONSUMED',automatic_retry=False)
        for channel in channels:
            r=results[channel];data=json.loads(Path(r['artifacts']['predictions']['path']).read_bytes())
            for row in data['rows']:
                row={**row,'model_id':'Adaptive V3','classification':m['classification'],'sealed_at':now(),
                     'population_scope':m.get('population_scope','COMPLETE_VERIFIED_SLATE'),
                     'history_note':'2026-only available history; conditional on appearance; not an exact historical replay','manifest':manifest_ref,'forecast_artifact':ref(self.root,r['artifacts']['forecast']['path'])}
                distribution(row['support'],row['pmf'])
                key=hashlib.sha256(gate.encoded([manifest_ref,row['game_id'],row['gsis'],row['team'],channel])).hexdigest()
                self.store.append('forecast',key,row,immutable=True)
        self.store.append('receipt',manifest_ref['sha256']+'-forecast',{'action':'FORECAST_SEALED','week':m['week'],'channels':list(channels),'manifest':manifest_ref,'provider_calls':0},immutable=True)
        return {'status':'FORECAST_SEALED','period':[2026,m['week']],'channels':list(channels)}

    def settle(self,manifest_ref,readiness_ref,completion_ref):
        m0=gate.value(self.root,manifest_ref)
        capability={'capability':'SETTLE'} if m0.get('schema_version')=='puntproof-lab-execution-manifest-v2' else {}
        with gate.execution_authority(self.root,m0['operation'],manifest_ref,readiness_ref,**capability) as m:
            journal=LifecycleJournal(self.root,m)
            if journal.phase()!='ATTEMPT_CONSUMED':raise ValueError('SETTLEMENT_NOT_READY_OR_ALREADY_DONE')
            completion=gate.value(self.root,completion_ref)
            if m.get('schema_version')=='puntproof-lab-execution-manifest-v2' and (m.get('full_fixture_census_verified') is not True or completion.get('full_fixture_census_verified') is not True):
                raise ValueError('COMPLETE_WEEK_FIXTURE_CENSUS_REQUIRED_FOR_LEARNING')
            if completion.get('status')!='VERIFIED_COMPLETE' or completion.get('all_fixtures_complete') is not True or completion.get('week')!=m['week'] or completion.get('season')!=2026 or set(completion.get('outcomes',{}))!=set(CHANNELS):raise ValueError('WHOLE_WEEK_COMPLETION_REQUIRED')
            # Validate all completion metadata before consuming the settlement lease.
            expected={f['game_id']:gate.timestamp(f['kickoff']) for f in m['fixtures']}
            if len(completion['fixtures'])!=len(expected) or {f['game_id'] for f in completion['fixtures']}!=set(expected):raise ValueError('COMPLETION_POPULATION_MISMATCH')
            for f in completion['fixtures']:
                if f['final'] is not True or gate.timestamp(f['kickoff'])!=expected[f['game_id']] or not expected[f['game_id']]<=gate.timestamp(f['completed_at'])<=dt.datetime.now(dt.timezone.utc):raise ValueError('COMPLETION_CLOCK_OR_FINALITY')
                gate.checked(self.root,f['source_ref'])
            self._lease(m,'settlement')
            journal.append('RESPONSE_ACCESS_ENABLED',completion=completion_ref,outcomes=completion['outcomes'])
            gate.require_phase('response');outputs={};scores={};predictions={}
            for channel in CHANNELS:
                oref=completion['outcomes'][channel];gate.require_response_artifact(self.root,gate.reference(self.root,oref),oref['sha256'],channel)
                out=gate.value(self.root,oref);outputs[channel]=out['rows'] if isinstance(out,dict) else out
                pred=self.root/m['execution_directory']/channel/'forecast'/'predictions.json'
                predictions[channel]=json.loads(pred.read_bytes())['rows']
                # Use the same exact issued census and label contract as the child
                # before declaring responses complete or opening any scoring phase.
                validate_outcomes(outputs[channel],predictions[channel],[2026,m['week']])
            journal.append('RESPONSES_COMPLETE',evidence=list(completion['outcomes'].values()))
            gate.require_phase('score')
            for channel in CHANNELS:
                scores[channel]=score_rows(predictions[channel],outputs[channel])
            scorepath=self.root/m['execution_directory']/'SCORES.json';write_once(scorepath,scores)
            journal.append('SCORING_COMPLETE',evidence=[ref(self.root,scorepath)])
            gate.require_phase('learn');seal=next(e for e in journal.records() if e['state']=='FORECAST_PACKAGES_SEALED');results={}
            for channel in CHANNELS:
                output=self.root/'data/private/research-lab/sessions'/manifest_ref['sha256']/channel
                req=self._request(m,manifest_ref,readiness_ref,channel,'settle',seal['pending_sessions'][channel],output,outcomes=completion['outcomes'][channel],provenance={'completion':completion_ref,'outcomes':completion['outcomes'][channel]})
                results[channel]=self._invoke(req)
            modelrefs={c:ref(self.root,results[c]['artifacts']['session']['path']) for c in CHANNELS}
            if m['operation']==gate.FORWARD:journal.append('MODEL_STATE_UPDATED',evidence=list(modelrefs.values()))
            closed=journal.append('AUTHORITY_CLOSED',evidence=list(modelrefs.values()))
            consumer,_=gate.consumer_for(self.root,m['operation']);states={}
            for channel in CHANNELS:
                previous=m['model_artifacts'][channel]
                seed=previous if previous in consumer['seed_artifacts'] else gate.value(self.root,m['parent_state_receipts'][channel])['seed_artifact']
                body={'schema_version':'puntproof-lab-model-state-receipt-v1','status':'AUTHORITY_CLOSED','operation':m['operation'],'model_artifact':modelrefs[channel],'seed_artifact':seed,'season':2026,'history_through_week':m['week'],'implementation':consumer['implementation'],'closed_lifecycle':closed,'parent_model':previous,'channel':channel}
                p=self.root/'data/private/research-lab/sessions'/manifest_ref['sha256']/channel/'STATE.json';write_once(p,body);states[channel]=ref(self.root,p)
                monitor(self.store,scores[channel],manifest_ref['sha256']+channel)
                self.store.append('performance',manifest_ref['sha256']+channel,{'classification':m['classification'],'channel':channel,'season':2026,'week':m['week'],'cohorts':scores[channel]['cohorts'],'excluded':scores[channel]['excluded'],'source':ref(self.root,scorepath),'economic_performance':'ROI_NOT_ESTABLISHED'},immutable=True)
            head={'period':[2026,m['week']],'models':modelrefs,'state_receipts':states,'manifest':manifest_ref,'closed_lifecycle':closed}
            self.store.append('model_head',str(m['week']),head,immutable=True)
            self.store.set_setting('last_scientific_run',{'at':now(),'period':head['period'],'classification':m['classification'],'manifest':manifest_ref})
            self.store.audit('V3_SETTLED_AND_LEARNED',head)
            return {'status':'SETTLED_AND_LEARNED','period':head['period'],'head':head}
    def run_current(self):
        status=self.status(refresh=True)
        if not status['ready']:raise PermissionError(status.get('reason','RESEARCH_READINESS_REQUIRED'))
        plan=status['plan']
        if plan['operation']!=gate.FORWARD:raise PermissionError('SOURCE_PREPARATION_REQUIRES_ITS_SCOPED_READER')
        if plan['action']=='forecast':return self.forecast(plan['manifest'],plan['readiness'])
        if plan['action']=='settle':return self.settle(plan['manifest'],plan['readiness'],plan['completion'])
        raise ValueError('UNSUPPORTED_SCIENTIFIC_ACTION')
