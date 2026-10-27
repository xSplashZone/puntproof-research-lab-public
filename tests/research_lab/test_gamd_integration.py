"""Offline integration canaries; no retained football/model data is opened."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from core import Store
from gamd.registry import Registry
from gamd.source import project,read_partition,FIELDS
from gamd.runtime import Coordinator

class GAMDIntegrationTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.store=Store(self.root/'lab');self.registry=Registry(self.store)
 def spec(self):return {'family':'hist_gradient_boosting','channel':'receiving','campaign':'authored','mechanism':'missingness_matched_v1','population':'known_prior_only','endpoint':'signed_yards_crps','data_sha256':'a'*64,'recipe_version':1,'folds':[[2022,2023],[2023,2024]],'seed':17}
 def authored_preparation(self,coordinator):
  from gamd.registry import write_once
  def prepare(candidate,projection,policy):
   out=self.store.root/'gamd'/'attempts'/candidate['id']
   return write_once(out/'MANIFEST.json',{**candidate['plan'],'output':out.relative_to(self.root).as_posix(),'campaign_ref':policy['campaign_ref'],'as_of':candidate['created_at']})
  coordinator.prepare_fit=Mock(side_effect=prepare)
  coordinator.child_command=Mock(side_effect=lambda path,operation=None:['authored-base-python','-I','-B','-S','authored-child',str(self.root),str(path)])
 def test_cosmetic_name_or_code_change_cannot_reopen_consumed_science(self):
  a=self.registry.plan(self.spec());self.registry.claim(a);self.registry.finish(a,'REJECTED',{'decision':'REJECT_REGRESSION','fitted_models':2})
  b=self.registry.plan({**self.spec(),'name':'new wording','code_sha256':'b'*64});self.assertEqual(a,b)
  with self.assertRaisesRegex(ValueError,'CONSUMED'):self.registry.claim(b)
  self.assertEqual(self.registry.summary()['genuine_fits'],2)
 def test_interrupted_claim_is_retained_and_not_retried(self):
  a=self.registry.plan(self.spec());self.registry.claim(a);self.registry.recover();self.assertEqual(self.registry.attempts()[0]['status'],'INTERRUPTED')
  with self.assertRaisesRegex(ValueError,'CONSUMED'):self.registry.claim(a)
 def test_global_one_fit_and_fixed_six_recipe_budget(self):
  a=self.registry.plan(self.spec());self.registry.claim(a)
  b=self.registry.plan({**self.spec(),'channel':'rushing'})
  with self.assertRaisesRegex(ValueError,'CONCURRENCY'):self.registry.claim(b)
 def test_source_unknown_is_not_dnp_or_zero_and_stats_prove_cross_channel_zero(self):
  src={'path':'authored.csv','sha256':'a'*64,'observed_at':'2026-10-01T00:00:00Z','dataset':'rosters','season':2023}
  roster=[{'season':2023,'week':1,'team':'DAL','gsis_id':g,'position':'RB','_source':src} for g in ('00-0000001','00-0000002')]
  pbp=[{'season':2023,'week':1,'game_id':'2023_01_DAL_PHI','home_team':'PHI','away_team':'DAL','posteam':'DAL','play_type':'run','rusher_player_id':'00-0000001','_source':{**src,'dataset':'pbp'}}]
  stats=[{'season':2023,'week':1,'team':'DAL','player_id':'00-0000001','position':'RB','targets':'0','receiving_yards':'0','carries':'3','rushing_yards':'-2','_source':{**src,'dataset':'player_stats'}}]
  result=project([('rosters',roster),('pbp',pbp),('player_stats',stats)])['rows']
  unknown=[x for x in result if x['gsis']=='00-0000002'];self.assertTrue(all(x['appeared'] is None and x['yards'] is None for x in unknown))
  known=[x for x in result if x['gsis']=='00-0000001'];self.assertEqual({x['channel']:x['yards'] for x in known},{'receiving':0.,'rushing':-2.})
 def test_protected_year_rejected_before_open(self):
  with self.assertRaisesRegex(ValueError,'PARTITION_DENIED'):list(read_partition(self.root,{'dataset':'player_stats','season':2025,'path':'not-opened','allowed_fields':FIELDS['player_stats']},years=[2021,2022,2023,2024]))
 def test_unregistered_gate_never_claims_or_projects(self):
  worker=Mock();c=Coordinator(self.store,worker,self.root);c.gate=Mock(side_effect=ImportError('not registered'))
  self.assertEqual(c.run_once()['status'],'BLOCKED');self.assertEqual(c.registry.attempts(),[]);worker.resource_status.assert_not_called()
 def test_user_pause_blocks_fit_before_gate(self):
  worker=Mock();c=Coordinator(self.store,worker,self.root);c.gate=Mock(side_effect=AssertionError('must not run'));self.store.set_setting('worker_paused',True)
  self.assertEqual(c.run_once()['status'],'PAUSED')

 def test_source_reader_requires_explicit_reg_and_preserves_cutoff(self):
  import hashlib
  p=self.root/'source.csv';p.write_text('season,season_type,week,player_id,position,targets,receiving_yards,carries,rushing_yards\n2026,REG,6,a,RB,1,4,0,0\n2026,REG,7,a,RB,1,999,0,0\n')
  entry={'dataset':'player_stats','season':2026,'path':'source.csv','allowed_fields':FIELDS['player_stats'],'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'observed_at':'2026-10-27T00:00:00Z'}
  rows=list(read_partition(self.root,entry,years=[2026]));self.assertEqual(len(rows),1);self.assertEqual(rows[0]['receiving_yards'],'4')
  p.write_text('season,week,player_id,position,targets,receiving_yards,carries,rushing_yards\n2026,6,a,RB,1,4,0,0\n');entry['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
  with self.assertRaisesRegex(ValueError,'EXPLICIT_REGULAR'):list(read_partition(self.root,entry,years=[2026]))
 def test_windows_job_bounded_child_can_complete_and_timeout(self):
  import os,sys
  if os.name!='nt':self.skipTest('Windows job enforcement')
  from gamd.limits import run
  good=[sys.executable,'-I','-B','-c',"import sys; assert sys.stdin.buffer.readline()==b'EXECUTE\\n'; print('authored process')"]
  result=run(good,self.root,self.root/'ok.out',self.root/'ok.err',seconds=10,memory_mb=512)
  self.assertEqual(result['status'],'COMPLETED',(self.root/'ok.err').read_text())
  slow=[sys.executable,'-I','-B','-c',"import sys,time; sys.stdin.buffer.readline(); time.sleep(4)"]
  result=run(slow,self.root,self.root/'slow.out',self.root/'slow.err',seconds=1,memory_mb=512)
  self.assertEqual(result['status'],'TIMEOUT')
  nonce=b'EXECUTE '+b'a'*64+b'\n'
  bound=[sys.executable,'-I','-B','-c',"import sys; assert sys.stdin.buffer.readline()==b'EXECUTE '+b'a'*64+b'\\n'"]
  result=run(bound,self.root,self.root/'nonce.out',self.root/'nonce.err',seconds=10,memory_mb=512,handshake=nonce)
  self.assertEqual(result['status'],'COMPLETED')
  with self.assertRaisesRegex(ValueError,'HANDSHAKE'):
   run(bound,self.root,self.root/'bad.out',self.root/'bad.err',handshake=b'EXECUTE wrong\n')

 def test_direct_existing_base_runtime_has_exact_parent_nonce_and_job_without_football(self):
  import os
  if os.name!='nt':self.skipTest('Windows direct-base JobObject custody')
  from gamd.limits import validate_runtime,probe_runtime,RUNTIME_PATH
  project=Path(__file__).resolve().parents[2]
  value=validate_runtime(project,json.loads((project/RUNTIME_PATH).read_bytes()))
  result=probe_runtime(project,self.root/'direct-base-process',value)
  self.assertEqual(result['status'],'PASS');self.assertEqual(result['parent_pid'],os.getpid())
  self.assertTrue(result['nonce_verified'] and result['job_verified'])
  self.assertEqual((result['football_rows'],result['model_fits'],result['provider_calls']),(0,0,0))
  self.assertEqual(result['python'],'3.13.17')

 def test_cached_projection_uses_only_explicitly_accepted_exact_original_manifest(self):
  import hashlib
  from gamd.registry import write_once
  c=Coordinator(self.store,Mock(),self.root);directory=self.store.root/'gamd'/'projection';directory.mkdir(parents=True)
  payload=b'{"authored":true}\n';(directory/'ROWS.jsonl').write_bytes(payload)
  projection={'path':'lab/gamd/projection/ROWS.jsonl','sha256':hashlib.sha256(payload).hexdigest()}
  old={'path':'old.json','sha256':'a'*64};new={'path':'new.json','sha256':'b'*64}
  write_once(directory/'RECEIPT.json',{'source_manifest':old,'projection':projection})
  self.assertEqual(c.projection({'source_manifest':new,'accepted_projection_sources':[old]}),projection)
  with self.assertRaisesRegex(ValueError,'CUSTODY'):c.projection({'source_manifest':new})

 def test_forward_projection_preserves_unknowns_and_rejects_target_week_and_snaps(self):
  from datetime import datetime,timedelta,timezone
  from nfl_model import gamd_gate as g
  stamp=datetime.now(timezone.utc);cut=(stamp-timedelta(days=1)).isoformat();kick=(stamp+timedelta(days=1)).isoformat()
  data={'path':'data/private/research-lab/sources/authored/revised-source-01/DATA.json','sha256':'a'*64}
  fixtures={'path':'data/private/research-lab/sources/authored/receiving-fixtures.json','sha256':'b'*64}
  receipt={'path':'authored-receipt.json','sha256':'c'*64};model={'path':'authored-model','sha256':'d'*64}
  identity={'season':2026,'week':8,'game_id':'2026_08_DAL_PHI','gsis':'00-0000001','team':'DAL','opponent':'PHI','position':'RB','channel':'receiving'}
  target={**identity,'appeared':None,'opportunity':None,'yards':None,'participation_state':'PREGAME_UNKNOWN','source_observed_at':cut,'provenance':[receipt,fixtures],'kickoff_at':kick}
  origin={'week':8,'channels':['receiving'],'cutoff':cut,'input_files':[data,fixtures],'channel_inputs':{'receiving':{'fixtures':fixtures,'input_root':{'path':'data/private/research-lab/sources/authored','files':{'revised-source-01/DATA.json':'a'*64}}}},'fixtures':[{'game_id':identity['game_id'],'season':2026,'week':8,'kickoff':kick}]}
  history={k:v for k,v in identity.items() if k!='channel'};history.update(week=7,game_id='2026_07_DAL_PHI',identity_state='EXACT_DATED_ROSTER_GSIS',observed_at=cut,source_lineage=[receipt],appeared=None,targets=None,receiving_yards=None,state='UNKNOWN_PARTICIPATION')
  m={'v3_source':{'data':data,'fixtures':fixtures,'source_receipt':receipt},'week':8,'channel':'receiving','game_id':identity['game_id'],'model':model,'input_files':[data,fixtures],'source_cutoff':cut,'as_of':stamp.isoformat(),'targets':[target],'output':g.forward_directory(model,8,identity['game_id'],'receiving'),'population_scope':'EXISTING_V3_ADMITTED_COHORT_ONLY'}
  def read(root,r):return [identity] if r==fixtures else {'records':[history]}
  with patch.object(g,'_v3_origin',return_value=(origin,{'latest_observed_at':cut},[],[])),patch.object(g,'read',side_effect=read):
   rows,targets,_=g._forward_input_rows(self.root,m)
   self.assertIsNone(rows[0]['yards']);self.assertIsNone(rows[0]['appeared']);self.assertEqual(targets,[target]);self.assertNotIn('offense_snaps',rows[0])
   history['week']=8
   with self.assertRaisesRegex(ValueError,'HISTORY_PERIOD'):g._forward_input_rows(self.root,m)
   history['week']=7;history['offense_snaps']=3
   with self.assertRaisesRegex(ValueError,'SNAP_FIELDS'):g._forward_input_rows(self.root,m)
   history['offense_snaps']=None;history['targets']=0
   with self.assertRaisesRegex(ValueError,'UNKNOWN_NOT_ZERO'):g._forward_input_rows(self.root,m)

 def test_forward_status_counts_verified_scores_not_exclusions_or_model_updates(self):
  from gamd.forward import status,run
  self.store.append('gamd_observation','authored-unknown',{'forecast_id':'a','status':'EXCLUDED_UNKNOWN','outcome_observed_at':'2026-10-27T01:00:00Z'})
  self.store.append('gamd_observation','authored-scored',{'forecast_id':'b','status':'SCORED_VERIFIED_FINAL','outcome_observed_at':'2026-10-27T01:00:00Z'})
  result=status(self.root,self.store)
  self.assertEqual(result['verified_observations'],1);self.assertEqual(result['excluded_observations'],1);self.assertEqual(result['parameter_updates'],0)
  self.assertNotIn('FORWARD_OUTCOME_CHAIN_ADAPTER_NOT_READY',[p['code'] for p in result['prerequisites']])
  with patch('gamd.forward.settle_available',return_value={'new_scored_observations':0,'blockers':[]}):
   self.assertEqual(run(self.root,self.store,coordinator=Mock())['reason'],'NO_RECOMMENDED_MODEL')

 def test_source_provenance_and_missing_roster_losses_are_explicit(self):
  def src(dataset,at):return {'path':dataset+'.csv','sha256':dataset[0]*64,'observed_at':at,'dataset':dataset,'season':2023}
  calendar={'season':2023,'week':1,'game_id':'2023_01_DAL_PHI','home_team':'PHI','away_team':'DAL','_source':src('schedule','2026-10-03T00:00:00Z')}
  roster={'season':2023,'week':1,'team':'DAL','gsis_id':'00-0000001','position':'RB','_source':src('rosters','2026-10-01T00:00:00Z')}
  stats=[{'season':2023,'week':1,'team':'DAL','player_id':g,'position':'RB','opponent_team':'PHI','carries':'3','rushing_yards':'10','_source':src('player_stats','2026-10-02T00:00:00Z')} for g in ('00-0000001','00-0000002')]
  value=project([('schedule',[calendar]),('rosters',[roster]),('player_stats',stats)])
  self.assertEqual(value['rows'][0]['source_observed_at'],'2026-10-03T00:00:00Z')
  self.assertEqual(value['audit']['problems'][0]['code'],'STAT_IDENTITY_WITHOUT_DATED_ROSTER')
  stats[0]['opponent_team']='NYG';value=project([('schedule',[calendar]),('rosters',[roster]),('player_stats',stats)])
  self.assertTrue(all(r['appeared'] is None and r['yards'] is None for r in value['rows']))

 def test_worker_accepts_real_terminal_shape_without_refitting(self):
  import hashlib,threading
  worker=Mock();worker.run_lock=threading.Lock();worker.resource_status.return_value={'allowed':True,'reasons':[]}
  c=Coordinator(self.store,worker,self.root);gate=Mock();c.gate=Mock(return_value=gate)
  gate.require_campaign.return_value={'campaign':'authored','campaign_ref':{'path':'campaign.json','sha256':'b'*64},'recipes':[self.spec()]}
  gate.claim_fit.return_value='f'*64;self.authored_preparation(c)
  c.projection=Mock(return_value={'path':'authored.jsonl','sha256':'a'*64})
  def finish_child(argv,root,stdout,stderr,**kw):
   manifest=Path(argv[-1]);plan=json.loads(manifest.read_text(encoding='utf-8'))
   self.assertEqual(kw['handshake'],b'EXECUTE '+b'f'*64+b'\n')
   artifacts=manifest.parent/'artifacts';artifacts.mkdir()
   fixture=artifacts/'AUTHORED_ONLY.txt';fixture.write_bytes(b'Not a real fit')
   value={'status':'REJECTED','family':plan['family'],'channel':plan['channel'],'decision':{'status':'REJECTED','reasons':['AUTHORED_ONLY']},'fitted_models':4,'completed_folds':2,'folds':[{'id':'authored_a'},{'id':'authored_b'}],'artifacts':[{'path':str(fixture),'sha256':hashlib.sha256(fixture.read_bytes()).hexdigest()}]}
   resultfile=artifacts/'RESULT.json';resultfile.write_text(json.dumps(value),encoding='utf-8')
   value.update(result_artifact={'path':str(resultfile),'sha256':hashlib.sha256(resultfile.read_bytes()).hexdigest()},projection_file_sha256='a'*64,manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
   (manifest.parent/'TERMINAL.json').write_text(json.dumps(value),encoding='utf-8')
   return {'status':'COMPLETED','elapsed_seconds':.1}
  with patch('gamd.limits.run',side_effect=finish_child) as execute:
   self.assertEqual(c.run_once()['status'],'COMPLETED');self.assertEqual(c.registry.attempts()[0]['status'],'REJECTED')
   self.assertEqual(c.run_once()['status'],'STANDING_DEVELOPMENT');self.assertEqual(execute.call_count,1)
  self.assertEqual(c.registry.summary()['fitted_artifacts'],2)
 def test_preclaim_gate_failure_reuses_unchanged_manifest(self):
  import threading
  worker=Mock();worker.run_lock=threading.Lock();worker.resource_status.return_value={'allowed':True,'reasons':[]}
  c=Coordinator(self.store,worker,self.root);gate=Mock();c.gate=Mock(return_value=gate)
  gate.require_campaign.return_value={'campaign':'authored','campaign_ref':{'path':'campaign.json','sha256':'b'*64},'recipes':[self.spec()]}
  self.authored_preparation(c)
  c.projection=Mock(return_value={'path':'authored.jsonl','sha256':'a'*64});gate.require_fit.side_effect=ValueError('AUTHORED_GATE_BLOCK')
  for _ in range(2):
   result=c.run_once();self.assertEqual(result['status'],'PRECLAIM_BLOCKED');self.assertIn('AUTHORED_GATE_BLOCK',result['blockers'][0]['reason'])
  self.assertEqual(c.registry.attempts()[0]['status'],'PLANNED');self.assertEqual(gate.require_fit.call_count,2)
  self.assertEqual(len(self.store.records('gamd_preflight')),1)

 def test_fit_witness_checks_fixed_splits_without_reading_projection_rows(self):
  from gamd.preparation import fit_witness
  from gamd.trainer import FOLDS
  projection={'path':'authored.jsonl','sha256':'a'*64};receipt_ref={'path':'receipt.json','sha256':'b'*64}
  counts={f'{year}-{week:02d}':8 for year in range(2021,2025) for week in range(1,19)}
  receipt={'projection':projection,'audit':{'unknowns_imputed_zero':False,'snap_fields_used':False,'label_rows_by_channel_season_week':{'receiving':counts}}}
  witness=fit_witness(receipt,projection,receipt_ref,'receiving',FOLDS)
  self.assertEqual(witness['fold_counts'][0]['calibration'],48)
  self.assertEqual(witness['fold_counts'][1]['evaluation'],144)
  for week in range(13,19):counts[f'2022-{week:02d}']=0
  with self.assertRaisesRegex(ValueError,'INSUFFICIENT_FIXED_SPLIT'):
   fit_witness(receipt,projection,receipt_ref,'receiving',FOLDS)

 def test_hurdle_preclaim_needs_twenty_positive_train_and_calibration_labels(self):
  from gamd.preparation import fit_witness
  from gamd.trainer import FOLDS
  projection={'path':'authored.jsonl','sha256':'a'*64};receipt_ref={'path':'receipt.json','sha256':'b'*64}
  counts={f'{year}-{week:02d}':8 for year in range(2021,2025) for week in range(1,19)}
  positives=dict(counts)
  receipt={'projection':projection,'audit':{'unknowns_imputed_zero':False,'snap_fields_used':False,'label_rows_by_channel_season_week':{'receiving':counts},'positive_label_rows_by_channel_season_week':{'receiving':positives}}}
  self.assertEqual(fit_witness(receipt,projection,receipt_ref,'receiving',FOLDS,family='hurdle_glm')['positive_fold_counts'][0]['calibration'],48)
  for week in range(13,19):positives[f'2022-{week:02d}']=3
  with self.assertRaisesRegex(ValueError,'INSUFFICIENT_HURDLE_POSITIVE_SPLIT'):
   fit_witness(receipt,projection,receipt_ref,'receiving',FOLDS,family='hurdle_glm')
  # The independent tree recipe still has sufficient labelled rows.
  self.assertEqual(fit_witness(receipt,projection,receipt_ref,'receiving',FOLDS,family='extra_trees')['fold_counts'][0]['calibration'],48)

 def test_second_coordinator_cannot_recover_a_live_process_claim(self):
  import os
  if os.name!='nt':self.skipTest('Windows app lock')
  a=Coordinator(self.store,Mock(),self.root);b=Coordinator(self.store,Mock(),self.root)
  self.assertTrue(a.claim_coordinator())
  try:self.assertFalse(b.claim_coordinator())
  finally:a.release_coordinator()
  self.assertTrue(b.claim_coordinator());b.release_coordinator()

 def test_existing_app_exposes_separate_actual_fit_counts_and_knowledge(self):
  from server import Application
  app=Application(self.root/'app',self.root/'absent_repo',self.root/'absent_pack',start_worker=False)
  value=app.state()['gamd'];self.assertEqual(value['fitted_artifacts'],0);self.assertEqual(value['genuine_fits'],0)
  self.assertEqual(value['knowledge_map']['concept_count'],49);self.assertEqual(value['admission']['status'],'BLOCKED')
  self.assertIsNone(value['genuine_roi']);self.assertIsNone(value['best_candidate'])
