"""Saved challenger forward inference and verified observation bridge.
No source acquisition, V3 engine calls, refitting or retrospective issuance.
Initial coverage is explicitly the already admitted V3 player cohort.
"""
from pathlib import Path
import hashlib
import json
import os
import secrets
from core import now,digest
from gamd.registry import Registry,write_once


def recommended(store):
 from nfl_model.gamd_gate import CAMPAIGN
 return [a for a in Registry(store).attempts() if a['plan'].get('campaign')==CAMPAIGN and a['status']=='COMPLETED' and (a.get('result') or {}).get('decision',{}).get('status')=='COMPLETED']


def status(root,store):
 accepted=recommended(store);seals=store.records('gamd_forecast_seal');latest={}
 for row in sorted(store.records('gamd_observation'),key=lambda r:(r.get('outcome_observed_at',''),r['id'])):latest[row['forecast_id']]=row
 scored=[r for r in latest.values() if r.get('status')=='SCORED_VERIFIED_FINAL']
 runtime=store.get_setting('gamd_forward_runtime',{});metrics=store.get_setting('gamd_forward_metrics',{});prerequisites=[]
 if not accepted:prerequisites.append({'code':'NO_RECOMMENDED_MODEL','affects':'New GAMD prospective forecasts','action':'Only a saved challenger passing its declared gates can enter forward admission; rejected, failed and inconclusive attempts stay preserved.'})
 if not seals:prerequisites.append({'code':'NO_GAMD_PREGAME_SEAL','affects':'GAMD prospective scoring','action':'Wait for a recommended saved challenger and an admitted V3 player cohort for a future game. Past Week 7 records cannot become GAMD prospective evidence.'})
 if seals and not scored:prerequisites.append({'code':'WAITING_VERIFIED_FINAL_OUTCOMES','affects':'GAMD observation scoring','action':'The existing V3 response chain must verify final statistics for each exact sealed identity. Missing players remain unknown.'})
 for issue in runtime.get('blockers',[]):prerequisites.append({'code':issue.get('code','FORWARD_PREREQUISITE'),'affects':issue.get('affects','GAMD forward operation'),'action':issue['reason']})
 return {'recommended_models':len(accepted),'genuine_pregame_seals':len(seals),'forecast_rows':sum(r.get('records',0) for r in seals),'verified_observations':len(scored),'excluded_observations':len(latest)-len(scored),'parameter_updates':0,'automatic_additional_fits':False,'observation_learning_completed':len(scored),'learning_description':'Verified observations update error and calibration summaries; saved model parameters remain frozen.','population_scope':'EXISTING_V3_ADMITTED_COHORT_ONLY','prerequisites':prerequisites,'runtime':runtime,'per_model':metrics.get('per_model',{}),'error_atlas':metrics.get('error_atlas',[]),'export':metrics.get('export'),'genuine_roi':None}


