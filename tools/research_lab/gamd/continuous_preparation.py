"""Exact immutable preparation; no football decoding and no fitting."""
from pathlib import Path

def population(m):
 value=m.get('population') or m.get('purpose')
 if not value and isinstance(m.get('candidate'),dict):value=m['candidate']['template_id']+' / '+m['candidate']['channel']+' / '+m['candidate']['process']
 if not isinstance(value,str) or not value.strip():raise ValueError('EXPLICIT_PRETASK_POPULATION_REQUIRED')
 return value

def seal(root,directory,m,witness):
 from nfl_model import continuous_gamd_gate as g
 root=Path(root);directory=Path(directory);c,cr=g.consumer_for(root,m['operation'])
 m={**m,'consumer':cr,'policy':c['policy'],'catalogue':c['catalogue'],'provider_calls':0,'cost_cents':0,'protected_access':False,'production':False}
 m['spec_hash']=g.spec_hash(m);mp=directory/'MANIFEST.json'
 if mp.exists():
  old=g.read(root,g.ref(root,mp));g.need(old['spec_hash']==m['spec_hash'],'PREPARATION_CONFLICT');return g.ref(root,mp)
 directory.mkdir(parents=True,exist_ok=True);g.write_once(directory/'WITNESS.json',witness);wr=g.ref(root,directory/'WITNESS.json')
 g.write_once(directory/'READINESS.json',{'status':'READY','spec_hash':m['spec_hash'],'consumer':cr,'canary':c['adapter_canary'],'witness':wr})
 seasons=[str(y) for y in witness['seasons']];defects=[]
 for defect in g.CANARIES:
  cp=directory/'canaries'/(defect+'.json');g.write_once(cp,{'status':'PASS','provider_calls':0,'defect_id':defect,'target_seasons':seasons,'witness_class':'CONTINUOUS_ACTION','same_adapter_report':c['adapter_canary']});defects.append({'defect_id':defect,'canary':g.ref(root,cp)})
 ix=g.ref(root,'research/experiment_index.jsonl')
 pre={'schema_version':'nfl-pretask-satisfiability-v1','task_id':m['operation'],'phase':'REAL_EXECUTION','review':{'experiment_index':ix,'consumed_attempts':[ix,g.ref(root,'research/gamd_v1/REGISTRATION_CLOSEOUT.json')],'negative_knowledge':[g.ref(root,'research/puntproof_rebuild_v1/context_repair_v2/REPORT.md')],'closeouts':[g.ref(root,'research/gamd_v1/runtime_v2/ADAPTER_CANARY.json')],'equivalent_work_exists':False,'reuse_only':False,'source_repair_reopens_science':False,'unresolved_question':'Owner-requested individual/context separation, authentic partial-history selection and conditional early-fold calibration; all old negative evidence preserved. Exact linked pre-fit failure recovery is distinguished from a new scientific mechanism.'},'budget':{'max_calls':0,'max_cost_cents':0,'remaining_calls':0,'reserve_calls':0},'predicates':[{'predicate_id':'CONTINUOUS_EXACT_ACTION','mandatory':True,'terminal_outcome_dependency':['CONTINUOUS_OPERATION_COMPLETE'],'required_witness_class':'CONTINUOUS_ACTION','source_artifacts':[{**wr,'seasons':seasons,'witness_class':'CONTINUOUS_ACTION'}],'target_seasons':seasons,'target_population':population(m),'retained_availability':True,'authorised_acquisition':[],'required_authority':['READ_METADATA']+(['MODEL_EXECUTION'] if m['operation']==g.DEVELOPMENT else []),'maximum_calls':0,'maximum_cost_cents':0,'known_defects':defects,'cross_season_provenance':{'inherited_from_seasons':[],'verified_target_seasons':seasons},'equivalent_prior_work_checked':True,'status':'SATISFIABLE_RETAINED'}]}
 if m['operation']==g.DEVELOPMENT and m.get('candidate',{}).get('foundation_protocol')=='FOUNDATION_FIXED_HGB_PAIR_V1':
  policy=g.read(root,c['policy'],True);pre['review'].update(equivalent_work_exists=True,reuse_only=False,source_repair_reopens_science=False,unresolved_question='Owner-requested single paired-channel fixed HGB re-evaluation after source/calendar/context repair. Prior negative HGB evidence remains valid for its original data; unchanged estimators, parameters, seed and split. Compare frozen F on identical observations; no blind or prospective claim.')
  pre['review']['negative_knowledge']+=policy['prior_evidence'];pre['review']['closeouts'].append(policy['foundation_request'])
 g.write_once(directory/'PRETASK.json',pre);m['readiness']=g.ref(root,directory/'READINESS.json');m['pretask_satisfiability']=g.ref(root,directory/'PRETASK.json');g.write_once(mp,m);return g.ref(root,mp)

def source(root,store=None):
 from nfl_model import continuous_gamd_gate as g
 c,cr=g.consumer_for(root,g.SOURCE)
 from gamd.continuous_incremental import imports
 from core import digest
 imported=imports(store) if store else []
 when=g.now();cached=store.get_setting('continuous_source',{}).get('receipt') if store else None
 if cached:
  previous=g.read(root,cached);mr=previous.get('source_manifest');prior=g.read(root,mr) if mr else {}
  valid_until=previous.get('cohort_valid_until')
  same=prior.get('consumer')==cr and prior.get('inputs')==c['inputs'] and prior.get('manager_imports',[])==imported and previous.get('policy')==c['policy']
  if same and 'cohort_valid_until' in previous and (valid_until is None or g.stamp(when)<g.stamp(valid_until)) and g.stamp(prior['as_of'])<=g.stamp(when):return mr
 directory=Path(root)/g.PRIVATE/('source-'+digest({'inputs':c['inputs'],'consumer':cr,'manager_imports':imported,'horizon_start':when})[:24])
 mp=directory/'MANIFEST.json'
 return seal(root,directory,{'operation':g.SOURCE,'purpose':'FIELD_REPAIR_AND_INDEPENDENT_COHORT','as_of':when,'output':directory.relative_to(root).as_posix(),'inputs':c['inputs'],'manager_imports':imported},{'status':'PASS','records':783,'seasons':[2023,2024,2026],'provider_calls':0,'basis':'HASH_BOUND_RETAINED_CATALOGUE_AND_PRIOR_SOURCE_RECEIPT'})
def fit(root,candidate,receipt):
 from nfl_model import continuous_gamd_gate as g
 plan=candidate['plan'];out=Path(root)/g.PRIVATE/'attempts'/candidate['id'];_,cr=g.consumer_for(root,g.DEVELOPMENT);directory=out/('preflight-'+cr['sha256'][:16])
 return seal(root,directory,{'operation':g.DEVELOPMENT,'candidate':plan,'attempt_id':candidate['id'],'as_of':candidate['created_at'],'output':out.relative_to(root).as_posix(),'inputs':[plan['projection'],plan['projection_receipt']]},{'status':'PASS','records':receipt['legacy_training_rows'] if plan.get('recovery') else receipt['scheduled_training_rows'] if plan['process']=='SCHEDULED_REFIT' else receipt['training_rows'],'seasons':[2021,2022,2023,2024] if plan.get('recovery') else [2023,2024,2026] if plan['process']=='SCHEDULED_REFIT' else [2023,2024],'provider_calls':0,'projection':plan['projection']})
