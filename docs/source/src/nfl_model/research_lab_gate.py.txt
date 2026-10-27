"""Exact conditional Research Lab admission; no source/model work on import.

These local custody controls are not an OS sandbox. Registration alone never
establishes source or runner readiness. Target-season evidence and the actual
committed adapter remain mandatory, with no generic operation exception.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

FAMILY = 'puntproof_research_lab_v1'
SOURCE = 'PUNTPROOF_RESEARCH_LAB_SOURCE_SYNC_V1'
REPLAY = 'PUNTPROOF_RESEARCH_LAB_V3_REPLAY_V1'
FORWARD = 'PUNTPROOF_RESEARCH_LAB_V3_FORWARD_V1'
DEVELOPMENT = 'PUNTPROOF_RESEARCH_LAB_DEVELOPMENT_V1'
OPERATIONS = (SOURCE, REPLAY, FORWARD, DEVELOPMENT)
MODEL_OPERATIONS = (REPLAY, FORWARD, DEVELOPMENT)
BASE = 'research/research_lab_v1/admission_v1/'
RUNTIME = 'data/private/research-lab/'
CONSUMERS = dict(zip(OPERATIONS, (BASE+x+'_CONSUMER.json' for x in ('SOURCE','REPLAY','FORWARD','DEVELOPMENT'))))
ENCODING_REPAIR_CONSUMERS = {op: BASE+'encoding_repair_v1/'+Path(path).name for op,path in CONSUMERS.items()}
FINISH_BASE = 'research/research_lab_v1/finish_v1/'
FINISH_CONSUMERS = {op: FINISH_BASE+Path(path).name for op,path in CONSUMERS.items()}
INPUT_REPAIR_CONSUMERS = {op: FINISH_BASE+'input_repair_v1/'+Path(path).name for op,path in CONSUMERS.items()}
RECOVERY_BASE = FINISH_BASE+'forecast_recovery_v1/'
RECOVERY_CONSUMERS = {op: RECOVERY_BASE+Path(path).name for op,path in CONSUMERS.items()}
RECOVERY_CONTRACT = RECOVERY_BASE+'RECOVERY_CONTRACT.json'
TYPE_REPAIR_BASE = FINISH_BASE+'forecast_type_repair_v1/'
TYPE_REPAIR_CONSUMERS = {op: TYPE_REPAIR_BASE+Path(path).name for op,path in CONSUMERS.items()}
TYPE_REPAIR_CONTRACT = TYPE_REPAIR_BASE+'RECOVERY_CONTRACT.json'
AUTOMATION_CONSUMERS = {op: 'research/research_lab_v1/automation_v1/'+Path(path).name for op,path in CONSUMERS.items()}
RUNTIME_CONSUMERS = {op: 'research/research_lab_v1/runtime_v1/'+Path(path).name for op,path in CONSUMERS.items()}
GAMD_COMPAT_CONSUMERS = {op: 'research/research_lab_v1/gamd_compat_v1/'+Path(path).name for op,path in CONSUMERS.items()}
GAMD_RUNTIME_COMPAT_CONSUMERS = {op: 'research/research_lab_v1/gamd_compat_v2/'+Path(path).name for op,path in CONSUMERS.items()}
CAPABILITIES = {SOURCE: ('SOURCE_IMPORT','PREPARE_FORECAST'), REPLAY: ('SEED_INSPECTION','FORECAST','SETTLE'), FORWARD: ('FORECAST','SETTLE'), DEVELOPMENT: ('FORECAST','SETTLE')}
CAPABILITY_CHECKS = {'SOURCE_IMPORT': ('source_path_hash_custody','source_projection_scope','missing_not_zero'), 'PREPARE_FORECAST': ('missing_not_zero','temporal_identity','same_week_poisoning'), 'SEED_INSPECTION': ('seed_hash_before_load','seed_channel_schema','inspection_no_fit_score'), 'FORECAST': ('missing_not_zero','temporal_identity','same_week_poisoning','partial_scope_labels'), 'SETTLE': ('positive_snap_zero_production','missing_not_zero','duplicate_settlement','whole_week_completion','artifact_roundtrip')}
INSPECTION_STATES = ('PRETASK_VALID','INPUTS_VERIFIED','SEED_LOADED','INSPECTION_COMPLETE')
MODEL_STATES = ('PRETASK_VALID','INPUTS_VERIFIED','FORECAST_PACKAGES_SEALED',
    'GLOBAL_EXECUTION_SEAL_VERIFIED','ATTEMPT_CONSUMED','RESPONSE_ACCESS_ENABLED',
    'RESPONSES_COMPLETE','SCORING_COMPLETE','AUTHORITY_CLOSED')
FORWARD_STATES = MODEL_STATES[:-1]+('MODEL_STATE_UPDATED','AUTHORITY_CLOSED')
DEFECTS = ('positive_snap_zero_production','missing_not_zero','qb_rbfb_lateral_credit',
    'complete_prior_week_context','temporal_identity','same_week_poisoning',
    'duplicate_settlement','artifact_roundtrip','retained_offer_not_illustration')
_ACTIVE = ContextVar('puntproof_research_lab_execution', default=None)
UTC = timezone.utc


def need(ok, code):
    if not ok:
        raise ValueError('RESEARCH_LAB:'+code)


def timestamp(value):
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    need(result.tzinfo is not None,'TIMEZONE_REQUIRED')
    return result.astimezone(UTC)


def encoded(value):
    return (json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()


def reference(root, ref):
    need(isinstance(ref,dict) and set(ref)=={'path','sha256'},'EXACT_REFERENCE')
    name=ref['path']
    need(isinstance(name,str) and name and '\\' not in name and ':' not in name
         and not name.startswith('/') and all(p not in ('','.','..') for p in name.split('/')),'REFERENCE_PATH')
    need(isinstance(ref['sha256'],str) and re.fullmatch('[a-f0-9]{64}',ref['sha256']) is not None,'REFERENCE_HASH')
    root=Path(root).resolve(); path=root/name
    need(path.resolve().is_relative_to(root),'REFERENCE_ESCAPE')
    for part in (path,*path.parents):
        need(not part.is_symlink() and not getattr(part,'is_junction',lambda:False)(),'REFERENCE_LINK')
        if part==root:break
    return path


def checked(root, ref, *, committed=False):
    path=reference(root,ref); data=path.read_bytes()
    need(hashlib.sha256(data).hexdigest()==ref['sha256'],'HASH_DRIFT:'+ref['path'])
    if committed:
        proc=subprocess.run(['git','-C',str(root),'show','HEAD:'+ref['path']],capture_output=True,timeout=20,
            **({'creationflags':subprocess.CREATE_NO_WINDOW} if hasattr(subprocess,'CREATE_NO_WINDOW') else {}))
        need(proc.returncode==0 and hashlib.sha256(proc.stdout).hexdigest()==ref['sha256'],'COMMITTED_BINDING:'+ref['path'])
    return data


def value(root,ref,*,committed=False):
    return json.loads(checked(root,ref,committed=committed))


def consumer_for(root, operation, authority=None):
    from nfl_model import stage
    root=Path(root).resolve(); authority=authority or stage.current_authority()
    need(operation in OPERATIONS and operation in authority['allowed_real_data_operations'],'OPERATION_NOT_REGISTERED')
    need(authority['CURRENT_AUTHORISED_STAGE']==8 and authority['PROTECTED_EVALUATION']=='DISABLED'
         and authority['REAL_DATA_MODELLING']=='BLOCKED' and authority['LATER_STAGE_REAL_DATA_MODELLING']=='BLOCKED','GLOBAL_BOUNDARIES')
    semantics=authority['pre_model_semantics']
    need(semantics['operations'].get(operation)==[FAMILY],'EXACT_FAMILY')
    ref=semantics['consumers'].get(operation)
    need(ref is not None and ref['path'] in (CONSUMERS[operation],ENCODING_REPAIR_CONSUMERS[operation],FINISH_CONSUMERS[operation],INPUT_REPAIR_CONSUMERS[operation],RECOVERY_CONSUMERS[operation],TYPE_REPAIR_CONSUMERS[operation],AUTOMATION_CONSUMERS[operation],RUNTIME_CONSUMERS[operation],GAMD_COMPAT_CONSUMERS[operation],GAMD_RUNTIME_COMPAT_CONSUMERS[operation],'research/research_lab_v1/bdl_compat_v1/'+Path(CONSUMERS[operation]).name,'research/research_lab_v1/bdl_compat_v2/'+Path(CONSUMERS[operation]).name,'research/research_lab_v1/bdl_format_v3/'+Path(CONSUMERS[operation]).name,'research/research_lab_v1/bdl_format_v4/'+Path(CONSUMERS[operation]).name,'research/research_lab_v1/bdl_closeout_v5/'+Path(CONSUMERS[operation]).name,'research/research_lab_v1/continuous_compat_v1/'+Path(CONSUMERS[operation]).name,'research/research_lab_v1/continuous_compat_v2/'+Path(CONSUMERS[operation]).name,'research/research_lab_v1/continuous_compat_v3/'+Path(CONSUMERS[operation]).name,'research/research_lab_v1/continuous_compat_v4/'+Path(CONSUMERS[operation]).name,'research/research_lab_v1/continuous_compat_v5/'+Path(CONSUMERS[operation]).name,'research/research_lab_v1/continuous_compat_v6/'+Path(CONSUMERS[operation]).name),'EXACT_CONSUMER')
    consumer=value(root,ref,committed=True)
    need(consumer['schema_version'] in ('puntproof-lab-consumer-v1','puntproof-lab-consumer-v2') and consumer['operation']==operation
         and consumer['family']==FAMILY and consumer['status']=='REGISTERED_CONDITIONAL','CONSUMER_SCHEMA')
    need(consumer['foundations']==semantics['certificates'][FAMILY],'FOUNDATIONS')
    for item in consumer['foundations'].values():checked(root,item,committed=True)
    need(consumer['limits']=={'external_access':'NONE','provider_calls':0,'cost_cents':0,
        'protected_access':False,'production':False,'seasons':[2026],'weeks':list(range(1,19)),
        'max_candidates_per_week':2,'max_candidate_seconds':300,'concurrency':1},'CONSUMER_LIMITS')
    need(consumer['implementation'] and len({x['path'] for x in consumer['implementation']})==len(consumer['implementation']),'IMPLEMENTATION_BINDING')
    for item in consumer['implementation']:checked(root,item,committed=True)
    need(isinstance(consumer['seed_artifacts'],list) and consumer['seed_artifacts'],'SEED_ARTIFACTS_REQUIRED')
    if consumer['schema_version']=='puntproof-lab-consumer-v1':
        for item in consumer['seed_artifacts']:checked(root,item)
    else:
        paths={r['path'] for r in consumer['implementation']}
        need({'src/nfl_model/research_lab_gate.py','tools/codex_task_guard.py','src/nfl_model/stage.py','src/nfl_model/data/semantic_certificate.py'}<=paths,'CORE_IMPLEMENTATION_BINDING')
        need(not any(p.startswith('tools/research_lab/static/') or p=='tools/research_lab/server.py' for p in paths),'PRESENTATION_NOT_SCIENTIFIC_BINDING')
        need(consumer['capabilities']==list(CAPABILITIES[operation]),'EXACT_CAPABILITIES')
    policy=value(root,consumer['source_policy'],committed=True)
    need(policy['schema_version']=='puntproof-lab-retained-source-policy-v1'
         and policy['operation']==SOURCE and policy['seasons']==[2026]
         and policy['provider_calls']==0 and policy['protected_access'] is False
         and policy['production'] is False,'SOURCE_POLICY_SCOPE')
    registry=json.loads((root/'config/source_registry.json').read_bytes()).get(FAMILY,{})
    need(registry.get('operation')==SOURCE and registry.get('source_policy')==consumer['source_policy']
         and registry.get('consumer')==semantics['consumers'].get(SOURCE),'SOURCE_REGISTRY_BINDING')
    need(timestamp(consumer['expires_at'])>datetime.now(UTC),'ADMISSION_EXPIRED')
    return consumer,ref


def _manifest(root,operation,consumer,consumer_ref,inputs):
    if operation==DEVELOPMENT and inputs and value(root,inputs[FAMILY][0]).get('purpose')=='PAIRED_COMPARISON':
        from nfl_model.research_lab_pair_gate import validate_pair_manifest
        return validate_pair_manifest(root,consumer,consumer_ref,inputs[FAMILY][0])
    if consumer.get('schema_version')=='puntproof-lab-consumer-v2':
        return _finish_manifest(root,operation,consumer,consumer_ref,inputs)
    need(isinstance(inputs,dict) and set(inputs)=={FAMILY} and isinstance(inputs[FAMILY],list)
         and len(inputs[FAMILY])==1,'ONE_EXECUTION_MANIFEST_REQUIRED')
    ref=inputs[FAMILY][0]
    need(ref['path'].startswith(RUNTIME+'executions/') and ref['path'].endswith('/MANIFEST.json'),'MANIFEST_SCOPE')
    manifest=value(root,ref)
    need(manifest['schema_version']=='puntproof-lab-execution-manifest-v1' and manifest['operation']==operation
         and manifest['consumer']==consumer_ref and manifest['source_policy']==consumer['source_policy'],'MANIFEST_BINDING')
    need(manifest['season']==2026 and type(manifest['week']) is int and 1<=manifest['week']<=18,'MANIFEST_SEASON_WEEK')
    need(manifest['provider_calls']==manifest['cost_cents']==0 and manifest['protected_access'] is False
         and manifest['production'] is False,'MANIFEST_CAPABILITIES')
    need(manifest['execution_directory']==str(Path(ref['path']).parent).replace('\\','/'),'EXECUTION_DIRECTORY')
    need(timestamp(manifest['cutoff'])<=timestamp(manifest['as_of']),'CUTOFF_AFTER_OBSERVATION')
    admitted={str(reference(root,ref).resolve()):ref['sha256']}; refs=[ref]
    for item in manifest['input_files']:
        need(item['path'].startswith(RUNTIME+'sources/'),'MODEL_INPUT_PATH')
        checked(root,item); path=str(reference(root,item).resolve())
        need(path not in admitted,'DUPLICATE_INPUT'); admitted[path]=item['sha256']; refs.append(item)
    if operation in MODEL_OPERATIONS:
        need(manifest['input_files'],'MODEL_INPUTS_EMPTY')
        artifacts=manifest['model_artifacts']
        need(isinstance(artifacts,dict) and set(artifacts)=={'receiving','rushing'},'TWO_CHANNEL_ARTIFACTS_REQUIRED')
        for channel,artifact in artifacts.items():
            checked(root,artifact)
            if artifact not in consumer['seed_artifacts']:
                need(artifact['path'].startswith(RUNTIME+'sessions/'),'UNADMITTED_MODEL_ARTIFACT')
                parent_ref=manifest['parent_state_receipts'][channel]
                parent=value(root,parent_ref)
                need(parent['schema_version']=='puntproof-lab-model-state-receipt-v1'
                     and parent['status']=='AUTHORITY_CLOSED' and parent['operation'] in (REPLAY,FORWARD)
                     and parent['model_artifact']==artifact and parent['seed_artifact'] in consumer['seed_artifacts']
                     and parent['channel']==channel and parent['season']==2026
                     and parent['history_through_week']<manifest['week']
                     and parent['implementation']==consumer['implementation'],'MODEL_PARENT_CUSTODY')
                refs.append(parent_ref)
            admitted[str(reference(root,artifact).resolve())]=artifact['sha256']; refs.append(artifact)
        fixtures=manifest['fixtures']
        need(isinstance(fixtures,list) and fixtures and len({f['game_id'] for f in fixtures})==len(fixtures),'EXACT_FIXTURE_SET_REQUIRED')
        need(all(timestamp(f['kickoff']) for f in fixtures),'FIXTURE_KICKOFF_REQUIRED')
        source=value(root,manifest['source_receipt'])
        need(source['schema_version']=='puntproof-lab-source-receipt-v1' and source['status']=='PASS'
             and source['operation']==SOURCE and source['season']==2026
             and source['source_policy']==consumer['source_policy']
             and source['inputs']==manifest['input_files'],'SOURCE_NOT_READY')
        need(source['identity_conflicts']==source['duplicate_keys']==0 and source['history_complete'] is True
             and source['snap_denominator_verified'] is True and source['unknowns_preserved'] is True,'SOURCE_SEMANTICS_INCOMPLETE')
        need(manifest['week'] in source['target_weeks_verified'],'TARGET_WEEK_UNWITNESSED')
        refs.append(manifest['source_receipt'])
        need(source['fixture_count']>0 and source['roster_count']>0,'FIXTURE_OR_ROSTER_MISSING')
        if operation==FORWARD:
            need(manifest['classification']=='GENUINE_PROSPECTIVE' and source['pregame_snapshots_verified'] is True,'PREGAME_SNAPSHOT_REQUIRED')
            kickoff=timestamp(manifest['first_kickoff'])
            need(kickoff==min(timestamp(f['kickoff']) for f in fixtures),'FIRST_KICKOFF_MISMATCH')
            need(timestamp(manifest['as_of'])<kickoff and timestamp(source['latest_observed_at'])<=timestamp(manifest['cutoff']),'FORWARD_CLOCK')
        else:
            need(manifest['classification']=='EXPOSED_RETROSPECTIVE_DEVELOPMENT','RETROSPECTIVE_LABEL_REQUIRED')
        if operation==DEVELOPMENT:
            spec=value(root,manifest['candidate_spec'])
            need(spec['schema_version']=='puntproof-lab-candidate-spec-v1' and spec['frozen'] is True
                 and type(spec['max_trials']) is int and spec['max_trials']==1
                 and type(spec['max_seconds']) in (int,float) and 0<spec['max_seconds']<=300 and spec['provider_calls']==0
                 and spec['protected_access'] is False and spec['candidate_ordinal'] in (1,2)
                 and spec['week_fingerprint']==source['whole_week_fingerprint'],'CANDIDATE_BUDGET')
            need(all(spec.get(k) for k in ('hypothesis','mechanism','features','algorithm','hyperparameters','seed',
                 'time_partitions','endpoint','matched_population','selection_rule','uncertainty_rule','duplicate_review')),'CANDIDATE_SPEC_INCOMPLETE')
            review=value(root,spec['duplicate_review'])
            need(review['status']=='NEW_UNCONSUMED_MECHANISM' and review['consumed_equivalent'] is False
                 and review['fingerprint']==spec['week_fingerprint'],'CONSUMED_OR_DUPLICATE_CANDIDATE')
            refs.extend([manifest['candidate_spec'],spec['duplicate_review']])
    return manifest,admitted,refs


def validate_manifest(root,operation,authority,inputs):
    need(operation in MODEL_OPERATIONS,'MODEL_OPERATION_REQUIRED')
    from nfl_model import stage
    stage.require_model_operation(operation,authority)
    consumer,ref=consumer_for(root,operation,authority)
    _,admitted,refs=_manifest(root,operation,consumer,ref,inputs)
    return admitted,[ref,*consumer['foundations'].values(),consumer['source_policy'],*consumer['implementation'],*refs]


def _readiness(root,operation,consumer,ref,manifest_ref,manifest,readiness_ref):
    if consumer.get('schema_version')=='puntproof-lab-consumer-v2':
        return _finish_readiness(root,operation,consumer,ref,manifest_ref,manifest,readiness_ref)
    ready=value(root,readiness_ref)
    need(ready['schema_version']=='puntproof-lab-runner-readiness-v1' and ready['status']=='READY'
         and ready['operation']==operation and ready['consumer']==ref and ready['manifest']==manifest_ref
         and ready['implementation']==consumer['implementation'],'RUNNER_NOT_READY')
    need(ready['season']==2026 and ready['week']==manifest['week'] and ready['attempt_consumed'] is False,'READINESS_TARGET_OR_ATTEMPT')
    witness=value(root,ready['target_week_witness'])
    need(witness['season']==2026 and witness['week']==manifest['week'] and witness['status']=='PASS'
         and witness['records']>0 and witness['provider_calls']==0,'TARGET_WEEK_WITNESS')
    tests=ready['authored_adapter_checks']
    need(tests,'AUTHORED_ADAPTER_CHECKS_REQUIRED')
    covered=set()
    for item in tests:
        test=value(root,item)
        need(test['status']=='PASS' and test['same_adapter'] is True and test['provider_calls']==0
             and test['real_outcomes_opened']==0,'ADAPTER_CHECK_FAILURE')
        covered.update(test['passed_defects'])
    need(set(DEFECTS)<=covered,'KNOWN_DEFECT_CANARY_MISSING')
    if operation in MODEL_OPERATIONS:
        preparation=value(root,ready['real_zero_outcome_preparation'])
        need(preparation['status']=='PASS' and preparation['mode']=='REAL_ZERO_OUTCOME_PREPARATION'
             and preparation['manifest']==manifest_ref and preparation['implementation']==consumer['implementation']
             and preparation['real_outcomes_opened']==preparation['scores']==0
             and preparation['attempt_consumed'] is False and preparation['committed_runner'] is True,'REAL_RUNNER_NOT_READY')
    return ready


def validate_lifecycle_events(events,operation):
    states=FORWARD_STATES if operation==FORWARD else MODEL_STATES
    need(isinstance(events,list) and len(events)<=len(states),'LIFECYCLE_LENGTH')
    prior=None
    for index,event in enumerate(events):
        need(event['sequence']==index and event['state']==states[index] and event['previous_hash']==prior,'LIFECYCLE_ORDER')
        unsigned={k:v for k,v in event.items() if k!='sha256'}
        need(hashlib.sha256(encoded(unsigned)).hexdigest()==event['sha256'],'LIFECYCLE_HASH')
        timestamp(event['at']); prior=event['sha256']
    return events[-1]['state'] if events else None


def _events(root,manifest):
    directory=lifecycle_directory(root,manifest)
    events=[json.loads(p.read_bytes()) for p in sorted(directory.glob('*.json'))] if directory.exists() else []
    return events,validate_lifecycle_events(events,manifest['operation'])


def check_lifecycle(root,charter):
    operation=charter.get('model_execution',{}).get('operation')
    if operation in MODEL_OPERATIONS:
        consumer,ref=consumer_for(root,operation)
        if consumer.get('schema_version')=='puntproof-lab-consumer-v2':
            return _finish_lifecycle(root,charter,consumer,ref)
    operation=charter['model_execution']['operation']
    need(operation in MODEL_OPERATIONS and charter['stage_authority']['operations']==[operation],'LIFECYCLE_OPERATION')
    need(charter['model_execution']['seasons']==[2026] and charter['external_access']=='NONE'
         and not any(charter.get(k,False) for k in ('protected_result_access','paid_access','stage9_authorised','push_authorised')),'LIFECYCLE_SCOPE')
    ref=charter.get('preexecution_lifecycle'); need(ref is not None,'LIFECYCLE_REQUIRED')
    plan=value(root,ref)
    consumer,consumer_ref=consumer_for(root,operation)
    inputs=charter['model_execution']['inputs']
    manifest,_,_=_manifest(root,operation,consumer,consumer_ref,inputs)
    states=list(FORWARD_STATES if operation==FORWARD else MODEL_STATES)
    need(plan['operation']==operation and plan['states']==states and plan['attempts']==1
         and plan['retry_after_consumption'] is False and plan['manifest']==inputs[FAMILY][0]
         and plan['implementation']==consumer['implementation'],'LIFECYCLE_BINDING')
    _readiness(root,operation,consumer,consumer_ref,inputs[FAMILY][0],manifest,charter['protected_runner_readiness'])
    _,phase=_events(root,manifest)
    need(phase!='AUTHORITY_CLOSED','ATTEMPT_CLOSED')
    return {'status':'PASS','operation':operation,'phase':phase or 'PREPARATION'}


def _pretask(root,context):
    """The recurring plan is separately bound; a closed developer charter is never reused."""
    from nfl_model import stage
    import importlib.util
    path=Path(root)/'tools/codex_task_guard.py'
    spec=importlib.util.spec_from_file_location('lab_runtime_guard_primitives',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    manifest=context['manifest']
    inspection=_capability(manifest)=='SEED_INSPECTION'
    declaration={'task_id':manifest['operation'],'task_class':'ENGINEERING' if inspection else 'SOURCE_ADMISSION' if manifest['operation']==SOURCE else 'MODEL_EXPERIMENT','allowed_actions':['READ_METADATA']+(['MODEL_EXECUTION'] if manifest['operation'] in MODEL_OPERATIONS and not inspection else []),
        'completion_states':['LAB_RUNTIME_OPERATION_COMPLETE'],'external_access':'NONE',
        'paid_access':False,'protected_result_access':False,
        'pretask_satisfiability':manifest['pretask_satisfiability']}
    return module.satisfiability(Path(root),declaration,execution=True)


def scheduled_execution(root,operation):
    if (Path(root)/'TASK_CHARTER.json').exists():return None
    active=_ACTIVE.get()
    if active is None or operation not in (SOURCE,FORWARD,REPLAY,DEVELOPMENT) or active['operation']!=operation or active['root']!=Path(root).resolve():return None
    consumer,ref=consumer_for(root,operation)
    need(ref==active['consumer'],'RUNTIME_CONSUMER_CHANGED')
    if operation==DEVELOPMENT:
        from nfl_model.research_lab_pair_gate import validate_pair_manifest
        validate_pair_manifest(Path(root),consumer,ref,active['manifest_ref'])
        _pretask(root,active)
        return {'status':'PASS','phase':'SCOPED_AUTOMATIC_PAIRED_COMPARISON','operation':operation,'consumer':ref,'provider_calls':0,'cost_cents':0}
    if operation==REPLAY:
        need(consumer['schema_version']=='puntproof-lab-consumer-v2','REPLAY_RUNTIME_NOT_ADMITTED')
        cap=_capability(active['manifest'])
        need(cap=='SEED_INSPECTION' or active['manifest']['week'] in consumer['catchup_weeks'],'FINITE_CATCHUP_SCOPE')
    _readiness(root,operation,consumer,ref,active['manifest_ref'],active['manifest'],active['readiness_ref'])
    _pretask(root,active)
    return {'status':'PASS','phase':'SCOPED_RECURRING_LAB','operation':operation,'consumer':ref,'provider_calls':0,'cost_cents':0}


@contextmanager
def execution_authority(root,operation,manifest_ref,readiness_ref,*,capability=None):
    root=Path(root).resolve(); consumer,ref=consumer_for(root,operation)
    raw=value(root,manifest_ref)
    selected=capability or _default_capability(raw)
    need(selected in CAPABILITIES[operation],'CAPABILITY_NOT_ADMITTED')
    if (root/'TASK_CHARTER.json').exists() and operation in MODEL_OPERATIONS:
        live=json.loads((root/'TASK_CHARTER.json').read_bytes())
        need(live.get('model_execution',{}).get('inputs')=={FAMILY:[manifest_ref]},'LIVE_CHARTER_INPUTS_DIFFER')
    if operation==REPLAY and consumer.get('schema_version')=='puntproof-lab-consumer-v2' and not (root/'TASK_CHARTER.json').exists():
        need(selected=='SEED_INSPECTION' or raw['week'] in consumer['catchup_weeks'],'FINITE_CATCHUP_SCOPE')
    pending=_ACTIVE.set({'root':root,'operation':operation,'manifest_ref':manifest_ref,'capability':selected})
    inputs={FAMILY:[manifest_ref]}
    try:
        manifest,_,_=_manifest(root,operation,consumer,ref,inputs)
        _readiness(root,operation,consumer,ref,manifest_ref,manifest,readiness_ref)
    finally:_ACTIVE.reset(pending)
    if operation==DEVELOPMENT or operation==REPLAY and consumer['schema_version']=='puntproof-lab-consumer-v1':need((root/'TASK_CHARTER.json').exists(),'ONE_SHOT_CHARTER_REQUIRED')
    if (root/'TASK_CHARTER.json').exists() and operation in MODEL_OPERATIONS:
        charter=json.loads((root/'TASK_CHARTER.json').read_bytes())
        need(charter.get('model_execution',{}).get('inputs')==inputs,'LIVE_CHARTER_INPUTS_DIFFER')
    token=_ACTIVE.set({'root':root,'operation':operation,'consumer':ref,'manifest_ref':manifest_ref,
        'manifest':manifest,'readiness_ref':readiness_ref,'source_policy':consumer['source_policy'],'capability':selected,'version':consumer['schema_version']})
    try:
        from nfl_model import stage
        if operation==SOURCE:
            stage.require_stage(operation)
            yield dict(manifest)
        else:
            from nfl_model.data.semantic_certificate import model_inputs
            with model_inputs(operation,inputs=inputs):yield dict(manifest)
    finally:_ACTIVE.reset(token)


def require_retained_source(root,path,sha256,dataset,season):
    active=_ACTIVE.get(); root=Path(root).resolve()
    need(active is not None and active['root']==root and active['operation']==SOURCE,'SOURCE_CONTEXT_REQUIRED')
    need(season==2026,'SOURCE_SEASON_DENIED')
    policy=value(root,active['source_policy'])
    candidate=Path(path).resolve()
    matches=[r for r in policy.get('retained_inputs',[]) if Path(r['path']).resolve()==candidate
        and r['sha256']==sha256 and r['dataset']==dataset and r['season']==season]
    if not matches:
        entries=active.get('manifest',{}).get('retained_inputs',[])
        enumerated=[r for r in entries if Path(r['path']).resolve()==candidate and r['sha256']==sha256
                    and r['dataset']==dataset and r['season']==season]
        need(len(enumerated)==1,'SOURCE_NOT_ENUMERATED')
        item=enumerated[0]
        rules=[r for r in policy.get('retained_partitions',[]) if r['id']==item['partition_id']
               and r['dataset']==dataset and r['season']==2026 and r['permission']=='ADMITTED_RETAINED_SOURCE']
        need(len(rules)==1,'SOURCE_PARTITION_NOT_ADMITTED')
        rule=rules[0]; archive=Path(rule['archive_root']).resolve()
        need(candidate.is_relative_to(archive) and candidate!=archive,'SOURCE_PARTITION_ESCAPE')
        witness=value(root,item['metadata_witness'])
        need(witness['schema_version']=='puntproof-lab-producer-metadata-witness-v1'
             and witness['source']==rule['source_id'] and witness['dataset']==dataset
             and witness['season']==2026 and witness['metadata_database']==rule['metadata_database'], 'SOURCE_METADATA_WITNESS')
        import sqlite3
        db_path=Path(rule['metadata_database'])
        need(db_path.name=='acquisitions.sqlite3' and db_path.resolve().parent==archive,'SOURCE_METADATA_DATABASE_SCOPE')
        db=sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)
        try:
            db.row_factory=sqlite3.Row; db.execute('PRAGMA query_only=ON')
            row=db.execute('SELECT id,source,dataset,season,acquired,raw_path,raw_hash,records_path,records_hash FROM snapshots WHERE id=? AND source=? AND dataset=? AND season=2026',
                (witness['snapshot_id'],rule['source_id'],dataset)).fetchone()
        finally:db.close()
        need(row is not None and dict(row)==witness['row'],'SOURCE_METADATA_ROW_CHANGED')
        field=item['stored_field']; need(field in ('raw_path','records_path'),'SOURCE_METADATA_FIELD')
        hash_field='raw_hash' if field=='raw_path' else 'records_hash'
        located=Path(row[field]); located=located if located.is_absolute() else archive/located
        need(located.resolve()==candidate and row[hash_field]==sha256,'SOURCE_METADATA_PATH_HASH')
        timestamp(row['acquired'])
        matches=[dict(item,permission=rule['permission'],protected=False,provider_calls=0,
                      allowed_fields=rule['allowed_fields'],clock_semantics=rule['clock_semantics'])]
    need(len(matches)==1 and matches[0]['permission']=='ADMITTED_RETAINED_SOURCE','SOURCE_NOT_EXACTLY_ADMITTED')
    need(matches[0].get('protected',False) is False and matches[0].get('provider_calls',0)==0,'SOURCE_RIGHTS')
    for part in (Path(path),*Path(path).parents):
        need(not part.is_symlink() and not getattr(part,'is_junction',lambda:False)(),'SOURCE_LINK')
    with candidate.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
    need(actual==sha256,'SOURCE_BYTES_CHANGED')
    return dict(matches[0])


def require_phase(capability):
    active=_ACTIVE.get(); need(active is not None and active['operation'] in MODEL_OPERATIONS,'MODEL_CONTEXT_REQUIRED')
    manifest=active['manifest']; root=active['root']
    if active.get('version')=='puntproof-lab-consumer-v2':
        selected=active['capability']
        if capability=='inspect':
            need(selected=='SEED_INSPECTION','INSPECTION_ONLY');return {'operation':active['operation'],'phase':'SEED_INSPECTION','capability':capability}
        need(selected==('SETTLE' if capability in ('response','score','learn') else 'FORECAST'),'ACTION_CAPABILITY_MISMATCH')
    if manifest.get('engineering_recovery') and capability=='forecast':
        _require_recovery_claim(root,manifest,active['manifest_ref'])
    events,phase=_events(root,manifest)
    allowed={'forecast':{'PRETASK_VALID','INPUTS_VERIFIED'},
        'response':{'RESPONSE_ACCESS_ENABLED'},
        'score':{'RESPONSES_COMPLETE'},'learn':{'SCORING_COMPLETE'}}
    need(capability in allowed and phase in allowed[capability],'CAPABILITY_PHASE_DENIED:'+capability)
    if capability in ('response','score','learn'):
        sealed=next((x for x in events if x['state']=='GLOBAL_EXECUTION_SEAL_VERIFIED'),None)
        need(sealed is not None and sealed.get('evidence'),'FORECAST_SEAL_REQUIRED')
        for ref in sealed['evidence']:checked(root,ref)
    if capability in ('response','score','learn'):
        access=next((x for x in events if x['state']=='RESPONSE_ACCESS_ENABLED'),None)
        need(access is not None and access.get('completion'),'WHOLE_WEEK_COMPLETION_RECEIPT_REQUIRED')
        completion=value(root,access['completion'])
        need(completion['status']=='VERIFIED_COMPLETE' and completion['season']==2026
             and completion['week']==manifest['week'] and completion['all_fixtures_complete'] is True,'WHOLE_WEEK_INCOMPLETE')
        expected={f['game_id']:(timestamp(f['kickoff']) if f.get('kickoff') else None) for f in manifest['fixtures']}
        actual=completion['fixtures']
        need(len(actual)==len(expected) and {f['game_id'] for f in actual}==set(expected),'COMPLETION_FIXTURE_MISMATCH')
        for fixture in actual:
            kickoff=expected[fixture['game_id']]
            need(fixture['final'] is True and timestamp(fixture['completed_at'])<=datetime.now(UTC),'COMPLETION_CLOCK_OR_FINAL_STATE')
            if kickoff is not None:need(timestamp(fixture['kickoff'])==kickoff and kickoff<=timestamp(fixture['completed_at']),'COMPLETION_CLOCK_OR_FINAL_STATE')
            else:need(active['operation']==REPLAY and fixture.get('completion_basis')=='RETAINED_FINAL_MARKER','RETROSPECTIVE_FINAL_WITNESS')
            need(fixture['source_ref']['path'].startswith(RUNTIME+'sources/'),'COMPLETION_SOURCE_SCOPE')
            checked(root,fixture['source_ref'])
    if active['operation']==FORWARD and capability=='forecast':
        need(datetime.now(UTC)<timestamp(manifest['first_kickoff']),'KICKOFF_PASSED')
    return {'operation':active['operation'],'phase':phase,'capability':capability}


def require_model_artifact(root,path,sha256,channel):
    """Authorize only the initial model or this run's globally sealed pending session."""
    active=_ACTIVE.get(); root=Path(root).resolve()
    need(active is not None and active['root']==root and active['operation'] in MODEL_OPERATIONS,'MODEL_CONTEXT_REQUIRED')
    need(channel in ('receiving','rushing'),'MODEL_CHANNEL')
    manifest=active['manifest']; expected=manifest['model_artifacts'][channel]
    target=Path(path).resolve()
    if target==reference(root,expected).resolve() and sha256==expected['sha256']:
        checked(root,expected); return expected
    events,phase=_events(root,manifest)
    need(phase in ('FORECAST_PACKAGES_SEALED','GLOBAL_EXECUTION_SEAL_VERIFIED','ATTEMPT_CONSUMED',
        'RESPONSE_ACCESS_ENABLED','RESPONSES_COMPLETE','SCORING_COMPLETE','MODEL_STATE_UPDATED'),'PENDING_SESSION_PHASE')
    seal=next((event for event in events if event['state']=='FORECAST_PACKAGES_SEALED'),None)
    need(seal is not None and set(seal.get('pending_sessions',{}))==set(manifest.get('channels',('receiving','rushing'))),'PENDING_SESSIONS_NOT_SEALED')
    expected=seal['pending_sessions'][channel]
    need(expected['path'].startswith(manifest['execution_directory']+'/')
         and target==reference(root,expected).resolve() and sha256==expected['sha256'],'PENDING_SESSION_CUSTODY')
    checked(root,expected); return expected


