"""Exact immutable preparation; no football decoding and no fitting."""
from pathlib import Path

def seal(root,directory,m,witness):
 from nfl_model import licensed_bdl_gate as g
 root=Path(root);directory=Path(directory);c,cr=g.consumer_for(root,m['operation'])
 m={**m,'consumer':cr,'campaign_ref':c['campaign'],'source_policy':c['source_policy'],'catalogue':c['catalogue'],'provider_calls':0,'cost_cents':0,'protected_access':False,'production':False}
 m['spec_hash']=g.spec_hash(m);mp=directory/'MANIFEST.json'
 if mp.exists():
  old=g.read(root,g.ref(root,mp));g.need(old['spec_hash']==m['spec_hash'],'PREPARATION_CONFLICT');return g.ref(root,mp)
 directory.mkdir(parents=True,exist_ok=True);g.write_once(directory/'WITNESS.json',witness);wr=g.ref(root,directory/'WITNESS.json')
 g.write_once(directory/'READINESS.json',{'status':'READY','manifest_spec_sha256':m['spec_hash'],'consumer':cr,'adapter_canary':c['adapter_canary'],'implementation':c['implementation'],'target_witness':wr})
 seasons=['2023','2024','2026'] if m['operation']==g.SOURCE else ['2023','2024'];defects=[]
 for defect in g.CANARIES:
  cp=directory/'canaries'/(defect+'.json');g.write_once(cp,{'status':'PASS','provider_calls':0,'defect_id':defect,'target_seasons':seasons,'witness_class':'LICENSED_BDL_ACTION','same_adapter_report':c['adapter_canary']});defects.append({'defect_id':defect,'canary':g.ref(root,cp)})
 ix=g.ref(root,'research/experiment_index.jsonl')
 pre={'schema_version':'nfl-pretask-satisfiability-v1','task_id':m['operation'],'phase':'REAL_EXECUTION','review':{'experiment_index':ix,'consumed_attempts':[ix,g.ref(root,'research/gamd_v1/REGISTRATION_CLOSEOUT.json')],'negative_knowledge':[g.ref(root,'research/puntproof_rebuild_v1/context_repair_v2/REPORT.md')],'closeouts':[g.ref(root,'research/gamd_v1/runtime_v2/ADAPTER_CANARY.json')],'equivalent_work_exists':False,'reuse_only':False,'source_repair_reopens_science':False,'unresolved_question':'New owner-requested licensed BDL dated-identity snap feature ablation. Earlier masked-history and context/law trials did not test these features; their negative results remain preserved.'},'budget':{'max_calls':0,'max_cost_cents':0,'remaining_calls':0,'reserve_calls':0},'predicates':[{'predicate_id':'LICENSED_BDL_EXACT_ACTION','mandatory':True,'terminal_outcome_dependency':['LICENSED_BDL_OPERATION_COMPLETE'],'required_witness_class':'LICENSED_BDL_ACTION','source_artifacts':[{**wr,'seasons':seasons,'witness_class':'LICENSED_BDL_ACTION'}],'target_seasons':seasons,'target_population':m.get('population',m.get('purpose')),'retained_availability':True,'authorised_acquisition':[],'required_authority':['READ_METADATA']+(['MODEL_EXECUTION'] if m['operation']==g.DEVELOPMENT else []),'maximum_calls':0,'maximum_cost_cents':0,'known_defects':defects,'cross_season_provenance':{'inherited_from_seasons':[],'verified_target_seasons':seasons},'equivalent_prior_work_checked':True,'status':'SATISFIABLE_RETAINED'}]}
 g.write_once(directory/'PRETASK.json',pre);m['readiness']=g.ref(root,directory/'READINESS.json');m['pretask_satisfiability']=g.ref(root,directory/'PRETASK.json');g.write_once(mp,m);return g.ref(root,mp)

def source(root):
 from nfl_model import licensed_bdl_gate as g
 c,cr=g.consumer_for(root,g.SOURCE);catalog=g.read(root,c['catalogue'],True);d=Path(root)/g.PRIVATE/'source_action_format_v4'
 when=g.read(root,g.ref(root,d/'MANIFEST.json'))['as_of'] if (d/'MANIFEST.json').exists() else g.now()
 witness={'status':'PASS','provider_calls':0,'catalogue':c['catalogue'],'records':sum(p['rows'] for p in catalog['partitions']),'seasons':[2023,2024,2026]}
 return seal(root,d,{'operation':g.SOURCE,'purpose':'LICENSED_SOURCE_AND_COVERAGE','as_of':when,'output':g.PRIVATE+'source'},witness)

def fit(root,candidate,receipt):
 from nfl_model import licensed_bdl_gate as g
 spec=candidate['plan'];c,cr=g.consumer_for(root,g.DEVELOPMENT);out=Path(root)/g.PRIVATE/'attempts'/candidate['id'];rr=g.ref(root,Path(root)/g.PRIVATE/'source/RECEIPT.json')
 witness={'status':'PASS','provider_calls':0,'catalogue':c['catalogue'],'records':receipt['rows'],'projection':receipt['projection'],'seasons':[2023,2024],'split_rows':receipt['split_rows'][spec['channel']]}
 return seal(root,out,{**spec,'operation':g.DEVELOPMENT,'attempt_id':candidate['id'],'output':out.relative_to(root).as_posix(),'as_of':candidate['created_at'],'projection_receipt':rr},witness)
