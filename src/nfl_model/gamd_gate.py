"""Exact bounded GAMD authority; registration is not scientific success.

No payload reads or fits on import. Source, development and future observations
have separate contexts. Standing contexts never supersede a live task charter.
"""
from contextlib import contextmanager, closing
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess

FAMILY='puntproof_gamd_v1'
SOURCE='PUNTPROOF_GAMD_SOURCE_V1'
DEVELOPMENT='PUNTPROOF_GAMD_DEVELOPMENT_V1'
FORWARD='PUNTPROOF_GAMD_FORWARD_V1'
OPS=(SOURCE,DEVELOPMENT,FORWARD)
BASE='research/gamd_v1/'
PRIVATE='data/private/research-lab/gamd/'
CAMPAIGN='PUNTPROOF_GAMD_MASKED_HISTORY_2021_2024_V1'
_ACTIVE=ContextVar('gamd_execution',default=None)
GIT=r'C:\Program Files\Git\cmd\git.exe'
CANARIES=('missing_not_zero','temporal_identity','same_week_poisoning','signed_law',
          'artifact_roundtrip','consumed_attempt','resource_limits','correction_custody','source_partition')
LIMITS={'provider_calls':0,'cost_cents':0,'protected_access':False,'production':False,
        'max_attempts':6,'max_fold_artifacts':12,'seconds':300,'memory_mb':4096,'threads':1,'concurrency':1}

def need(ok,code):
 if not ok:raise ValueError('GAMD:'+code)