def require_response_artifact(root,path,sha256,channel):
    """Outcome bytes become accessible only through the post-seal response event."""
    active=_ACTIVE.get(); root=Path(root).resolve()
    need(active is not None and active['root']==root and active['operation'] in MODEL_OPERATIONS,'MODEL_CONTEXT_REQUIRED')
    need(channel in ('receiving','rushing'),'RESPONSE_CHANNEL')
    events,phase=_events(root,active['manifest'])
    capability={'RESPONSE_ACCESS_ENABLED':'response','RESPONSES_COMPLETE':'score','SCORING_COMPLETE':'learn'}.get(phase)
    need(capability is not None,'RESPONSE_ARTIFACT_PHASE')
    require_phase(capability)
    access=next(event for event in events if event['state']=='RESPONSE_ACCESS_ENABLED')
    need(set(access.get('outcomes',{}))==set(active['manifest'].get('channels',('receiving','rushing'))),'OUTCOMES_NOT_SEALED')
    expected=access['outcomes'][channel]
    need(expected['path'].startswith(RUNTIME+'sources/') and Path(path).resolve()==reference(root,expected).resolve()
         and sha256==expected['sha256'],'RESPONSE_ARTIFACT_CUSTODY')
    completion=value(root,access['completion'])
    need(completion.get('outcomes')==access['outcomes'],'COMPLETION_OUTCOME_BINDING')
    checked(root,expected); return expected


