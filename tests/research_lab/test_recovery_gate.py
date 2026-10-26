"""Authored exact I/O continuation gates; no real model or source payloads."""
import copy,hashlib,json,importlib.util
from pathlib import Path
import sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from nfl_model import research_lab_gate as gate

class RecoveryGateTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
  self.real_checked=gate.checked
  self.mock=patch.object(gate,'checked',side_effect=lambda r,f,committed=False:self.real_checked(r,f))
  self.mock.start()
  self.origin=gate.RUNTIME+'executions/authored-origin';self.child=gate.RUNTIME+'executions/authored-recovery'
  seed=self.write('authored/seed.json',{'authored':True})
  self.original={'operation':gate.REPLAY,'season':2026,'week':7,'channels':['receiving','rushing'],'purpose':'FORECAST_CYCLE','execution_directory':self.origin,'consumer':{'old':True},'pretask_satisfiability':{'old':True},'model_artifacts':{'receiving':seed,'rushing':seed},'fixtures':[{'game_id':'authored'}],'input_files':[],'as_of':'2026-10-01T00:00:00Z','cutoff':'2026-10-01T00:00:00Z'}
  original=self.write(self.origin+'/MANIFEST.json',self.original)
  request=self.write(self.origin+'/receiving/forecast-request.json',{'action':'forecast','channel':'receiving','period':[2026,7],'authority':{'execution_manifest':original},'parent_session':seed})
  archive=self.write(self.origin+'/receiving/forecast/forecast.npz',{'authored_archive_not_numerical':True})
  failure=self.write(self.origin+'/receiving/forecast/FAILED.json',{'status':'QUARANTINED','action':'forecast','channel':'receiving','period':[2026,7],'error':'OSError: [Errno 9] Bad file descriptor'})
  lease=self.write(self.origin+'/FORECAST_LEASE.json',{'operation':gate.REPLAY,'season':2026,'week':7,'action':'forecast','automatic_retry':False})
  prefix=[];previous=None
  for i,state in enumerate(gate.MODEL_STATES[:2]):
   e={'sequence':i,'state':state,'previous_hash':previous,'at':'2026-10-01T00:00:00Z'};e['sha256']=hashlib.sha256(gate.encoded(e)).hexdigest();previous=e['sha256']
   prefix.append(self.write(self.origin+f'/lifecycle/{i:03}.json',e))
  self.contract={'schema_version':'puntproof-lab-forecast-recovery-v1','operation':gate.REPLAY,'season':2026,'week':7,'recovery_channel':'receiving','first_execution_channels':['rushing'],'max_recoveries':1,'recompute_expert_predictions':True,'receiving_forecast_week_allowed':False,'fits':0,'updates':0,'target_outcomes_opened':0,'scientific_reset':False,'preserve_original_archive':True,'require_bitwise_archive_parity':True,'original_execution_directory':self.origin,'refs':{'original_archive':archive,'original_request':request,'original_manifest':original,'original_failure':failure},'original_lease':lease,'original_journal_prefix':prefix,'original_error_log':self.write(self.origin+'/error.json',{'authored_error':True})}
  self.cref=self.write(gate.RECOVERY_CONTRACT,self.contract)
  self.manifest={**copy.deepcopy(self.original),'consumer':{'new':True},'execution_directory':self.child,'pretask_satisfiability':{'new':True},'engineering_recovery':self.cref}
  self.mref=self.write(self.child+'/MANIFEST.json',self.manifest)
 def tearDown(self):self.mock.stop();self.temp.cleanup()
 def write(self,name,data):
  p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(gate.encoded(data));return {'path':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
 def claim(self):
  return self.write(self.origin+'/RECOVERY_CLAIM.json',{'schema_version':'puntproof-lab-recovery-claim-v1','manifest':self.mref,'contract':self.cref,'origin_manifest':self.contract['refs']['original_manifest'],'original_lease':self.contract['original_lease'],'created_at':'2026-10-01T00:01:00Z'})
 def active(self):
  return gate._ACTIVE.set({'root':self.root,'operation':gate.REPLAY,'capability':'FORECAST','manifest':self.manifest,'manifest_ref':self.mref,'version':'puntproof-lab-consumer-v2'})
 def test_exact_recovery_uses_original_journal_and_requires_exclusive_claim(self):
  self.assertEqual(gate.lifecycle_directory(self.root,self.manifest),self.root/self.origin/'lifecycle')
  self.assertEqual(gate._recovery_contract(self.root,self.manifest,{'engineering_recovery':self.cref}),self.contract)
  token=self.active()
  try:
   with self.assertRaisesRegex(ValueError,'EXCLUSIVE_CLAIM'):gate.require_forecast_recovery(self.root,'receiving')
   self.claim();self.assertEqual(gate.require_forecast_recovery(self.root,'receiving'),self.contract['refs'])
   with self.assertRaisesRegex(ValueError,'RECOVERY_CONTEXT'):gate.require_forecast_recovery(self.root,'rushing')
  finally:gate._ACTIVE.reset(token)
 def test_population_inputs_method_or_scope_changes_fail_before_recovery(self):
  for key,val in [('fixtures',[]),('as_of','2026-10-02T00:00:00Z'),('channels',['receiving']),('input_files',[{'invented':True}])]:
   m=copy.deepcopy(self.manifest);m[key]=val
   with self.subTest(key=key),self.assertRaisesRegex(ValueError,'SCIENTIFIC_INPUTS_CHANGED'):gate._recovery_contract(self.root,m)
  c=copy.deepcopy(self.contract);c['fits']=1;self.manifest['engineering_recovery']=self.write(gate.RECOVERY_CONTRACT,c)
  with self.assertRaisesRegex(ValueError,'SCIENTIFIC_BOUNDARIES'):gate._recovery_contract(self.root,self.manifest)
 def test_archive_and_original_journal_cannot_be_replaced(self):
  self.write(self.contract['refs']['original_archive']['path'],{'changed':True})
  with self.assertRaisesRegex(ValueError,'HASH_DRIFT'):gate._recovery_contract(self.root,self.manifest)
 def test_valid_continuation_is_visible_but_cannot_repeat_receiving_inference(self):
  self.claim();prior=gate._events(self.root,self.manifest)[0][-1]['sha256']
  for i,state in enumerate(gate.MODEL_STATES[2:5],start=2):
   e={'sequence':i,'state':state,'previous_hash':prior,'at':'2026-10-01T00:01:00Z'};e['sha256']=hashlib.sha256(gate.encoded(e)).hexdigest();prior=e['sha256'];self.write(self.origin+f'/lifecycle/{i:03}.json',e)
  self.assertEqual(gate._events(self.root,self.manifest)[1],'ATTEMPT_CONSUMED')
  token=self.active()
  try:
   with self.assertRaises(ValueError):gate.require_forecast_recovery(self.root,'receiving')
  finally:gate._ACTIVE.reset(token)
 def test_existing_rushing_or_wrong_claim_denies_recovery(self):
  self.claim();claim=json.loads((self.root/self.origin/'RECOVERY_CLAIM.json').read_bytes());claim['manifest']={'wrong':True};self.write(self.origin+'/RECOVERY_CLAIM.json',claim)
  token=self.active()
  try:
   with self.assertRaisesRegex(ValueError,'CLAIM_BINDING'):gate.require_forecast_recovery(self.root,'receiving')
  finally:gate._ACTIVE.reset(token)
  (self.root/self.origin/'rushing').mkdir()
  with self.assertRaisesRegex(ValueError,'ORIGINAL_OUTPUT_STATE'):gate._recovery_contract(self.root,self.manifest)

 def test_exact_replacement_cannot_expand_source_seed_or_attempt_scope(self):
  path=Path(__file__).resolve().parents[2]/'tools/codex_task_guard.py'
  spec=importlib.util.spec_from_file_location('authored_recovery_guard',path);guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
  op=gate.REPLAY;old=gate.INPUT_REPAIR_CONSUMERS[op];new=gate.RECOVERY_CONSUMERS[op]
  body={'schema_version':'puntproof-lab-consumer-v2','operation':op,'family':gate.FAMILY,'foundations':{},'source_policy':{},'seed_artifacts':[],'seed_channels':{},'capabilities':['SEED_INSPECTION','FORECAST','SETTLE'],'catchup_weeks':[7],'limits':{'provider_calls':0},'expires_at':'2027-01-18T00:00:00Z'}
  oldref=self.write(old,body);c={'allowed_paths':[gate.RECOVERY_BASE],'forbidden_paths':[],'authority_transition':{'receipt_path':gate.RECOVERY_BASE+'AUTHORITY_TRANSITION.json'}}
  change={'operation':'replace_consumer','path':'/pre_model_semantics/consumers/'+op,'expected_current':oldref,'artifact_path':new,'sha256_from_artifact':True}
  before={'pre_model_semantics':{'consumers':{op:oldref}}}
  owner=' '.join([*gate.OPERATIONS,gate.FAMILY,'GOVERNANCE_REPAIR','GOVERNANCE_BOOTSTRAP','AUTHORITY_TRANSITION','SOURCE_AUTHORITY_CHANGE','COMMIT','allowed_real_data_operations','pre_model_semantics','PuntProof_Research_Lab_Finish_And_Simplify.txt'])
  guard.deferred_lab_replacement(self.root,c,change,owner,copy.deepcopy(before),{},final=False)
  self.write(new,{**body,'engineering_recovery':self.cref})
  after=copy.deepcopy(before);guard.deferred_lab_replacement(self.root,c,change,owner,after,{},final=True)
  self.assertEqual(after['pre_model_semantics']['consumers'][op]['path'],new)
  for key in ('source_policy','foundations','seed_artifacts','seed_channels','capabilities','catchup_weeks','limits','expires_at'):
   bad=copy.deepcopy(body);bad[key]=None;self.write(new,bad)
   with self.subTest(key=key),self.assertRaisesRegex(Exception,'BOUNDARIES_CHANGED'):
    guard.deferred_lab_replacement(self.root,c,change,owner,copy.deepcopy(before),{},final=True)


 def test_type_repair_preserves_failed_claim_and_rejects_any_computed_output(self):
  self.claim()
  failed_request=self.write(self.child+'/receiving/forecast-request.json',{'action':'recover_forecast','period':[2026,7],'channel':'receiving','authority':{'execution_manifest':self.mref},'recovery':self.contract['refs']})
  failure=self.write(self.child+'/receiving/forecast/FAILED.json',{'action':'recover_forecast','channel':'receiving','error':'ValueError: ENGINE_BRIDGE:RECOVERY_EXPERT_TYPES','period':[2026,7],'retry_authorized':False,'status':'QUARANTINED'})
  error=self.write(self.child+'/receiving/forecast.err.log',{'trace':'recover_forecast_state RECOVERY_EXPERT_TYPES'})
  source=Path(__file__).resolve().parents[2]/gate.TYPE_REPAIR_BASE/'PREDECESSOR_ENGINE_BRIDGE.py'
  target=self.root/gate.TYPE_REPAIR_BASE/'PREDECESSOR_ENGINE_BRIDGE.py';target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source.read_bytes())
  source_ref={'path':target.relative_to(self.root).as_posix(),'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
  c=copy.deepcopy(self.contract);c['claim_filename']='RECOVERY_CLAIM_TYPE_REPAIR.json'
  c['predecessor_recovery']={'contract':self.cref,'manifest':self.mref,'claim':{'path':self.origin+'/RECOVERY_CLAIM.json','sha256':hashlib.sha256((self.root/self.origin/'RECOVERY_CLAIM.json').read_bytes()).hexdigest()},'request':failed_request,'failure':failure,'error_log':error,'bridge_source':source_ref,'stopped_before_expert_prediction':True,'failure_code':'RECOVERY_EXPERT_TYPES'}
  m={**copy.deepcopy(self.manifest),'execution_directory':gate.RUNTIME+'executions/authored-type-repair','engineering_recovery':self.write(gate.TYPE_REPAIR_CONTRACT,c)}
  self.assertEqual(gate._recovery_contract(self.root,m),c)
  self.write(self.child+'/receiving/forecast/forecast.npz',{'unexpected':'computed output'})
  with self.assertRaisesRegex(ValueError,'UNEXPECTED_COMPUTED_OUTPUT'):gate._recovery_contract(self.root,m)

if __name__=='__main__':unittest.main()


