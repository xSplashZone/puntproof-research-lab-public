"""Standing bounded lane sharing the existing application, queue and worker lock."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import json,os,secrets,hashlib,csv,io
from core import now,digest
class StopControl:
 def __init__(self,c):self.c=c
 def is_set(self):return self.c.stop_event.is_set() or self.c.store.get_setting('worker_paused',False)
from .continuous_registry import Queue,verify_legacy_recovery

def status(c):
 q=Queue(c.store);rows=q.rows()
 return {'runtime':c.store.get_setting('continuous_runtime',{'status':'AWAITING_ACTIVATION'}),'budget':q.budget(),'attempts':rows,'source':c.store.get_setting('continuous_source',{}),'forward':c.store.get_setting('continuous_forward',{}),'processes':{'A':'EXPOSED_HISTORICAL_DEVELOPMENT','B':c.store.get_setting('continuous_refit_status','WAITING_VERIFIED_COMPLETED_PERIOD_AND_NEW_VERSIONED_SNAPSHOT'),'C':'FROZEN_PROSPECTIVE_BENCHMARK'},'paused':c.store.get_setting('worker_paused',False),'next_wake':(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat(),'duplicate_policy':'Same recipe/schema/data/split/seed is never rerun; interruptions are preserved.'}

def launch(c,mr):
 from nfl_model import continuous_gamd_gate as g
 from gamd.limits import run,load_runtime
 m=g.read(c.root,mr);out=c.root/m['output'];value=load_runtime(c.root);nonce=secrets.token_hex(32)
 g.write_once(out/'CLAIM.json',{'manifest':mr,'parent_pid':os.getpid(),'nonce_sha256':hashlib.sha256(nonce.encode()).hexdigest(),'at':now()})
 argv=[value['base_executable']['path'],'-I','-B','-S',str(c.root/'tools/research_lab/gamd/continuous_child.py'),str(c.root/mr['path'])]
 argv.insert(-1,str(c.root))
 return run(argv,c.root,out/'stdout.log',out/'stderr.log',seconds=300,memory_mb=4096,stop=StopControl(c),handshake=('EXECUTE '+nonce+'\n').encode())

def publish(c,receipt):
 from nfl_model import continuous_gamd_gate as g
 report=g.read(c.root,receipt['coverage']);exports=c.store.root/'exports';exports.mkdir(exist_ok=True);links={}
 for name,body in [('CONTINUOUS_COVERAGE',report),('CONTINUOUS_FIELD_65',report['priority_65']),('CONTINUOUS_NAMED_GAPS',g.read(c.root,receipt['named_gaps']))]:
  path=exports/(name+'-'+digest(body)[:16]+'.json');g.write_once(path,body);links[name]='/api/download/'+path.name
 stream=io.StringIO(newline='');writer=csv.DictWriter(stream,fieldnames=list(report['rows'][0]));writer.writeheader()
 for row in report['rows']:writer.writerow({k:json.dumps(v) if isinstance(v,(list,dict)) else v for k,v in row.items()})
 raw=stream.getvalue().encode();path=exports/('CONTINUOUS_TRANSITIONS-'+hashlib.sha256(raw).hexdigest()[:16]+'.csv')
 if not path.exists():path.write_bytes(raw)
 links['csv']='/api/download/'+path.name
 c.store.set_setting('continuous_source',{'summary':receipt['summary'],'downloads':links,'transition_matrix':report['transition_matrix'],'provider_calls':0,'original_forecasts_modified':0,'receipt':receipt['receipt'],'source_cutoff':receipt['source_cutoff']})

def run_next(c):
 from nfl_model import continuous_gamd_gate as g
 from gamd.continuous_preparation import source,fit
 from gamd.continuous_source import execute
 from gamd.continuous_forward import run_next as forward
 if c.stop_event.is_set() or c.store.get_setting('worker_paused',False):return {'status':'PAUSED'}
 q=Queue(c.store)
 try:
  consumer,_=g.consumer_for(c.root,g.SOURCE);g.consumer_for(c.root,g.DEVELOPMENT)
  if (c.root/'TASK_CHARTER.json').exists():return {'status':'LIVE_CHARTER_ACTIVE'}
  source_manifest=source(c.root,c.store);receipt=execute(c.root,c.store,source_manifest);publish(c,receipt)
  if not getattr(c,'continuous_recovered',False):q.recover(c.root);c.continuous_recovered=True
  policy=g.read(c.root,consumer['policy'],True)
  proposed=[]
  for template in policy['templates']:
   scheduled=template['process']=='SCHEDULED_REFIT'
   if scheduled and not receipt['verified_complete_games']:continue
   legacy=bool(template.get('recovery_of'));projection=receipt['legacy_training'] if legacy else receipt['scheduled_training'] if scheduled else receipt['training_projection'];rr=receipt['legacy_receipt'] if legacy else receipt['scheduled_receipt'] if scheduled else receipt['receipt']
   plan={k:v for k,v in template.items() if k not in ('id','recovery_of')};plan.update(template_id=template['id'],projection=projection,projection_receipt=rr,data_sha256=projection['sha256'])
   if legacy:plan['recovery']=verify_legacy_recovery(c.root,template['recovery_of'])
   proposed.append(plan)
  with g.authority(c.root,g.SOURCE,source_manifest):
   skipped=q.admit_distinct(c.root,proposed)
  if skipped:c.store.set_setting('continuous_duplicate_evidence',skipped)
  pending=[r for r in q.rows() if r['status']=='PLANNED'];budget=q.budget()
  if pending and budget['allowed']:
   r=pending[0];mr=fit(c.root,r,receipt);g.manifest(c.root,g.DEVELOPMENT,mr);g.pretask(c.root,g.DEVELOPMENT,g.read(c.root,mr));c.child_command(c.root/mr['path']);q.claim(r['id']);c.force_idle=False
   c.store.set_setting('continuous_runtime',{'status':'TRAINING','attempt':r['id'],'template':r['plan']['template_id'],'started_at':now(),'next_action':'One bounded fit; every outcome retained.'})
   try:
    execution=launch(c,mr);tp=c.root/g.PRIVATE/'attempts'/r['id']/'TERMINAL.json'
    if execution['status']=='COMPLETED' and tp.exists():
     value=g.read(c.root,g.ref(c.root,tp));g.need(value['manifest_sha256']==mr['sha256'],'TERMINAL_BINDING')
     # Validate exact immutable artifact hashes before publishing the result.
     for artifact in value['artifacts']:
      path=Path(artifact['path']);g.need(path.resolve().is_relative_to(tp.parent.resolve()),'TERMINAL_ARTIFACT_SCOPE');g.need(g.sha(path)==artifact['sha256'],'TERMINAL_ARTIFACT_HASH')
     q.finish(r['id'],value['status'],result=value,elapsed=execution['elapsed_seconds'])
     if r['plan']['process']=='SCHEDULED_REFIT':c.store.set_setting('continuous_refit_status','VERSIONED_MODEL_SAVED_AWAITING_FUTURE_EVALUATION')
     if not c.store.latest('continuous_model',r['id']):c.store.append('continuous_model',r['id'],value,immutable=True)
    else:q.finish(r['id'],execution['status'] if execution['status']!='COMPLETED' else 'FAILED',error='Inspect retained stdout/stderr; no automatic scientific retry.',elapsed=execution['elapsed_seconds'])
   except Exception as exc:
    if next(a for a in q.rows() if a['id']==r['id'])['status']=='CLAIMED':q.finish(r['id'],'FAILED',error=str(exc),elapsed=300)
    raise
  forward(c,receipt,q.rows())
  from gamd.continuous_settlement import run as settle_benchmarks
  settle_benchmarks(c,receipt)
  pending=[r for r in q.rows() if r['status']=='PLANNED'];state={'status':'QUEUED' if pending and q.budget()['allowed'] else 'BUDGET_COOLDOWN' if pending else 'IDLE_EVIDENCE_EXHAUSTED','at':now(),'next_action':'Next tested source/schema mechanism runs on the next eligible wake.' if pending else 'All distinct admitted templates evaluated. Await a new admitted snapshot or tested mechanism; no repeated fits.'}
  c.store.set_setting('continuous_runtime',state);c.store.set_setting('gamd_next_action',state['next_action']);return state
 except Exception as exc:
  state={'status':'BLOCKED','reason':str(exc),'at':now(),'next_action':'Inspect the exact continuous-lane prerequisite. Existing forecasts and settlement remain separate.'};c.store.set_setting('continuous_runtime',state);c.store.alert('CONTINUOUS_GAMD','P1','Continuous research needs investigation',state,'continuous');return state