def require_active_source(root,authority):
    active=_ACTIVE.get()
    need(active is not None and active['root']==Path(root).resolve() and active['operation']==SOURCE,'SOURCE_CONTEXT_REQUIRED')
    _,ref=consumer_for(root,SOURCE,authority)
    need(ref==active['consumer'],'SOURCE_CONSUMER_CHANGED')

def _default_capability(manifest):
    return {'SOURCE_IMPORT':'SOURCE_IMPORT','FEATURE_PREPARATION':'PREPARE_FORECAST',
            'SEED_INSPECTION':'SEED_INSPECTION','FORECAST_CYCLE':'FORECAST'}.get(manifest.get('purpose'),
            'SOURCE_IMPORT' if manifest.get('operation')==SOURCE else 'FORECAST')


def _capability(manifest):
    active=_ACTIVE.get()
    return active['capability'] if active and active.get('operation')==manifest['operation'] else _default_capability(manifest)


def _finish_manifest(root,operation,consumer,consumer_ref,inputs):
    need(isinstance(inputs,dict) and set(inputs)=={FAMILY} and isinstance(inputs[FAMILY],list)
         and len(inputs[FAMILY])==1,'ONE_EXECUTION_MANIFEST_REQUIRED')
    ref=inputs[FAMILY][0]
    need(ref['path'].startswith(RUNTIME+'executions/') and ref['path'].endswith('/MANIFEST.json'),'MANIFEST_SCOPE')
    manifest=value(root,ref); cap=_capability(manifest)
    compatible=cap=='SETTLE' and ref in consumer.get('accepted_pending_manifests',[])
    if compatible:
        events,phase=_events(root,manifest)
        need(phase in ('ATTEMPT_CONSUMED','RESPONSE_ACCESS_ENABLED','RESPONSES_COMPLETE','SCORING_COMPLETE'),'PENDING_CONTINUATION_PHASE')
        need(any(e['state']=='GLOBAL_EXECUTION_SEAL_VERIFIED' for e in events),'PENDING_GLOBAL_SEAL_REQUIRED')
    need(manifest['schema_version']=='puntproof-lab-execution-manifest-v2' and manifest['operation']==operation
         and (manifest['consumer']==consumer_ref or compatible) and manifest['source_policy']==consumer['source_policy'],'MANIFEST_BINDING')
    need(cap in CAPABILITIES[operation],'CAPABILITY_NOT_ADMITTED')
    purposes={'SOURCE_IMPORT':'SOURCE_IMPORT','PREPARE_FORECAST':'FEATURE_PREPARATION',
              'SEED_INSPECTION':'SEED_INSPECTION','FORECAST':'FORECAST_CYCLE','SETTLE':'FORECAST_CYCLE'}
    need(manifest['purpose']==purposes[cap],'PURPOSE_CAPABILITY_MISMATCH')
    need(manifest['season']==2026 and (manifest['week'] is None and cap in ('SOURCE_IMPORT','SEED_INSPECTION')
         or type(manifest['week']) is int and 1<=manifest['week']<=18),'MANIFEST_SEASON_WEEK')
    need(manifest['provider_calls']==manifest['cost_cents']==0 and manifest['protected_access'] is False
         and manifest['production'] is False,'MANIFEST_CAPABILITIES')
    need(manifest['execution_directory']==str(Path(ref['path']).parent).replace('\\','/'),'EXECUTION_DIRECTORY')
    need(timestamp(manifest['cutoff'])<=timestamp(manifest['as_of'])<=datetime.now(UTC),'CUTOFF_AFTER_OBSERVATION')
    if manifest.get('engineering_recovery'):
        _recovery_contract(root,manifest,consumer)
    admitted={str(reference(root,ref).resolve()):ref['sha256']}; refs=[ref]
    for item in manifest['input_files']:
        need(item['path'].startswith(RUNTIME+'sources/'),'MODEL_INPUT_PATH')
        checked(root,item); path=str(reference(root,item).resolve())
        need(path not in admitted,'DUPLICATE_INPUT'); admitted[path]=item['sha256']; refs.append(item)
    if cap=='SOURCE_IMPORT':
        need(not manifest['input_files'] and not manifest.get('model_artifacts'),'SOURCE_IMPORT_NO_MODEL_INPUTS')
        need(manifest.get('retained_inputs'),'RETAINED_INPUTS_REQUIRED')
    if cap=='PREPARE_FORECAST':
        need(manifest['input_files'] and not manifest.get('model_artifacts'),'FEATURE_PREPARATION_NO_MODEL')
    if operation not in MODEL_OPERATIONS:return manifest,admitted,refs
    channels=manifest['channels']
    need(isinstance(channels,list) and channels and len(set(channels))==len(channels)
         and set(channels)<= {'receiving','rushing'},'MODEL_CHANNELS')
    artifacts=manifest['model_artifacts']
    need(set(artifacts)==set(channels),'CHANNEL_ARTIFACT_MISMATCH')
    for channel,artifact in artifacts.items():
        checked(root,artifact)
        if cap=='SEED_INSPECTION':need(artifact==consumer['seed_channels'][channel],'EXACT_SEED_INSPECTION')
        elif artifact not in consumer['seed_artifacts']:
            need(artifact['path'].startswith(RUNTIME+'sessions/'),'UNADMITTED_MODEL_ARTIFACT')
            parent_ref=manifest['parent_state_receipts'][channel]; parent=value(root,parent_ref)
            need(parent['schema_version']=='puntproof-lab-model-state-receipt-v1' and parent['status']=='AUTHORITY_CLOSED'
                 and parent['operation'] in (REPLAY,FORWARD) and parent['model_artifact']==artifact
                 and parent['seed_artifact']==consumer['seed_channels'][channel] and parent['channel']==channel
                 and parent['season']==2026 and parent['history_through_week']<manifest['week']
                 and parent['implementation']==consumer['implementation'],'MODEL_PARENT_CUSTODY')
            refs.append(parent_ref)
        admitted[str(reference(root,artifact).resolve())]=artifact['sha256'];refs.append(artifact)
    if cap=='SEED_INSPECTION':
        need(not manifest['input_files'] and set(channels)=={'receiving','rushing'}
             and not manifest.get('fixtures'),'INSPECTION_NO_FOOTBALL_INPUTS')
        return manifest,admitted,refs
    need(manifest['input_files'],'MODEL_INPUTS_EMPTY')
    fixtures=manifest['fixtures']
    need(isinstance(fixtures,list) and fixtures and len({f['game_id'] for f in fixtures})==len(fixtures),'EXACT_FIXTURE_SET_REQUIRED')
    need(manifest['population_scope'] in ('VERIFIED_SUBSET','COMPLETE_VERIFIED_SLATE'),'POPULATION_SCOPE_REQUIRED')
    source=value(root,manifest['source_receipt'])
    need(source['schema_version']=='puntproof-lab-source-receipt-v2' and source['status']=='PASS'
         and source['operation']==SOURCE and source['season']==2026 and source['source_policy']==consumer['source_policy']
         and source['inputs']==manifest['input_files'],'SOURCE_NOT_READY')
    need(source['identity_conflicts']==source['duplicate_keys']==0 and source['unknowns_preserved'] is True
         and source['known_snap_denominators_verified'] is True,'SOURCE_SEMANTICS_INCOMPLETE')
    need(manifest['week'] in source['target_weeks_verified'] and source['fixture_count']>0
         and source['roster_count']>0,'TARGET_WEEK_UNWITNESSED')
    refs.append(manifest['source_receipt'])
    if operation==FORWARD:
        need(manifest['classification']=='GENUINE_PROSPECTIVE' and source['pregame_snapshots_verified'] is True,'PREGAME_SNAPSHOT_REQUIRED')
        kickoff=min(timestamp(f['kickoff']) for f in fixtures)
        need(timestamp(manifest['first_kickoff'])==kickoff and timestamp(manifest['as_of'])<kickoff
             and timestamp(source['latest_observed_at'])<=timestamp(manifest['cutoff']),'FORWARD_CLOCK')
    else:need(manifest['classification']=='EXPOSED_RETROSPECTIVE_DEVELOPMENT','RETROSPECTIVE_LABEL_REQUIRED')
    if operation==DEVELOPMENT:
        spec=value(root,manifest['candidate_spec'])
        need(spec['schema_version']=='puntproof-lab-candidate-spec-v1' and spec['frozen'] is True
             and type(spec['max_trials']) is int and spec['max_trials']==1 and 0<spec['max_seconds']<=300
             and spec['provider_calls']==0 and spec['protected_access'] is False and spec['candidate_ordinal'] in (1,2)
             and spec['week_fingerprint']==source['whole_week_fingerprint'],'CANDIDATE_BUDGET')
        need(all(spec.get(k) for k in ('hypothesis','mechanism','features','algorithm','hyperparameters','seed',
             'time_partitions','endpoint','matched_population','selection_rule','uncertainty_rule','duplicate_review')),'CANDIDATE_SPEC_INCOMPLETE')
        review=value(root,spec['duplicate_review'])
        need(review['status']=='NEW_UNCONSUMED_MECHANISM' and review['consumed_equivalent'] is False
             and review['fingerprint']==spec['week_fingerprint'],'CONSUMED_OR_DUPLICATE_CANDIDATE')
        refs.extend([manifest['candidate_spec'],spec['duplicate_review']])
    return manifest,admitted,refs


