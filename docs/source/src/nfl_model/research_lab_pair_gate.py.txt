"""Approved exact sealed-component comparison gate; no fit or source acquisition."""
from contextlib import contextmanager
from pathlib import Path
from nfl_model import research_lab_gate as g

POLICY='research/research_lab_v1/runtime_v1/PAIRED_POLICY.json'
RECIPES=('SEALED_EXPERT1_ONLY','SEALED_EXPERT2_ONLY')

def validate_pair_manifest(root,consumer,consumer_ref,manifest_ref):
    root=Path(root).resolve()
    g.need(consumer_ref['path']==g.RUNTIME_CONSUMERS[g.DEVELOPMENT],'PAIRED_CONSUMER_REQUIRED')
    pref=consumer.get('automatic_paired_policy',{})
    g.need(pref.get('path')==POLICY,'PAIRED_POLICY_REQUIRED')
    policy=g.value(root,pref,committed=True)
    g.need(policy.get('recipes')==list(RECIPES) and policy.get('maximum_candidates_per_week')==2 and policy.get('fit') is False and policy.get('provider_calls')==0,'PAIRED_POLICY_SCOPE')
    g.need(manifest_ref['path'].startswith(g.RUNTIME+'executions/paired-') and manifest_ref['path'].endswith('/MANIFEST.json'),'PAIRED_MANIFEST_PATH')
    m=g.value(root,manifest_ref)
    g.need(m.get('purpose')=='PAIRED_COMPARISON' and m.get('operation')==g.DEVELOPMENT and m.get('consumer')==consumer_ref and m.get('policy')==pref,'PAIRED_BINDING')
    g.need(m.get('season')==2026 and type(m.get('week')) is int and 7<=m['week']<=18 and m.get('recipes')==list(RECIPES),'PAIRED_TARGET')
    g.need(m.get('phase') in ('PREPARE_SEALED_COMPONENTS','EVALUATE_COMPLETE_WEEK') and m.get('provider_calls')==m.get('cost_cents')==0 and m.get('protected_access') is False and m.get('fit') is False,'PAIRED_CAPABILITIES')
    origin=g.value(root,m['forecast_manifest'])
    g.need(origin.get('season')==2026 and origin.get('week')==m['week'] and origin['operation'] in (g.REPLAY,g.FORWARD),'PAIRED_ORIGIN_SCOPE')
    g.checked(root,origin['consumer'],committed=True)
    events,phase=g._events(root,origin)
    package=next((e for e in events if e['state']=='FORECAST_PACKAGES_SEALED'),None)
    seal=next((e for e in events if e['state']=='GLOBAL_EXECUTION_SEAL_VERIFIED'),None)
    g.need(package is not None and seal is not None and any(e['state']=='ATTEMPT_CONSUMED' for e in events),'PAIRED_ORIGINAL_SEAL_REQUIRED')
    g.need(m['pending_sessions']==package['pending_sessions'] and set(m['pending_sessions'])=={'receiving','rushing'},'PAIRED_PENDING_CUSTODY')
    refs=[manifest_ref,m['forecast_manifest'],pref,m['specification'],m['pretask_satisfiability']]
    spec=g.value(root,m['specification'])
    g.need(spec.get('recipes')==list(RECIPES) and spec.get('forecast_manifest')==m['forecast_manifest'] and spec.get('frozen') is True and spec.get('seed')==20261027 and spec.get('max_seconds')==300,'PAIRED_SPECIFICATION')
    g.need(spec.get('duplicate_review',{}).get('status')=='NEW_UNCONSUMED_MECHANISM' and spec['duplicate_review'].get('consumed_equivalent') is False,'PAIRED_DUPLICATION_REVIEW')
    g.checked(root,spec['duplicate_review']['experiment_index'])
    for channel in ('receiving','rushing'):
        pending=m['pending_sessions'][channel];baseline=m['baseline'][channel]
        expected=origin['execution_directory']+'/'+channel+'/forecast/predictions.json'
        g.need(pending in package['evidence'] and baseline in seal['evidence'] and baseline['path']==expected,'PAIRED_ARTIFACT_NOT_SEALED')
        refs.extend((pending,baseline))
    if m['phase']=='EVALUATE_COMPLETE_WEEK':
        g.need(phase=='AUTHORITY_CLOSED','PAIRED_COMPLETED_LEARNING_REQUIRED')
        response=next((e for e in events if e['state']=='RESPONSE_ACCESS_ENABLED'),None)
        g.need(response and m['completion']==response['completion'],'PAIRED_RESPONSE_BINDING')
        completion=g.value(root,m['completion'])
        g.need(completion.get('status')=='VERIFIED_COMPLETE' and completion.get('all_fixtures_complete') is True and completion.get('full_fixture_census_verified') is True and completion.get('week')==m['week'],'PAIRED_WHOLE_WEEK_REQUIRED')
        refs.append(m['completion']);refs.extend(completion['outcomes'].values())
        for channel in ('receiving','rushing'):
            component=m['components'][channel]
            g.need(component['path'].startswith(g.RUNTIME+'executions/paired-components-') and component['path'].endswith('/'+channel+'.json'),'PAIRED_COMPONENT_PATH')
            data=g.value(root,component)
            g.need(data['pending_session']==m['pending_sessions'][channel] and data['forecast_manifest']==m['forecast_manifest'] and data['fits']==data['predict_calls']==0,'PAIRED_COMPONENT_LINEAGE')
            refs.append(component)
    admitted={}
    for ref in refs:
        g.checked(root,ref);admitted[str(g.reference(root,ref).resolve())]=ref['sha256']
    return m,admitted,refs

@contextmanager
def paired_authority(root,manifest_ref):
    root=Path(root).resolve();g.need(not (root/'TASK_CHARTER.json').exists(),'LIVE_CHARTER_PRECEDENCE')
    consumer,cref=g.consumer_for(root,g.DEVELOPMENT)
    m,_,_=validate_pair_manifest(root,consumer,cref,manifest_ref)
    active=dict(root=root,operation=g.DEVELOPMENT,consumer=cref,manifest_ref=manifest_ref,manifest=m,capability='PAIRED_COMPARISON',version=consumer['schema_version'])
    g._pretask(root,active)
    token=g._ACTIVE.set(active)
    try:
        from nfl_model.data.semantic_certificate import model_inputs
        with model_inputs(g.DEVELOPMENT,inputs={g.FAMILY:[manifest_ref]}):yield m
    finally:g._ACTIVE.reset(token)
