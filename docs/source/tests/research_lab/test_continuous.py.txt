import unittest,tempfile,json,copy
from pathlib import Path
from datetime import datetime,timezone,timedelta
from core import Store
from gamd.continuous_registry import Queue,scientific_key,verify_legacy_recovery
from gamd.continuous_source import category_proofs,training_context
from gamd.continuous_features import _features,_context,build_examples,build_target_features

def row(w,**kwargs):
 return dict(season=2024,week=w,game_id='2024_%02d_A_B'%w,gsis='00-0000001',team='A',opponent='B',position='WR',channel='receiving',appeared=True,opportunity=3,yards=-2,participation_state='POSITIVE_OFFENSIVE',source_observed_at='2026-10-26T00:00:00Z',provenance=[{'authored':True}],**kwargs)
def plan(**extra):
 return dict(family='hist_gradient_boosting',channel='receiving',mechanism='individual_v1',feature_schema='schema_v1',data_sha256='a'*64,folds=[{'train':2023,'test':2024}],seed=20261027,calibration='early',process='DEVELOPMENT',**extra)
class ContinuousTests(unittest.TestCase):
 def test_individual_context_separation(self):
  focal=row(1);unknown=dict(row(1),gsis='00-0000002',appeared=None,opportunity=None,yards=None)
  rows=training_context([focal,unknown]);self.assertEqual(rows[0]['yards'],-2);self.assertEqual(_context(rows),(None,None,.5))
  result=_features(row(3),[focal,dict(row(2),appeared=None,opportunity=None,yards=None)],rows,[])
  self.assertEqual(result['verified_history_count'],1);self.assertEqual(result['unknown_history_count'],1);self.assertEqual(result['x'][4],.5);self.assertIsNone(result['x'][22])
 def test_complete_category_zero(self):
  f={'game_id':'g','team':'A','gsis':'x','position':'WR','original_values':{'targets':3,'receptions':2,'receiving_yards':-2}}
  p={'data':{'records':[f],'team_context':[{'game_id':'g','team':'A','targets':3,'receptions':2,'receiving_yards':-2}]}}
  self.assertTrue(category_proofs(p)['g','A','receiving']['complete_reconciled'])
  p['data']['team_context'][0]['targets']=4;self.assertFalse(category_proofs(p)['g','A','receiving']['complete_reconciled'])
 def test_missing_not_zero(self):
  r=dict(row(1),yards=None);out=training_context([r]);self.assertIsNone(out[0]['yards']);self.assertFalse(out[0]['team_context_valid'])
 def test_signed_yards(self):self.assertEqual(training_context([row(1)])[0]['yards'],-2)
 def test_unknown_denominator(self):
  history=[row(1),dict(row(2),appeared=None,yards=None,opportunity=None),row(3)]
  built=_features(row(4),history,history,[]);self.assertEqual(built['x'][3],3);self.assertAlmostEqual(built['x'][4],2/3)
 def test_chronological_groups(self):
  rows=[row(w) for w in range(1,7)];a=build_examples(rows,'receiving',apply_mask=False,allowed_seasons=(2024,));rows[-1]['yards']=9999;b=build_examples(rows,'receiving',apply_mask=False,allowed_seasons=(2024,));self.assertEqual([x['x'] for x in a['examples']],[x['x'] for x in b['examples']])
 def test_partial_history_selection(self):
  history=[row(1),dict(row(2),appeared=None,yards=None,opportunity=None),row(3),row(4)]
  a=build_examples(history,'receiving',apply_mask=False,allowed_seasons=(2024,));self.assertEqual(len(a['examples']),1);self.assertEqual(a['examples'][0]['unknown_history_count'],1)
 def test_deduplication(self):
  with tempfile.TemporaryDirectory() as d:
   q=Queue(Store(Path(d)));aid,fresh=q.plan(plan(name='a'));other,fresh2=q.plan(plan(name='renamed'));self.assertTrue(fresh);self.assertFalse(fresh2);self.assertEqual(aid,other);q.claim(aid)
   with self.assertRaises(ValueError):q.claim(aid)
   q.finish(aid,'REJECTED',elapsed=2);self.assertFalse(q.plan(plan(name='third'))[1])
 def test_rolling_budget(self):
  with tempfile.TemporaryDirectory() as d:
   q=Queue(Store(Path(d)))
   for n in range(12):
    p=plan();p['mechanism']=str(n);aid,_=q.plan(p);q.claim(aid);q.finish(aid,'FAILED',elapsed=300)
   self.assertFalse(q.budget()['allowed'])
   with self.assertRaises(ValueError):q.plan(plan())
   self.assertTrue(q.budget(datetime.now(timezone.utc)+timedelta(hours=25))['allowed'])
 def test_recovery_before_fit_only(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);p=root/'data/private/research-lab/gamd/attempts/02e513962ad99b23bb8ef98386312897';p.mkdir(parents=True);(p/'CLAIM.json').write_text('{}');(p/'stdout.log').write_text('');(p/'stderr.log').write_text('GAMD:FIT_PARENT_CLAIM');self.assertEqual(verify_legacy_recovery(root,p.name)['state'],'FAILED_BEFORE_NUMERICAL_FIT_AND_EVALUATION');(p/'EXECUTION.json').write_text('{}')
   with self.assertRaises(ValueError):verify_legacy_recovery(root,p.name)
 def test_restart_does_not_refit(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);q=Queue(Store(root/'data/private/research-lab'));aid,_=q.plan(plan());q.claim(aid);q.recover(root);self.assertEqual(q.rows()[0]['status'],'INTERRUPTED');self.assertFalse(q.plan(plan())[1])
 def test_future_cutoff(self):
  target=dict(row(8),season=2026,appeared=None,yards=None,opportunity=None,kickoff_at='2026-10-30T00:00:00Z')
  h=[dict(row(1),season=2026,source_observed_at='2026-10-29T00:00:00Z')]
  with self.assertRaisesRegex(ValueError,'HISTORY_OBSERVED_AFTER'):build_target_features(h,target,'2026-10-27T00:00:00Z',allowed_history_seasons=(2026,))
 def test_protected_year(self):
  with self.assertRaises(ValueError):build_examples([dict(row(1),season=2025)],'receiving',apply_mask=False,allowed_seasons=(2023,2024))
 def test_source_custody(self):
  from nfl_model.gamd_gate import checked
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'value.json';p.write_text('{}')
   with self.assertRaises(ValueError):checked(Path(d),{'path':'value.json','sha256':'0'*64})

 def test_import_chain_hash(self):
  from gamd.continuous_incremental import imports
  with tempfile.TemporaryDirectory() as d:
   store=Store(Path(d));store.append('source_import','authored',{'dataset':'pbp','validation':'ACTUAL_PAYLOAD_READ_AND_HASH_VERIFIED','through_period':[2026,7],'artifact':{'path':'a','sha256':'a'*64},'manifest':{'path':'b','sha256':'b'*64},'source_clock':'2026-10-27T00:00:00Z'},immutable=True)
   self.assertEqual(imports(store)[0]['dataset'],'pbp')
 def test_model_roundtrip(self):
  # The application and frozen estimator runtime intentionally use different Python versions.
  try:import joblib
  except ImportError:
   import subprocess
   root=Path(__file__).resolve().parents[2]
   interpreter=root/'scratch/v3-python/cpython-3.13.17-windows-x86_64-none/python.exe'
   code="import sys,unittest;sys.path[:0]=['scratch/v3-env/Lib/site-packages','src','tools/research_lab','tests/research_lab'];r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName('test_continuous.ContinuousTests.test_model_roundtrip'));raise SystemExit(not r.wasSuccessful())"
   result=subprocess.run([str(interpreter),'-I','-B','-S','-c',code],cwd=root,capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
   return
  import numpy as np,joblib,io
  from gamd.trainer import _fit,_predict
  from gamd.continuous_train import calibrate,law_for
  from test_gamd import row as original
  rows=[original(season=2023,week=w,gsis='p'+str(i),yards=(i*w)%60,opportunity=i%9) for w in range(1,19) for i in range(15)]
  ex=build_examples(rows,'receiving',apply_mask=False,allowed_seasons=(2023,))['examples'];model=_fit('hist_gradient_boosting',ex[:120],np);prediction=_predict(model,ex[120:],np);cal=calibrate(prediction,ex[120:]);law=law_for(cal,prediction,0,ex[120]);self.assertAlmostEqual(sum(law['pmf']),1)
  buf=io.BytesIO();joblib.dump(model,buf);restored=joblib.load(io.BytesIO(buf.getvalue()));self.assertTrue(np.array_equal(prediction['point'],_predict(restored,ex[120:],np)['point']))

 def test_native_string_schedule_week(self):
  from gamd.continuous_source import future_cohort
  from unittest.mock import patch
  from datetime import timezone
  schedule=[dict(season='2026',week=str(w),game_id='2026_%02d_DAL_PHI'%w,home_team='PHI',away_team='DAL',gameday='2026-10-'+str(20 if w==7 else 30),gametime='20:15') for w in (7,8)]
  roster={'season':'2026','week':'7','game_type':'REG','gsis_id':'00-0000001','team':'PHI','position':'WR','_observed_at':'2026-10-26T00:00:00Z','_source':{'authored':True}}
  with patch('source_adapter.schedule_timezone',return_value=timezone.utc):r=future_cohort(schedule,[roster],[],'2026-10-27T00:00:00Z')
  self.assertEqual(r['fixtures'][0]['week'],8);self.assertEqual(r['history'][0]['week'],7);self.assertEqual(len(r['targets']),0)
 def test_source_proof_idempotent_clock(self):
  from operations import retained_settlement_clock
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'SOURCE_PROOF.json';inputs=[{'path':'a','sha256':'a'*64}];p.write_text(json.dumps({'source_inputs':inputs,'source_observed_at':'2026-10-27T01:00:00Z'}));before=p.read_bytes()
   self.assertEqual(retained_settlement_clock(p,inputs,'2026-10-27T02:00:00Z'),'2026-10-27T01:00:00Z');self.assertEqual(p.read_bytes(),before)
   with self.assertRaises(ValueError):retained_settlement_clock(p,[],'2026-10-27T02:00:00Z')
   with self.assertRaises(ValueError):retained_settlement_clock(p,inputs,'2026-10-27T00:00:00Z')

 def test_candidate_population_metadata(self):
  from gamd.continuous_preparation import population
  self.assertEqual(population({'candidate':{'template_id':'fixed','channel':'receiving','process':'DEVELOPMENT'}}),'fixed / receiving / DEVELOPMENT')
  with self.assertRaises(ValueError):population({})
 def test_completed_child_restart_uses_claimed_manifest(self):
  import hashlib
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);q=Queue(Store(root/'data/private/research-lab'));spec=plan();aid,_=q.plan(spec);q.claim(aid);p=root/'data/private/research-lab/gamd/continuous_v1/attempts'/aid;out=p/'preflight-authored';out.mkdir(parents=True)
   mp=out/'MANIFEST.json';mp.write_text(json.dumps({'candidate':spec}));mr={'path':mp.relative_to(root).as_posix(),'sha256':hashlib.sha256(mp.read_bytes()).hexdigest()};(p/'CLAIM.json').write_text(json.dumps({'manifest':mr}));(p/'MANIFEST.json').write_text('preserved invalid predecessor')
   (p/'TERMINAL.json').write_text(json.dumps({'manifest_sha256':mr['sha256'],'status':'REJECTED','artifacts':[],'resources':{'wall_seconds':2}}));q.recover(root);self.assertEqual(q.rows()[0]['status'],'REJECTED');self.assertFalse(q.plan(spec)[1])

 def test_metadata_only_snapshot_does_not_refit(self):
  import hashlib
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);q=Queue(Store(root/'store'))
   def projection(name,**change):
    r=dict(row(1),**change);p=root/name;p.write_text(json.dumps(r)+'\n');return {'path':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
   a=projection('a');b=projection('b',source_observed_at='2026-10-27T00:00:00Z',provenance=[{'new_capture':True}]);c=projection('c',yards=4)
   plans=[dict(plan(),projection=x,data_sha256=x['sha256']) for x in (a,b,c)]
   first,_=q.plan(plans[0]);q.claim(first);q.finish(first,'REJECTED',elapsed=3)
   duplicate,_=q.plan(plans[1]);q.admit_distinct(root,plans)
   states={r['id']:r for r in q.rows()};self.assertEqual(states[first]['status'],'REJECTED');self.assertEqual(states[duplicate]['status'],'PREFLIGHT_FAILED');self.assertEqual(states[duplicate]['elapsed_seconds'],0);self.assertEqual(len(states),3)
   q.admit_distinct(root,plans);self.assertEqual(len(q.rows()),3)
 def test_numerical_dedup_retains_context_and_identity(self):
  import hashlib
  from gamd.continuous_registry import numerical_key
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);keys=[]
   for n,changes in enumerate(({},dict(team_context_valid=True),dict(position='TE'),dict(appeared=False,opportunity=None,yards=None))):
    r=row(1);r.update(changes);p=root/str(n);p.write_text(json.dumps(r)+'\n');ref={'path':str(n),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()};keys.append(numerical_key(root,dict(plan(),projection=ref)))
   self.assertEqual(len(set(keys)),4)