def prepare_inference(root,store,candidate,origin_ref,game_id):
 from nfl_model import gamd_gate as g
 from gamd.preparation import seal_manifest
 c,cr=g.consumer_for(root,g.FORWARD);channel=candidate['plan']['channel']
 attempt=Path(root)/'data/private/research-lab/gamd/attempts'/candidate['id']
 model=g.ref(root,attempt/'artifacts/development_2024/FITTED_MODEL.joblib');terminal=g.ref(root,attempt/'TERMINAL.json')
 origin=g.read(root,origin_ref)
 chain={'manifest':origin_ref,'readiness':g.ref(root,Path(root)/Path(origin_ref['path']).parent/'READINESS.json'),'source_receipt':origin['source_receipt']}
 _,source,_,_=g._v3_origin(root,chain,origin['week'])
 if channel not in origin['channels']:raise ValueError('V3_CHANNEL_NOT_ADMITTED')
 data=[r for r in origin['input_files'] if r['path'].endswith('/revised-source-01/DATA.json')]
 if len(data)!=1:raise ValueError('CANONICAL_DATA_REFERENCE_MISSING')
 chain.update(data=data[0],fixtures=origin['channel_inputs'][channel]['fixtures'])
 fixtures=[f for f in origin['fixtures'] if f['game_id']==game_id]
 if len(fixtures)!=1:raise ValueError('EXACT_FUTURE_GAME_REQUIRED')
 fixture=fixtures[0];stamp=now()
 if g.stamp(stamp)>=g.stamp(fixture['kickoff']):raise ValueError('GAME_ALREADY_STARTED_NO_RETROSPECTIVE_GAMD_ISSUE')
 out=Path(root)/g.forward_directory(model,origin['week'],game_id,channel);mp=out/'MANIFEST.json'
 if mp.exists():return mp
 targets=[]
 # Source metadata has been authenticated before the exact admitted identity file.
 for row in g.read(root,chain['fixtures']):
  if row['game_id']!=game_id:continue
  targets.append({**{k:row[k] for k in ('season','week','game_id','gsis','team','opponent','position','channel')},'appeared':None,'opportunity':None,'yards':None,'participation_state':'PREGAME_UNKNOWN','source_observed_at':source['latest_observed_at'],'provenance':[chain['source_receipt'],chain['fixtures']],'kickoff_at':fixture['kickoff']})
 if not targets:raise ValueError('NO_ADMITTED_PLAYER_IDENTITIES')
 m={'purpose':'PREGAME_INFERENCE','operation':g.FORWARD,'season':2026,'week':origin['week'],'channel':channel,'game_id':game_id,'model':model,'model_terminal':terminal,'v3_source':chain,'source_cutoff':origin['cutoff'],'as_of':stamp,'targets':targets,'input_files':[chain['data'],chain['fixtures']],'output':out.relative_to(root).as_posix(),'fit':False,'population_scope':'EXISTING_V3_ADMITTED_COHORT_ONLY'}
 w={'status':'PASS','seasons':[2026],'provider_calls':0,'records':len(targets),'model':model,'source_manifest':origin_ref,'game_id':game_id,'target_labels_null':True,'source_cutoff':origin['cutoff'],'kickoff':fixture['kickoff']}
 return seal_manifest(root,out,m,c,cr,w)


def execute_child(root,manifest_ref,token):
 from nfl_model import gamd_gate as g
 m=g.read(root,manifest_ref);out=Path(root)/m['output'];parent=g.read(root,g.ref(root,out/'PARENT.json'))
 if parent['manifest']!=manifest_ref or parent['parent_pid']!=os.getppid() or parent['token_sha256']!=hashlib.sha256(token.encode()).hexdigest():raise ValueError('FORWARD_PARENT_CLAIM')
 if g.read(root,parent['claim'])['manifest']!=manifest_ref or (out/'EXECUTION.json').exists():raise ValueError('FORWARD_EXECUTION_ALREADY_CONSUMED')
 with (out/'EXECUTION.json').open('xb') as f:
  f.write(g.encoded({'manifest':manifest_ref,'parent':g.ref(root,out/'PARENT.json'),'child_pid':os.getpid(),'at':now()}));f.flush();os.fsync(f.fileno())
 with g.execution_authority(root,g.FORWARD,manifest_ref):
  history,targets=g.verified_forward_inputs(root,manifest_ref)
  from gamd.features import build_target_features
  from gamd.trainer import predict_artifact
  import joblib
  # Only deserialize the exact own model authenticated by the active gate.
  g.checked(root,m['model']);artifact=joblib.load(g.path_for(root,m['model']))
  features=build_target_features(history,targets,m['source_cutoff'],allowed_history_seasons=(2026,))
  predictions=predict_artifact(artifact,features,model_sha256=m['model']['sha256'])
  for row in predictions:row['id']='gamd-forecast-'+digest([m['model']['sha256'],g._forward_key(row)])[:32]
  g.seal_forward(root,manifest_ref,predictions)


