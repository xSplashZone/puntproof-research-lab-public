"""Automatically preregister and execute two fixed sealed-expert comparisons.
No fit, predict, backcasting, outcome-conditioned recipe or promotion exists here.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE));sys.path.insert(0,str(ROOT/'src'))
from core import now,Store,digest
from v3_runtime import ref,write_once,LifecycleJournal
from nfl_model import research_lab_gate as gate
from nfl_model.research_lab_pair_gate import paired_authority,RECIPES
from experiments import ExperimentRegistry,evaluate_paired


def candidate_spec(plan,ordinal,fingerprint):
    recipe=RECIPES[ordinal-1]
    return dict(schema_version='puntproof-lab-candidate-spec-v1',frozen=True,
      hypothesis='The already sealed '+recipe+' distribution may outperform the locked adaptive mixture on the newly completed matched 2026 cohort.',
      mechanism=recipe,features='Exact sealed V3 prior feature matrix; no new features',algorithm='SEALED_PAIRED_DISTRIBUTION_COMPARISON',
      hyperparameters={'component_index':ordinal-1,'recalibration':False},seed=20261027,
      time_partitions={'forecast':plan['forecast_manifest'],'evaluate':[2026,plan['week']]},endpoint='paired_crps',
      matched_population='Exact shared sealed receiving/rushing identities; UNKNOWN/NONAPPEARANCE excluded identically',
      selection_rule='Both fixed selected experts; never select from observed outcome performance',uncertainty_rule='WHOLE_WEEK_97_5_MIN_8_WEEKS',
      duplicate_review=plan['specification'],max_trials=1,max_seconds=300,candidate_ordinal=ordinal,provider_calls=0,protected_access=False,
      week_fingerprint=fingerprint,season=2026,week=plan['week'],coverage_policy='Exact issued population; no population widening',
      multiple_testing_budget={'candidates':2,'confidence':.975,'disclosure':'Repeated exposed development; no independent blind inference'},
      acceptance_rejection_criteria='CRPS upper interval <0; no pooled or higher-usage MAE regression; >=30 higher-usage cases; minimum eight whole weeks for interval',
      comparison_controls='Identical cases, original prior-only high-usage groups, signed supports and completion version',
      stop_criteria=['300 seconds','resource limit','custody or identity mismatch'],code_and_input_binding=plan['specification'])


def prepare_plan(operations):
    store=operations.store;root=operations.root;active=store.get_setting('active_forecast_plan')
    if not active:return None
    consumer,cref=gate.consumer_for(root,gate.DEVELOPMENT)
    if not consumer.get('automatic_paired_policy'):return None
    origin=gate.value(root,active['manifest']);journal=LifecycleJournal(root,origin)
    events=journal.records();package=next((e for e in events if e['state']=='FORECAST_PACKAGES_SEALED'),None)
    if not package or not any(e['state']=='GLOBAL_EXECUTION_SEAL_VERIFIED' for e in events):return None
    key=digest({'origin':active['manifest'],'consumer':cref});directory=store.root/'executions'/('paired-'+key[:24])
    manifest_path=directory/'MANIFEST.json'
    if manifest_path.is_file():return ref(root,manifest_path)
    week=origin['week'];baseline={c:next(e for e in package['evidence'] if e['path']==origin['execution_directory']+'/'+c+'/forecast/predictions.json') for c in ('receiving','rushing')}
    index=ref(root,root/'research/experiment_index.jsonl')
    # Fixed, reviewed recipes are new Lab comparisons. Any later matching entry
    # or existing same-week attempt blocks preparation, not silently renaming it.
    text=(root/index['path']).read_text(encoding='utf8')
    if any(recipe in text for recipe in RECIPES):raise ValueError('CONSUMED_EQUIVALENT_REQUIRES_REVIEW')
    specification=dict(frozen=True,recipes=list(RECIPES),forecast_manifest=active['manifest'],seed=20261027,max_seconds=300,
      hypothesis='Evaluate the two originally sealed selected-expert laws against their locked mixture on the newly completed whole week.',
      mechanism='Paired ablation of the two saved forecast components; no new model or inference',features='Exact prior feature matrix',
      baseline=baseline,partitions={'season':2026,'week':week},eligible_cohorts='Original issued population; prior base_opportunities_12 >=3 receiving or >=8 rushing',
      metrics=['CRPS','MAE','RMSE','80% coverage','zero Brier','high-usage MAE'],exclusions=['unknown appearance','verified nonappearance'],
      budget={'candidates':2,'attempts_each':1,'seconds_each':300,'concurrency':1},stop_criteria=['custody mismatch','resource limit','same-week consumed attempt'],
      implementation=consumer['implementation'],duplicate_review={'status':'NEW_UNCONSUMED_MECHANISM','consumed_equivalent':False,'experiment_index':index,
      'scope':'Two fixed sealed-component comparisons on the newly completed Lab week; no reopening of prior historical selection or protected studies.'})
    spec_ref=ref(root,write_once(directory/'SPECIFICATION.json',specification))
    # Reuse the reviewed generic action witness/canary builder; the new manifest
    # gets its own DEVELOPMENT pretask identity and immutable target witness.
    source_m,_=operations._plan('PREPARE_FORECAST',gate.SOURCE,week,{'population':'Already sealed matched candidate distributions','records':sum(len(store.records('forecast')) for _ in [0]),'forecast_manifest':active['manifest']},inputs=[],identity={'paired':key})
    source=gate.value(root,source_m);pretask=gate.value(root,source['pretask_satisfiability']);pretask['task_id']=gate.DEVELOPMENT
    pretask['review']['unresolved_question']=specification['hypothesis'];pretask['review']['equivalent_work_exists']=False;pretask['review']['reuse_only']=False
    pretask['review']['experiment_index']=index
    pt=ref(root,write_once(directory/'PRETASK.json',pretask))
    m=dict(purpose='PAIRED_COMPARISON',operation=gate.DEVELOPMENT,consumer=cref,policy=consumer['automatic_paired_policy'],
      phase='PREPARE_SEALED_COMPONENTS',season=2026,week=week,recipes=list(RECIPES),forecast_manifest=active['manifest'],pending_sessions=package['pending_sessions'],baseline=baseline,
      specification=spec_ref,pretask_satisfiability=pt,provider_calls=0,cost_cents=0,protected_access=False,fit=False)
    return ref(root,write_once(manifest_path,m))


def run_available(operations):
    from manager_bridge import bounded_child
    root=operations.root;store=operations.store
    mr=prepare_plan(operations)
    if mr is None:return {'status':'WAITING_FOR_SEALED_FORECASTS','executed':0}
    m=gate.value(root,mr);key=digest(m['forecast_manifest']);directory=store.root/'executions'/('paired-components-'+key[:24])
    if not (directory/'COMPLETE.json').is_file():
        if (directory/'CLAIM.json').exists():return {'status':'PREPARATION_INTERRUPTED','reason':'Inspect original component-export claim; no hidden retry or new prediction.'}
        write_once(directory/'CLAIM.json',{'manifest':mr,'at':now(),'no_fit':True,'no_predict':True})
        try:bounded_child([str(root/'scratch/v3-env/Scripts/python.exe'),'-I','-B',str(HERE/'paired_worker.py'),'--export',json.dumps(mr)],root,directory/'export',seconds=300)
        except (ValueError,OSError,RuntimeError) as e:
            write_once(directory/'FAILED.json',{'reason':str(e),'at':now()});raise
    components=gate.value(root,ref(root,directory/'COMPLETE.json'))['components']
    origin=gate.value(root,m['forecast_manifest']);journal=LifecycleJournal(root,origin)
    if journal.phase()!='AUTHORITY_CLOSED':
        return {'status':'WAITING_FOR_VERIFIED_WHOLE_WEEK','prepared_candidates':2,'executed':0,'specification':m['specification'],'reason':'Both sealed component candidates are prepared. Evaluation waits for the original week to finish settlement and learning.'}
    response=next(e for e in journal.records() if e['state']=='RESPONSE_ACCESS_ENABLED')
    evaluation=dict(m,phase='EVALUATE_COMPLETE_WEEK',components=components,completion=response['completion'])
    dest=store.root/'executions'/('paired-'+digest(evaluation)[:24]);er=ref(root,write_once(dest/'MANIFEST.json',evaluation))
    with paired_authority(root,er):
        whole_hash=digest(response['completion']);registry=ExperimentRegistry(store)
        with store.connect() as db:existing={x['ordinal']:dict(x) for x in db.execute('SELECT * FROM lab_candidate_attempts WHERE season=2026 AND week=?',(m['week'],))}
        for ordinal in (1,2):
            if ordinal in existing:continue
            spec=candidate_spec(evaluation,ordinal,whole_hash)
            attempt=registry.reserve(spec,whole_week_receipt={'status':'VERIFIED_COMPLETE','fingerprint':whole_hash},duplicate_review={'status':'NEW_UNCONSUMED_MECHANISM','consumed_equivalent':False,'fingerprint':whole_hash})
            try:
                baseline=[];candidate=[];outcomes=[];completion=gate.value(root,evaluation['completion'])
                for channel in ('receiving','rushing'):
                    data=gate.value(root,components[channel]);baseline.extend(data['baseline']);candidate.extend(data['candidates'][str(ordinal)])
                    outcomes.extend(gate.value(root,completion['outcomes'][channel])['rows'])
                result=evaluate_paired(spec,baseline,candidate,outcomes)
            except Exception as e:result={'decision':'FAILED','reason':type(e).__name__+': '+str(e),'trials':1,'promotion':'DISABLED'}
            registry.finish(attempt,dict(result,classification='EXPOSED_RETROSPECTIVE_DEVELOPMENT',season=2026,week=m['week'],ordinal=ordinal,completed_at=now(),manifest=er))
        return {'status':'PAIRED_COMPARISONS_COMPLETE','executed':sum(x.get('week')==m['week'] for x in store.records('experiment_result')),'manifest':er}


def export_components(manifest_ref):
    from engine_bridge import no_network,verify_package,import_engine,runtime_versions
    with no_network(),paired_authority(ROOT,manifest_ref) as m:
        if m['phase']!='PREPARE_SEALED_COMPONENTS':raise ValueError('EXPORT_PHASE_REQUIRED')
        package=ROOT/'scratch/research-lab-starter/engine/PuntProof_Adaptive_V3'
        verify_package(package);runtime_versions();import_engine(package)
        import joblib
        key=digest(m['forecast_manifest']);directory=ROOT/'data/private/research-lab/executions'/('paired-components-'+key[:24]);components={}
        for channel in ('receiving','rushing'):
            session=joblib.load(gate.reference(ROOT,m['pending_sessions'][channel]));pending=session.pending
            if pending is None or list(pending['period'])!=[2026,m['week']]:raise ValueError('PENDING_COMPONENT_PERIOD')
            baseline=gate.value(ROOT,m['baseline'][channel])['rows'];fixtures=pending['fixtures']
            if len(baseline)!=len(fixtures):raise ValueError('COMPONENT_POPULATION')
            candidates={'1':[],'2':[]};feature=session.feature_names.index('base_opportunities_12')
            for i,f in enumerate(fixtures):
                high=bool(pending['x'][i,feature]>=(3 if channel=='receiving' else 8));baseline[i]=dict(baseline[i],high_usage=high)
                for ordinal in (1,2):
                    support,pmf,_=pending['laws'][ordinal-1]
                    candidates[str(ordinal)].append(dict(f,high_usage=high,support=support.tolist(),pmf=pmf[i].tolist()))
            body=dict(pending_session=m['pending_sessions'][channel],forecast_manifest=m['forecast_manifest'],baseline=baseline,candidates=candidates,fits=0,predict_calls=0)
            components[channel]=ref(ROOT,write_once(directory/(channel+'.json'),body))
        write_once(directory/'COMPLETE.json',{'components':components,'fits':0,'predict_calls':0,'manifest':manifest_ref})
        return {'status':'SEALED_COMPONENTS_EXPORTED','components':components,'fits':0,'predict_calls':0}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--export',required=True);a=p.parse_args();print(json.dumps(export_components(json.loads(a.export))))