def _finish_readiness(root,operation,consumer,ref,manifest_ref,manifest,readiness_ref):
    ready=value(root,readiness_ref); cap=_capability(manifest)
    need(ready['schema_version']=='puntproof-lab-action-readiness-v2' and ready['status']=='READY'
         and ready['capability']==cap and ready['operation']==operation and ready['consumer']==ref
         and ready['manifest']==manifest_ref and ready['implementation']==consumer['implementation'],'ACTION_NOT_READY')
    need(ready['season']==2026 and ready['week']==manifest['week'] and ready['attempt_consumed'] is False,'READINESS_TARGET_OR_ATTEMPT')
    checks=CAPABILITY_CHECKS[cap]
    need(set(ready['prechecks'])==set(checks) and all(v is True for v in ready['prechecks'].values()),'ACTION_PRECHECKS')
    witness=value(root,ready['target_witness'])
    need(witness['season']==2026 and witness['status']=='PASS' and witness['provider_calls']==0
         and witness['witness_class']=='LAB_ACTION_INPUTS' and witness['capability']==cap
         and witness['records']>0,'ACTION_TARGET_WITNESS')
    need(witness.get('week')==manifest['week'],'ACTION_WITNESS_WEEK')
    covered=set(); bindings={item['path']:item['sha256'] for item in consumer['implementation']}
    need(ready['authored_adapter_checks'],'AUTHORED_ADAPTER_CHECKS_REQUIRED')
    for item in ready['authored_adapter_checks']:
        test=value(root,item)
        need(test['status']=='PASS' and test['same_adapter'] is True and test['provider_calls']==0
             and test['real_outcomes_opened']==0,'ADAPTER_CHECK_FAILURE')
        need(all(test['code_sha256'].get(p)==sha for p,sha in bindings.items()),'CANARY_IMPLEMENTATION_DRIFT')
        covered.update(test['passed_defects'])
    need(set(checks)<=covered,'KNOWN_DEFECT_CANARY_MISSING')
    if cap=='FORECAST':
        prep=value(root,ready['feature_preparation'])
        need(prep['status']=='FEATURES_BUILT' and prep['real_outcomes_opened']==prep['fits']==0
             and prep['feature_count']==228 and prep['eligible']>0 and prep['inputs']==manifest['input_files']
             and prep['source_receipt']==manifest['source_receipt'] and prep['channels']==manifest['channels'], 'REAL_FEATURES_NOT_READY')
    if cap=='SETTLE':
        events,phase=_events(root,manifest)
        need(phase in ('ATTEMPT_CONSUMED','RESPONSE_ACCESS_ENABLED','RESPONSES_COMPLETE','SCORING_COMPLETE','MODEL_STATE_UPDATED'),'SETTLEMENT_FORECAST_NOT_SEALED')
        need(any(e['state']=='GLOBAL_EXECUTION_SEAL_VERIFIED' and e.get('evidence') for e in events),'FORECAST_SEAL_REQUIRED')
    return ready


