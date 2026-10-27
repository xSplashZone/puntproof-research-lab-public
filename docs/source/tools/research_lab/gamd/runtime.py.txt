"""GAMD extension sharing the Lab worker lock, resources and pause control."""
import csv
import hashlib
import json
from pathlib import Path
import threading
import time
from core import now,encoded,digest
from gamd.registry import Registry,write_once,FAMILIES,CHANNELS

ROOT=Path(__file__).resolve().parents[3]

class Coordinator:
 def __init__(self,store,worker,root=ROOT):
  self.store=store;self.worker=worker;self.root=Path(root);self.registry=Registry(store)
  self.lock_handle=None;self.thread=None;self.stop_event=threading.Event();self.wake_event=threading.Event();self.force_idle=False;self.recovery_pending=True;self.runtime_probe_checked=False
 def gate(self):
  from nfl_model import gamd_gate
  return gamd_gate
 def status(self):
  result=self.registry.summary()
  from gamd.continuous_runtime import status as continuous_status
  result['continuous']=continuous_status(self)
  from gamd.licensed_runtime import read_status
  result['licensed_bdl']={'runtime':read_status(self.store),'source':self.store.get_setting('licensed_bdl_source',{}),'ablation':self.store.get_setting('licensed_bdl_ablation',{})}
  from gamd.knowledge import knowledge_feature_map
  result['knowledge_map']=knowledge_feature_map(self.store.records('knowledge'))
  measured=[r for r in result['attempts'] if r.get('result') and r['result'].get('decision',{}).get('paired_mae_interval',{}).get('mean_delta') is not None]
  ordered=sorted(measured,key=lambda r:r['result']['decision']['paired_mae_interval']['mean_delta'])
  def brief(r):return {'attempt':r['id'],'family':r['plan']['family'],'channel':r['plan']['channel'],'matched_mae_delta':r['result']['decision']['paired_mae_interval']['mean_delta'],'status':r['status']}
  result['best_candidate']=brief(ordered[0]) if ordered else None
  result['biggest_regression']=brief(ordered[-1]) if ordered and ordered[-1]['result']['decision']['paired_mae_interval']['mean_delta']>0 else None
  try:admission=self.gate().status(self.root)
  except (ImportError,OSError,ValueError,RuntimeError):admission={'status':'AWAITING_EXACT_GAMD_AUTHORITY','reason':'Historical projection and new candidate fits are not registered for this consumer.'}
  return {**result,'admission':admission,'runtime':self.store.get_setting('gamd_runtime',{}),'coverage':self.store.records('coverage_reconciliation'),'preflight_blockers':self.store.records('gamd_preflight'),
   'paused':self.store.get_setting('worker_paused',False),'next_action':self.store.get_setting('gamd_next_action','Await the exact historical projection and fit admission.'),'thread_running':bool(self.thread and self.thread.is_alive())}
 def start(self):
  if self.thread and self.thread.is_alive():return
  if not self.claim_coordinator():return
  try:self.recovery_pending=True;self.stop_event.clear();self.thread=threading.Thread(target=self.loop,name='PuntProofGAMD',daemon=True);self.thread.start()
  except BaseException:self.release_coordinator();raise
 def stop(self):
  self.stop_event.set();self.wake_event.set()
  if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=10)
 def wake(self,*,force_idle=False):self.force_idle=force_idle;self.wake_event.set()
 def claim_coordinator(self):
  import os,msvcrt
  directory=self.store.root/'gamd';directory.mkdir(parents=True,exist_ok=True)
  handle=(directory/'COORDINATOR.lock').open('a+b')
  if not handle.tell():handle.write(b'0');handle.flush()
  handle.seek(0)
  try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
  except OSError:handle.close();return False
  self.lock_handle=handle;return True
 def release_coordinator(self):
  if self.lock_handle:
   import msvcrt
   handle=self.lock_handle;self.lock_handle=None;handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1);handle.close()
 def loop(self):
  try:
   while not self.stop_event.is_set():
    try:self.run_once()
    except Exception as exc:
     self.store.set_setting('gamd_next_action','GAMD stopped on an execution error; inspect its retained receipt.')
     self.store.alert('GAMD_EXECUTION','P1','GAMD needs investigation',{'actual':str(exc),'next_action':'Open Research and the exact GAMD attempt receipt; do not reset consumed work.'},'gamd')
    self.wake_event.wait(60);self.wake_event.clear()
  finally:self.release_coordinator()
 def projection(self,policy):
  directory=self.store.root/'gamd'/'projection';receipt=directory/'RECEIPT.json';output=directory/'ROWS.jsonl'
  if receipt.exists():
   saved=json.loads(receipt.read_bytes())
   if saved['source_manifest'] not in [policy['source_manifest'],*policy.get('accepted_projection_sources',[])] or hashlib.sha256(output.read_bytes()).hexdigest()!=saved['projection']['sha256']:raise ValueError('GAMD_PROJECTION_CUSTODY_CHANGED')
   return saved['projection']
  from gamd.source import execute
  result=execute(self.root,self.root/policy['source_manifest']['path'])
  directory.mkdir(parents=True,exist_ok=True)
  # A partial projection is retained and cannot be overwritten on restart.
  with output.open('xb') as f:
   for row in result['rows']:f.write(encoded(row)+b'\n')
   f.flush();__import__('os').fsync(f.fileno())
  ref={'path':output.relative_to(self.root).as_posix(),'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}
  write_once(receipt,{'source_manifest':policy['source_manifest'],'projection':ref,'at':now(),'audit':result['audit']})
  self.store.append('gamd_projection',ref['sha256'],{'projection':ref,'audit':result['audit'],'recorded_at':now()},immutable=True)
  return ref
 def prepare_fit(self,candidate,projection,policy):
  from gamd.preparation import seal_manifest,fit_witness
  gate=self.gate();consumer,consumer_ref=gate.consumer_for(self.root,gate.DEVELOPMENT)
  spec=candidate['plan'];out=self.store.root/'gamd'/'attempts'/candidate['id']
  receipt_ref=gate.ref(self.root,self.store.root/'gamd'/'projection'/'RECEIPT.json')
  receipt=gate.read(self.root,receipt_ref)
  campaign=gate.read(self.root,policy['campaign_ref'])
  witness=fit_witness(receipt,projection,receipt_ref,spec['channel'],spec['folds'],campaign['minimum_split_rows'],family=spec['family'])
  manifest={**spec,'operation':gate.DEVELOPMENT,'attempt_id':candidate['id'],
   'output':out.relative_to(self.root).as_posix(),'campaign_ref':policy['campaign_ref'],
   'projection_receipt':receipt_ref,'as_of':candidate['created_at']}
  return seal_manifest(self.root,out,manifest,consumer,consumer_ref,witness)
 def child_command(self,manifest,operation=None):
  from gamd.limits import load_runtime,probe_runtime,command
  value=load_runtime(self.root,operation)
  if not self.runtime_probe_checked:
   import os,secrets
   directory=self.store.root/'gamd'/'runtime-checks'/(str(os.getpid())+'-'+secrets.token_hex(8))
   receipt=probe_runtime(self.root,directory,value,stop=self.stop_event)
   write_once(directory/'RECEIPT.json',receipt)
   self.store.set_setting('gamd_runtime_probe',{'status':'PASS','receipt':self.gate().ref(self.root,directory/'RECEIPT.json'),'football_rows':0,'model_fits':0,'provider_calls':0})
   self.runtime_probe_checked=True
  return command(self.root,manifest,operation)
 def forward_work(self):
  try:
   from gamd.forward import run
   result=run(self.root,self.store,coordinator=self)
  except Exception as exc:
   result={'status':'BLOCKED','reason':str(exc),'new_forecasts':0,'new_scored_observations':0}
  self.store.set_setting('gamd_forward_runtime',result)
  return result
 def record_preclaim_blocker(self,candidate,error):
  body={'attempt':candidate['id'],'family':candidate['plan']['family'],'channel':candidate['plan']['channel'],
   'phase':'PRECLAIM','consumed':False,'error':str(error),'projection':candidate['plan']['projection']}
  key=digest(body)
  if not any(row['id']==key for row in self.store.records('gamd_preflight')):
   self.store.append('gamd_preflight',key,{**body,'recorded_at':now()},immutable=True)
  self.store.alert('GAMD_PREFLIGHT_BLOCKED','P1','A candidate lacks a required input',
   {**body,'next_action':'Inspect this exact prerequisite. Other eligible recipes and coverage continue; no fit was consumed.'},candidate['id'])
 def terminal_result(self,candidate,manifest_path,projection):
  """Authenticate retained own-result artifacts; never deserialize or refit."""
  gate=self.gate();out=Path(manifest_path).parent;terminal=out/'TERMINAL.json'
  value=json.loads(terminal.read_bytes());decision=value.get('decision',{});state=value.get('status');spec=candidate['plan']
  if (state not in ('COMPLETED','REJECTED','INCONCLUSIVE') or decision.get('status')!=state
      or value.get('family')!=spec['family'] or value.get('channel')!=spec['channel']
      or value.get('projection_file_sha256')!=projection['sha256']
      or value.get('manifest_sha256')!=hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest()
      or value.get('completed_folds')!=2 or len(value.get('folds',[]))!=2):raise ValueError('TERMINAL_IDENTITY_OR_CUSTODY_MISMATCH')
  artifacts=value.get('artifacts',[])
  if not artifacts or not value.get('result_artifact'):raise ValueError('TERMINAL_ARTIFACTS_MISSING')
  for item in [*artifacts,value['result_artifact']]:
   path=Path(item['path']).resolve();path.relative_to((out/'artifacts').resolve())
   gate.checked(self.root,{'path':path.relative_to(self.root).as_posix(),'sha256':item['sha256']})
  retained=json.loads(Path(value['result_artifact']['path']).read_bytes())
  expected={k:v for k,v in value.items() if k not in ('result_artifact','projection_file_sha256','manifest_sha256')}
  if retained!=expected:raise ValueError('TERMINAL_RESULT_DIFFERS_FROM_FITTED_ARTIFACT')
  return value
 def publish_result(self,candidate,value):
  self.store.append('gamd_model',candidate['id'],{'family':candidate['plan']['family'],'channel':candidate['plan']['channel'],
   'decision':value['decision'],'artifact_directory':str(self.store.root/'gamd'/'attempts'/candidate['id']),'result':value},immutable=True)
 def recover_claims(self,policy):
  # Called only after live authority and the coordinator lock have been checked.
  # A completed child can be reconciled; an interrupted child is never restarted.
  for candidate in self.registry.attempts():
   if candidate['status']!='CLAIMED' or candidate['plan'].get('campaign')!=policy['campaign']:continue
   out=self.store.root/'gamd'/'attempts'/candidate['id'];mp=out/'MANIFEST.json'
   if not (out/'TERMINAL.json').exists():
    self.registry.finish(candidate['id'],'INTERRUPTED',error='Previous process ended after scientific claim; no completed terminal artifact. Automatic retry forbidden.');continue
   try:
    manifest=json.loads(mp.read_bytes())
    if manifest.get('campaign_ref')!=policy['campaign_ref'] or any(manifest.get(k)!=v for k,v in candidate['plan'].items()):raise ValueError('RECOVERY_MANIFEST_BINDING')
    value=self.terminal_result(candidate,mp,candidate['plan']['projection'])
   except Exception as exc:
    self.registry.finish(candidate['id'],'INTERRUPTED',error='Retained terminal could not be authenticated: '+str(exc));continue
   self.registry.finish(candidate['id'],value['status'],value,elapsed=value.get('resources',{}).get('wall_seconds'))
   self.publish_result(candidate,value)
  self.recovery_pending=False
 def run_once(self):
  if self.stop_event.is_set() or self.store.get_setting('worker_paused',False):return {'status':'PAUSED'}
  if not self.worker.run_lock.acquire(blocking=False):return {'status':'EXISTING_LAB_WORKER_BUSY'}
  try:
   # Observation has its own admitted source path and requires no fitting budget.
   from gamd.continuous_runtime import observe
   observation=observe(self)
   observed={k:v for k,v in observation.items() if k!='receipt'}
   try:policy=self.gate().require_campaign(self.root)
   except (ImportError,OSError,ValueError,RuntimeError) as exc:
    self.store.set_setting('gamd_next_action','Historical development needs its exact authority; independent observation was checked.')
    return {'status':'BLOCKED','reason':str(exc),'observation':observed}
   resource=self.worker.resource_status(force=self.force_idle)
   if not resource['allowed']:
    self.store.set_setting('gamd_next_action','Fitting waits for PC resources: '+', '.join(resource['reasons']))
    return {'status':'RESOURCE_WAIT','observation':observed}
   if self.recovery_pending:self.recover_claims(policy)
   # Independent source work is allowed even if historical fitting has no labels,
   # and a coverage source problem does not disable the separate fit pipeline.
   try:
    from gamd.coverage import run as reconcile_coverage
    coverage=reconcile_coverage(self.root,self.store,policy)
    self.store.set_setting('gamd_coverage_runtime',coverage)
   except Exception as exc:
    self.store.set_setting('gamd_coverage_runtime',{'status':'BLOCKED','reason':str(exc)})
    self.store.alert('GAMD_COVERAGE_BLOCKED','P1','Coverage reconciliation needs an input',
     {'actual':str(exc),'next_action':'Inspect the coverage manifest and exact source prerequisite. Historical candidate work continues independently.'},'gamd-coverage')
   projection=self.projection(policy)
   if self.stop_event.is_set() or self.store.get_setting('worker_paused',False):return {'status':'PAUSED'}
   for recipe in policy['recipes']:
    spec={**recipe,'campaign':policy['campaign'],'data_sha256':projection['sha256'],'projection':projection}
    self.registry.plan(spec)
   candidates=[x for x in self.registry.attempts() if x['status']=='PLANNED' and x['plan'].get('campaign')==policy['campaign']]
   if not candidates:
    from gamd.licensed_runtime import run_next
    licensed=run_next(self)
    if licensed.get('status') not in ('CAMPAIGN_COMPLETE','BLOCKED','LIVE_CHARTER_ACTIVE'):return licensed
    forward=self.forward_work()
    from gamd.continuous_runtime import run_next as continuous_next
    continuous=continuous_next(self,observation=observation)
    return {'status':'STANDING_DEVELOPMENT','forward':forward,'continuous':continuous}
   selected=None;blocked=[]
   for candidate in candidates:
    try:
     mp=self.prepare_fit(candidate,projection,policy)
     self.gate().require_fit(self.root,mp)  # Exact gate before scientific claim.
     argv=self.child_command(mp)  # Prove exact parent/Job/nonce before claiming.
    except Exception as exc:
     self.record_preclaim_blocker(candidate,exc);blocked.append({'attempt':candidate['id'],'reason':str(exc)});continue
    selected=candidate;break
   if selected is None:
    forward=self.forward_work()
    self.store.set_setting('gamd_next_action','Candidate fitting is waiting for the listed input prerequisites; no new fit has been consumed.')
    return {'status':'PRECLAIM_BLOCKED','blockers':blocked,'forward':forward}
   candidate=selected;spec=candidate['plan'];attempt=candidate['id'];out=mp.parent
   self.registry.claim(attempt);self.force_idle=False
   self.store.set_setting('gamd_runtime',{'status':'FITTING','family':spec['family'],'channel':spec['channel'],'attempt':attempt,'started_at':now()})
   from gamd.limits import run
   try:
    nonce=self.gate().claim_fit(self.root,mp)
    result=run(argv,self.root,out/'stdout.log',out/'stderr.log',seconds=300,memory_mb=4096,stop=self.stop_event,handshake=('EXECUTE '+nonce+'\n').encode('ascii'))
   except Exception as exc:
    self.registry.finish(attempt,'FAILED',error=str(exc));self.store.set_setting('gamd_runtime',{'status':'FAILED','attempt':attempt,'at':now()});raise
   terminal=out/'TERMINAL.json'
   if result['status']=='COMPLETED' and terminal.exists():
    try:
     value=self.terminal_result(candidate,mp,projection);state=value['status']
    except Exception as exc:
     self.registry.finish(attempt,'FAILED',error=str(exc),elapsed=result['elapsed_seconds']);raise
    self.registry.finish(attempt,state,value,elapsed=result['elapsed_seconds'])
    self.publish_result(candidate,value)
   else:
    self.registry.finish(attempt,result['status'] if result['status']!='COMPLETED' else 'FAILED',error='Child did not produce a valid terminal result; inspect retained stderr.',elapsed=result['elapsed_seconds'])
    self.store.alert('GAMD_MODEL_FAILURE','P1','A candidate fit failed',{'attempt':attempt,'actual':result,'next_action':'Inspect stdout/stderr and immutable input manifest; create a repair ticket without resetting this attempt.'},attempt)
   self.store.set_setting('gamd_runtime',{'status':'IDLE','last_attempt':attempt,'at':now()})
   self.export_ledger();return result
  finally:self.worker.run_lock.release()
 def export_ledger(self):
  rows=self.registry.attempts();version=digest(rows);(self.store.root/'exports').mkdir(parents=True,exist_ok=True);out=self.store.root/'exports'/('GAMD_EXPERIMENT_LEDGER-'+version[:16]+'.csv')
  if out.exists():return str(out)
  with out.open('x',encoding='utf-8',newline='') as f:
   writer=csv.DictWriter(f,fieldnames=['id','family','channel','status','created_at','started_at','finished_at','elapsed_seconds','decision','fitted_models','classification']);writer.writeheader()
   for r in rows:
    result=r['result'] or {};writer.writerow({**{k:r.get(k) for k in ('id','status','created_at','started_at','finished_at','elapsed_seconds')},'family':r['plan']['family'],'channel':r['plan']['channel'],'decision':(result.get('decision') or {}).get('status'),'fitted_models':result.get('fitted_models'),'classification':'REVISED_HISTORY_DEVELOPMENT_NOT_BLIND'})
  return str(out)
