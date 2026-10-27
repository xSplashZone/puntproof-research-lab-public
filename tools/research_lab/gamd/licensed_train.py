"""Fixed licensed snap ablation using the existing GAMD estimators and scoring law."""
from collections import defaultdict
from pathlib import Path
import io,json,statistics,time
from . import trainer as t
from .features import build_examples,identity,mean,period
SNAP_FEATURES=['prior_snap_games','prior_snap_share_mean3','prior_snap_share_mean5','prior_offense_snaps_mean5','prior_last_snap_share','prior_snap_missing_fraction']
def build(rows,channel,*,include_snaps):
 rows=list(rows);built=build_examples(rows,channel,apply_mask=False,allowed_seasons=(2023,2024));history=defaultdict(list)
 for r in rows:
  if r['channel']==channel:history[(r['gsis'],r['team'])].append(r)
 for r in built['examples']:
  prior=sorted((x for x in history[r['gsis'],r['team']] if period(x)<period(r)),key=period)[-24:]
  known=[x for x in prior if x.get('snap_share') is not None]
  features=[len(known),mean(x['snap_share'] for x in known[-3:]),mean(x['snap_share'] for x in known[-5:]),mean(x['offense_snaps'] for x in known[-5:]),known[-1]['snap_share'] if known else None,1-len(known)/len(prior) if prior else None]
  r['snap_history_keys']=[list(identity(x)) for x in known]
  if include_snaps:r['x']+=features
 if include_snaps:built['feature_names']+=SNAP_FEATURES
 built['audit'].update(snap_fields_used=SNAP_FEATURES if include_snaps else [],artificial_mask=False,same_population_both_arms=True)
 return built

def split(examples):
 return ([r for r in examples if r['season']==2023],[r for r in examples if r['season']==2024 and r['week']<=4],[r for r in examples if r['season']==2024 and r['week']>=5])

