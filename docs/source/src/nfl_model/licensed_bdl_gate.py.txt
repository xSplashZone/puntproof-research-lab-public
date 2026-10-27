"""Retained licensed BDL research: isolated finite campaign, no network on import."""
from contextlib import contextmanager,closing
from contextvars import ContextVar
from datetime import datetime,timezone
import hashlib,importlib.util,json,os,re,secrets,sqlite3
from pathlib import Path
from .gamd_gate import checked,read,ref,sha,stamp,now,write_once,encoded,path_for
SOURCE='PUNTPROOF_BDL_RESEARCH_SOURCE_V1'
DEVELOPMENT='PUNTPROOF_BDL_RESEARCH_DEVELOPMENT_V1'
OPS=(SOURCE,DEVELOPMENT)
FAMILY='puntproof_licensed_bdl_v1'
BASE='research/licensed_bdl_v1/continuous_compat_v6/'
PRIVATE='data/private/research-lab/gamd/licensed_bdl_v1/'
CAMPAIGN='GAMD_BDL_SNAP_ABLATION_2023_2024_V1'
OWNER_SHA='9f89a9882662693fe5f6a7a6403965e780b35fd87772801809e44f7df2e55d92'
LIMITS={'provider_calls':0,'cost_cents':0,'protected_access':False,'production':False,'max_attempts':4,'max_artifacts':4,'seconds':300,'memory_mb':4096,'threads':1,'concurrency':1}
CANARIES=('raw_custody','dated_identity','ambiguous_identity','protected_year','missing_not_zero','snap_denominator','explicit_zero','source_isolation','same_week_poisoning','chronological_splits','consumed_attempt','artifact_roundtrip')
_ACTIVE=ContextVar('licensed_bdl_execution',default=None)

def need(ok,reason):
 if not ok:raise ValueError('LICENSED_BDL:'+reason)

def consumer_for(root,operation,authority=None):
 from . import stage
 root=Path(root).resolve();a=authority or stage.current_authority();need(operation in OPS and operation in a['allowed_real_data_operations'],'OPERATION_NOT_REGISTERED')
 need(a['CURRENT_AUTHORISED_STAGE']==8 and a['PROTECTED_EVALUATION']=='DISABLED' and a['REAL_DATA_MODELLING']=='BLOCKED' and a['LATER_STAGE_REAL_DATA_MODELLING']=='BLOCKED','GLOBAL_BOUNDARY')
 sem=a['pre_model_semantics'];need(sem['operations'].get(operation)==[FAMILY],'FAMILY_BINDING');cr=sem['consumers'][operation]
 need(cr['path']==BASE+('SOURCE' if operation==SOURCE else 'DEVELOPMENT')+'_CONSUMER.json','CONSUMER_PATH')
 c=read(root,cr,True);need(c['operation']==operation and c['family']==FAMILY and c['limits']==LIMITS and c['foundations']==sem['certificates'][FAMILY],'CONSUMER_SCOPE')
 for item in [*c['foundations'].values(),c['owner_request'],c['source_policy'],c['catalogue'],c['campaign'],c['adapter_canary'],*c['implementation']]:checked(root,item,True)
 need(c['owner_request']['sha256']==OWNER_SHA and stamp(c['expires_at'])>datetime.now(timezone.utc),'OWNER_OR_EXPIRY')
 registry=json.loads((root/'config/source_registry.json').read_bytes())[FAMILY];need(registry['consumer']==sem['consumers'][SOURCE] and registry['source_policy']==c['source_policy'] and registry['model_use'] is True,'SOURCE_REGISTRY_BINDING')
 policy=read(root,c['source_policy'],True);need(policy['new_api_calls']==0 and policy['protected_numerical_years_excluded']==[2025] and policy['pfr_model_use'] is False,'SOURCE_BOUNDARIES')
 canary=read(root,c['adapter_canary'],True);need(canary['status']=='PASS' and canary['real_fits']==canary['provider_calls']==0 and canary['code_sha256']=={x['path']:x['sha256'] for x in c['implementation']} and set(CANARIES)<=set(canary['passed_defects']),'SAME_ADAPTER_CANARIES')
 need(len(c['implementation'])==len({x['path'] for x in c['implementation']}),'DUPLICATE_CODE_BINDING')
 return c,cr

def spec_hash(m):return hashlib.sha256(encoded({k:v for k,v in m.items() if k not in ('spec_hash','readiness','pretask_satisfiability')})).hexdigest()

