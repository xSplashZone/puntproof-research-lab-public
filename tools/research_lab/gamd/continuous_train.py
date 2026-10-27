"""Fixed licensed snap ablation using the existing GAMD estimators and scoring law."""
from collections import defaultdict
from pathlib import Path
import io,json,statistics,time
from . import trainer as t
from .features import build_examples,identity,mean,period
from .continuous_features import build_examples,identity,SCHEMA

def build(rows,channel,**unused):
 built=build_examples(rows,channel,apply_mask=False,allowed_seasons=(2023,2024))
 histories=defaultdict(list)
 for row in rows:
  if row['channel']==channel:histories[row['gsis'],row['team']].append(row)
 for example in built['examples']:
  prior=sorted((r for r in histories[example['gsis'],example['team']] if period(r)<period(example) and r['appeared'] is True and r['yards'] is not None),key=period)[-24:];ema=None
  for r in prior:ema=r['yards'] if ema is None else .4*r['yards']+.6*ema
  example['baseline_recency']=ema
 return built

def group(row):return str(row['unknown_history_count']>0)+'/'+str(row['high_usage'])
def calibrate(pred,cal):
 groups=defaultdict(list);all_res=[]
 for i,r in enumerate(cal):
  residual=r['yards']-float(pred['point'][i]);groups[group(r)].append(residual);all_res.append(residual)
 return {'method':'EARLIER_FOLD_SIGNED_RESIDUALS_BY_PARTIAL_HISTORY_AND_PRIOR_USAGE','minimum_group_n':40,'pooled':sorted(all_res),'groups':{k:sorted(v) for k,v in groups.items() if len(v)>=40},'group_counts':{k:len(v) for k,v in groups.items()}}
def law_for(cal,pred,i,row):
 values=cal['groups'].get(group(row),cal['pooled'])
 return t.empirical_law([float(pred['point'][i])+v for v in t._residual_draws(values,128)])

def split(examples):
 return ([r for r in examples if r['season']==2023],[r for r in examples if r['season']==2024 and r['week']<=4],[r for r in examples if r['season']==2024 and r['week']>=5])

