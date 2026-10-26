"""Authored action-dependency canaries: never opens retained football/model data."""
import hashlib
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from nfl_model import research_lab_gate as gate

class ActionGateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.consumer={'schema_version':'puntproof-lab-consumer-v2','source_policy':{'path':'policy.json','sha256':'1'*64},'implementation':[{'path':'adapter.py','sha256':'2'*64}],'seed_channels':{},'seed_artifacts':[],'catchup_weeks':[7]}
        self.cref={'path':'consumer.json','sha256':'3'*64}
    def tearDown(self):self.temp.cleanup()
    def write(self,name,obj):
        p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);data=gate.encoded(obj);p.write_bytes(data)
        return {'path':name,'sha256':hashlib.sha256(data).hexdigest()}
    def manifest(self,op=gate.SOURCE,purpose='SOURCE_IMPORT',week=None):
        return {'schema_version':'puntproof-lab-execution-manifest-v2','operation':op,'purpose':purpose,'consumer':self.cref,'source_policy':self.consumer['source_policy'],'season':2026,'week':week,'provider_calls':0,'cost_cents':0,'protected_access':False,'production':False,'execution_directory':gate.RUNTIME+'executions/authored','as_of':'2026-10-01T00:00:00Z','cutoff':'2026-10-01T00:00:00Z','input_files':[],'retained_inputs':[{'authored_metadata':True}]}
    def validate(self,m):
        r=self.write(m['execution_directory']+'/MANIFEST.json',m)
        return gate._manifest(self.root,m['operation'],self.consumer,self.cref,{gate.FAMILY:[r]})
    def readiness(self,m,cap):
        mr=self.write(m['execution_directory']+'/MANIFEST.json',m)
        witness=self.write('witness.json',{'status':'PASS','season':2026,'week':m['week'],'provider_calls':0,'witness_class':'LAB_ACTION_INPUTS','capability':cap,'records':1})
        checks=self.write('checks.json',{'status':'PASS','same_adapter':True,'provider_calls':0,'real_outcomes_opened':0,'passed_defects':list(gate.CAPABILITY_CHECKS[cap]),'code_sha256':{'adapter.py':'2'*64}})
        ready={'schema_version':'puntproof-lab-action-readiness-v2','status':'READY','operation':m['operation'],'capability':cap,'consumer':self.cref,'manifest':mr,'implementation':self.consumer['implementation'],'season':2026,'week':m['week'],'attempt_consumed':False,'prechecks':dict.fromkeys(gate.CAPABILITY_CHECKS[cap],True),'target_witness':witness,'authored_adapter_checks':[checks]}
        return mr,ready
    def test_source_import_needs_neither_saved_model_nor_target_results(self):
        m=self.manifest();actual,admitted,refs=self.validate(m)
        self.assertEqual(actual,m);self.assertEqual(len(admitted),1)
        mr,r=self.readiness(m,'SOURCE_IMPORT');rr=self.write('ready.json',r)
        self.assertEqual(gate._readiness(self.root,gate.SOURCE,self.consumer,self.cref,mr,m,rr)['status'],'READY')
    def test_source_import_rejects_unenumerated_and_model_inputs(self):
        m=self.manifest();m['retained_inputs']=[]
        with self.assertRaisesRegex(ValueError,'RETAINED_INPUTS_REQUIRED'):self.validate(m)
        m=self.manifest();m['model_artifacts']={'receiving':{'path':'untrusted','sha256':'f'*64}}
        with self.assertRaisesRegex(ValueError,'SOURCE_IMPORT_NO_MODEL_INPUTS'):self.validate(m)
    def test_missing_source_canary_or_changed_code_never_passes(self):
        m=self.manifest();mr,r=self.readiness(m,'SOURCE_IMPORT');r['prechecks']['missing_not_zero']=False
        with self.assertRaisesRegex(ValueError,'ACTION_PRECHECKS'):gate._readiness(self.root,gate.SOURCE,self.consumer,self.cref,mr,m,self.write('ready.json',r))
        r['prechecks']['missing_not_zero']=True
        self.consumer['implementation'][0]['sha256']='4'*64;r['implementation']=self.consumer['implementation']
        with self.assertRaisesRegex(ValueError,'CANARY_IMPLEMENTATION_DRIFT'):gate._readiness(self.root,gate.SOURCE,self.consumer,self.cref,mr,m,self.write('ready.json',r))
    def test_seed_inspection_is_exact_and_independent(self):
        m=self.manifest(gate.REPLAY,'SEED_INSPECTION')
        m['channels']=['receiving','rushing'];m['model_artifacts']={c:self.write('authored/'+c+'.json',{'synthetic_artifact':c}) for c in m['channels']}
        self.consumer['seed_channels']=dict(m['model_artifacts']);self.consumer['seed_artifacts']=list(m['model_artifacts'].values())
        self.assertEqual(self.validate(m)[0],m)
        m['fixtures']=[{'game_id':'authored'}]
        with self.assertRaisesRegex(ValueError,'INSPECTION_NO_FOOTBALL_INPUTS'):self.validate(m)
    def test_inspection_cannot_forecast_score_or_learn(self):
        m=self.manifest(gate.REPLAY,'SEED_INSPECTION');token=gate._ACTIVE.set({'root':self.root,'operation':gate.REPLAY,'manifest':m,'version':'puntproof-lab-consumer-v2','capability':'SEED_INSPECTION'})
        try:
            self.assertEqual(gate.require_phase('inspect')['phase'],'SEED_INSPECTION')
            for cap in ('forecast','response','score','learn'):
                with self.subTest(cap=cap),self.assertRaisesRegex(ValueError,'ACTION_CAPABILITY_MISMATCH'):gate.require_phase(cap)
        finally:gate._ACTIVE.reset(token)
    def test_live_charter_blocks_seed_access_before_hash(self):
        m=self.manifest(gate.REPLAY,'SEED_INSPECTION');mr=self.write(m['execution_directory']+'/MANIFEST.json',m);self.write('TASK_CHARTER.json',{'model_execution':{'inputs':{}}})
        with patch.object(gate,'consumer_for',return_value=(self.consumer,self.cref)),patch.object(gate,'_manifest',side_effect=AssertionError('must not inspect model bytes')):
            with self.assertRaisesRegex(ValueError,'LIVE_CHARTER_INPUTS_DIFFER'):
                with gate.execution_authority(self.root,gate.REPLAY,mr,{},capability='SEED_INSPECTION'):pass
    def test_finite_replay_denies_other_week_before_model_hash(self):
        m=self.manifest(gate.REPLAY,'FORECAST_CYCLE',8);mr=self.write(m['execution_directory']+'/MANIFEST.json',m)
        with patch.object(gate,'consumer_for',return_value=(self.consumer,self.cref)),patch.object(gate,'_manifest',side_effect=AssertionError('must not inspect model bytes')):
            with self.assertRaisesRegex(ValueError,'FINITE_CATCHUP_SCOPE'):
                with gate.execution_authority(self.root,gate.REPLAY,mr,{}):pass
    def test_settlement_cannot_use_forecast_capability(self):
        m=self.manifest(gate.REPLAY,'FORECAST_CYCLE',7);token=gate._ACTIVE.set({'root':self.root,'operation':gate.REPLAY,'manifest':m,'version':'puntproof-lab-consumer-v2','capability':'FORECAST'})
        try:
            for cap in ('response','score','learn'):
                with self.subTest(cap=cap),self.assertRaisesRegex(ValueError,'ACTION_CAPABILITY_MISMATCH'):gate.require_phase(cap)
        finally:gate._ACTIVE.reset(token)
    def test_future_observation_and_other_season_are_denied(self):
        m=self.manifest();m['as_of']='2099-01-01T00:00:00Z'
        with self.assertRaisesRegex(ValueError,'CUTOFF_AFTER_OBSERVATION'):self.validate(m)
        m=self.manifest();m['season']=2025
        with self.assertRaisesRegex(ValueError,'MANIFEST_SEASON_WEEK'):self.validate(m)

    def test_retrospective_subset_forecast_needs_features_not_outcomes(self):
        m=self.manifest(gate.REPLAY,'FORECAST_CYCLE',7)
        m.update(channels=['receiving'],population_scope='VERIFIED_SUBSET',classification='EXPOSED_RETROSPECTIVE_DEVELOPMENT',fixtures=[{'game_id':'2026_07_A_B','kickoff':None}])
        artifact=self.write('authored/seed.json',{'authored':True});m['model_artifacts']={'receiving':artifact}
        self.consumer['seed_artifacts']=[artifact];self.consumer['seed_channels']={'receiving':artifact}
        m['input_files']=[self.write(gate.RUNTIME+'sources/inputs.json',{'prior_week':6})]
        source={'schema_version':'puntproof-lab-source-receipt-v2','status':'PASS','operation':gate.SOURCE,'season':2026,'source_policy':self.consumer['source_policy'],'inputs':m['input_files'],'identity_conflicts':0,'duplicate_keys':0,'unknowns_preserved':True,'known_snap_denominators_verified':True,'target_weeks_verified':[7],'fixture_count':1,'roster_count':1}
        m['source_receipt']=self.write(gate.RUNTIME+'sources/receipt.json',source)
        self.assertEqual(self.validate(m)[0],m)
        mr,r=self.readiness(m,'FORECAST')
        prep={'status':'FEATURES_BUILT','real_outcomes_opened':0,'fits':0,'feature_count':228,'eligible':1,'inputs':m['input_files'],'source_receipt':m['source_receipt'],'channels':m['channels']}
        r['feature_preparation']=self.write('features.json',prep)
        self.assertEqual(gate._readiness(self.root,gate.REPLAY,self.consumer,self.cref,mr,m,self.write('ready.json',r))['status'],'READY')
        prep['feature_count']=227;r['feature_preparation']=self.write('features.json',prep)
        with self.assertRaisesRegex(ValueError,'REAL_FEATURES_NOT_READY'):gate._readiness(self.root,gate.REPLAY,self.consumer,self.cref,mr,m,self.write('ready.json',r))

    def test_input_repair_replacement_preserves_every_bound_scope(self):
        module_path=Path(__file__).resolve().parents[2]/'tools/codex_task_guard.py'
        spec=importlib.util.spec_from_file_location('authored_input_repair_guard',module_path)
        guard=importlib.util.module_from_spec(spec);spec.loader.exec_module(guard)
        op=gate.SOURCE;old='research/research_lab_v1/finish_v1/SOURCE_CONSUMER.json'
        new='research/research_lab_v1/finish_v1/input_repair_v1/SOURCE_CONSUMER.json'
        body={'schema_version':'puntproof-lab-consumer-v2','operation':op,'family':gate.FAMILY,
              'foundations':{'authored':True},'source_policy':{'authored':True},'seed_artifacts':[],
              'seed_channels':{},'capabilities':['SOURCE_IMPORT','PREPARE_FORECAST'],
              'catchup_weeks':[],'limits':{'provider_calls':0},'expires_at':'2027-01-18T00:00:00Z'}
        oldref=self.write(old,body)
        c={'allowed_paths':['research/research_lab_v1/finish_v1/input_repair_v1/'],'forbidden_paths':[],
           'authority_transition':{'receipt_path':'research/research_lab_v1/finish_v1/input_repair_v1/AUTHORITY_TRANSITION.json'}}
        change={'operation':'replace_consumer','path':'/pre_model_semantics/consumers/'+op,
                'expected_current':oldref,'artifact_path':new,'sha256_from_artifact':True}
        before={'pre_model_semantics':{'consumers':{op:oldref}}}
        owner=' '.join([*gate.OPERATIONS,gate.FAMILY,'GOVERNANCE_REPAIR','GOVERNANCE_BOOTSTRAP','AUTHORITY_TRANSITION',
                       'SOURCE_AUTHORITY_CHANGE','COMMIT','allowed_real_data_operations','pre_model_semantics',
                       'PuntProof_Research_Lab_Finish_And_Simplify.txt'])
        guard.deferred_lab_replacement(self.root,c,change,owner,copy.deepcopy(before),{},final=False)
        self.write(new,body)
        after=copy.deepcopy(before)
        guard.deferred_lab_replacement(self.root,c,change,owner,after,{},final=True)
        self.assertEqual(after['pre_model_semantics']['consumers'][op]['path'],new)
        for key in ('source_policy','foundations','seed_artifacts','seed_channels','capabilities','catchup_weeks','limits','expires_at'):
            bad=copy.deepcopy(body);bad[key]=None;self.write(new,bad)
            with self.subTest(key=key),self.assertRaisesRegex(Exception,'LAB_INPUT_REPAIR_BOUNDARIES_CHANGED'):
                guard.deferred_lab_replacement(self.root,c,change,owner,copy.deepcopy(before),{},final=True)

if __name__=='__main__':unittest.main()