def run(rows,m,out):
 import numpy as np,joblib,sklearn
 started=time.monotonic();cpu=time.process_time();out=Path(out);out.mkdir(parents=True,exist_ok=False)
 built=build(rows,m['channel'],include_snaps=m['include_snaps']);examples=built['examples'];train,cal,evaluation=split(examples)
 if min(map(len,(train,cal,evaluation)))<100:raise ValueError('LICENSED_FIT_SPLIT_TOO_SMALL')
 refs=[t.write_json_once(out/'PLAN.json',m),t.write_bytes_once(out/'FEATURES.jsonl',b''.join(t.encoded(r) for r in examples)),t.write_json_once(out/'SPLIT.json',{'train':[r['identity'] for r in train],'calibration':[r['identity'] for r in cal],'evaluation':[r['identity'] for r in evaluation]})]
 model=t._fit('hist_gradient_boosting',train,np);predcal=t._predict(model,cal,np);lawcal=t._calibrate(model,predcal,cal);pred=t._predict(model,evaluation,np)
 model.update(law_calibration=lawcal,feature_names=built['feature_names'],channel=m['channel'],mechanism=m['mechanism'],recipe_version=m['recipe_version'],fold=m['folds'][0],source_projection_sha256=m['projection']['sha256'],classification='EXPOSED_REVISED_HISTORY_DEVELOPMENT',versions={'numpy':np.__version__,'sklearn':sklearn.__version__,'joblib':joblib.__version__})
 buf=io.BytesIO();joblib.dump(model,buf,compress=3);mr=t.write_bytes_once(out/'FITTED_MODEL.joblib',buf.getvalue());refs.append(mr)
 # Exact saved-artifact roundtrip is verified without fitting again.
 loaded=joblib.load(io.BytesIO(buf.getvalue()));roundtrip=t._predict(loaded,evaluation,np)
 if not all(np.array_equal(pred[k],roundtrip[k]) for k in pred):raise ValueError('ARTIFACT_ROUNDTRIP_FAILED')
 base_res=t._baseline_calibration(cal);draws=t._residual_draws(base_res,64) if base_res else None;center=statistics.fmean(draws) if draws else None
 scores=[];laws=[];source_rows={identity(r):r for r in rows}
 for i,r in enumerate(evaluation):
  law=t._law_for(model,lawcal,pred,i);s=t.score_law(law,r['yards']);s.update({k:r[k] for k in ('season','week','game_id','gsis','team','position','channel','high_usage','changed_role','verified_history_count','unknown_history_count','masked_history_count')})
  s.update(identity=r['identity'],actual=r['yards'],actual_workload=r['workload'],predicted_mean=law['mean'],predicted_median=law['median'],predicted_workload=float(pred['workload'][i]),workload_absolute_error=abs(float(pred['workload'][i])-r['workload']),baseline_score=None,model_sha256=mr['sha256'])
  bl=None
  if r['baseline'] is not None and draws:
   bl=t.empirical_law([r['baseline']+v-center for v in draws]);bs=t.score_law(bl,r['yards']);bs.update(actual=r['yards'],workload_absolute_error=abs(r['baseline_workload']-r['workload']));s['baseline_score']=bs
  controls={}
  for label,item in source_rows[tuple(r['identity'])].get('frozen_controls',{}).items():
   f=item['forecast'];atoms=f['yard_atoms'];support=[a[0] for a in atoms];pmf=[a[1] for a in atoms]
   if abs(sum(pmf)-1)>1e-7 or any(p<0 for p in pmf):raise ValueError('FROZEN_CONTROL_INVALID_LAW')
   control_law={'support':support,'pmf':pmf,'mean':f['mean_yards'],'median':f['median_yards'],'lower80':f['intervals']['80'][0],'upper80':f['intervals']['80'][1]}
   cs=t.score_law(control_law,r['yards']);cs.update(actual=r['yards'],workload_absolute_error=abs(f['mean_opportunities']-r['workload']));controls[label]=cs
  s['frozen_control_scores']=controls
  scores.append(s);laws.append({'identity':r['identity'],'candidate':law,'baseline_prior5':bl,'model_sha256':mr['sha256']})
 refs+=[t.write_json_once(out/'CALIBRATION.json',lawcal),t.write_bytes_once(out/'LAWS.jsonl',b''.join(t.encoded(r) for r in laws)),t.write_bytes_once(out/'SCORES.jsonl',b''.join(t.encoded(r) for r in scores))]
 matched=[r for r in scores if r['baseline_score'] is not None]
 # Existing prior5 comparator remains a diagnostic, never relabelled frozen B/F.
 decision=t.research_decision(scores);decision['interpretation']='PRIOR5_DIAGNOSTIC; SNAP_ABLATION_REPORTED_SEPARATELY; NO_PROMOTION'
 common=[r for r in scores if set(r['frozen_control_scores'])=={'B','F'}]
 frozen={'same_population_n':len(common),'candidate':t.summarize_scores(common),'B':t.summarize_scores([r['frozen_control_scores']['B'] for r in common]),'F':t.summarize_scores([r['frozen_control_scores']['F'] for r in common]),'unmatched_n':len(scores)-len(common),'higher_usage':{'candidate':t.summarize_scores([r for r in common if r['high_usage']]),'B':t.summarize_scores([r['frozen_control_scores']['B'] for r in common if r['high_usage']]),'F':t.summarize_scores([r['frozen_control_scores']['F'] for r in common if r['high_usage']])},'V3':None,'V3_reason':'Supplied V3 saved seed includes later history; no immutable2024 forecast controls. A historical replay would be a new experiment and is not run.'}
 result={'frozen_controls':frozen,'status':decision['status'],'decision':decision,'family':m['family'],'channel':m['channel'],'campaign':m['campaign'],'recipe_id':m['recipe_id'],'include_snaps':m['include_snaps'],'mechanism':m['mechanism'],'classification':'EXPOSED_REVISED_HISTORY_DEVELOPMENT','projection_file_sha256':m['projection']['sha256'],'fitted_models':model['fitted_models'],'completed_folds':1,'folds':[{'id':m['folds'][0]['id'],'counts':dict(zip(('train','calibration','evaluation'),map(len,(train,cal,evaluation)))),'metrics':t.summarize_scores(scores),'model':mr,'fitted_models':model['fitted_models']}],'metrics':t.summarize_scores(scores),'matched_candidate_metrics':t.summarize_scores(matched),'matched_baseline_metrics':t.summarize_scores([r['baseline_score'] for r in matched]),'slices':{'high_usage':t.summarize_scores([r for r in scores if r['high_usage']]),'unknown_history':t.summarize_scores([r for r in scores if r['unknown_history_count']>0])},'feature_audit':built['audit'],'feature_names':built['feature_names'],'artifacts':refs,'artifact_roundtrip':'PASS','genuine_roi':None,'paper_bets':0,'resources':{'wall_seconds':time.monotonic()-started,'cpu_seconds':time.process_time()-cpu,'fit_threads':1},'fixed_b_status':'ORIGINAL_SAVED2024_LAWS_MATCHED_WITHOUT_REPLAY','fixed_f_status':'ORIGINAL_SAVED2024_LAWS_MATCHED_WITHOUT_REPLAY','v3_status':'NO_MATCHED_2024_IMMUTABLE_V3_FORECASTS; NO_REPLAY','limitations':['Revised and previously exposed historical sources; not a blind or prospective evaluation.','Labels conditional on verified offensive appearance. Unknown is not zero.','No real pregame offers; ROI unavailable.','No hyperparameter search or automatic refit; both feature arms retained regardless of result.']}
 rr=t.write_json_once(out/'RESULT.json',result);return {**result,'result_artifact':rr}