def run(rows,m,out,phase=None):
 import numpy as np,joblib,sklearn
 started=time.monotonic();cpu=time.process_time();out=Path(out);out.mkdir(parents=True,exist_ok=False)
 built=build(rows,m['channel'],include_snaps=False);examples=built['examples'];train,cal,evaluation=split(examples)
 if min(map(len,(train,cal,evaluation)))<100:raise ValueError('LICENSED_FIT_SPLIT_TOO_SMALL')
 refs=[t.write_json_once(out/'PLAN.json',m),t.write_bytes_once(out/'FEATURES.jsonl',b''.join(t.encoded(r) for r in examples)),t.write_json_once(out/'SPLIT.json',{'train':[r['identity'] for r in train],'calibration':[r['identity'] for r in cal],'evaluation':[r['identity'] for r in evaluation]})]
 if phase:phase('FIT_STARTED')
 model=t._fit(m['family'],train,np)
 if phase:phase('FIT_COMPLETED')
 predcal=t._predict(model,cal,np);lawcal=calibrate(predcal,cal)
 if phase:phase('EVALUATION_OPENED')
 pred=t._predict(model,evaluation,np)
 model.update(law_calibration=lawcal,feature_names=built['feature_names'],channel=m['channel'],mechanism=m['mechanism'],recipe_version=m['feature_schema'],fold=m['folds'][0],source_projection_sha256=m['projection']['sha256'],classification='EXPOSED_REVISED_HISTORY_DEVELOPMENT',versions={'numpy':np.__version__,'sklearn':sklearn.__version__,'joblib':joblib.__version__})
 buf=io.BytesIO();joblib.dump(model,buf,compress=3);mr=t.write_bytes_once(out/'FITTED_MODEL.joblib',buf.getvalue());refs.append(mr)
 # Exact saved-artifact roundtrip is verified without fitting again.
 loaded=joblib.load(io.BytesIO(buf.getvalue()));roundtrip=t._predict(loaded,evaluation,np)
 if not all(np.array_equal(pred[k],roundtrip[k]) for k in pred):raise ValueError('ARTIFACT_ROUNDTRIP_FAILED')
 base_res=t._baseline_calibration(cal);draws=t._residual_draws(base_res,64) if base_res else None;center=statistics.fmean(draws) if draws else None
 recency_res=sorted(r['yards']-r['baseline_recency'] for r in cal if r['baseline_recency'] is not None);recency_scores=[]
 scores=[];laws=[];source_rows={identity(r):r for r in rows}
 for i,r in enumerate(evaluation):
  law=law_for(lawcal,pred,i,r);s=t.score_law(law,r['yards']);s.update({k:r[k] for k in ('season','week','game_id','gsis','team','position','channel','high_usage','changed_role','verified_history_count','unknown_history_count','masked_history_count')})
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
  if r['baseline_recency'] is not None and len(recency_res)>=40:
   recency_law=t.empirical_law([r['baseline_recency']+v for v in t._residual_draws(recency_res,128)]);rs=t.score_law(recency_law,r['yards']);rs.update(actual=r['yards'],identity=r['identity']);recency_scores.append(rs)
  s['frozen_control_scores']=controls
  scores.append(s);laws.append({'identity':r['identity'],'candidate':law,'baseline_prior5':bl,'model_sha256':mr['sha256']})
 refs+=[t.write_json_once(out/'CALIBRATION.json',lawcal),t.write_bytes_once(out/'LAWS.jsonl',b''.join(t.encoded(r) for r in laws)),t.write_bytes_once(out/'SCORES.jsonl',b''.join(t.encoded(r) for r in scores))]
 matched=[r for r in scores if r['baseline_score'] is not None]
 # Existing prior5 comparator remains a diagnostic, never relabelled frozen B/F.
 decision=t.research_decision(scores);decision['interpretation']='PRIOR5_DIAGNOSTIC; SNAP_ABLATION_REPORTED_SEPARATELY; NO_PROMOTION'
 common=[r for r in scores if set(r['frozen_control_scores'])=={'B','F'}]
 frozen={'same_population_n':len(common),'candidate':t.summarize_scores(common),'B':t.summarize_scores([r['frozen_control_scores']['B'] for r in common]),'F':t.summarize_scores([r['frozen_control_scores']['F'] for r in common]),'unmatched_n':len(scores)-len(common),'higher_usage':{'candidate':t.summarize_scores([r for r in common if r['high_usage']]),'B':t.summarize_scores([r['frozen_control_scores']['B'] for r in common if r['high_usage']]),'F':t.summarize_scores([r['frozen_control_scores']['F'] for r in common if r['high_usage']])},'V3':None,'V3_reason':'Supplied V3 saved seed includes later history; no immutable2024 forecast controls. A historical replay would be a new experiment and is not run.'}
 result={'frozen_controls':frozen,'model':{'path':str(Path(m['output'])/'artifacts/FITTED_MODEL.joblib').replace('\\','/'),'sha256':mr['sha256']},'feature_schema':m['feature_schema'],'status':decision['status'],'decision':decision,'family':m['family'],'channel':m['channel'],'campaign':'CONTINUOUS_V1','recipe_id':m['template_id'],'include_snaps':False,'mechanism':m['mechanism'],'classification':'EXPOSED_REVISED_HISTORY_DEVELOPMENT','projection_file_sha256':m['projection']['sha256'],'fitted_models':model['fitted_models'],'completed_folds':1,'folds':[{'id':m['folds'][0]['id'],'counts':dict(zip(('train','calibration','evaluation'),map(len,(train,cal,evaluation)))),'metrics':t.summarize_scores(scores),'model':mr,'fitted_models':model['fitted_models']}],'metrics':t.summarize_scores(scores),'matched_candidate_metrics':t.summarize_scores(matched),'matched_baseline_metrics':t.summarize_scores([r['baseline_score'] for r in matched]),'slices':{'zero_games':t.summarize_scores([r for r in scores if r['actual']==0]),'extreme_yards':t.summarize_scores([r for r in scores if abs(r['actual'])>=100]),'complete_history':t.summarize_scores([r for r in scores if r['unknown_history_count']==0]),'high_usage':t.summarize_scores([r for r in scores if r['high_usage']]),'unknown_history':t.summarize_scores([r for r in scores if r['unknown_history_count']>0])},'feature_audit':built['audit'],'feature_names':built['feature_names'],'artifacts':refs,'artifact_roundtrip':'PASS','genuine_roi':None,'paper_bets':0,'resources':{'wall_seconds':time.monotonic()-started,'cpu_seconds':time.process_time()-cpu,'fit_threads':1},'fixed_b_status':'ORIGINAL_SAVED2024_LAWS_MATCHED_WITHOUT_REPLAY','fixed_f_status':'ORIGINAL_SAVED2024_LAWS_MATCHED_WITHOUT_REPLAY','v3_status':'NO_MATCHED_2024_IMMUTABLE_V3_FORECASTS; NO_REPLAY','limitations':['Revised and previously exposed historical sources; not a blind or prospective evaluation.','Labels conditional on verified offensive appearance. Unknown is not zero.','No real pregame offers; ROI unavailable.','All trials retained; standing compute allowance does not replenish blind evidence. Individual labels preserved and unverified team aggregates masked.']}
 result['recency_baseline']=t.summarize_scores(recency_scores)
 oldkeys={tuple(r['identity']) for r in __import__('gamd.licensed_train',fromlist=['build']).build(rows,m['channel'],include_snaps=False)['examples'] if r['season']==2024 and r['week']>=5};newkeys={tuple(r['identity']) for r in scores}
 result['population_comparison']={'old_cohort':len(oldkeys),'new_cohort':len(newkeys),'intersection':len(oldkeys&newkeys),'newly_admitted':len(newkeys-oldkeys),'not_in_new':len(oldkeys-newkeys),'union':len(oldkeys|newkeys),'intersection_metrics':t.summarize_scores([r for r in scores if tuple(r['identity']) in oldkeys]),'newly_admitted_metrics':t.summarize_scores([r for r in scores if tuple(r['identity']) not in oldkeys]),'season':2024,'channel':m['channel']}
 result['probability_status']='EXPERIMENTAL_UNVALIDATED'
 result['calibration_admission']={'nominal_coverage':0.8,'required_range':[0.75,0.85],'rule':'Each partial-history and high-usage slice with n>=100 must meet range; otherwise distributions remain experimental. No promotion.'}
 rr=t.write_json_once(out/'RESULT.json',result);return {**result,'result_artifact':rr}