def _finish_lifecycle(root,charter,consumer,consumer_ref):
    operation=charter['model_execution']['operation']
    need(charter['stage_authority']['operations']==[operation] and charter['model_execution']['seasons']==[2026]
         and charter['external_access']=='NONE' and not any(charter.get(k,False) for k in
         ('protected_result_access','paid_access','stage9_authorised','push_authorised')),'LIFECYCLE_SCOPE')
    inputs=charter['model_execution']['inputs'];manifest,_,_=_manifest(root,operation,consumer,consumer_ref,inputs)
    plan=value(root,charter['preexecution_lifecycle']); cap=_capability(manifest)
    states=list(INSPECTION_STATES if cap=='SEED_INSPECTION' else FORWARD_STATES if operation==FORWARD else MODEL_STATES)
    need(plan['operation']==operation and plan['states']==states and plan['manifest']==inputs[FAMILY][0]
         and plan['implementation']==consumer['implementation'] and plan['retry_after_consumption'] is False
         and plan['attempts']==(0 if cap=='SEED_INSPECTION' else 1),'LIFECYCLE_BINDING')
    _readiness(root,operation,consumer,consumer_ref,inputs[FAMILY][0],manifest,charter['protected_runner_readiness'])
    phase=None if cap=='SEED_INSPECTION' else _events(root,manifest)[1]
    need(phase!='AUTHORITY_CLOSED','ATTEMPT_CLOSED')
    return {'status':'PASS','operation':operation,'phase':phase or 'PREPARATION','capability':cap}


