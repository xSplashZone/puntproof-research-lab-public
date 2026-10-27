"""Scheduled versioned refit: past completed weeks only; no retrospective test score."""
from pathlib import Path
import time,io,json
from gamd import trainer as t
from gamd.continuous_features import build_examples
from gamd.continuous_train import calibrate

def run(rows,m,out,phase):
 import numpy as np,joblib
 start=time.monotonic();built=build_examples(rows,m['channel'],apply_mask=False,allowed_seasons=(2023,2024,2026));examples=built['examples'];weeks=sorted({r['week'] for r in examples if r['season']==2026})
 if len(weeks)<3:raise ValueError('SCHEDULED_REFIT_NEEDS_THREE_VERIFIED_2026_WEEKS')
 calweeks=weeks[-2:];train=[r for r in examples if r['season']<2026 or r['week']<calweeks[0]];cal=[r for r in examples if r['season']==2026 and r['week'] in calweeks]
 if min(len(train),len(cal))<40:raise ValueError('SCHEDULED_REFIT_INSUFFICIENT_EARLIER_CALIBRATION')
 out=Path(out);out.mkdir(parents=True,exist_ok=False);phase('FIT_STARTED');model=t._fit(m['family'],train,np);phase('FIT_COMPLETED');p=t._predict(model,cal,np);law=calibrate(p,cal);model.update(law_calibration=law,feature_names=built['feature_names'],channel=m['channel'],classification='SCHEDULED_REFIT_NO_EVALUATION_OPENED');b=io.BytesIO();joblib.dump(model,b,compress=3);loaded=joblib.load(io.BytesIO(b.getvalue()));again=t._predict(loaded,cal,np)
 if not all(np.array_equal(p[k],again[k]) for k in p):raise ValueError('REFIT_ROUNDTRIP')
 modelref=t.write_bytes_once(out/'FITTED_MODEL.joblib',b.getvalue());refs=[modelref,t.write_json_once(out/'CALIBRATION.json',law),t.write_json_once(out/'PLAN.json',m),t.write_json_once(out/'SPLIT.json',{'train':[r['identity'] for r in train],'calibration':[r['identity'] for r in cal],'evaluation':[]})]
 result={'status':'INCONCLUSIVE','model':{'path':str(Path(m['output'])/'artifacts/FITTED_MODEL.joblib').replace(chr(92),'/'),'sha256':modelref['sha256']},'feature_schema':m['feature_schema'],'family':m['family'],'channel':m['channel'],'process':'SCHEDULED_REFIT','classification':'SCHEDULED_PAST_DATA_REFIT_NO_TEST_RESULTS','fitted_models':model['fitted_models'],'completed_folds':0,'metrics':None,'calibration_weeks':calweeks,'artifacts':refs,'resources':{'wall_seconds':time.monotonic()-start,'fit_threads':1},'probability_status':'EXPERIMENTAL_UNVALIDATED','genuine_roi':None,'decision':{'status':'INCONCLUSIVE','reason':'Saved version only; no future evaluation opened.'}}
 result['result_artifact']=t.write_json_once(out/'RESULT.json',result);return result