def encoded(v):return (json.dumps(v,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def stamp(v):
 t=datetime.fromisoformat(v.replace('Z','+00:00'));need(t.tzinfo is not None,'TIMEZONE_REQUIRED');return t.astimezone(timezone.utc)
def now():return datetime.now(timezone.utc).isoformat()
def ref(root,path):
 p=Path(path);p=p if p.is_absolute() else Path(root)/p
 return {'path':p.resolve().relative_to(Path(root).resolve()).as_posix(),'sha256':sha(p)}
def path_for(root,r):
 need(isinstance(r,dict) and set(r)=={'path','sha256'} and re.fullmatch('[a-f0-9]{64}',r['sha256'] or ''),'REFERENCE_SCHEMA')
 name=r['path'];need(isinstance(name,str) and '\\' not in name and ':' not in name and not name.startswith('/') and all(x not in ('','.','..') for x in name.split('/')),'REFERENCE_PATH')
 root=Path(root).resolve();p=root/name;need(p.resolve().is_relative_to(root),'REFERENCE_ESCAPE')
 for x in (p,*p.parents):
  need(not x.is_symlink() and not getattr(x,'is_junction',lambda:False)(),'REFERENCE_LINK')
  if x==root:break
 return p
def checked(root,r,committed=False):
 p=path_for(root,r);raw=p.read_bytes();need(hashlib.sha256(raw).hexdigest()==r['sha256'],'HASH_CHANGED:'+r['path'])
 if committed:
  value=subprocess.run([GIT,'-C',str(root),'show','HEAD:'+r['path']],capture_output=True,timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
  need(value.returncode==0 and hashlib.sha256(value.stdout).hexdigest()==r['sha256'],'UNCOMMITTED_BINDING:'+r['path'])
 return raw
def read(root,r,committed=False):return json.loads(checked(root,r,committed))
def write_once(p,value):
 p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);raw=encoded(value)
 if p.exists():need(p.read_bytes()==raw,'IMMUTABLE_CONFLICT:'+p.name);return p
 with p.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 return p

def consumer_for(root,operation,authority=None):
 from nfl_model import stage
 root=Path(root).resolve();a=authority or stage.current_authority()
 need(operation in OPS and operation in a['allowed_real_data_operations'],'OPERATION_NOT_REGISTERED')
 need(a['CURRENT_AUTHORISED_STAGE']==8 and a['REAL_DATA_MODELLING']=='BLOCKED' and a['LATER_STAGE_REAL_DATA_MODELLING']=='BLOCKED' and a['PROTECTED_EVALUATION']=='DISABLED','GLOBAL_BOUNDARY')
 s=a['pre_model_semantics'];need(s['operations'].get(operation)==[FAMILY],'FAMILY_BINDING')
 r=s['consumers'].get(operation);label=dict(zip(OPS,('SOURCE','DEVELOPMENT','FORWARD')))[operation]
 need(r and r['path'] in (BASE+label+'_CONSUMER.json',BASE+'runtime_v2/'+label+'_CONSUMER.json',BASE+'bdl_compat_v1/'+label+'_CONSUMER.json',BASE+'bdl_compat_v2/'+label+'_CONSUMER.json',BASE+'bdl_format_v3/'+label+'_CONSUMER.json',BASE+'bdl_format_v4/'+label+'_CONSUMER.json',BASE+'bdl_closeout_v5/'+label+'_CONSUMER.json',BASE+'continuous_compat_v1/'+label+'_CONSUMER.json',BASE+'continuous_compat_v2/'+label+'_CONSUMER.json',BASE+'continuous_compat_v3/'+label+'_CONSUMER.json',BASE+'continuous_compat_v4/'+label+'_CONSUMER.json',BASE+'continuous_compat_v5/'+label+'_CONSUMER.json',BASE+'continuous_compat_v6/'+label+'_CONSUMER.json'),'CONSUMER_PATH')
 c=read(root,r,True);need(c['operation']==operation and c['family']==FAMILY and c['status']=='REGISTERED_CONDITIONAL' and c['limits']==LIMITS,'CONSUMER_SCOPE')
 need(c['foundations']==s['certificates'][FAMILY],'FOUNDATION_BINDING')
 for item in [*c['foundations'].values(),c['source_policy'],c['campaign'],c['catalogue'],c['owner_approval'],c['adapter_canary'],*c['implementation']]:checked(root,item,True)
 need(len({x['path'] for x in c['implementation']})==len(c['implementation']),'DUPLICATE_IMPLEMENTATION')
 need({'src/nfl_model/gamd_gate.py','tools/research_lab/gamd/source.py','tools/research_lab/gamd/trainer.py','tools/codex_task_guard.py','src/nfl_model/stage.py','src/nfl_model/data/semantic_certificate.py'}<={x['path'] for x in c['implementation']},'REQUIRED_IMPLEMENTATION')
 need(stamp(c['expires_at'])>datetime.now(timezone.utc),'AUTHORITY_EXPIRED')
 registry=json.loads((root/'config/source_registry.json').read_bytes()).get(FAMILY,{})
 need(registry.get('operation')==SOURCE and registry.get('consumer')==s['consumers'][SOURCE] and registry.get('source_policy')==c['source_policy'],'SOURCE_REGISTRY_BINDING')
 canary=read(root,c['adapter_canary'],True)
 need(canary['status']=='PASS' and canary['provider_calls']==canary['real_model_fits']==0 and canary['code_sha256']=={x['path']:x['sha256'] for x in c['implementation']} and set(CANARIES)<=set(canary['passed_defects']),'ADAPTER_CANARIES')
 return c,r

def active_registration(root,consumer):
 name=consumer.get('active_path',BASE+'ACTIVE.json')
 need(name in (BASE+'ACTIVE.json',BASE+'runtime_v2/ACTIVE.json'),'ACTIVE_PATH')
 value=read(root,ref(root,name),True)
 need(value['campaign']==consumer['campaign'] and value['catalogue']==consumer['catalogue'],'ACTIVE_BINDING')
 old_source={'path':'research/gamd_v1/actions/historical/MANIFEST.json','sha256':'ee08ffa01ed8f72d2b0ccbf580c3e9354ca850a9fceda67f351e0976f9b25211'}
 if value.get('accepted_projection_sources'):
  need(value['accepted_projection_sources']==[old_source],'LEGACY_PROJECTION_SCOPE')
  receipt=read(root,value['accepted_projection_receipt'])
  need(receipt['source_manifest']==old_source and receipt['projection']=={'path':PRIVATE+'projection/ROWS.jsonl','sha256':'ae53582b3348c4f32e9f1d672007d01840ab5bd7843fca3a0b95e4248c8ab5bb'},'LEGACY_PROJECTION_CUSTODY')
  checked(root,old_source,True)
 if value.get('accepted_coverage_receipt'):
  r=value['accepted_coverage_receipt'];need(r['path']==PRIVATE+'coverage/c56edb2052a86a3da79f7717/RECEIPT.json','LEGACY_COVERAGE_SCOPE')
  receipt=read(root,r);need(receipt['status']=='COMPLETE' and receipt['summary']['total']==783 and receipt['original_forecasts_modified']==0,'LEGACY_COVERAGE_CUSTODY')
 return value


def status(root):
 try:
  c,r=consumer_for(root,DEVELOPMENT)
  return {'status':'REGISTERED_CONDITIONAL','consumer':r,'live_task_precedence':(Path(root)/'TASK_CHARTER.json').exists(),'limits':LIMITS,'source_readiness':'PER_ACTION_REQUIRED'}
 except (KeyError,ValueError,OSError) as e:return {'status':'BLOCKED','reason':str(e)}

def require_campaign(root):
 root=Path(root).resolve();need(not (root/'TASK_CHARTER.json').exists(),'LIVE_TASK_PRECEDENCE')
 c,cr=consumer_for(root,DEVELOPMENT);a=active_registration(root,c)
 need(a['campaign']==c['campaign'] and a['catalogue']==c['catalogue'],'ACTIVE_BINDING')
 campaign=read(root,c['campaign'],True)
 need(campaign['campaign']==CAMPAIGN and campaign['active'] is True and len(campaign['recipes'])==6,'CAMPAIGN_SCOPE')
 recipes=[]
 for recipe in campaign['recipes']:
  need(recipe['family'] in ('hurdle_glm','hist_gradient_boosting','extra_trees') and recipe['channel'] in ('receiving','rushing'),'RECIPE_SCOPE')
  recipes.append({'family':recipe['family'],'channel':recipe['channel'],'mechanism':campaign['mechanism'],'population':campaign['population']['target'],'endpoint':'MATCHED_SIGNED_YARD_MAE_CRPS','recipe_version':campaign['recipe_version'],'folds':campaign['folds'],'seed':campaign['seed']})
 need(len({(r['family'],r['channel']) for r in recipes})==6,'SIX_DISTINCT_RECIPES')
 return {'campaign':CAMPAIGN,'campaign_ref':c['campaign'],'source_manifest':a['historical_manifest'],'coverage_manifest':a['coverage_manifest'],'recipes':recipes,'consumer':cr,'accepted_projection_sources':a.get('accepted_projection_sources',[]),'accepted_coverage_receipt':a.get('accepted_coverage_receipt')}

def spec_hash(m):return hashlib.sha256(encoded({k:v for k,v in m.items() if k not in ('spec_hash','readiness','pretask_satisfiability')})).hexdigest()

def _manifest(root,operation,r):
 c,cr=consumer_for(root,operation);m=read(root,r)
 need(m.get('spec_hash')==spec_hash(m),'MANIFEST_SPEC_CHANGED')
 need(m['operation']==operation and m['consumer']==cr and m['campaign_ref']==c['campaign'] and m['source_policy']==c['source_policy'],'MANIFEST_BINDING')
 need(m['provider_calls']==m['cost_cents']==0 and m['protected_access'] is False and m['production'] is False,'MANIFEST_BOUNDARY')
 need(stamp(m['as_of'])<=datetime.now(timezone.utc),'FUTURE_MANIFEST_CLOCK')
 admitted={str(path_for(root,r).resolve()):r['sha256']};refs=[r]
 if operation==SOURCE:
  need(m['purpose'] in ('HISTORICAL_PROJECTION','COVERAGE_RECONCILIATION'),'SOURCE_PURPOSE')
  need(m['catalogue']==c['catalogue'],'CATALOGUE_BINDING')
  active=active_registration(root,c)
  need(r==active['historical_manifest' if m['purpose']=='HISTORICAL_PROJECTION' else 'coverage_manifest'],'SOURCE_MANIFEST_NOT_REGISTERED')
 elif operation==DEVELOPMENT:
  campaign=read(root,c['campaign'],True)
  need(m['campaign']==CAMPAIGN and m['family'] in ('hurdle_glm','hist_gradient_boosting','extra_trees') and m['channel'] in ('receiving','rushing'),'FIT_RECIPE')
  for key in ('mechanism','recipe_version','seed','folds'):need(m[key]==campaign[key],'FIXED_RECIPE_DRIFT:'+key)
  need(m['population']==campaign['population']['target'] and m['endpoint']=='MATCHED_SIGNED_YARD_MAE_CRPS','FIXED_POPULATION_ENDPOINT')
  need(m['output']==PRIVATE+'attempts/'+m['attempt_id'] and re.fullmatch('[a-f0-9]{32}',m['attempt_id']),'FIT_OUTPUT_PATH')
  receipt=read(root,m['projection_receipt']);need(receipt['projection']==m['projection'] and receipt['source_manifest'] in [active_registration(root,c)['historical_manifest'],*active_registration(root,c).get('accepted_projection_sources',[])],'PROJECTION_RECEIPT')
  need(receipt['audit']['unknowns_imputed_zero'] is False and receipt['audit']['snap_fields_used'] is False,'PROJECTION_SEMANTICS')
  need(receipt['audit']['fixtures_by_season']=={'2021':272,'2022':271,'2023':272,'2024':272},'HISTORICAL_FIXTURE_WITNESSES')
  for item in (m['projection'],m['projection_receipt']):
   need(item['path'].startswith(PRIVATE),'MODEL_INPUT_SCOPE');checked(root,item);admitted[str(path_for(root,item).resolve())]=item['sha256'];refs.append(item)
 elif operation==FORWARD:
  need(m['purpose'] in ('PREGAME_INFERENCE','VERIFIED_OBSERVATIONS'),'FORWARD_PURPOSE')
  need(m['season']==2026 and 1<=m['week']<=18,'FORWARD_PERIOD')
  need(m['model']['path'].startswith(PRIVATE+'attempts/') and m['model']['path'].endswith('/artifacts/development_2024/FITTED_MODEL.joblib'),'SAVED_MODEL_PATH')
  terminal=read(root,m['model_terminal']);model_path=path_for(root,m['model']);terminal_path=path_for(root,m['model_terminal'])
  need(terminal['status']=='COMPLETED' and terminal.get('decision',{}).get('status')=='COMPLETED' and terminal.get('channel')==m.get('channel') and terminal_path.parent==model_path.parents[2] and terminal_path.name=='TERMINAL.json' and any(x.get('sha256')==m['model']['sha256'] and Path(x['path']).resolve()==model_path.resolve() for x in terminal['artifacts']),'SHADOW_MODEL_NOT_RECOMMENDED')
  fit_manifest=read(root,ref(root,terminal_path.parent/'MANIFEST.json'));need(fit_manifest['campaign']==CAMPAIGN and fit_manifest['campaign_ref']==c['campaign'] and terminal['manifest_sha256']==sha(terminal_path.parent/'MANIFEST.json'),'SHADOW_MODEL_CAMPAIGN')
  forward_refs=_forward_manifest(root,m,c)
  for item in [m['model'],m['model_terminal'],*forward_refs]:
   checked(root,item);admitted[str(path_for(root,item).resolve())]=item['sha256'];refs.append(item)
 return c,cr,m,admitted,refs


def _forward_key(row):
 return tuple(row[k] for k in ('season','week','game_id','gsis','team','channel'))


def forward_directory(model,week,game_id,channel):
 """One immutable own-model/game/channel lease, independent of V3 leases."""
 key=hashlib.sha256(encoded([model['sha256'],2026,week,game_id,channel])).hexdigest()[:32]
 return PRIVATE+'forward/'+key


def _v3_origin(root,chain,week):
 """Authenticate retained producer evidence without reopening its lifecycle."""
 from nfl_model import research_lab_gate as old
 mr=chain['manifest'];need(mr['path'].startswith('data/private/research-lab/executions/') and mr['path'].endswith('/MANIFEST.json'),'V3_ORIGIN_PATH')
 origin=read(root,mr);consumer=read(root,origin['consumer'],True)
 need(origin.get('schema_version')=='puntproof-lab-execution-manifest-v2' and origin.get('purpose')=='FORECAST_CYCLE' and origin.get('operation') in (old.REPLAY,old.FORWARD),'V3_ORIGIN_TYPE')
 need(origin['season']==2026 and origin['week']==week and consumer['operation']==origin['operation'],'V3_ORIGIN_PERIOD')
 need(origin['execution_directory']==str(Path(mr['path']).parent).replace('\\','/'),'V3_ORIGIN_DIRECTORY')
 need(origin['provider_calls']==origin['cost_cents']==0 and origin['protected_access'] is False and origin['production'] is False,'V3_ORIGIN_BOUNDARY')
 policy=read(root,origin['source_policy'],True)
 gamd_policy=read(root,consumer_for(root,FORWARD)[0]['source_policy'],True)
 need(origin['source_policy']==consumer['source_policy']==gamd_policy['coverage']['existing_policy'],'V3_SOURCE_POLICY_NOT_REUSED')
 need(policy.get('protected_access',False) is False,'V3_PROTECTED_SOURCE')
 ready=read(root,chain['readiness'])
 need(chain['readiness']['path']==str(Path(mr['path']).parent/'READINESS.json').replace('\\','/') and ready.get('manifest')==mr and ready.get('status')=='READY' and ready.get('capability')=='FORECAST' and ready.get('operation')==origin['operation'],'V3_ORIGIN_READINESS')
 need(ready['consumer']==origin['consumer'] and ready['implementation']==consumer['implementation'],'V3_ORIGIN_READINESS_BINDING')
 source=read(root,chain['source_receipt'])
 need(chain['source_receipt']==origin['source_receipt'] and source.get('status')=='PASS' and source.get('operation')==old.SOURCE and source.get('season')==2026 and source.get('source_policy')==origin['source_policy'] and source.get('inputs')==origin['input_files'],'V3_SOURCE_RECEIPT')
 need(source.get('identity_conflicts')==source.get('duplicate_keys')==0 and source.get('unknowns_preserved') is True and week in source.get('target_weeks_verified',[]),'V3_SOURCE_IDENTITY')
 events,phase=old._events(root,origin)
 need(any(e['state']=='GLOBAL_EXECUTION_SEAL_VERIFIED' for e in events) and any(e['state']=='ATTEMPT_CONSUMED' for e in events),'V3_ORIGINAL_SEAL_REQUIRED')
 for event in events:need(stamp(event['at'])<=datetime.now(timezone.utc),'V3_FUTURE_EVENT')
 return origin,source,events,[mr,chain['readiness'],chain['source_receipt'],origin['consumer'],origin['source_policy']]


def _forward_input_rows(root,m):
 """Field-project the exact admitted V3 cohort; no snap/context/model columns."""
 chain=m['v3_source'];origin,source,events,refs=_v3_origin(root,chain,m['week'])
 channel=m['channel'];need(channel in ('receiving','rushing') and channel in origin['channels'],'FORWARD_CHANNEL')
 need(chain['fixtures']==origin['channel_inputs'][channel]['fixtures'] and chain['fixtures'] in origin['input_files'],'V3_FIXTURE_INPUT')
 data=chain['data'];need(data in origin['input_files'] and data['path'].endswith('/revised-source-01/DATA.json'),'V3_CANONICAL_INPUT')
 need(data['path']==origin['channel_inputs'][channel]['input_root']['path']+'/revised-source-01/DATA.json' and origin['channel_inputs'][channel]['input_root']['files']['revised-source-01/DATA.json']==data['sha256'],'V3_CANONICAL_CUSTODY')
 need(m['input_files']==[data,chain['fixtures']],'FORWARD_EXACT_INPUTS')
 need(stamp(source['latest_observed_at'])<=stamp(origin['cutoff'])==stamp(m['source_cutoff'])<=stamp(m['as_of']),'FORWARD_SOURCE_CLOCK')
 game=m['game_id'];fixtures=[f for f in origin['fixtures'] if f['game_id']==game]
 need(len(fixtures)==1 and fixtures[0]['season']==2026 and fixtures[0]['week']==m['week'],'FORWARD_GAME')
 kickoff=fixtures[0]['kickoff'];need(stamp(m['as_of'])<stamp(kickoff) and datetime.now(timezone.utc)<stamp(kickoff),'PREGAME_CLOCK')
 target=[];seen=set()
 for row in read(root,chain['fixtures']):
  if row['game_id']!=game:continue
  need(row['season']==2026 and row['week']==m['week'] and row['channel']==channel,'V3_TARGET_PERIOD')
  key=_forward_key(row);need(key not in seen and re.fullmatch(r'00-\d{7}',row['gsis']) is not None,'V3_TARGET_IDENTITY');seen.add(key)
  target.append({**{k:row[k] for k in ('season','week','game_id','gsis','team','opponent','position','channel')},'appeared':None,'opportunity':None,'yards':None,'participation_state':'PREGAME_UNKNOWN','source_observed_at':source['latest_observed_at'],'provenance':[chain['source_receipt'],chain['fixtures']],'kickoff_at':kickoff})
 need(target and m['targets']==target,'FORWARD_COMPLETE_ADMITTED_COHORT')
 need(m['output']==forward_directory(m['model'],m['week'],game,channel),'FORWARD_OUTPUT_IDENTITY')
 need(m.get('population_scope')=='EXISTING_V3_ADMITTED_COHORT_ONLY','FORWARD_POPULATION_SCOPE')
 history=[];identities=set();positions={'receiving':('WR','TE','RB','FB'),'rushing':('RB','FB')}
 for row in read(root,data)['records']:
  need(row['season']==2026 and row['week']<m['week'] and row.get('identity_state')=='EXACT_DATED_ROSTER_GSIS','FORWARD_HISTORY_PERIOD_OR_IDENTITY')
  need(all(row.get(k) is None for k in ('offense_snaps','snap_share','snap_denominator')),'FORWARD_SNAP_FIELDS_DENIED')
  if row['position'] not in positions[channel]:continue
  need(stamp(row['observed_at'])<=stamp(m['source_cutoff']) and row.get('source_lineage'),'FORWARD_HISTORY_CLOCK')
  r={**{k:row[k] for k in ('season','week','game_id','gsis','team','opponent','position')},'channel':channel,'appeared':row['appeared'],'opportunity':row['targets' if channel=='receiving' else 'carries'],'yards':row['receiving_yards' if channel=='receiving' else 'rushing_yards'],'participation_state':row['state'],'source_observed_at':row['observed_at'],'provenance':row['source_lineage']}
  if r['appeared'] is not True:need(r['opportunity'] is None and r['yards'] is None,'FORWARD_UNKNOWN_NOT_ZERO')
  key=_forward_key(r);need(key not in identities,'FORWARD_HISTORY_DUPLICATE');identities.add(key);history.append(r)
 return history,target,[*refs,data,chain['fixtures']]


def _own_forward_seal(root,m):
 original=read(root,m['forecast_manifest']);seal=read(root,m['forecast_seal']);forecasts=read(root,m['forecast_rows'])
 need(original.get('purpose')=='PREGAME_INFERENCE' and original['spec_hash']==spec_hash(original) and original['model']==m['model'] and original['model_terminal']==m['model_terminal'],'OWN_FORECAST_BINDING')
 need(original['season']==2026 and original['week']==m['week'] and original['channel']==m['channel'] and original['game_id']==m['game_id'],'OWN_FORECAST_PERIOD')
 consumer=read(root,original['consumer'],True);need(consumer['operation']==FORWARD and original['campaign_ref']==consumer['campaign'],'OWN_FORECAST_CONSUMER')
 out=forward_directory(m['model'],m['week'],m['game_id'],m['channel'])
 need(original['output']==out and m['forecast_manifest']['path']==out+'/MANIFEST.json' and m['forecast_seal']['path']==out+'/SEAL.json' and m['forecast_rows']['path']==out+'/FORECASTS.json','OWN_FORECAST_PATH')
 need(not (Path(root)/out/'LATE.json').exists(),'OWN_FORECAST_LATE')
 need(seal.get('status')=='SEALED_PREGAME' and seal['manifest']==m['forecast_manifest'] and seal['forecasts']==m['forecast_rows'] and seal['model']==m['model'],'OWN_FORECAST_SEAL')
 claim=read(root,seal['claim']);need(claim['manifest']==m['forecast_manifest'] and claim['model']==m['model'] and seal['claim']['path']==out+'/CLAIM.json','OWN_FORECAST_CLAIM')
 need(stamp(claim['at'])<=stamp(seal['sealed_at'])<=stamp(m['as_of']),'OWN_FORECAST_CLAIM_CLOCK')
 targets={_forward_key(row):row for row in original['targets']};rows=forecasts['rows']
 need(rows and len(rows)==len(targets) and len({_forward_key(row) for row in rows})==len(rows),'OWN_FORECAST_POPULATION')
 for row in rows:
  key=_forward_key(row);need(key in targets and row['model_sha256']==m['model']['sha256'],'OWN_FORECAST_IDENTITY')
  need(row['sealed_at']==seal['sealed_at'] and row['source_cutoff']==original['source_cutoff'] and stamp(row['source_cutoff'])<=stamp(row['sealed_at'])<stamp(row['kickoff_at'])==stamp(targets[key]['kickoff_at']),'OWN_FORECAST_PREGAME_CLOCK')
  need(row.get('classification')=='GENUINE_PROSPECTIVE' and row.get('forecast_ref') is None and row.get('seal_ref') is None,'OWN_FORECAST_STORAGE_SCHEMA')
 return original,seal,rows,[m['forecast_manifest'],m['forecast_seal'],m['forecast_rows'],seal['claim'],original['consumer']]


def _forward_outcome_rows(root,m):
 from nfl_model import research_lab_gate as old
 original,seal,forecasts,refs=_own_forward_seal(root,m)
 chain=m['v3_source'];origin,source,events,vrefs=_v3_origin(root,chain,m['week']);refs.extend(vrefs)
 need(chain==original['v3_source'],'OUTCOME_ORIGINAL_SOURCE_CHANGED')
 response=next((e for e in events if e['state']=='RESPONSE_ACCESS_ENABLED'),None)
 completed=next((e for e in events if e['state']=='RESPONSES_COMPLETE'),None)
 need(response is not None and completed is not None,'WAITING_V3_VERIFIED_RESPONSES')
 directory=old.lifecycle_directory(Path(root),origin)
 need(m['v3_response_event']==ref(root,directory/(str(response['sequence']).zfill(3)+'.json')) and m['completion']==response['completion'],'OUTCOME_RESPONSE_EVENT_BINDING')
 completion=read(root,m['completion']);proof=read(root,m['source_proof'])
 need(completion.get('status')=='VERIFIED_COMPLETE' and completion.get('all_fixtures_complete') is True and completion.get('full_fixture_census_verified') is True and completion['season']==2026 and completion['week']==m['week'],'OUTCOME_COMPLETION_SCOPE')
 channel=m['channel'];need(m['channel_outcomes']==completion['outcomes'][channel]==response['outcomes'][channel] and m['channel_outcomes'] in completed['evidence'],'OUTCOME_CHANNEL_BINDING')
 need(proof.get('status')=='VERIFIED_COMPLETE' and proof.get('full_fixture_census_verified') is True and proof.get('provider_calls')==0,'OUTCOME_SOURCE_PROOF')
 need(all(f.get('source_ref')==m['source_proof'] for f in completion['fixtures']),'OUTCOME_PROOF_BINDING')
 sm=read(root,m['source_manifest']);sr=read(root,m['source_readiness']);sc=read(root,sm['consumer'],True)
 need(sm['operation']==old.SOURCE and sm['purpose']=='FEATURE_PREPARATION' and sm['season']==2026 and sm['week']==m['week'] and sm['input_files']==proof['source_inputs'] and sm['source_policy']==origin['source_policy']==sc['source_policy'],'OUTCOME_PRODUCER_SOURCE')
 need(sr['manifest']==m['source_manifest'] and sr['status']=='READY' and sr['capability']=='PREPARE_FORECAST' and sr['operation']==old.SOURCE and sr['consumer']==sm['consumer'] and sr['implementation']==sc['implementation'],'OUTCOME_PRODUCER_READINESS')
 need(sm['provider_calls']==sm['cost_cents']==0 and sm['protected_access'] is False and sm['production'] is False,'OUTCOME_PRODUCER_BOUNDARY')
 need(m['source_manifest']['path'].startswith('data/private/research-lab/executions/prepare_forecast-') and m['source_manifest']['path'].endswith('/MANIFEST.json') and m['source_readiness']['path']==str(Path(m['source_manifest']['path']).parent/'READINESS.json').replace('\\','/'),'OUTCOME_PRODUCER_PATH')
 need(all(r['path'].startswith('data/private/research-lab/sources/') for r in proof['source_inputs']),'OUTCOME_PRODUCER_INPUT_PATH')
 need(stamp(sm['as_of'])<=stamp(proof['source_observed_at'])<=stamp(m['as_of']) and stamp(completed['at'])<=stamp(m['as_of']),'OUTCOME_OBSERVATION_CLOCK')
 rows=read(root,m['channel_outcomes'])['rows'];need(rows==proof['outcomes'][channel],'OUTCOME_PROOF_ROWS')
 fixtures={f['game_id']:f for f in completion['fixtures']};origin_fixtures={f['game_id']:f for f in origin['fixtures']}
 need(len(fixtures)==len(completion['fixtures']) and set(fixtures)==set(origin_fixtures),'OUTCOME_FULL_FIXTURE_CENSUS')
 for game,f in fixtures.items():
  need(f['final'] is True and stamp(f['kickoff'])==stamp(origin_fixtures[game]['kickoff'])<stamp(f['completed_at'])<=stamp(proof['source_observed_at']),'OUTCOME_FINAL_CLOCK')
 forecast_keys={_forward_key(f) for f in forecasts};result=[];seen=set()
 for row in rows:
  key=_forward_key(row);need(key not in seen and key[:2]==(2026,m['week']) and row['channel']==channel,'OUTCOME_IDENTITY_OR_DUPLICATE');seen.add(key)
  if key not in forecast_keys:continue
  f=fixtures[row['game_id']];need(row['status'] in ('APPEARED','UNKNOWN'),'OUTCOME_STATE')
  if row['status']=='UNKNOWN':need(row['yards'] is None and row['opportunities'] is None,'OUTCOME_UNKNOWN_NOT_ZERO')
  result.append({**row,'outcome_ref':m['channel_outcomes'],'source_ref':m['source_proof'],'completion_ref':m['completion'],'source_verification':{'operation':old.SOURCE,'consumer':sm['consumer'],'manifest':m['source_manifest'],'readiness':m['source_readiness']},'verified':True,'identity_state':'EXACT_DATED_ROSTER_GSIS','final':True,'kickoff_at':f['kickoff'],'completed_at':f['completed_at'],'observed_at':proof['source_observed_at']})
 refs.extend([m[k] for k in ('v3_response_event','completion','source_proof','channel_outcomes','source_manifest','source_readiness')]);refs.extend([sm['consumer'],*proof['source_inputs']])
 need(m['input_files']==[m['forecast_rows'],m['channel_outcomes']],'OBSERVATION_EXACT_INPUTS')
 # Original V3 final receipts are immutable. A changed outcome source is not an
 # implicit correction grant: it needs an explicit independently verified chain.
 need(not m.get('correction'),'UNREGISTERED_OUTCOME_CORRECTION')
 return [{**row,'forecast_ref':m['forecast_rows'],'seal_ref':m['forecast_seal']} for row in forecasts],result,refs


def _forward_manifest(root,m,consumer):
 need(m.get('channel') in ('receiving','rushing') and m.get('game_id') and m.get('fit') is False,'FORWARD_NO_FIT')
 if m['purpose']=='PREGAME_INFERENCE':return _forward_input_rows(root,m)[2]
 return _forward_outcome_rows(root,m)[2]


def verified_forward_inputs(root,manifest):
 require_active_execution(root,FORWARD)
 r=manifest if isinstance(manifest,dict) else ref(root,manifest)
 need(_ACTIVE.get()['manifest']==r,'FORWARD_ACTIVE_MANIFEST')
 m=read(root,r);need(m['purpose']=='PREGAME_INFERENCE','FORWARD_INPUT_PURPOSE')
 history,targets,_=_forward_input_rows(root,m);return history,targets


def verified_forward_outcomes(root,manifest):
 require_active_execution(root,FORWARD)
 r=manifest if isinstance(manifest,dict) else ref(root,manifest)
 need(_ACTIVE.get()['manifest']==r,'FORWARD_ACTIVE_MANIFEST')
 m=read(root,r);need(m['purpose']=='VERIFIED_OBSERVATIONS','FORWARD_OUTCOME_PURPOSE')
 forecasts,outcomes,_=_forward_outcome_rows(root,m);return forecasts,outcomes


def claim_forward(root,manifest):
 require_active_execution(root,FORWARD)
 r=manifest if isinstance(manifest,dict) else ref(root,manifest);need(_ACTIVE.get()['manifest']==r,'FORWARD_ACTIVE_MANIFEST')
 m=read(root,r);need(m['purpose']=='PREGAME_INFERENCE','FORECAST_CLAIM_PURPOSE')
 _forward_input_rows(root,m)
 p=Path(root)/m['output']/'CLAIM.json';need(not p.exists(),'FORWARD_ALREADY_CLAIMED_NO_RETRY')
 body={'manifest':r,'model':m['model'],'targets':[_forward_key(x) for x in m['targets']],'at':now(),'automatic_retry':False}
 p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('xb') as f:f.write(encoded(body));f.flush();os.fsync(f.fileno())
 return ref(root,p)


def seal_forward(root,manifest,rows):
 require_active_execution(root,FORWARD)
 r=manifest if isinstance(manifest,dict) else ref(root,manifest);need(_ACTIVE.get()['manifest']==r,'FORWARD_ACTIVE_MANIFEST')
 m=read(root,r);need(m['purpose']=='PREGAME_INFERENCE','FORECAST_SEAL_PURPOSE')
 _,targets,_=_forward_input_rows(root,m);out=Path(root)/m['output'];claim=ref(root,out/'CLAIM.json')
 need(read(root,claim)['manifest']==r and len(rows)==len(targets) and {_forward_key(x) for x in rows}=={_forward_key(x) for x in targets},'FORECAST_SEAL_COHORT')
 sealed_at=now();kickoff=min(stamp(x['kickoff_at']) for x in targets);need(stamp(sealed_at)<kickoff,'FORECAST_SEAL_TOO_LATE')
 saved=[{**row,'sealed_at':sealed_at,'source_cutoff':m['source_cutoff'],'classification':'GENUINE_PROSPECTIVE'} for row in rows]
 need(all(x['model_sha256']==m['model']['sha256'] and x.get('forecast_ref') is None and x.get('seal_ref') is None for x in saved),'FORECAST_SEAL_MODEL')
 for row in saved:
  from gamd.observations import _forecast
  _forecast({**row,'forecast_ref':r,'seal_ref':r},stamp(sealed_at))
 forecast_ref=ref(root,write_once(out/'FORECASTS.json',{'rows':saved}))
 seal={'status':'SEALED_PREGAME','manifest':r,'model':m['model'],'forecasts':forecast_ref,'claim':claim,'sealed_at':sealed_at,'provider_calls':0,'fits':0,'classification':'GENUINE_PROSPECTIVE','population_scope':'EXISTING_V3_ADMITTED_COHORT_ONLY'}
 seal_ref=ref(root,write_once(out/'SEAL.json',seal))
 if datetime.now(timezone.utc)>=kickoff:
  write_once(out/'LATE.json',{'status':'LATE_NONISSUED','finished_at':now(),'seal':seal_ref});raise ValueError('GAMD:FORECAST_COMPLETED_AFTER_KICKOFF_NONISSUED')
 return {'seal':seal_ref,'forecasts':forecast_ref,'rows':saved,'sealed_at':sealed_at}

def _readiness(root,operation,r,m,c,cr):
 ready=read(root,m['readiness']);need(ready['status']=='READY' and ready['operation']==operation and ready['consumer']==cr and ready['manifest_spec_sha256']==m['spec_hash'] and ready['implementation']==c['implementation'],'ACTION_NOT_READY')
 witness=read(root,ready['target_witness']);expected=[2021,2022,2023,2024] if operation==DEVELOPMENT or m.get('purpose')=='HISTORICAL_PROJECTION' else [2026]
 need(witness['status']=='PASS' and witness['seasons']==expected and witness['provider_calls']==0 and witness.get('records',0)>0,'TARGET_SEASON_WITNESS')
 if operation==DEVELOPMENT:need(witness.get('projection')==m['projection'] and witness.get('projection_receipt')==m['projection_receipt'],'FIT_WITNESS_INPUTS')
 if operation==SOURCE:need(witness.get('catalogue')==c['catalogue'],'SOURCE_WITNESS_INPUTS')
 need(ready['adapter_canary']==c['adapter_canary'],'READINESS_ADAPTER_BINDING')
 return ready

def _pretask(root,operation,r,m,c):
 path=Path(root)/'tools/codex_task_guard.py';spec=importlib.util.spec_from_file_location('gamd_runtime_guard',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 d={'task_id':operation,'task_class':'SOURCE_ADMISSION' if operation==SOURCE else 'MODEL_EXPERIMENT','allowed_actions':['READ_METADATA']+(['MODEL_EXECUTION'] if operation!=SOURCE else []),'completion_states':['GAMD_OPERATION_COMPLETE'],'external_access':'NONE','paid_access':False,'protected_result_access':False,'pretask_satisfiability':m['pretask_satisfiability'],'owner_authority':{'path':str(Path(root)/c['owner_approval']['path']),'sha256':c['owner_approval']['sha256']}}
 if operation!=SOURCE:d['model_execution']={'operation':operation,'zone':'DEVELOPMENT' if operation==DEVELOPMENT else 'PROSPECTIVE','seasons':[2021,2022,2023,2024] if operation==DEVELOPMENT else [2026],'inputs':{FAMILY:[r]}}
 return module.satisfiability(Path(root),d,execution=True)

def scheduled_execution(root,operation):
 if (Path(root)/'TASK_CHARTER.json').exists():return None
 a=_ACTIVE.get()
 if not a or a['root']!=Path(root).resolve() or a['operation']!=operation:return None
 c,cr,m,_,_=_manifest(root,operation,a['manifest'])
 _readiness(root,operation,a['manifest'],m,c,cr);_pretask(root,operation,a['manifest'],m,c)
 if operation==DEVELOPMENT:
  need(a.get('execution_consumed') is True and _attempt(root,m)['status']=='CLAIMED','CHILD_EXECUTION_NOT_CLAIMED')
  out=Path(root)/m['output'];execution=json.loads((out/'EXECUTION.json').read_bytes());claim=read(root,execution['claim'])
  need(execution['manifest']==a['manifest'] and execution['child_pid']==os.getpid() and claim['manifest']==a['manifest'] and claim['parent_pid']==os.getppid(),'CHILD_EXECUTION_CUSTODY')
 return {'status':'PASS','operation':operation,'phase':'EXACT_BOUNDED_GAMD_RUNTIME','consumer':cr,'provider_calls':0}

def require_active_execution(root,operation):
 a=_ACTIVE.get();need(a and a['root']==Path(root).resolve() and a['operation']==operation,'ACTIVE_EXECUTION_REQUIRED')
 c,cr,m,_,_=_manifest(root,operation,a['manifest']);_readiness(root,operation,a['manifest'],m,c,cr)
 if operation==DEVELOPMENT:
  need(a.get('execution_consumed') is True and _attempt(root,m)['status']=='CLAIMED','CHILD_EXECUTION_NOT_CLAIMED')
  out=Path(root)/m['output'];execution=json.loads((out/'EXECUTION.json').read_bytes());claim=read(root,execution['claim'])
  need(execution['manifest']==a['manifest'] and execution['child_pid']==os.getpid() and claim['parent_pid']==os.getppid() and claim['manifest']==a['manifest'],'CHILD_EXECUTION_CUSTODY')


def require_active_source(root):
 a=_ACTIVE.get();need(a and a['root']==Path(root).resolve() and a['operation']==SOURCE,'ACTIVE_SOURCE_REQUIRED')

def validate_manifest(root,operation,authority,inputs):
 from nfl_model import stage
 stage.require_model_operation(operation,authority)
 need(operation in (DEVELOPMENT,FORWARD) and set(inputs)=={FAMILY} and len(inputs[FAMILY])==1,'ONE_MODEL_MANIFEST')
 c,cr,m,admitted,refs=_manifest(root,operation,inputs[FAMILY][0])
 return admitted,[cr,*c['foundations'].values(),c['source_policy'],c['campaign'],c['catalogue'],*c['implementation'],*refs]

@contextmanager
def execution_authority(root,operation,manifest,*,execution_consumed=False):
 root=Path(root).resolve();r=manifest if isinstance(manifest,dict) else ref(root,manifest)
 c,cr,m,_,_=_manifest(root,operation,r);_readiness(root,operation,r,m,c,cr)
 if (root/'TASK_CHARTER.json').exists():
  live=json.loads((root/'TASK_CHARTER.json').read_bytes());need(live.get('stage_authority',{}).get('operations')==[operation],'LIVE_TASK_PRECEDENCE')
  if operation!=SOURCE:need(live.get('model_execution',{}).get('inputs')=={FAMILY:[r]},'LIVE_CHARTER_INPUTS_DIFFER')
 token=_ACTIVE.set({'root':root,'operation':operation,'manifest':r,'execution_consumed':execution_consumed})
 try:
  from nfl_model import stage
  if operation==SOURCE:stage.require_stage(operation);yield m
  else:
   from nfl_model.data.semantic_certificate import model_inputs
   with model_inputs(operation,inputs={FAMILY:[r]}):yield m
 finally:_ACTIVE.reset(token)

@contextmanager
def source_authority(root,manifest):
 with execution_authority(root,SOURCE,manifest):yield require_source(root,manifest)

def require_source(root,manifest):
 require_active_source(root);r=manifest if isinstance(manifest,dict) else ref(root,manifest)
 need(_ACTIVE.get()['manifest']==r,'ACTIVE_SOURCE_MANIFEST_MISMATCH')
 c,cr,m,_,_=_manifest(root,SOURCE,r);catalogue=read(root,c['catalogue'],True)
 entries=catalogue['historical_inputs'] if m['purpose']=='HISTORICAL_PROJECTION' else catalogue['coverage_inputs']+catalogue['identity_inputs']
 if m['purpose']=='HISTORICAL_PROJECTION':need(len(entries)==16 and {(x['season'],x['dataset']) for x in entries}=={(y,d) for y in (2021,2022,2023,2024) for d in ('player_stats','pbp','rosters','schedule')},'EXACT_HISTORICAL_PARTITIONS')
 else:need(len(entries)==4 and all(x['season']==2026 for x in entries),'EXACT_COVERAGE_PARTITIONS')
 for entry in entries:
  need(entry['dataset'] in ('player_stats','pbp','rosters','schedule') and entry['season']!=2025 and entry['observed_at'],'SOURCE_SCOPE')
  source_root=Path(entry['source_root']).resolve();need(source_root in (Path('C:/Dev/nfl-model').resolve(),Path('C:/Dev/nfl-model/data/private/puntproof/data-manager').resolve()),'SOURCE_ROOT')
  path=(source_root/entry['path']).resolve();need(path.is_relative_to(source_root) and not path.is_symlink(),'SOURCE_PATH')
  need(sha(path)==entry['sha256'],'SOURCE_BYTES_CHANGED:'+entry['id'])
  need(stamp(entry['observed_at'])<=stamp(m['as_of']),'SOURCE_CLOCK')
  if entry.get('producer_metadata'):
   checked(source_root,entry['producer_metadata'])
  if entry.get('producer_row_witness'):
   dbpath=Path(entry['producer_database']);need(dbpath.resolve()==Path('C:/Dev/nfl-model/data/private/puntproof/data-manager/acquisitions.sqlite3').resolve(),'PRODUCER_DATABASE')
   with closing(sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True)) as db:
    db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM snapshots WHERE id=?',(entry['producer_snapshot_id'],)).fetchone()
   need(row and all(dict(row).get(k)==v for k,v in entry['producer_row_witness'].items()),'PRODUCER_ROW_CHANGED')
 return {'source_root':catalogue['source_root'],'years':[2021,2022,2023,2024] if m['purpose']=='HISTORICAL_PROJECTION' else [2026],'inputs':entries,'manifest':m,'consumer':cr,'catalogue':c['catalogue']}

def _attempt(root,m):
 dbpath=Path(root)/'data/private/research-lab/lab.sqlite3'
 # The registered app Store path is explicit; never create a DB during a gate read.
 need(dbpath.exists(),'APP_DATABASE_MISSING')
 with closing(sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True)) as db:
  db.row_factory=sqlite3.Row;row=db.execute('SELECT * FROM gamd_attempts WHERE id=?',(m['attempt_id'],)).fetchone()
 need(row is not None,'UNREGISTERED_ATTEMPT');row=dict(row);plan=json.loads(row['plan'])
 keys=('mechanism','population','endpoint','data_sha256','family','channel','recipe_version','folds','seed')
 identity={k:plan[k] for k in keys};key=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
 need(row['scientific_key']==key and row['id']==key[:32] and all(m.get(k)==v for k,v in plan.items()),'ATTEMPT_PLAN_MANIFEST_MISMATCH')
 need(plan['campaign']==CAMPAIGN and plan['data_sha256']==m['projection']['sha256'],'ATTEMPT_CAMPAIGN_DATA')
 with closing(sqlite3.connect(dbpath.as_uri()+'?mode=ro',uri=True)) as db:
  duplicates=db.execute("SELECT count(*) FROM gamd_attempts WHERE id<>? AND status<>'PLANNED' AND json_extract(plan,'$.campaign')=? AND json_extract(plan,'$.family')=? AND json_extract(plan,'$.channel')=?",(m['attempt_id'],CAMPAIGN,m['family'],m['channel'])).fetchone()[0]
 need(duplicates==0,'RECIPE_ALREADY_CONSUMED')
 return row

def require_fit(root,manifest):
 r=ref(root,manifest);c,cr,m,_,_=_manifest(root,DEVELOPMENT,r);_readiness(root,DEVELOPMENT,r,m,c,cr);_pretask(root,DEVELOPMENT,r,m,c)
 attempt=_attempt(root,m);need(attempt['status']=='PLANNED','ATTEMPT_ALREADY_CONSUMED')
 need(json.loads(attempt['plan'])['data_sha256']==m['projection']['sha256'],'ATTEMPT_DATA_BINDING')
 return m

def claim_fit(root,manifest):
 r=ref(root,manifest);c,cr,m,_,_=_manifest(root,DEVELOPMENT,r)
 need(_attempt(root,m)['status']=='CLAIMED','REGISTRY_CLAIM_REQUIRED')
 out=Path(root)/m['output'];need(not (out/'CLAIM.json').exists() and not (out/'EXECUTION.json').exists(),'FIT_CLAIM_ALREADY_EXISTS')
 token=secrets.token_hex(32);write_once(out/'CLAIM.json',{'manifest':r,'attempt':m['attempt_id'],'parent_pid':os.getpid(),'token_sha256':hashlib.sha256(token.encode()).hexdigest(),'at':now()})
 return token

@contextmanager
def fitting_authority(root,manifest,token):
 r=ref(root,manifest);c,cr,m,_,_=_manifest(root,DEVELOPMENT,r);out=Path(root)/m['output'];claim=json.loads((out/'CLAIM.json').read_bytes())
 need(claim['manifest']==r and claim['attempt']==m['attempt_id'] and claim['parent_pid']==os.getppid() and claim['token_sha256']==hashlib.sha256(token.encode()).hexdigest(),'FIT_PARENT_CLAIM')
 need(_attempt(root,m)['status']=='CLAIMED' and not (out/'EXECUTION.json').exists(),'FIT_EXECUTION_ALREADY_CONSUMED')
 write_once(out/'EXECUTION.json',{'manifest':r,'claim':ref(root,out/'CLAIM.json'),'child_pid':os.getpid(),'at':now()})
 with execution_authority(root,DEVELOPMENT,r,execution_consumed=True):yield m

def require_forward(root,manifest):
 r=ref(root,manifest);c,cr,m,_,_=_manifest(root,FORWARD,r);_readiness(root,FORWARD,r,m,c,cr);_pretask(root,FORWARD,r,m,c)
 return m

def check_lifecycle(root,c):
 operation=c.get('model_execution',{}).get('operation')
 if operation not in OPS:return None
 need(c['stage_authority']['operations']==[operation] and c['external_access']=='NONE' and not any(c.get(k,False) for k in ('protected_result_access','paid_access','stage9_authorised','push_authorised')),'CHARTER_LIFECYCLE_SCOPE')
 r=c['model_execution']['inputs'][FAMILY][0];consumer,cr,m,_,_=_manifest(root,operation,r)
 plan=read(root,c['preexecution_lifecycle']);need(plan['manifest']==r and plan['operation']==operation and plan['attempts']==1 and plan['retry_after_consumption'] is False,'LIFECYCLE_BINDING')
 _readiness(root,operation,r,m,consumer,cr)
 return {'status':'PASS','operation':operation,'phase':'PRECLAIM_EXPOSED_DEVELOPMENT' if operation==DEVELOPMENT else 'PREGAME_SHADOW'}