def _recovery_contract(root,manifest,consumer=None):
    """One exact pre-consumption I/O failure; never a general restart permission."""
    ref=manifest.get('engineering_recovery')
    need(ref is not None and ref.get('path') in (RECOVERY_CONTRACT,TYPE_REPAIR_CONTRACT),'RECOVERY_CONTRACT_SCOPE')
    if consumer is not None:need(consumer.get('engineering_recovery')==ref,'RECOVERY_CONSUMER_BINDING')
    c=value(root,ref,committed=True)
    if ref['path']==TYPE_REPAIR_CONTRACT:_type_repair_predecessor(root,c)
    else:need('predecessor_recovery' not in c and 'claim_filename' not in c,'RECOVERY_ORIGINAL_CONTRACT_CHANGED')
    need(c.get('schema_version')=='puntproof-lab-forecast-recovery-v1'
         and c.get('operation')==manifest.get('operation')==REPLAY and c.get('season')==manifest.get('season')==2026
         and c.get('week')==manifest.get('week')==7 and c.get('recovery_channel')=='receiving'
         and c.get('first_execution_channels')==['rushing'] and c.get('max_recoveries')==1,
         'RECOVERY_EXACT_ATTEMPT_SCOPE')
    need(c.get('recompute_expert_predictions') is True and c.get('receiving_forecast_week_allowed') is False
         and c.get('fits')==c.get('updates')==c.get('target_outcomes_opened')==0
         and c.get('scientific_reset') is False and c.get('preserve_original_archive') is True
         and c.get('require_bitwise_archive_parity') is True,'RECOVERY_SCIENTIFIC_BOUNDARIES')
    refs=c['refs']
    need(set(refs)=={'original_archive','original_request','original_manifest','original_failure'},'RECOVERY_REFERENCE_SET')
    origin=value(root,refs['original_manifest'])
    directory=c['original_execution_directory']
    need(directory==origin['execution_directory'] and refs['original_manifest']['path']==directory+'/MANIFEST.json'
         and directory.startswith(RUNTIME+'executions/') and manifest['execution_directory']!=directory,'RECOVERY_ORIGIN_DIRECTORY')
    mutable={'consumer','execution_directory','pretask_satisfiability','engineering_recovery'}
    need({k:v for k,v in manifest.items() if k not in mutable}=={k:v for k,v in origin.items() if k not in mutable},
         'RECOVERY_SCIENTIFIC_INPUTS_CHANGED')
    need(origin['channels']==['receiving','rushing'] and origin['purpose']=='FORECAST_CYCLE','RECOVERY_ORIGINAL_CHANNELS')
    paths={'original_archive':'receiving/forecast/forecast.npz','original_request':'receiving/forecast-request.json',
           'original_failure':'receiving/forecast/FAILED.json'}
    for key,suffix in paths.items():
        need(refs[key]['path']==directory+'/'+suffix,'RECOVERY_ORIGINAL_PATH');checked(root,refs[key])
    request=value(root,refs['original_request'])
    need(request['action']=='forecast' and request['channel']=='receiving' and request['period']==[2026,7]
         and request['authority']['execution_manifest']==refs['original_manifest']
         and request['parent_session']['sha256']==origin['model_artifacts']['receiving']['sha256'],
         'RECOVERY_ORIGINAL_REQUEST')
    failure=value(root,refs['original_failure'])
    need(failure.get('status')=='QUARANTINED' and failure.get('action')=='forecast'
         and failure.get('channel')=='receiving' and failure.get('period')==[2026,7]
         and failure.get('error')=='OSError: [Errno 9] Bad file descriptor','RECOVERY_FAILURE_CLASS')
    need(c['original_lease']['path']==directory+'/FORECAST_LEASE.json','RECOVERY_LEASE_PATH')
    lease=value(root,c['original_lease'])
    need(lease['operation']==REPLAY and lease['season']==2026 and lease['week']==7
         and lease['action']=='forecast' and lease['automatic_retry'] is False,'RECOVERY_ORIGINAL_LEASE')
    prefix=c['original_journal_prefix']
    need(len(prefix)==2 and [x['path'] for x in prefix]==[directory+'/lifecycle/000.json',directory+'/lifecycle/001.json'],
         'RECOVERY_JOURNAL_PREFIX')
    prior=[value(root,r) for r in prefix]
    need(validate_lifecycle_events(prior,REPLAY)=='INPUTS_VERIFIED','RECOVERY_PREFIX_NOT_UNCONSUMED')
    checked(root,c['original_error_log'])
    need(not (Path(root)/directory/'rushing').exists()
         and not (Path(root)/directory/'receiving/forecast/session.joblib').exists()
         and not (Path(root)/directory/'receiving/forecast/predictions.json').exists(),'RECOVERY_ORIGINAL_OUTPUT_STATE')
    return c