def publish_seal(root,store,manifest,g):
 mr=g.ref(root,manifest);m=g.read(root,mr);out=Path(root)/m['output']
 if (out/'LATE.json').exists():raise ValueError('LATE_GAMD_RESULT_NOT_ISSUED')
 sr=g.ref(root,out/'SEAL.json');seal=g.read(root,sr);fr=seal['forecasts'];data=g.read(root,fr)
 check={'forecast_manifest':mr,'forecast_seal':sr,'forecast_rows':fr,'model':m['model'],'model_terminal':m['model_terminal'],'season':2026,'week':m['week'],'channel':m['channel'],'game_id':m['game_id'],'as_of':now()}
 g._own_forward_seal(root,check)
 body={**check,'as_of':seal['sealed_at'],'records':len(data['rows']),'sealed_at':seal['sealed_at'],'status':'SEALED_PREGAME','population_scope':m['population_scope']}
 store.append('gamd_forecast_seal',sr['sha256'],body,immutable=True)
 for row in data['rows']:store.append('gamd_forecast',row['id'],{**row,'forecast_ref':fr,'seal_ref':sr},immutable=True)
 export=store.root/'exports'/('GAMD-PREGAME-'+sr['sha256'][:16]+'.json')
 write_once(export,{'seal':sr,'manifest':mr,'forecasts':data['rows'],'population_scope':m['population_scope'],'genuine_roi':None})
 return {'status':'SEALED_PREGAME','new_forecasts':len(data['rows']),'seal':sr,'export':str(export)}


def prepare_observation(root,store,sealed):
 from nfl_model import gamd_gate as g,research_lab_gate as old
 from gamd.preparation import seal_manifest
 c,cr=g.consumer_for(root,g.FORWARD);original=g.read(root,sealed['forecast_manifest']);chain=original['v3_source']
 origin,_,events,_=g._v3_origin(root,chain,original['week'])
 response=next((e for e in events if e['state']=='RESPONSE_ACCESS_ENABLED'),None)
 if response is None or not any(e['state']=='RESPONSES_COMPLETE' for e in events):return None
 completion=g.read(root,response['completion']);proofs={digest(f['source_ref']):f['source_ref'] for f in completion['fixtures']}
 if len(proofs)!=1:raise ValueError('EXACT_COMPLETION_SOURCE_PROOF_REQUIRED')
 pr=next(iter(proofs.values()));proof=g.read(root,pr);matches=[]
 for p in (Path(root)/'data/private/research-lab/executions').glob('prepare_forecast-*/MANIFEST.json'):
  mr=g.ref(root,p);source=g.read(root,mr)
  if source.get('operation')==old.SOURCE and source.get('purpose')=='FEATURE_PREPARATION' and source.get('season')==2026 and source.get('week')==original['week'] and source.get('input_files')==proof['source_inputs'] and g.stamp(source['as_of'])<=g.stamp(proof['source_observed_at']):
   matches.append((source['as_of'],mr,g.ref(root,p.parent/'READINESS.json')))
 if not matches:raise ValueError('VERIFIED_COMPLETION_SOURCE_MANIFEST_MISSING')
 _,sm,sr=sorted(matches,key=lambda x:x[0])[0]
 key=digest([sealed['forecast_seal'],response['completion'],cr]);out=store.root/'gamd'/'observations'/key[:32];mp=out/'MANIFEST.json'
 if mp.exists():return mp
 m={k:original[k] for k in ('season','week','channel','game_id','model','model_terminal','v3_source')}
 m.update(purpose='VERIFIED_OBSERVATIONS',operation=g.FORWARD,fit=False,as_of=now(),forecast_manifest=sealed['forecast_manifest'],forecast_seal=sealed['forecast_seal'],forecast_rows=sealed['forecast_rows'],v3_response_event=g.ref(root,old.lifecycle_directory(Path(root),origin)/(str(response['sequence']).zfill(3)+'.json')),completion=response['completion'],source_proof=pr,channel_outcomes=completion['outcomes'][original['channel']],source_manifest=sm,source_readiness=sr,output=out.relative_to(root).as_posix(),population_scope='EXISTING_V3_ADMITTED_COHORT_ONLY')
 m['input_files']=[m['forecast_rows'],m['channel_outcomes']]
 w={'status':'PASS','seasons':[2026],'provider_calls':0,'records':sealed['records'],'forecast_seal':sealed['forecast_seal'],'completion':response['completion'],'existing_v3_response_chain':True}
 return seal_manifest(root,out,m,c,cr,w)