def manifest(root,operation,mr):
 c,cr=consumer_for(root,operation);m=read(root,mr)
 need(m['operation']==operation and m['consumer']==cr and m['source_policy']==c['source_policy'] and m['campaign_ref']==c['campaign'] and m['catalogue']==c['catalogue'] and m['spec_hash']==spec_hash(m),'MANIFEST_BINDING')
 need(m['provider_calls']==m['cost_cents']==0 and m['protected_access'] is False and m['production'] is False and stamp(m['as_of'])<=datetime.now(timezone.utc),'MANIFEST_BOUNDARY')
 need(m['output'].startswith(PRIVATE) and not any(x in ('..','.') for x in m['output'].split('/')),'OUTPUT_PATH')
 refs=[mr];admitted={str(path_for(root,mr).resolve()):mr['sha256']}
 if operation==SOURCE:
  need(m['purpose']=='LICENSED_SOURCE_AND_COVERAGE' and m['output']==PRIVATE+'source','SOURCE_PURPOSE')
 else:
  campaign=read(root,c['campaign'],True);need(campaign['campaign']==m['campaign']==CAMPAIGN and campaign['max_attempts']==4,'CAMPAIGN_BOUNDARY')
  recipe=next((x for x in campaign['recipes'] if x['id']==m['recipe_id']),None)
  need(recipe and all(m[k]==recipe[k] for k in ('family','channel','include_snaps','mechanism','recipe_version')) and m['folds']==campaign['folds'] and m['seed']==campaign['seed'],'FIXED_RECIPE')
  receipt=read(root,m['projection_receipt']);need(receipt['status']=='COMPLETE' and receipt['projection']==m['projection'] and receipt['source_policy']==c['source_policy'] and receipt['catalogue']==c['catalogue'] and receipt['provider_calls']==receipt['original_forecasts_modified']==0,'SOURCE_RECEIPT')
  need(m['projection']['path']==PRIVATE+'source/ROWS.jsonl' and m['projection_receipt']['path']==PRIVATE+'source/RECEIPT.json','FIT_SOURCE_PATH')
  need(m['output']==PRIVATE+'attempts/'+m['attempt_id'] and re.fullmatch('[a-f0-9]{32}',m['attempt_id']),'ATTEMPT_PATH')
  for item in (m['projection'],m['projection_receipt']):checked(root,item);refs.append(item);admitted[str(path_for(root,item).resolve())]=item['sha256']
 ready=read(root,m['readiness']);need(ready['status']=='READY' and ready['manifest_spec_sha256']==m['spec_hash'] and ready['consumer']==cr and ready['adapter_canary']==c['adapter_canary'] and ready['implementation']==c['implementation'],'ACTION_READINESS')
 witness=read(root,ready['target_witness']);need(witness['status']=='PASS' and witness['provider_calls']==0 and witness['records']>0,'TARGET_WITNESS')
 need(witness['catalogue']==c['catalogue'],'WITNESS_CATALOGUE')
 if operation==DEVELOPMENT:need(witness['projection']==m['projection'] and witness['seasons']==[2023,2024] and all(witness['split_rows'][s]>=100 for s in ('train','calibration','evaluation')),'FIT_SPLIT_WITNESSES')
 return c,cr,m,admitted,refs

def pretask(root,operation,m,c):
 spec=importlib.util.spec_from_file_location('licensed_bdl_task_guard',Path(root)/'tools/codex_task_guard.py');g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
 d={'task_id':operation,'task_class':'SOURCE_ADMISSION' if operation==SOURCE else 'MODEL_EXPERIMENT','allowed_actions':['READ_METADATA']+(['MODEL_EXECUTION'] if operation==DEVELOPMENT else []),'completion_states':['LICENSED_BDL_OPERATION_COMPLETE'],'external_access':'NONE','paid_access':False,'protected_result_access':False,'pretask_satisfiability':m['pretask_satisfiability']}
 return g.satisfiability(Path(root),d,execution=True)

def require_active(root,operation):
 a=_ACTIVE.get();need(a and a['root']==Path(root).resolve() and a['operation']==operation,'ACTIVE_CONTEXT_REQUIRED');return a

def scheduled_execution(root,operation):
 if (Path(root)/'TASK_CHARTER.json').exists():return None
 a=require_active(root,operation);c,cr,m,_,_=manifest(root,operation,a['manifest']);pretask(root,operation,m,c)
 if operation==DEVELOPMENT:need(a.get('consumed') is True and attempt(root,m)['status']=='CLAIMED','CONSUMED_CHILD_REQUIRED')
 return {'status':'PASS','operation':operation,'consumer':cr,'provider_calls':0}

def validate_manifest(root,operation,authority,inputs):
 need(operation==DEVELOPMENT and set(inputs)=={FAMILY} and len(inputs[FAMILY])==1,'ONE_EXACT_MODEL_MANIFEST')
 c,cr,m,admitted,refs=manifest(root,operation,inputs[FAMILY][0]);return admitted,[cr,*c['foundations'].values(),*c['implementation'],*refs]