def lifecycle_directory(root,manifest):
    if manifest.get('engineering_recovery'):
        c=_recovery_contract(root,manifest)
        return Path(root)/c['original_execution_directory']/'lifecycle'
    return Path(root)/manifest['execution_directory']/'lifecycle'


def _require_recovery_claim(root,manifest,manifest_ref):
    c=_recovery_contract(root,manifest)
    path=Path(root)/c['original_execution_directory']/c.get('claim_filename','RECOVERY_CLAIM.json')
    need(path.is_file(),'RECOVERY_EXCLUSIVE_CLAIM_REQUIRED')
    claim=json.loads(path.read_bytes())
    need(claim.get('schema_version')=='puntproof-lab-recovery-claim-v1'
         and claim.get('manifest')==manifest_ref and claim.get('contract')==manifest['engineering_recovery']
         and claim.get('origin_manifest')==c['refs']['original_manifest']
         and claim.get('original_lease')==c['original_lease'],'RECOVERY_CLAIM_BINDING')
    timestamp(claim['created_at'])
    return c


def require_forecast_recovery(root,channel):
    active=_ACTIVE.get();root=Path(root).resolve()
    need(active is not None and active['root']==root and active['operation']==REPLAY
         and active['capability']=='FORECAST' and channel=='receiving','RECOVERY_CONTEXT_REQUIRED')
    require_phase('forecast')
    c=_require_recovery_claim(root,active['manifest'],active['manifest_ref'])
    need(_events(root,active['manifest'])[1]=='INPUTS_VERIFIED','RECOVERY_ALREADY_SEALED')
    return dict(c['refs'])


