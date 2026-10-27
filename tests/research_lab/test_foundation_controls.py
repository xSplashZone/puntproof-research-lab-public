"""Authored malformed-transition checks; no real football payloads or fitted models."""
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace
import hashlib,importlib.util,json,unittest
ROOT=Path(__file__).resolve().parents[2]
def load():
 spec=importlib.util.spec_from_file_location('foundation_transition_tests',ROOT/'tools/continuous_gamd_transition.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod
class FoundationControlsTests(unittest.TestCase):
 def setUp(self):
  self.mod=load();old='research/gamd_continuous_v1/runtime_v4/SOURCE_CONSUMER.json';oldref={'path':old,'sha256':hashlib.sha256((ROOT/old).read_bytes()).hexdigest()}
  self.c={'allowed_actions':['GOVERNANCE_REPAIR','GOVERNANCE_BOOTSTRAP','AUTHORITY_TRANSITION','SOURCE_AUTHORITY_CHANGE','COMMIT'],'allowed_paths':[self.mod.FOUNDATION_BASE],'forbidden_paths':[],'owner_authority':{'path':str(ROOT/'research/gamd_continuous_v1/OWNER_REQUEST.txt'),'sha256':self.mod.OWNER_SHA}}
  self.change={'operation':'replace_consumer','path':'/pre_model_semantics/consumers/'+self.mod.OPS[0],'expected_current':oldref,'artifact_path':self.mod.FOUNDATION_BASE+'SOURCE_CONSUMER.json','sha256_from_artifact':True}
  self.authority={'pre_model_semantics':{'consumers':{self.mod.OPS[0]:deepcopy(oldref)},'certificates':{}},'unrelated_preserved':'unchanged'}
  def need(condition,code):
   if not condition:raise ValueError(code)
  self.api=SimpleNamespace(need=need,sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest(),matches=lambda p,patterns:any(p==a.rstrip('/') or a.endswith('/') and p.startswith(a) for a in patterns))
 def apply(self):
  self.mod.foundation_repair(ROOT,self.c,self.change,self.authority,{},self.api,False)
 def test_exact_successor_changes_only_target_consumer(self):
  before=deepcopy(self.authority);self.apply();before['pre_model_semantics']['consumers'][self.mod.OPS[0]]={'path':self.change['artifact_path'],'sha256':'0'*64};self.assertEqual(self.authority,before)
 def test_mismatched_current_binding_is_rejected(self):
  self.authority['pre_model_semantics']['consumers'][self.mod.OPS[0]]['sha256']='a'*64
  with self.assertRaisesRegex(ValueError,'FOUNDATION_CONSUMER_SCOPE'):self.apply()
 def test_protected_scope_is_rejected(self):
  self.c['protected_result_access']=True
  with self.assertRaisesRegex(ValueError,'FOUNDATION_BOUNDARIES'):self.apply()
 def test_missing_existing_grant_is_rejected(self):
  self.c['allowed_actions'].remove('SOURCE_AUTHORITY_CHANGE')
  with self.assertRaisesRegex(ValueError,'FOUNDATION_REPAIR_GRANT'):self.apply()
 def test_unregistered_operation_is_rejected(self):
  self.change['path']='/pre_model_semantics/consumers/NEW_UNREGISTERED_OPERATION'
  with self.assertRaisesRegex(ValueError,'FOUNDATION_CONSUMER_SCOPE'):self.apply()
 def test_another_destination_is_rejected(self):
  self.change['artifact_path']=self.mod.FOUNDATION_BASE+'OTHER_CONSUMER.json'
  with self.assertRaisesRegex(ValueError,'FOUNDATION_SUCCESSOR_PATH'):self.apply()
 def test_controlled_policy_preserves_estimator_and_split(self):
  original=json.loads((ROOT/'research/gamd_continuous_v1/POLICY.json').read_bytes());new=json.loads((ROOT/self.mod.FOUNDATION_BASE/'POLICY.json').read_bytes())
  self.assertEqual([{k:v for k,v in t.items() if k!='question'} for t in new['templates']],[{k:v for k,v in t.items() if k!='question'} for t in original['templates'][:2]]);self.assertEqual(new['limits'],original['limits']);self.assertFalse(new['controlled_reevaluation']['automatic_replenishment']);self.assertEqual(new['controlled_reevaluation']['max_fits'],2);self.assertEqual(new['controlled_reevaluation']['prefit_recovery'],'EXISTING_EXACT_LINKED_RECOVERY_POLICY')

class FoundationSourceCacheTests(unittest.TestCase):
 def run_cache(self,*,valid_until='2026-10-28T00:00:00Z',now='2026-10-27T12:00:00Z',changed=False,tampered=False):
  import tempfile
  from unittest.mock import patch
  from gamd import continuous_preparation as prep
  from nfl_model import continuous_gamd_gate as gate
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory)
   def write(name,value):
    p=root/name;p.write_text(json.dumps(value),encoding='utf8');return {'path':name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
   consumer={'inputs':[],'policy':{'path':'POLICY.json','sha256':'a'*64}};binding={'path':'CONSUMER.json','sha256':'b'*64}
   old={'consumer':binding,'inputs':[],'manager_imports':[],'as_of':'2026-10-27T10:00:00Z'};mr=write('MANIFEST.json',old)
   rr=write('RECEIPT.json',{'source_manifest':mr,'policy':consumer['policy'],'cohort_valid_until':valid_until})
   if changed:consumer['inputs']=[{'path':'changed.json','sha256':'c'*64}]
   if tampered:(root/'RECEIPT.json').write_text('{}',encoding='utf8')
   store=SimpleNamespace(get_setting=lambda *args:{'receipt':rr})
   with patch.object(gate,'consumer_for',return_value=(consumer,binding)),patch.object(gate,'now',return_value=now),patch('gamd.continuous_incremental.imports',return_value=[]),patch.object(prep,'seal',return_value={'new':True}) as seal:
    value=prep.source(root,store);return value,mr,seal.call_count
 def test_reuses_hash_checked_receipt_before_fixture_boundary(self):
  value,old,calls=self.run_cache();self.assertEqual(value,old);self.assertEqual(calls,0)
 def test_refreshes_at_exact_fixture_boundary(self):
  value,old,calls=self.run_cache(now='2026-10-28T00:00:00Z');self.assertEqual(value,{'new':True});self.assertEqual(calls,1)
 def test_changed_source_inputs_invalidate_cache(self):
  value,old,calls=self.run_cache(changed=True);self.assertEqual(calls,1)
 def test_tampered_receipt_is_rejected_not_reused(self):
  with self.assertRaises(ValueError):self.run_cache(tampered=True)
 def test_no_retained_future_boundaries_reuses_until_input_change(self):
  value,old,calls=self.run_cache(valid_until=None);self.assertEqual(value,old);self.assertEqual(calls,0)
