"""Standing private development, independent source/cohort, and prospective gates.

No fitting or source loading on import. Old attempts and model gates are untouched.
"""
from contextlib import contextmanager,closing
from contextvars import ContextVar
from datetime import datetime,timezone
from pathlib import Path
import hashlib,importlib.util,json,os,re,sqlite3
from .gamd_gate import checked,read,ref,sha,stamp,now,write_once,encoded,path_for
SOURCE='PUNTPROOF_GAMD_CONTINUOUS_SOURCE_V1'
DEVELOPMENT='PUNTPROOF_GAMD_CONTINUOUS_DEVELOPMENT_V1'
FORWARD='PUNTPROOF_GAMD_CONTINUOUS_FORWARD_V1'
OPS=(SOURCE,DEVELOPMENT,FORWARD)
FAMILY='puntproof_gamd_continuous_v1'
BASE='research/gamd_continuous_v1/'
PRIVATE='data/private/research-lab/gamd/continuous_v1/'
OWNER_SHA='110ede2223119257de630b11fa60d1f1355b1c1fdabb3b287bea043c29f1852e'
LIMITS={'provider_calls':0,'cost_cents':0,'protected_access':False,'production':False,'new_specs_24h':12,'elapsed_seconds_24h':3600,'seconds':300,'memory_mb':4096,'threads':1,'concurrency':1,'recovery_per_error':1}
CANARIES=('individual_context_separation','complete_category_zero','missing_not_zero','signed_yards','unknown_denominator','chronological_groups','partial_history_selection','deduplication','rolling_budget','recovery_before_fit_only','future_cutoff','model_roundtrip','protected_year','source_custody')
_ACTIVE=ContextVar('continuous_gamd',default=None)
def need(ok,code):
 if not ok:raise ValueError('CONTINUOUS_GAMD:'+code)
def consumer_for(root,operation,authority=None):
 from . import stage
 root=Path(root).resolve();a=authority or stage.current_authority();need(operation in OPS and operation in a['allowed_real_data_operations'],'OPERATION_NOT_REGISTERED')
 need(a['CURRENT_AUTHORISED_STAGE']==8 and a['PROTECTED_EVALUATION']=='DISABLED' and a['REAL_DATA_MODELLING']=='BLOCKED' and a['LATER_STAGE_REAL_DATA_MODELLING']=='BLOCKED','GLOBAL_BOUNDARY')
 sem=a['pre_model_semantics'];need(sem['operations'].get(operation)==[FAMILY],'FAMILY_BINDING');cr=sem['consumers'][operation]
 need(cr['path'] in [BASE+prefix+dict(zip(OPS,('SOURCE','DEVELOPMENT','FORWARD')))[operation]+'_CONSUMER.json' for prefix in ('','runtime_v2/','runtime_v3/','runtime_v4/')],'EXACT_CONSUMER')
 c=read(root,cr,True);need(c['operation']==operation and c['family']==FAMILY and c['limits']==LIMITS and c['foundations']==sem['certificates'][FAMILY],'CONSUMER_SCOPE')
 for item in [*c['foundations'].values(),c['owner_request'],c['policy'],c['catalogue'],c['adapter_canary'],*c['implementation']]:checked(root,item,True)
 need(c['owner_request']['sha256']==OWNER_SHA and stamp(c['expires_at'])>datetime.now(timezone.utc),'OWNER_OR_EXPIRY')
 policy=read(root,c['policy'],True);need(policy['limits']==LIMITS and policy['protected_years']==[2025] and policy['provider_calls']==0 and policy['promotion'] is False,'POLICY_BOUNDARIES')
 canary=read(root,c['adapter_canary'],True);need(canary['status']=='PASS' and canary['provider_calls']==canary['real_fits']==0 and canary['code_sha256']=={x['path']:x['sha256'] for x in c['implementation']} and set(CANARIES)<=set(canary['passed_defects']),'CURRENT_CANARIES')
 registry=json.loads((root/'config/source_registry.json').read_bytes())[FAMILY];need(registry['consumer']==sem['consumers'][SOURCE] and registry['policy']==c['policy'],'SOURCE_REGISTRY')
 return c,cr

