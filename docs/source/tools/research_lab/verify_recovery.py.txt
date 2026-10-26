"""Isolated authored crash/recovery parity check; never opens a real NFL model."""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import copy
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import sys
import traceback
from types import SimpleNamespace
import uuid
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE))
import engine_bridge as bridge
from verify_engine import authored_inputs,fixture


def checks(package,root):
    import numpy as np
    import joblib
    modules=bridge.import_engine(package)
    from adaptive import AdaptiveMixer,OnlineCDFCalibrator
    from learning_session import ResearchSession
    from models import QuantileModel,LeafDistributionForest,UpdatingLeafForest
    passed=[]
    def assert_model_state_equal(left,right):
        # sklearn Tree node records have seven unused padding bytes. Compare
        # every named scientific field exactly, excluding only unused padding.
        assert set(left)==set(right)
        for name in left:
            a,b=left[name],right[name]
            assert type(a) is type(b) and set(a.__dict__)==set(b.__dict__)
            for key in a.__dict__:
                if key!='forest':
                    assert joblib.hash(getattr(a,key))==joblib.hash(getattr(b,key)),(name,key)
                    continue
                fa,fb=a.forest,b.forest
                assert set(fa.__dict__)==set(fb.__dict__)
                for field in fa.__dict__:
                    if field!='estimators_':
                        assert joblib.hash(getattr(fa,field))==joblib.hash(getattr(fb,field)),field
                assert len(fa.estimators_)==len(fb.estimators_)
                for ta,tb in zip(fa.estimators_,fb.estimators_):
                    assert set(ta.__dict__)==set(tb.__dict__)
                    for field in ta.__dict__:
                        if field!='tree_':
                            assert joblib.hash(getattr(ta,field))==joblib.hash(getattr(tb,field)),field
                    sa,sb=ta.tree_.__getstate__(),tb.tree_.__getstate__()
                    assert set(sa)==set(sb)
                    for field in sa:
                        if field=='nodes':
                            assert sa[field].dtype==sb[field].dtype and sa[field].shape==sb[field].shape
                            for column in sa[field].dtype.names:
                                assert bridge.bitwise_array_equal(sa[field][column],sb[field][column]),column
                        elif isinstance(sa[field],np.ndarray):
                            assert bridge.bitwise_array_equal(sa[field],sb[field]),field
                        else:assert sa[field]==sb[field],field
    inputs=root/'authored-inputs'; authored_inputs(inputs)
    fixtures=[fixture(i) for i in range(10)]
    fixtures_path=root/'fixtures.json'
    bridge.write_new(fixtures_path,fixtures)
    prepared=bridge.build_features(package,inputs,fixtures,[2026,7])
    assert prepared['x'].shape==(10,228) and not prepared['exclusions']
    names=prepared['names'];rng=np.random.default_rng(20261027)
    hx=rng.normal(size=(80,228)).astype(np.float32)
    hx[:,names.index('base_opportunities_12')]=rng.uniform(1,8,80)
    hy=rng.integers(-10,60,80).astype(float);hw=rng.integers(0,10,80).astype(float)
    lock=bridge.read_json(package/'LOCK.json')
    selection={key:lock['primary']['receiving'][key] for key in ('channel','expert1','expert2','policy')}
    experts=[selection['expert1'],selection['expert2']]
    configs={name:copy.deepcopy(lock['configs'][name]) for name in experts}
    assert [configs[name]['kind'] for name in experts]==['quantile','forest']
    assert all(configs[name]['update']=='4weeks' for name in experts)
    # Preserve the authenticated kinds/cadence; reduce model size only for the
    # disposable authored engineering test. Native construction matches export.
    configs['Qlong_online'].update(trees=3,leaves=3,leaf=5)
    configs['RFlong_online'].update(trees=4,leaf=5,depth=3)
    models={name:modules['learning_session'].model_from(config).fit(hx,hy,hw) for name,config in configs.items()}
    assert type(models['Qlong_online']) is QuantileModel
    assert type(models['RFlong_online']) is LeafDistributionForest
    for model in models.values():model.train_cutoff=(2026,6)
    passed.append('exact_locked_expert_types')
    state={'mixer':AdaptiveMixer(shrink=5).to_dict(),'calibrator':OnlineCDFCalibrator(prior=5).to_dict()}
    keys=[(2026,f'AUTHORED_HISTORY_{i}',f'00-{1000+i:07d}','AAA','receiving') for i in range(80)]
    seed=ResearchSession(selection,configs,models,state,names,hx,hy,hw,[(2026,6)]*80,keys,(2026,6))
    seed_path=root/'seed.joblib';seed.save_new(seed_path);seed_sha=bridge.sha(seed_path)

    def ref(path):
        path=Path(path)
        return {'path':path.relative_to(root).as_posix(),'sha256':bridge.sha(path)}
    def checked(base,reference):
        path=bridge.safe_path(base,reference['path'])
        assert bridge.sha(path)==reference['sha256']
        return path.read_bytes()
    def admitted_model(base,path,expected,channel):
        assert channel=='receiving' and bridge.sha(path)==expected
    phases=[]
    gate=SimpleNamespace(require_phase=lambda phase:phases.append(phase),
        require_model_artifact=admitted_model,require_response_artifact=admitted_model,
        checked=checked,require_forecast_recovery=lambda base,channel:recovery_refs)
    input_refs=[ref(inputs/name) for name in bridge.INPUT_FILES]+[ref(fixtures_path)]
    common={'season':2026,'week':7,'cutoff':'2026-01-01T00:00:00+00:00',
        'as_of':'2026-01-01T00:00:00+00:00','classification':'OFFLINE_ENGINEERING_FIXTURES',
        'fixtures':[{'game_id':fixtures[0]['game_id']}],'input_files':input_refs}
    operation='PUNTPROOF_RESEARCH_LAB_V3_REPLAY_V1'
    def forecast_request(label,action='forecast'):
        execution='data/private/research-lab/executions/'+label
        manifest=dict(common,execution_directory=execution)
        directory=root/execution;directory.mkdir(parents=True)
        manifest_path=directory/'MANIFEST.json';bridge.write_new(manifest_path,manifest)
        request={'schema_version':1,'action':action,'operation':operation,'channel':'receiving','period':[2026,7],
            'engine_root':str(package),'package_manifest_sha256':bridge.PACKAGE_MANIFEST_SHA256,
            'authority':{'execution_manifest':ref(manifest_path),'readiness':{}},
            'parent_session':{'path':str(seed_path),'sha256':seed_sha,'through_period':[2026,6]},
            'input_root':{'path':str(inputs),'files':{name:bridge.sha(inputs/name) for name in bridge.INPUT_FILES}},
            'fixtures':ref(fixtures_path),'output_dir':str(directory/'receiving/forecast'),
            'thresholds':[0,25,25.5,50,75,100]}
        return request,manifest,directory
    def save_request(request,directory):
        path=directory/'receiving/forecast-request.json';path.parent.mkdir(parents=True,exist_ok=True)
        bridge.write_new(path,{k:v for k,v in request.items() if k!='_request_sha256'})
        request['_request_sha256']=bridge.sha(path)
        return path
    def invoke(request,manifest):
        # Only custody bindings are authored doubles. The feature builder, bridge
        # action, unchanged model classes, serialization and learning are real.
        with patch.object(bridge,'validate_consumer_binding',return_value=package), \
             patch.object(bridge,'verify_package',return_value={'manifest_sha256':bridge.PACKAGE_MANIFEST_SHA256}):
            return bridge._execute_admitted(root,request,manifest,gate,{})

    baseline_request,baseline_manifest,baseline_dir=forecast_request('baseline')
    save_request(baseline_request,baseline_dir)
    baseline_result=invoke(baseline_request,baseline_manifest)
    baseline=joblib.load(baseline_result['artifacts']['session']['path'])
    passed.append('normal_bridge_forecast_and_windows_writable_fsync')

    orphan_request,orphan_manifest,orphan_dir=forecast_request('orphan')
    orphan_request_path=save_request(orphan_request,orphan_dir)
    with patch.object(ResearchSession,'save_new',side_effect=OSError('AUTHORED_POST_INFERENCE_CHECKPOINT_FAILURE')):
        try:invoke(orphan_request,orphan_manifest)
        except OSError as error:assert 'AUTHORED_POST_INFERENCE' in str(error)
        else:raise AssertionError('injected checkpoint failure did not fail')
    orphan_leaf=Path(orphan_request['output_dir']);archive=orphan_leaf/'forecast.npz'
    assert archive.is_file() and (orphan_leaf/'FAILED.json').is_file()
    assert not (orphan_leaf/'session.joblib').exists() and not (orphan_leaf/'predictions.json').exists()
    original_sha=bridge.sha(archive)
    passed.append('same_adapter_post_inference_crash_preserves_orphan_archive')
    recovery_refs={'original_archive':ref(archive),'original_request':ref(orphan_request_path),
        'original_manifest':ref(orphan_dir/'MANIFEST.json'),'original_failure':ref(orphan_leaf/'FAILED.json')}
    recovery_request,recovery_manifest,recovery_dir=forecast_request('recovery','recover_forecast')
    recovery_request['recovery']=recovery_refs
    save_request(recovery_request,recovery_dir)
    calls={'quantile':0,'forest':0}
    quantile_predict=QuantileModel.predict;forest_predict=LeafDistributionForest.predict
    def count_quantile(model,x):
        calls['quantile']+=1
        return quantile_predict(model,x)
    def count_forest(model,x):
        calls['forest']+=1
        return forest_predict(model,x)
    with ExitStack() as stack:
        stack.enter_context(patch.object(QuantileModel,'predict',count_quantile))
        stack.enter_context(patch.object(LeafDistributionForest,'predict',count_forest))
        for owner,name in ((ResearchSession,'forecast_week'),(ResearchSession,'settle_week'),
                          (QuantileModel,'fit'),(LeafDistributionForest,'fit'),(UpdatingLeafForest,'fit'),(UpdatingLeafForest,'update'),
                          (AdaptiveMixer,'update_week'),(OnlineCDFCalibrator,'update_week'),
                          (OnlineCDFCalibrator,'apply')):
            stack.enter_context(patch.object(owner,name,side_effect=AssertionError('FORBIDDEN_RECOVERY_CALL:'+name)))
        recovered_result=invoke(recovery_request,recovery_manifest)
    assert calls=={'quantile':1,'forest':1}
    recovered=joblib.load(recovered_result['artifacts']['session']['path'])
    assert recovered_result['recovery']['forecast_week_calls']==recovered_result['fits']==0
    assert recovered.through_period==(2026,6) and recovered.pending['period']==(2026,7)
    assert recovered.pending['archive_path']!=str(archive)
    assert bridge.sha(recovered.pending['archive_path'])==original_sha==bridge.sha(archive)
    assert bridge.sha(seed_path)==seed_sha
    for name in ('support','raw','x','groups'):
        assert bridge.bitwise_array_equal(baseline.pending[name],recovered.pending[name]),name
    law_arrays=0
    for left,right in zip(baseline.pending['laws'],recovered.pending['laws']):
        for a,b in zip(left,right):
            assert bridge.bitwise_array_equal(a,b)
            law_arrays+=1
    assert recovered.pending['keys']==baseline.pending['keys'] and recovered.pending['fixtures']==baseline.pending['fixtures']
    assert_model_state_equal(recovered.models,baseline.models)
    assert recovered.mixer.to_dict()==baseline.mixer.to_dict()
    assert recovered.calibrator.to_dict()==baseline.calibrator.to_dict()
    baseline_predictions=bridge.read_json(baseline_result['artifacts']['predictions']['path'])
    recovered_predictions=bridge.read_json(recovered_result['artifacts']['predictions']['path'])
    assert baseline_predictions==recovered_predictions
    passed+=['one_predict_per_selected_expert_no_fit_update_forecast_or_calibration',
        'all_original_forecast_arrays_bitwise_equal','six_expert_law_arrays_equal_uninterrupted_baseline',
        'native_pending_state_roundtrip','original_archive_and_seed_immutable','prediction_json_equals_baseline']

    # A one-ULP change must fail, not pass through a numerical tolerance.
    with np.load(archive,allow_pickle=False) as original:
        damaged={name:original[name].copy() for name in original.files}
    damaged['weights'][0]=np.nextafter(damaged['weights'][0],np.inf)
    damaged_path=root/'authored-one-ulp-mismatch.npz'
    with damaged_path.open('xb') as stream:np.savez_compressed(stream,**damaged)
    fresh=joblib.load(seed_path)
    try:bridge.recover_forecast_state(fresh,damaged_path,bridge.sha(damaged_path),root/'must-not-exist.npz',
        prepared,[2026,7],modules)
    except ValueError as error:assert 'RECOVERY_BITWISE_MISMATCH:weights' in str(error)
    else:raise AssertionError('one-ULP archive mismatch accepted')
    assert fresh.pending is None and not (root/'must-not-exist.npz').exists()
    signed_zero_a=np.array([0.],dtype=np.float64);signed_zero_b=np.array([-0.],dtype=np.float64)
    assert np.array_equal(signed_zero_a,signed_zero_b) and not bridge.bitwise_array_equal(signed_zero_a,signed_zero_b)
    passed.append('one_ulp_and_signed_zero_require_literal_byte_parity')

    # The actual incident was a guard/test using rushing's updating subclass
    # for receiving's scheduled-refit forest. Reject that exact wrong class.
    wrong_type=joblib.load(seed_path)
    wrong_type.models['RFlong_online']=UpdatingLeafForest(configs['RFlong_online'])
    with patch.object(QuantileModel,'predict',side_effect=AssertionError('prediction before type rejection')):
        try:bridge.recover_forecast_state(wrong_type,archive,original_sha,root/'wrong-type-forbidden.npz',
            prepared,[2026,7],modules)
        except ValueError as error:assert 'RECOVERY_EXPERT_TYPES' in str(error)
        else:raise AssertionError('receiving accepted updating-forest subclass')
    wrong_kind=joblib.load(seed_path);wrong_kind.configs['RFlong_online']['kind']='updating_forest'
    with patch.object(QuantileModel,'predict',side_effect=AssertionError('prediction before kind rejection')):
        try:bridge.recover_forecast_state(wrong_kind,archive,original_sha,root/'wrong-kind-forbidden.npz',
            prepared,[2026,7],modules)
        except ValueError as error:assert 'RECOVERY_EXPERT_KINDS' in str(error)
        else:raise AssertionError('receiving accepted wrong locked expert kind')
    passed+=['updating_forest_subclass_rejected_before_predict','wrong_expert_kind_rejected_before_predict']

    fresh=joblib.load(seed_path);fresh.models['Qlong_online'].train_cutoff=(2025,6)
    with patch.object(QuantileModel,'predict',side_effect=AssertionError('prediction before refit rejection')):
        try:bridge.recover_forecast_state(fresh,archive,original_sha,root/'refit-forbidden.npz',prepared,[2026,7],modules)
        except ValueError as error:assert 'RECOVERY_REFIT_REQUIRED' in str(error)
        else:raise AssertionError('recovery refit accepted')
    changed=copy.deepcopy(prepared);changed['x'][0,names.index('base_opportunities_12')]+=np.float32(1)
    fresh=joblib.load(seed_path)
    with patch.object(QuantileModel,'predict',side_effect=AssertionError('prediction before input rejection')):
        try:bridge.recover_forecast_state(fresh,archive,original_sha,root/'input-forbidden.npz',changed,[2026,7],modules)
        except ValueError as error:assert 'RECOVERY_FEATURE_BYTES_CHANGED' in str(error)
        else:raise AssertionError('changed features accepted')
    passed+=['refit_due_rejected_before_predict','feature_mismatch_rejected_before_predict']

    checkpoint_request,checkpoint_manifest,checkpoint_dir=forecast_request('post-save-flush-failure')
    save_request(checkpoint_request,checkpoint_dir)
    with patch.object(bridge,'_fsync',side_effect=OSError('AUTHORED_POST_SAVE_FLUSH_FAILURE')):
        try:invoke(checkpoint_request,checkpoint_manifest)
        except OSError as error:assert 'AUTHORED_POST_SAVE_FLUSH_FAILURE' in str(error)
        else:raise AssertionError('post-save flush failure did not fail')
    checkpoint_leaf=Path(checkpoint_request['output_dir'])
    checkpoint=joblib.load(checkpoint_leaf/'session.joblib')
    assert checkpoint.pending['period']==(2026,7) and len(checkpoint.pending['laws'])==2
    assert not (checkpoint_leaf/'predictions.json').exists()
    passed.append('future_forecast_persists_pending_before_flush_and_summary')

    outcomes=[]
    for i,row in enumerate(fixtures):
        outcomes.append(dict(row,status='UNKNOWN' if i==9 else 'NONAPPEARANCE' if i==8 else 'APPEARED',
            yards=None if i>=8 else -3 if i==0 else 0 if i==1 else i*4,
            opportunities=None if i>=8 else 0 if i==1 else i+1))
    outcomes_path=root/'authored-outcomes.json';bridge.write_new(outcomes_path,{'rows':outcomes})
    settled=[]
    for label,prior_result,manifest in (('baseline',baseline_result,baseline_manifest),('recovery',recovered_result,recovery_manifest)):
        request={'schema_version':1,'action':'settle','operation':operation,'channel':'receiving','period':[2026,7],
            'engine_root':str(package),'package_manifest_sha256':bridge.PACKAGE_MANIFEST_SHA256,
            'parent_session':dict(prior_result['artifacts']['session'],through_period=[2026,6]),
            'output_dir':str(root/'data/private/research-lab/sessions'/label/'receiving'),
            'outcomes':ref(outcomes_path),'provenance':{'scope':'AUTHORED_SYNTHETIC_ONLY'},
            '_request_sha256':'0'*64}
        result=invoke(request,manifest)
        assert result['receipt']['training_rows_added']==8
        assert result['receipt']['excluded']=={'NONAPPEARANCE':1,'UNKNOWN':1}
        settled.append(joblib.load(result['artifacts']['session']['path']))
    left,right=settled
    for name in ('history_x','history_y','history_workload'):
        assert bridge.bitwise_array_equal(getattr(left,name),getattr(right,name))
    assert left.history_keys==right.history_keys and left.history_periods==right.history_periods
    assert left.mixer.to_dict()==right.mixer.to_dict() and left.calibrator.to_dict()==right.calibrator.to_dict()
    assert_model_state_equal(left.models,right.models)
    # Actual receiving experts learn on their refit cadence. This settlement
    # updates the adaptive mixer and stored history, not forest leaf tables.
    assert_model_state_equal(left.models,baseline.models)
    assert left.mixer.updates==right.mixer.updates==1
    assert left.calibrator.updates==right.calibrator.updates==0
    assert all(type(item) is not UpdatingLeafForest for item in left.models.values())
    assert left.through_period==right.through_period==(2026,7) and left.pending is right.pending is None
    assert left.receipts[-1]['forecast_sha256']==baseline_result['artifacts']['forecast']['sha256']
    assert right.receipts[-1]['forecast_sha256']==original_sha
    # Different ZIP creation timestamps may give baseline and orphan different
    # archive hashes; each receipt must bind its own exact archive. Every other
    # native receipt field, including provenance, must be identical.
    normalized=lambda rows:[{k:v for k,v in row.items() if k not in ('sha256','forecast_sha256')} for row in rows]
    assert normalized(left.receipts)==normalized(right.receipts)
    assert bridge.sha(seed_path)==seed_sha and bridge.sha(archive)==original_sha
    passed+=['same_adapter_settlement_matches_uninterrupted_baseline',
        'adaptive_mixer_history_learning_and_scheduled_experts_match_after_recovery','unknown_and_nonappearance_not_learned_as_zero',
        'native_receipts_equal_with_each_own_archive_hash_verified','deterministic_pending_recovery']
    return {'status':'PASS','same_adapter':True,'passed_checks':passed,'passed_defects':passed,'checks_passed':len(passed),
        'forecast_rows':10,'expert_predict_calls_during_recovery':calls,'expert_law_arrays_compared':law_arrays,
        'archive_arrays_compared':5,'settlement_rows_added_per_branch':8,'excluded_per_branch':2,
        'baseline_recovered_scientific_state_equal':True,'tree_record_unused_padding_bytes_excluded':True,
        'scientific_state_compared':['all_model_attributes','forest_estimator_attributes','every_tree_node_field',
            'tree_values','mixer','calibrator','history_x','history_y','history_workload','history_keys','history_periods',
            'through_period','pending_state','native_receipts_with_own_archive_hashes'],'original_archive_sha256':original_sha,
        'recovered_archive_sha256':recovered.pending['archive_sha256'],'seed_sha256':seed_sha,
        'locked_expert_kinds':{name:configs[name]['kind'] for name in experts},
        'native_expert_classes':{name:type(models[name]).__name__ for name in experts},
        'receiving_experts_unchanged_after_settlement':True}