@contextmanager
def authority(root,operation,mr,*,consumed=False):
 root=Path(root).resolve();c,cr,m,_,_=manifest(root,operation,mr)
 if (root/'TASK_CHARTER.json').exists():
  live=json.loads((root/'TASK_CHARTER.json').read_bytes());need(live['stage_authority']['operations']==[operation],'LIVE_CHARTER_PRECEDENCE')
  if operation==DEVELOPMENT:need(live.get('model_execution',{}).get('inputs')=={FAMILY:[mr]},'LIVE_MANIFEST_MISMATCH')
 token=_ACTIVE.set({'root':root,'operation':operation,'manifest':mr,'consumed':consumed})
 try:
  if operation==SOURCE:
   from . import stage
   stage.require_stage(operation);yield m
  else:
   from .data.semantic_certificate import model_inputs
   with model_inputs(operation,inputs={FAMILY:[mr]}):yield m
 finally:_ACTIVE.reset(token)

def attempt(root,m):
 with closing(sqlite3.connect((Path(root)/'data/private/research-lab/lab.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
  db.row_factory=sqlite3.Row;r=db.execute('select * from gamd_attempts where id=?',(m['attempt_id'],)).fetchone()
  need(r is not None,'UNREGISTERED_ATTEMPT');p=json.loads(r['plan']);need(all(m.get(k)==v for k,v in p.items()) and p['campaign']==CAMPAIGN,'PLAN_MANIFEST_MISMATCH')
  key={k:p[k] for k in ('mechanism','population','endpoint','data_sha256','family','channel','recipe_version','folds','seed')};key=hashlib.sha256(json.dumps(key,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest();need(r['id']==key[:32] and r['scientific_key']==key and p['data_sha256']==m['projection']['sha256'],'SCIENTIFIC_IDENTITY')
  rows=db.execute("select plan from gamd_attempts where json_extract(plan,'$.campaign')=?",(CAMPAIGN,)).fetchall();need(len(rows)<=4,'CAMPAIGN_BUDGET');recipes=[json.loads(x[0])['recipe_id'] for x in rows];need(len(recipes)==len(set(recipes)),'DUPLICATE_RECIPE')
 return dict(r)

def require_fit(root,mr):
 c,cr,m,_,_=manifest(root,DEVELOPMENT,mr);pretask(root,DEVELOPMENT,m,c);need(attempt(root,m)['status']=='PLANNED','ATTEMPT_CONSUMED');return m

def claim(root,mr):
 c,cr,m,_,_=manifest(root,DEVELOPMENT,mr);need(attempt(root,m)['status']=='CLAIMED','REGISTRY_CLAIM_REQUIRED');out=Path(root)/m['output'];need(not (out/'CLAIM.json').exists() and not (out/'EXECUTION.json').exists(),'CONSUMED_CLAIM')
 nonce=secrets.token_hex(32);write_once(out/'CLAIM.json',{'manifest':mr,'parent_pid':os.getpid(),'nonce_sha256':hashlib.sha256(nonce.encode()).hexdigest(),'at':now()});return nonce

@contextmanager
def fitting_authority(root,mr,nonce):
 c,cr,m,_,_=manifest(root,DEVELOPMENT,mr);need(attempt(root,m)['status']=='CLAIMED','FIT_ALREADY_TERMINAL');out=Path(root)/m['output'];cl=json.loads((out/'CLAIM.json').read_bytes())
 need(cl['manifest']==mr and cl['parent_pid']==os.getppid() and cl['nonce_sha256']==hashlib.sha256(nonce.encode()).hexdigest(),'PARENT_CUSTODY')
 need(not (out/'EXECUTION.json').exists(),'EXECUTION_ALREADY_CONSUMED');write_once(out/'EXECUTION.json',{'manifest':mr,'claim':ref(root,out/'CLAIM.json'),'child_pid':os.getpid(),'at':now()})
 with authority(root,DEVELOPMENT,mr,consumed=True):yield m

def check_lifecycle(root,c):
 operation=c.get('model_execution',{}).get('operation')
 if operation!=DEVELOPMENT:return None
 mr=c['model_execution']['inputs'][FAMILY][0];consumer,cr,m,_,_=manifest(root,operation,mr);plan=read(root,c['preexecution_lifecycle']);need(plan['manifest']==mr and plan['attempts']==1 and plan['retry_after_consumption'] is False,'LIFECYCLE_SCOPE');return {'status':'PASS','operation':operation,'phase':'PRECLAIM_REVISED_HISTORY'}