def spec_hash(m):return hashlib.sha256(encoded({k:v for k,v in m.items() if k not in ('spec_hash','readiness','pretask_satisfiability')})).hexdigest()
def manifest(root,operation,mr):
 c,cr=consumer_for(root,operation);m=read(root,mr);need(m['operation']==operation and m['consumer']==cr and m['policy']==c['policy'] and m['catalogue']==c['catalogue'] and m['spec_hash']==spec_hash(m),'MANIFEST_BINDING')
 need(m['provider_calls']==m['cost_cents']==0 and m['production'] is False and m['protected_access'] is False and stamp(m['as_of'])<=datetime.now(timezone.utc),'MANIFEST_BOUNDARY')
 need(m['output'].startswith(PRIVATE) and all(x not in ('.','..','') for x in m['output'].split('/')),'OUTPUT_BOUNDARY')
 policy=read(root,c['policy'],True);refs=[mr,*m['inputs']]
 for item in m['inputs']:checked(root,item)
 if operation==SOURCE:
  need(m['purpose']=='FIELD_REPAIR_AND_INDEPENDENT_COHORT' and m['inputs']==c['inputs'],'SOURCE_SCOPE')
 elif operation==DEVELOPMENT:
  plan=m['candidate'];template=next((t for t in policy['templates'] if t['id']==plan['template_id']),None)
  need(template is not None and all(plan[k]==template[k] for k in ('family','channel','mechanism','feature_schema','calibration','process','folds','seed','criteria')),'FIXED_TEMPLATE')
  need(m['inputs']==[plan['projection'],plan['projection_receipt']] and plan['data_sha256']==plan['projection']['sha256'],'PROJECTION_BINDING')
  receipt=read(root,plan['projection_receipt']);need(receipt['status']=='COMPLETE' and receipt['training_projection']==plan['projection'] and receipt['policy']==c['policy'],'SOURCE_RECEIPT')
  need(m['output']==PRIVATE+'attempts/'+m['attempt_id'] and re.fullmatch('[a-f0-9]{32}',m['attempt_id']),'ATTEMPT_PATH')
  if template.get('recovery_of'):
   from importlib import import_module
   import_module('gamd.continuous_registry').verify_legacy_recovery(root,template['recovery_of'])
 else:
  need(m['purpose']=='PREGAME_BENCHMARK' and m['fit'] is False and m['inputs']==[m['model'],m['model_result'],m['cohort'],m['history']],'FORWARD_SCOPE')
  result=read(root,m['model_result']);need(result['status'] in ('COMPLETED','REJECTED','INCONCLUSIVE') and result['model']==m['model'] and result['feature_schema']==m['feature_schema'],'OWN_SAVED_MODEL')
  cohort=read(root,m['cohort']);need(cohort['policy']==c['policy'] and cohort['source_receipt']==m['source_receipt'] and cohort['population_scope']=='INDEPENDENT_DATED_ROSTER_COHORT' and m['game_id'] in {x['game_id'] for x in cohort['fixtures']},'COHORT_SCOPE')
  need(m['game_id']!='2026_07_DAL_PHI' and all(stamp(x['kickoff_at'])>stamp(m['as_of']) for x in cohort['targets'] if x['game_id']==m['game_id']),'PREGAME_CLOCK')
 ready=read(root,m['readiness']);need(ready['status']=='READY' and ready['spec_hash']==m['spec_hash'] and ready['consumer']==cr and ready['canary']==c['adapter_canary'],'READINESS_BINDING')
 witness=read(root,ready['witness']);need(witness['status']=='PASS' and witness['records']>0 and witness['provider_calls']==0 and 2025 not in witness['seasons'],'TARGET_WITNESS')
 return c,cr,m,{str(path_for(root,x).resolve()):x['sha256'] for x in refs},refs