def _type_repair_predecessor(root,c):
    """The first recovery was rejected before prediction; never release its claim."""
    import ast
    need(c.get('claim_filename')=='RECOVERY_CLAIM_TYPE_REPAIR.json','TYPE_REPAIR_CLAIM_PATH')
    p=c.get('predecessor_recovery',{})
    need(set(p)=={'contract','manifest','claim','request','failure','error_log','bridge_source',
         'stopped_before_expert_prediction','failure_code'} and p['stopped_before_expert_prediction'] is True
         and p['failure_code']=='RECOVERY_EXPERT_TYPES','TYPE_REPAIR_EVIDENCE')
    need(p['contract']['path']==RECOVERY_CONTRACT,'TYPE_REPAIR_PREDECESSOR_CONTRACT')
    previous=value(root,p['contract'],committed=True)
    need({k:v for k,v in c.items() if k not in ('claim_filename','predecessor_recovery','reconstruction_note')}
         =={k:v for k,v in previous.items() if k!='reconstruction_note'},'TYPE_REPAIR_SCOPE_CHANGED')
    old=value(root,p['manifest']);directory=old['execution_directory']
    need(p['manifest']['path']==directory+'/MANIFEST.json' and directory.startswith(RUNTIME+'executions/')
         and old.get('engineering_recovery')==p['contract'],'TYPE_REPAIR_FAILED_MANIFEST')
    original=value(root,c['refs']['original_manifest'])
    mutable={'consumer','execution_directory','pretask_satisfiability','engineering_recovery'}
    need({k:v for k,v in old.items() if k not in mutable}=={k:v for k,v in original.items() if k not in mutable},
         'TYPE_REPAIR_PREDECESSOR_INPUTS')
    need(p['claim']['path']==c['original_execution_directory']+'/RECOVERY_CLAIM.json','TYPE_REPAIR_OLD_CLAIM_PATH')
    claim=value(root,p['claim'])
    need(claim['schema_version']=='puntproof-lab-recovery-claim-v1' and claim['manifest']==p['manifest']
         and claim['contract']==p['contract'] and claim['origin_manifest']==c['refs']['original_manifest']
         and claim['original_lease']==c['original_lease'],'TYPE_REPAIR_OLD_CLAIM')
    need(p['request']['path']==directory+'/receiving/forecast-request.json'
         and p['failure']['path']==directory+'/receiving/forecast/FAILED.json'
         and p['error_log']['path']==directory+'/receiving/forecast.err.log','TYPE_REPAIR_FAILED_PATHS')
    request=value(root,p['request']);failure=value(root,p['failure']);error=checked(root,p['error_log']).decode('utf-8')
    need(request['action']=='recover_forecast' and request['period']==[2026,7]
         and request['channel']=='receiving' and request['authority']['execution_manifest']==p['manifest']
         and request['recovery']==c['refs'],'TYPE_REPAIR_FAILED_REQUEST')
    need(failure=={'action':'recover_forecast','channel':'receiving','error':'ValueError: ENGINE_BRIDGE:RECOVERY_EXPERT_TYPES',
         'period':[2026,7],'retry_authorized':False,'status':'QUARANTINED'}
         and 'RECOVERY_EXPERT_TYPES' in error and 'recover_forecast_state' in error,'TYPE_REPAIR_FAILURE_BEFORE_PREDICTION')
    need(p['bridge_source']['path']==TYPE_REPAIR_BASE+'PREDECESSOR_ENGINE_BRIDGE.py'
         and p['bridge_source']['sha256']=='4173c9572aaf4233d08738d0825e8a07d0977747573ac84de9387d49c9cff4ff',
         'TYPE_REPAIR_FAILED_IMPLEMENTATION')
    tree=ast.parse(checked(root,p['bridge_source'],committed=True).decode('utf-8'))
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='recover_forecast_state')
    stops=[n.lineno for n in ast.walk(fn) if isinstance(n,ast.Call) and any(isinstance(a,ast.Constant)
           and a.value=='RECOVERY_EXPERT_TYPES' for a in n.args)]
    predictions=[n.lineno for n in ast.walk(fn) if isinstance(n,ast.Call)
                 and isinstance(n.func,ast.Attribute) and n.func.attr in ('predict','fit','update','forecast_week','settle_week')]
    need(len(stops)==1 and predictions and stops[0]<min(predictions),'TYPE_REPAIR_ZERO_PREDICT_PROOF')
    need(not (Path(root)/directory/'rushing').exists() and all(not (Path(root)/directory/'receiving/forecast'/name).exists()
         for name in ('forecast.npz','session.joblib','predictions.json','COMPLETE.json','RESULT.json')),
         'TYPE_REPAIR_UNEXPECTED_COMPUTED_OUTPUT')