def main(argv=None):
    run_id='recovery-'+uuid.uuid4().hex[:16]
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',default=str(ROOT/'scratch/v3-recovery-verification'/run_id))
    parser.add_argument('--receipt',default=str(ROOT/'research/research_lab_v1/finish_v1/forecast_type_repair_v1'/
        ('RECOVERY_VERIFICATION_'+run_id+'.json')))
    args=parser.parse_args(argv)
    dest=bridge.safe_path(ROOT,args.output_dir);receipt=bridge.safe_path(ROOT,args.receipt)
    bridge.need(dest.is_relative_to(ROOT/'scratch') and not dest.exists(),'EXCLUSIVE_SYNTHETIC_SCRATCH')
    bridge.need(not receipt.exists(),'VERIFICATION_RECEIPT_EXISTS')
    package=ROOT/'scratch/research-lab-starter/engine/PuntProof_Adaptive_V3'
    versions=bridge.runtime_versions();info=bridge.verify_package(package)
    dest.mkdir(parents=True);root=dest/'authored-root';root.mkdir()
    os.environ['TMP']=str(dest);os.environ['TEMP']=str(dest);os.environ['MPLCONFIGDIR']=str(dest/'matplotlib')
    disposable=root/'engine';(disposable/'outputs').mkdir(parents=True)
    shutil.copytree(package/'src',disposable/'src')
    shutil.copy2(package/'outputs/feature_names.json',disposable/'outputs/feature_names.json')
    shutil.copy2(package/'LOCK.json',disposable/'LOCK.json')
    report={'schema_version':'puntproof-lab-recovery-verification-v1','status':'FAIL',
        'classification':'OFFLINE_ENGINEERING_FIXTURES','observed_at':datetime.now(timezone.utc).isoformat(),
        'provider_calls':0,'real_model_execution':False,'real_outcome_access':False,
        'real_saved_models_loaded':0,'python':sys.version,'versions':versions,
        'package_manifest_sha256':info['manifest_sha256'],
        'custody_test_bindings':'AUTHORED_TEST_DOUBLES_NOT_REAL_ADMISSION',
        'prior_recovery_verifier_status':'SUPERSEDED_WRONG_RECEIVING_FOREST_KIND',
        'prior_verifier_limitation':'The prior fixture used UpdatingLeafForest instead of locked receiving LeafDistributionForest; old receipts remain immutable.',
        'code_sha256':{name:bridge.sha(HERE/name) for name in ('engine_bridge.py','verify_recovery.py')}}
    try:
        with bridge.no_network():report['verification']=checks(disposable,root)
        report['passed_checks']=report['verification']['passed_checks']
        report['passed_defects']=report['verification']['passed_defects']
        assert bridge.verify_package(package)['status']=='VERIFIED'
        report['original_package_unchanged']=True;report['status']='PASS'
    except Exception as error:
        report['error']=type(error).__name__+': '+str(error)
        (dest/'failure-traceback.txt').write_text(traceback.format_exc(),encoding='utf-8',newline='\n')
    receipt.parent.mkdir(parents=True,exist_ok=True);bridge.write_new(receipt,report)
    print(json.dumps({'status':report['status'],'receipt':bridge.artifact(receipt),
        'checks_passed':report.get('verification',{}).get('checks_passed'),'error':report.get('error')},allow_nan=False))
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