def pretask(root,operation,m):
 s=importlib.util.spec_from_file_location('continuous_task_guard',Path(root)/'tools/codex_task_guard.py');g=importlib.util.module_from_spec(s);s.loader.exec_module(g)
 body={'task_id':operation,'task_class':'SOURCE_ADMISSION' if operation==SOURCE else 'MODEL_EXPERIMENT','allowed_actions':['READ_METADATA']+(['MODEL_EXECUTION'] if operation!=SOURCE else []),'completion_states':['CONTINUOUS_OPERATION_COMPLETE'],'external_access':'NONE','paid_access':False,'protected_result_access':False,'pretask_satisfiability':m['pretask_satisfiability']}
 return g.satisfiability(Path(root),body,execution=True)
def require_active(root,operation):
 a=_ACTIVE.get();need(a and a['root']==Path(root).resolve() and a['operation']==operation,'ACTIVE_CONTEXT_REQUIRED');return a
def attempt(root,m):
 with closing(sqlite3.connect((Path(root)/'data/private/research-lab/lab.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
  db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM gamd_continuous_attempts WHERE id=?',(m['attempt_id'],)).fetchone();need(row is not None and json.loads(row['plan'])==m['candidate'],'REGISTERED_EXACT_PLAN')
  return dict(row)
def scheduled_execution(root,operation):
 need(not (Path(root)/'TASK_CHARTER.json').exists(),'LIVE_CHARTER_PRECEDENCE');a=require_active(root,operation);_,_,m,_,_=manifest(root,operation,a['manifest']);pretask(root,operation,m)
 if operation==DEVELOPMENT:need(a['consumed'] and attempt(root,m)['status']=='CLAIMED','CONSUMED_CHILD_REQUIRED')
 return {'status':'PASS','operation':operation,'provider_calls':0}
def validate_manifest(root,operation,authority,inputs):
 need(operation in (DEVELOPMENT,FORWARD) and set(inputs)=={FAMILY} and len(inputs[FAMILY])==1,'EXACT_MODEL_MANIFEST');c,cr,m,admitted,refs=manifest(root,operation,inputs[FAMILY][0]);return admitted,[cr,*c['foundations'].values(),*c['implementation'],*refs]
@contextmanager
def authority(root,operation,mr,*,consumed=False):
 root=Path(root).resolve();c,cr,m,_,_=manifest(root,operation,mr)
 if (root/'TASK_CHARTER.json').exists():
  live=json.loads((root/'TASK_CHARTER.json').read_bytes());need(live['stage_authority']['operations']==[operation],'LIVE_CHARTER_PRECEDENCE')
  if operation!=SOURCE:need(live.get('model_execution',{}).get('inputs')=={FAMILY:[mr]},'LIVE_MANIFEST_MISMATCH')
 token=_ACTIVE.set({'root':root,'operation':operation,'manifest':mr,'consumed':consumed})
 try:
  if operation==SOURCE:
   from . import stage
   stage.require_stage(operation);yield m
  else:
   from .data.semantic_certificate import model_inputs
   with model_inputs(operation,inputs={FAMILY:[mr]}):yield m
 finally:_ACTIVE.reset(token)
@contextmanager
def fitting_authority(root,mr,nonce):
 c,cr,m,_,_=manifest(root,DEVELOPMENT,mr);need(attempt(root,m)['status']=='CLAIMED','FIT_ALREADY_TERMINAL');out=Path(root)/m['output'];claim=read(root,ref(root,out/'CLAIM.json'))
 need(claim['manifest']==mr and claim['parent_pid']==os.getppid() and claim['nonce_sha256']==hashlib.sha256(nonce.encode()).hexdigest(),'DIRECT_PARENT_CLAIM')
 need(not (out/'EXECUTION.json').exists(),'EXECUTION_ALREADY_STARTED');write_once(out/'EXECUTION.json',{'manifest':mr,'parent_claim':ref(root,out/'CLAIM.json'),'at':now(),'child_pid':os.getpid()})
 with authority(root,DEVELOPMENT,mr,consumed=True):yield m

def check_lifecycle(root,c):
 operation=c['model_execution']['operation'];mr=c['model_execution']['inputs'][FAMILY][0];_,_,m,_,_=manifest(root,operation,mr);plan=read(root,c['preexecution_lifecycle']);need(plan['manifest']==mr and plan['retry_after_fit'] is False,'LIFECYCLE_SCOPE');return {'status':'PASS','phase':'PRECLAIM','operation':operation}
