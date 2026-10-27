"""Saved-model future benchmark on an independent roster population."""
from pathlib import Path
import json,io

def predict(root,m,mr):
 from nfl_model import continuous_gamd_gate as g
 from gamd.continuous_features import build_target_features
 from gamd.continuous_train import law_for
 from gamd import trainer as t
 import numpy as np,joblib
 g.checked(root,m['source_receipt']);cohort=g.read(root,m['cohort']);g.need(cohort['history']==m['history'],'COHORT_HISTORY_BINDING')
 targets=[x for x in cohort['targets'] if x['game_id']==m['game_id'] and x['channel']==m['channel']]
 g.need(bool(targets) and all(g.stamp(x['kickoff_at'])>g.stamp(g.now()) for x in targets),'FUTURE_GAME_REQUIRED')
 history=[json.loads(x) for x in g.checked(root,m['history']).splitlines()]
 rows=build_target_features(history,targets,m['as_of'],allowed_history_seasons=(2026,))
 model=joblib.load(io.BytesIO(g.checked(root,m['model'])));pred=t._predict(model,rows,np);forecasts=[]
 for i,(r,target) in enumerate(zip(rows,targets)):
  law=law_for(model['law_calibration'],pred,i,r)
  forecasts.append({'identity':r['identity'],'name':target['name'],'channel':target['channel'],'point_mean':law['mean'],'point_median':law['median'],'expected_opportunities':float(pred['workload'][i]),'law':law,'threshold_probabilities':{str(n):sum(p for y,p in zip(law['support'],law['pmf']) if y>=n) for n in range(-20,301)},'unknown_history_count':r['unknown_history_count'],'verified_history_count':r['verified_history_count'],'availability':'UNKNOWN','kickoff_at':target['kickoff_at'],'roster_age_days':target['roster_observation_age_days'],'source_max_observed_at':r['max_source_observed_at'],'model':m['model'],'probability_status':'EXPERIMENTAL_UNVALIDATED','conditional_on':'VERIFIED_OFFENSIVE_APPEARANCE','offer':None})
 seal_at=g.now();g.need(all(g.stamp(x['kickoff_at'])>g.stamp(seal_at) for x in forecasts),'FINISHED_AFTER_KICKOFF_NO_PROSPECTIVE_SEAL')
 g.write_once(Path(root)/m['output']/'SEAL.json',{'status':'SEALED_PREGAME','sealed_at':seal_at,'manifest':mr,'model':m['model'],'forecasts':forecasts,'exclusions':cohort['exclusions'],'classification':'PROSPECTIVE_EXPERIMENTAL_OBSERVATION','fit':False,'original_forecasts_modified':0,'offers':None})

def run_next(c,receipt,attempts):
 from nfl_model import continuous_gamd_gate as g
 from gamd.continuous_preparation import seal
 from gamd.continuous_runtime import launch
 cohort=g.read(c.root,receipt['cohort']);results=[]
 for channel in ('receiving','rushing'):
  # Fixed comparator choice, never select a winner after observing evaluation scores.
  selected=next((a for a in attempts if a['plan']['template_id']=='individual_context_'+channel and a['result']),None)
  if not selected:continue
  value=selected['result'];outfit=c.root/g.PRIVATE/'attempts'/selected['id'];model=value['model'];modelresult=g.ref(c.root,outfit/'artifacts/RESULT.json')
  for fixture in cohort['fixtures']:
   if fixture['game_id']=='2026_07_DAL_PHI' or g.stamp(fixture['kickoff_at'])<=g.stamp(g.now()):continue
   targets=[x for x in cohort['targets'] if x['game_id']==fixture['game_id'] and x['channel']==channel]
   if not targets:continue
   out=c.root/g.PRIVATE/'forward'/(selected['id']+'-'+fixture['game_id']);sp=out/'SEAL.json'
   if sp.exists():
    saved=g.read(c.root,g.ref(c.root,sp));results.append(saved);continue
   if (out/'CLAIM.json').exists():continue # Immutable consumed inference; no late retry.
   m={'operation':g.FORWARD,'purpose':'PREGAME_BENCHMARK','fit':False,'as_of':g.now(),'output':out.relative_to(c.root).as_posix(),'game_id':fixture['game_id'],'channel':channel,'model':model,'model_result':modelresult,'feature_schema':value['feature_schema'],'cohort':receipt['cohort'],'history':receipt['history'],'source_receipt':receipt['receipt'],'inputs':[model,modelresult,receipt['cohort'],receipt['history']]}
   mr=seal(c.root,out,m,{'status':'PASS','records':len(targets),'seasons':[2026],'provider_calls':0})
   g.manifest(c.root,g.FORWARD,mr);g.pretask(c.root,g.FORWARD,g.read(c.root,mr));launch(c,mr)
   if sp.exists():results.append(g.read(c.root,g.ref(c.root,sp)))
 for result in results:
  key=result['manifest']['sha256']
  if not c.store.latest('continuous_benchmark',key):c.store.append('continuous_benchmark',key,result,immutable=True)
 c.store.set_setting('continuous_forward',{'status':'SEALED_PREGAME' if results else 'WAITING_SAVED_MODEL_OR_FUTURE_COHORT','games':len({x['forecasts'][0]['identity'][2] for x in results if x['forecasts']}),'forecasts':sum(len(x['forecasts']) for x in results),'targets':len(cohort['targets']),'exclusions':len(cohort['exclusions']),'availability':'UNKNOWN','next_action':'Await verified final player outcomes. Experimental probabilities are not promoted; no odds or ROI invented.'})
 return results
