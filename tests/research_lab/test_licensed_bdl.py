"""Authored canaries, never reported as real research or source recovery."""
import copy,gzip,hashlib,io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from gamd import licensed_source as s,licensed_train as t
from nfl_model import licensed_bdl_gate as g
from test_gamd import row
from test_coverage_review import history,evidence,admission,reconcile,SNAP_DENOMINATOR

class LicensedTests(unittest.TestCase):
 def test_raw_custody(self):
  with tempfile.TemporaryDirectory() as td:
   p=Path(td)/'a';p.write_bytes(b'authored')
   self.assertEqual(s.verified_bytes(td,{'path':'a','sha256':s.digest(b'authored')}),b'authored')
   with self.assertRaisesRegex(ValueError,'RAW_CUSTODY'):s.verified_bytes(td,{'path':'a','sha256':'0'*64})
 def test_retained_request_format(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);rows=[{'id':123}];raw=s.dump({'data':rows,'meta':{'next_cursor':None}}).encode();z=gzip.compress(raw)
   (root/'raw.gz').write_bytes(z)
   meta={'status':200,'endpoint':'/nfl/v1/games','raw_sha':s.digest(raw),'gzip_sha':s.digest(z),'query':{'seasons[]':[2023],'season_types[]':[2],'per_page':100}}
   (root/'meta.json').write_bytes(s.dump(meta).encode())
   page={'status':200,'cursor_in':None,'cursor_out':None,'meta_path':'meta.json','meta_sha':s.digest((root/'meta.json').read_bytes()),'raw_path':'raw.gz','raw_sha':s.digest(raw),'gzip_sha':s.digest(z),'rows':1,'rows_sha':s.digest(s.dump(sorted([s.digest(s.dump(rows[0]).encode())])).encode()),'retrieved_at':'2026-10-27T00:00:00Z','request_id':'authored'}
   part={'season':2023,'phase':2,'endpoint':'/nfl/v1/games','state':'EXHAUSTED','pages':1,'rows':1,'page_records':[page]}
   self.assertEqual(list(s.read_partition(root,part))[0][0],rows[0])
   meta['query']['season_types[]']=[3];(root/'meta.json').write_bytes(s.dump(meta).encode());page['meta_sha']=s.digest((root/'meta.json').read_bytes())
   with self.assertRaisesRegex(ValueError,'REQUEST_SEASON_SCOPE'):list(s.read_partition(root,part))
   meta['endpoint']=part['endpoint']='/nfl/v1/snap_counts';meta['query'].pop('season_types[]');meta['query']['season_type']=2
   (root/'meta.json').write_bytes(s.dump(meta).encode());page['meta_sha']=s.digest((root/'meta.json').read_bytes())
   self.assertEqual(list(s.read_partition(root,part))[0][0],rows[0])
   meta['query']['season_type']=3;(root/'meta.json').write_bytes(s.dump(meta).encode());page['meta_sha']=s.digest((root/'meta.json').read_bytes())
   with self.assertRaisesRegex(ValueError,'REQUEST_SEASON_SCOPE'):list(s.read_partition(root,part))
 def test_licensed_fit_is_not_old_forward_promotion(self):
  from gamd.forward import recommended
  from nfl_model.gamd_gate import CAMPAIGN
  rows=[{'id':'old','plan':{'campaign':CAMPAIGN},'status':'COMPLETED','result':{'decision':{'status':'COMPLETED'}}},{'id':'new','plan':{'campaign':g.CAMPAIGN},'status':'COMPLETED','result':{'decision':{'status':'COMPLETED'}}}]
  with patch('gamd.forward.Registry') as registry:
   registry.return_value.attempts.return_value=rows
   self.assertEqual([r['id'] for r in recommended(None)],['old'])
 def identity_case(self):
  r=dict(season=2024,week=1,game_id='2024_01_DAL_PHI',team='PHI',position='WR',name='Authored Player',gsis='00-0000001');game={**r,'home_team':'PHI','away_team':'DAL','native_game_id':5};native={'player':{'id':7,'first_name':'Authored','last_name':'Player','position_abbreviation':'WR','team':{'abbreviation':'DAL'}},'team':{'abbreviation':'PHI'}};return r,game,native
 def test_dated_identity(self):
  r,game,n=self.identity_case();self.assertEqual(s.match(n,game,s.make_index([r]))['gsis'],r['gsis'])
  for field,val in [('week',2),('team','DAL'),('position','TE')]:
   wrong={**r,field:val}
   with self.assertRaisesRegex(ValueError,'NO_UNIQUE'):s.match(n,game,s.make_index([wrong]))
 def test_ambiguous_identity(self):
  r,game,n=self.identity_case()
  with self.assertRaisesRegex(ValueError,'AMBIGUOUS'):s.match(n,game,s.make_index([r,{**r,'gsis':'00-0000002'}]))
 def test_protected_year(self):
  with self.assertRaisesRegex(ValueError,'PROTECTED'):list(s.read_partition('.',{'season':2025,'phase':2}))
 def test_snap_denominator(self):
  def n(pid,snaps):return {'game':{'id':5},'team':{'abbreviation':'PHI'},'player':{'id':pid},'offense_snaps':snaps}
  den=s.snap_values([n(1,20),n(2,60),n(3,0)])[(5,'PHI')];self.assertIsNone(den['snap_denominator']);self.assertEqual(den['observed_max_offense_snaps'],60);self.assertEqual(den['native_players'],3)
  self.assertFalse(s.snap_values([n(1,20),n(2,None)])[(5,'PHI')]['team_game_complete'])
 def proof(self,off=10,explicit=True):
  a=admission(source_id='licensed_balldontlie',dataset='snap_counts',rights_owner='BALLDONTLIE',licence_lane=g.FAMILY)
  a['authority'].update(operation=g.SOURCE,consumer='research/licensed_bdl_v1/closeout_v5/SOURCE_CONSUMER.json');a['allowed_fields']+=['explicit_player_stats','explicit_stats_ref'];a['evidence_types']+=['explicit_player_stats']
  values={'offense_snaps':off,'snap_denominator':60,'snap_denominator_definition':SNAP_DENOMINATOR,'team_game_complete':True}
  if explicit:values.update(explicit_player_stats={'targets':0,'receptions':0,'receiving_yards':0},explicit_stats_ref={'path':'authored/stats','sha256':'f'*64})
  e=evidence(kind='offensive_snaps',values=values,source_id=a['source_id'],dataset=a['dataset']);return a,e
 def test_missing_not_zero(self):
  for off in (0,10):
   a,e=self.proof(off,False);r=reconcile([history(1),history(2),history(5,None)],evidence_rows=[e],admissions=[a]);self.assertNotEqual(r['rows'][0]['source_repaired_reason'],'ELIGIBLE')
 def test_explicit_zero(self):
  a,e=self.proof();r=reconcile([history(1),history(2),history(5,None)],evidence_rows=[e],admissions=[a]);self.assertEqual(r['summary']['source_recovered_candidates'],1);self.assertEqual(r['original_forecasts_modified'],0)
 def test_source_isolation(self):
  a,e=self.proof();a['upstream_sources']=['PFR'];r=reconcile([history(1),history(2),history(5,None)],evidence_rows=[e],admissions=[a]);self.assertEqual(r['summary']['accepted_evidence'],0)
 def examples(self):return [{**row(season=2023,week=w),'snap_share':.5,'offense_snaps':30} for w in (1,2,3)]
 def test_same_week_poisoning(self):
  rows=self.examples();before=t.build(rows,'receiving',include_snaps=True);rows[1].update(yards=999,opportunity=99,snap_share=1,offense_snaps=999);after=t.build(rows,'receiving',include_snaps=True)
  self.assertEqual(before['examples'][1]['x'],after['examples'][1]['x']);self.assertTrue(all(k[1]<2 for k in before['examples'][1]['snap_history_keys']))
 def test_chronological_splits(self):
  ex=[row(season=y,week=w) for y in (2023,2024) for w in range(1,19)];train,cal,test=t.split(ex);self.assertEqual((len(train),len(cal),len(test)),(18,4,14));self.assertLess(max((r['season'],r['week']) for r in cal),min((r['season'],r['week']) for r in test))
  a=t.build(self.examples(),'receiving',include_snaps=True);b=t.build(self.examples(),'receiving',include_snaps=False);self.assertEqual([r['identity'] for r in a['examples']],[r['identity'] for r in b['examples']]);self.assertEqual(len(a['feature_names'])-len(b['feature_names']),6)
 def test_consumed_attempt(self):
  with patch.object(g,'manifest',return_value=({},None,{},None,None)),patch.object(g,'pretask'),patch.object(g,'attempt',return_value={'status':'COMPLETED'}):
   with self.assertRaisesRegex(ValueError,'ATTEMPT_CONSUMED'):g.require_fit(Path('.'),{})
 def test_artifact_roundtrip(self):
  # The application and frozen estimator runtime intentionally use different Python versions.
  try:import joblib
  except ImportError:
   import subprocess
   root=Path(__file__).resolve().parents[2]
   interpreter=root/'scratch/v3-python/cpython-3.13.17-windows-x86_64-none/python.exe'
   code="import sys,unittest;sys.path[:0]=['scratch/v3-env/Lib/site-packages','src','tools/research_lab','tests/research_lab'];r=unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromName('test_licensed_bdl.LicensedTests.test_artifact_roundtrip'));raise SystemExit(not r.wasSuccessful())"
   result=subprocess.run([str(interpreter),'-I','-B','-S','-c',code],cwd=root,capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stdout+result.stderr)
   return
  import numpy as np,joblib
  from gamd.trainer import _fit,_predict
  rows=[{'x':[float(i),None if i%3==0 else .5],'yards':float(i%11),'workload':float(i%5)} for i in range(120)]
  model=_fit('hist_gradient_boosting',rows,np);b=io.BytesIO();joblib.dump(model,b);copy_model=joblib.load(io.BytesIO(b.getvalue()));a=_predict(model,rows,np);c=_predict(copy_model,rows,np);self.assertTrue(all(np.array_equal(a[k],c[k]) for k in a))