def settle_available(root,store):
 from nfl_model import gamd_gate as g
 from gamd.observations import observe_sealed_forecasts
 total=0;blockers=[]
 for sealed in store.records('gamd_forecast_seal'):
  try:
   mp=prepare_observation(root,store,sealed)
   if mp is None:continue
   g.require_forward(root,mp)
   with g.execution_authority(root,g.FORWARD,mp) as m:
    forecasts,outcomes=g.verified_forward_outcomes(root,mp)
    result=observe_sealed_forecasts(forecasts,outcomes,as_of=m['as_of'],existing_observations=store.records('gamd_observation'))
   for row in result['new_records']:store.append('gamd_observation',row['id'],row,immutable=True)
   if not (mp.parent/'RESULT.json').exists():write_once(mp.parent/'RESULT.json',result)
   if result['quarantined']:blockers.append({'code':'OBSERVATION_QUARANTINE','reason':str(result['quarantined']),'affects':'Exact forecast/outcome joins'})
   total+=result['new_scored_observations']
   report={k:result[k] for k in ('per_model','error_atlas','latest_observation_ids','parameter_updates','new_fits','genuine_roi')}
   export=store.root/'exports'/('GAMD-OBSERVATIONS-'+digest(report)[:16]+'.json')
   write_once(export,report);store.set_setting('gamd_forward_metrics',{**report,'export':str(export)})
  except (ValueError,OSError,KeyError,TypeError,RuntimeError) as exc:blockers.append({'code':'VERIFIED_OUTCOME_PREREQUISITE','reason':str(exc),'affects':'GAMD verified outcome ingestion'})
 return {'new_scored_observations':total,'blockers':blockers}


def run(root,store,*,coordinator):
 from nfl_model import gamd_gate as g
 from gamd.limits import run as bounded
 root=Path(root).resolve();observed=settle_available(root,store);accepted=recommended(store)
 result={'status':'WAITING','new_forecasts':0,**observed,'parameter_updates':0,'new_fits':0,'provider_calls':0,'population_scope':'EXISTING_V3_ADMITTED_COHORT_ONLY'}
 if not accepted:return {**result,'reason':'NO_RECOMMENDED_MODEL'}
 origins={digest(r['manifest']):r['manifest'] for r in store.records('forecast') if r.get('manifest')}
 for candidate in accepted:
  for origin_ref in origins.values():
   origin=g.read(root,origin_ref)
   if origin.get('season')!=2026:continue
   for fixture in origin.get('fixtures',[]):
    if not fixture.get('kickoff') or g.stamp(fixture['kickoff'])<=g.stamp(now()):continue
    try:
     mp=prepare_inference(root,store,candidate,origin_ref,fixture['game_id']);out=mp.parent
     if (out/'SEAL.json').exists():publish_seal(root,store,mp,g);continue
     if (out/'CLAIM.json').exists():
      result['blockers'].append({'code':'FORWARD_ATTEMPT_ALREADY_CLAIMED','reason':str(out),'affects':'One own-model game forecast; no automatic retry'});continue
     g.require_forward(root,mp);argv=coordinator.child_command(mp,g.FORWARD);nonce=secrets.token_hex(32)
     with g.execution_authority(root,g.FORWARD,mp):claim=g.claim_forward(root,mp)
     write_once(out/'PARENT.json',{'manifest':g.ref(root,mp),'claim':claim,'parent_pid':os.getpid(),'token_sha256':hashlib.sha256(nonce.encode()).hexdigest(),'at':now()})
     ran=bounded(argv,root,out/'stdout.log',out/'stderr.log',seconds=300,memory_mb=4096,stop=coordinator.stop_event,handshake=('EXECUTE '+nonce+'\n').encode('ascii'))
     if ran['status']!='COMPLETED':
      write_once(out/'FAILED.json',dict(ran,at=now(),automatic_retry=False));raise ValueError('FORWARD_CHILD_FAILED:'+str(out))
     return {**result,**publish_seal(root,store,mp,g)}
    except (ValueError,OSError,KeyError,TypeError,RuntimeError) as exc:result['blockers'].append({'code':'PREGAME_INPUT_OR_EXECUTION_PREREQUISITE','reason':str(exc),'affects':'New GAMD prospective forecast'})
 return {**result,'reason':'WAITING_FOR_FUTURE_ADMITTED_V3_PLAYER_COHORT'}
