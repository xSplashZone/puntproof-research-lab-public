"""Offline verification of supplied code with authored fixtures only.

Run with scratch/v3-env/Scripts/python.exe -I -B. No real saved-model artifact,
protected outcome, historical design matrix, provider or production is opened.
"""
from __future__ import annotations
import argparse
import copy
from datetime import datetime,timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import unittest

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
if str(HERE) not in sys.path:sys.path.insert(0,str(HERE))
import engine_bridge as bridge


def fixture(index=0,week=7,channel='receiving',team='AAA'):
    return {'season':2026,'week':week,'game_id':f'2026_{week:02d}_AAA_BBB',
            'gsis':f'00-{index+1:07d}','team':team,'opponent':'BBB' if team=='AAA' else 'AAA',
            'position':'RB','channel':channel,'name':f'Synthetic player {index+1}'}


def authored_inputs(path):
    records=[];contexts=[];rb=[]
    for week in range(1,7):
        for team in ('AAA','BBB'):
            group=[]
            for i in range(10):
                f=fixture(i+(100 if team=='BBB' else 0),week,team=team)
                f.pop('channel');f.pop('name')
                targets=3+(i+week)%5; catches=targets-1; carries=4+(2*i+week)%7
                row=dict(f,appeared=True,targets=targets,receptions=catches,receiving_yards=(i+week)*3-8,
                    carries=carries,rushing_yards=(i+2*week)*2-7,snap_share=.4+(i%4)*.1,offense_snaps=30+i)
                records.append(row);group.append(row)
            context={k:group[0][k] for k in ('season','week','game_id','team','opponent')}
            context.update({k:sum(row[k] for row in group) for k in ('targets','receptions','receiving_yards','carries','rushing_yards')})
            contexts.append(context)
            rb.append(dict({k:context[k] for k in ('season','week','game_id','team','opponent')},
                rbfb_eligible_carries=context['carries'],rbfb_eligible_rushing_yards=context['rushing_yards']))
    data={'records':records,'team_context':contexts,'advanced':[]}
    save_inputs(path,data,rb)
    return data,rb


def save_inputs(path,data,rb):
    path=Path(path)
    for sub in ('revised-source-01','context-source-02'):(path/sub).mkdir(parents=True,exist_ok=True)
    (path/'revised-source-01/DATA.json').write_text(json.dumps(data,allow_nan=False),encoding='utf-8')
    (path/'context-source-02/RBFB_CONTEXT.json').write_text(json.dumps(rb,allow_nan=False),encoding='utf-8')


def synthetic_checks(package,dest):
    import numpy as np
    import joblib
    modules=bridge.import_engine(package)
    from adaptive import AdaptiveMixer,OnlineCDFCalibrator
    from learning_session import ResearchSession
    from models import UpdatingLeafForest,distribution_metrics
    passed=[]
    inputs=dest/'authored-inputs';data,rb=authored_inputs(inputs)
    fixtures=[fixture(i) for i in range(10)]
    prepared=bridge.build_features(package,inputs,fixtures,[2026,7])
    assert prepared['x'].shape==(10,228) and not prepared['exclusions']
    passed.append('exact_228_feature_schema')
    poisoned=copy.deepcopy(data);poisoned_rb=copy.deepcopy(rb)
    for future_week in (7,8):
        for old in data['records'][-20:]:
            row=dict(old,week=future_week,game_id=f'2026_{future_week:02d}_AAA_BBB',
                targets=99999,receptions=99999,receiving_yards=999999,carries=99999,rushing_yards=-999999)
            poisoned['records'].append(row)
        for old in data['team_context'][-2:]:
            poisoned['team_context'].append(dict(old,week=future_week,game_id=f'2026_{future_week:02d}_AAA_BBB',
                targets=999999,carries=999999,receiving_yards=999999,rushing_yards=-999999))
        for old in rb[-2:]:
            poisoned_rb.append(dict(old,week=future_week,game_id=f'2026_{future_week:02d}_AAA_BBB',
                rbfb_eligible_carries=999999,rbfb_eligible_rushing_yards=-999999))
    save_inputs(dest/'poisoned-inputs',poisoned,poisoned_rb)
    poisoned_features=bridge.build_features(package,dest/'poisoned-inputs',fixtures,[2026,7])
    np.testing.assert_array_equal(prepared['x'],poisoned_features['x'])
    passed.append('same_week_poisoning')
    changed=copy.deepcopy(data)
    changed['records'][100]['receiving_yards']+=123
    save_inputs(dest/'prior-changed-inputs',changed,rb)
    changed_features=bridge.build_features(package,dest/'prior-changed-inputs',fixtures,[2026,7])
    assert not np.array_equal(prepared['x'],changed_features['x'],equal_nan=True)
    passed.append('prior_history_positive_control')
    missing=copy.deepcopy(data);missing['records'][100]['receiving_yards']=None
    save_inputs(dest/'missing-inputs',missing,rb)
    missing_features=bridge.build_features(package,dest/'missing-inputs',fixtures,[2026,7])
    assert len(missing_features['fixtures'])==9 and missing_features['exclusions'][0]['reason']=='UNKNOWN_RECENT_CHANNEL_VALUES'
    passed.append('missing_not_zero')
    position=copy.deepcopy(fixtures);position[0]['position']='TE'
    excluded=bridge.build_features(package,inputs,position,[2026,7])
    assert excluded['exclusions'][0]['reason']=='DATED_POSITION_CHANGE'
    passed.append('temporal_identity')
    names=prepared['names'];rng=np.random.default_rng(20261026)
    hx=rng.normal(size=(120,228)).astype(np.float32);hx[:,names.index('base_opportunities_12')]=rng.uniform(1,8,120)
    hy=rng.integers(-12,65,120).astype(float);hw=rng.integers(0,12,120).astype(float)
    configs={'synthetic_a':{'kind':'updating_forest','trees':5,'leaf':5,'depth':3,'joint':True,'transform':'sqrt','update':'4weeks'},
             'synthetic_b':{'kind':'updating_forest','trees':7,'leaf':7,'depth':3,'extra':True,'joint':True,'update':'4weeks'}}
    models={}
    for name,config in configs.items():
        model=UpdatingLeafForest(config).fit(hx,hy,hw);model.train_cutoff=(2026,6);models[name]=model
    selection={'channel':'receiving','expert1':'synthetic_a','expert2':'synthetic_b','policy':'adaptive_cal'}
    state={'mixer':AdaptiveMixer(shrink=5).to_dict(),'calibrator':OnlineCDFCalibrator(prior=5).to_dict()}
    keys=[(2026,f'SYN_HISTORY_{i}',f'00-{1000+i:07d}','AAA','receiving') for i in range(len(hy))]
    session=ResearchSession(selection,configs,models,state,names,hx,hy,hw,[(2026,6)]*len(hy),keys,(2026,6))
    initial=dest/'synthetic_INITIAL.joblib';session.save_new(initial);initial_hash=bridge.sha(initial)
    cycles=[];archives=[];prior_sha=initial_hash;last=initial
    for week in (7,8):
        period=[2026,week];rows=[fixture(i,week) for i in range(10)]
        x=prepared['x'].copy();x[:,names.index('week_of_season')]=week
        restored=joblib.load(last)
        # Two independent copies yield numerically identical forecasts after reload.
        reference=copy.deepcopy(session).forecast_week(period,x,rows,dest/f'synthetic_W{week}_ROUNDTRIP.npz')
        actual=restored.forecast_week(period,x,rows,dest/f'synthetic_W{week}_FORECAST.npz')
        for field in ('support','pmf','workload','weights'):np.testing.assert_array_equal(reference[field],actual[field])
        np.testing.assert_allclose(actual['pmf'].sum(1),1,atol=1e-12)
        assert np.isfinite(actual['pmf']).all() and (actual['pmf']>=0).all()
        archive=dest/f'synthetic_W{week}_FORECAST.npz';archive_sha=bridge.sha(archive);archives.append((archive,archive_sha))
        pending=dest/f'synthetic_W{week}_PENDING.joblib';restored.save_new(pending)
        assert bridge.sha(last)==prior_sha
        settled=joblib.load(pending)
        outcomes=[]
        for i,row in enumerate(rows):
            status='NONAPPEARANCE' if i==8 else 'UNKNOWN' if i==9 else 'APPEARED'
            outcomes.append(dict(row,status=status,yards=None if i>=8 else (-3 if i==0 else 0 if i==1 else i*5+week),
                opportunities=None if i>=8 else (0 if i==1 else i+1)))
        bridge.validate_outcomes(outcomes,rows,period)
        receipt=settled.settle_week(period,outcomes,{'scope':'AUTHORED_SYNTHETIC_ONLY','week':week})
        assert receipt['training_rows_added']==8 and receipt['excluded']=={'NONAPPEARANCE':1,'UNKNOWN':1}
        assert settled.mixer.updates==week-6 and settled.calibrator.updates==week-6
        assert all(m.update_count==week-6 for m in settled.models.values())
        assert settled.pending is None and settled.through_period==(2026,week)
        assert bridge.sha(archive)==archive_sha
        try:settled.settle_week(period,outcomes,{'scope':'AUTHORED_SYNTHETIC_ONLY'})
        except ValueError:pass
        else:raise AssertionError('duplicate settlement accepted')
        last=dest/f'synthetic_AFTER_W{week}.joblib';settled.save_new(last);prior_sha=bridge.sha(last)
        session=settled
        metrics=distribution_metrics(actual['support'],actual['pmf'][:8],np.array([r['yards'] for r in outcomes[:8]]))
        cycles.append({'period':period,'forecasts':10,'training_rows_added':8,'excluded':receipt['excluded'],
            'mixer_updates':settled.mixer.updates,'calibrator_updates':settled.calibrator.updates,
            'forecast_sha256':archive_sha,'session_sha256':prior_sha,'synthetic_mean_crps':float(np.mean(metrics['crps']))})
    assert bridge.sha(initial)==initial_hash
    assert all(bridge.sha(path)==expected for path,expected in archives)
    passed+=['artifact_roundtrip','duplicate_settlement','two_successive_learning_cycles','forecast_archive_immutable',
        'signed_zero_labels','nonappearance_unknown_excluded','coherent_20_forecasts','mixer_calibrator_forest_updates']
    # Isolated bad fixtures and chronology attempts cannot change saved session bytes.
    before=bridge.sha(last);bad=[dict(fixture(i,9),y=999999) for i in range(10)]
    try:session.forecast_week((2026,9),prepared['x'],bad,dest/'forbidden.npz')
    except ValueError:pass
    else:raise AssertionError('outcome field accepted in fixture')
    assert not (dest/'forbidden.npz').exists() and bridge.sha(last)==before
    try:session.forecast_week((2026,10),prepared['x'],[fixture(i,10) for i in range(10)],dest/'gap.npz')
    except ValueError:pass
    else:raise AssertionError('week gap accepted')
    passed+=['outcome_in_fixture_rejected','unsettled_week_gap_rejected']
    return {'status':'PASS','same_adapter':True,'scope':'AUTHORED_SYNTHETIC_ONLY','provider_calls':0,
        'real_outcomes_opened':0,'real_saved_models_loaded':0,'synthetic_forecasts':20,'cycles':cycles,
        'feature_count':228,'passed_defects':passed,'final_through_period':[2026,8],
        'frozen_initial_unchanged':True,'excluded_count':4,'learning_rows_added':16}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-root',default=str(ROOT/'scratch/research-lab-starter/engine/PuntProof_Adaptive_V3'))
    import uuid
    run_id='validation-'+uuid.uuid4().hex[:16]
    parser.add_argument('--output-dir',default=str(ROOT/'scratch/v3-verification'/run_id))
    parser.add_argument('--receipt',default=str(ROOT/'research/research_lab_v1'/('ENGINE_'+run_id+'.json')))
    args=parser.parse_args(argv)
    package=bridge.safe_path(ROOT,args.engine_root);dest=bridge.safe_path(ROOT,args.output_dir)
    receipt_path=bridge.safe_path(ROOT,args.receipt)
    bridge.need(dest.is_relative_to(ROOT/'scratch') and not dest.exists(),'EXCLUSIVE_SCRATCH_VERIFICATION_DIRECTORY')
    bridge.need(not receipt_path.exists(),'VERIFICATION_RECEIPT_EXISTS')
    versions=bridge.runtime_versions();info=bridge.verify_package(package)
    dest.mkdir(parents=True,exist_ok=False)
    os.environ['TMP']=str(dest);os.environ['TEMP']=str(dest)
    # Copy only authenticated source/schema. Real joblibs and inputs never enter this test sandbox.
    disposable=dest/'package';(disposable/'outputs').mkdir(parents=True)
    shutil.copytree(package/'src',disposable/'src')
    shutil.copy2(package/'outputs/feature_names.json',disposable/'outputs/feature_names.json')
    report={'schema_version':'puntproof-engine-verification-v1','observed_at_utc':datetime.now(timezone.utc).isoformat(),
        'status':'FAIL','scope':'AUTHORED_SYNTHETIC_ONLY','python':sys.version,'versions':versions,
        'engine_manifest_sha256':info['manifest_sha256'],'provider_calls':0,'real_outcomes_opened':0,
        'real_saved_models_loaded':0,'historical_matrix_tests':'NOT_RUN: missing Runtime Edition inputs; real-data admission required',
        'full_saved_replay':'UNAVAILABLE: Runtime Edition lacks full evidence replay inputs'}
    with bridge.no_network():
        bridge.import_engine(disposable)
        import test_learning,test_model
        suite=unittest.TestSuite()
        loader=unittest.TestLoader()
        for group in (test_learning.LearningTests,test_model.ProbabilityTests,test_model.LearningTests):
            suite.addTests(loader.loadTestsFromTestCase(group))
        stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
        (dest/'supplied-synthetic-tests.txt').write_text(stream.getvalue(),encoding='utf-8')
        report['supplied_tests']={'run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
            'skipped':len(result.skipped),'status':'PASS' if result.wasSuccessful() else 'FAIL'}
        if result.wasSuccessful():
            try:
                report['authored_checks']=synthetic_checks(disposable,dest)
                report['status']='PASS'
            except Exception as error:
                import traceback
                report['error']=type(error).__name__+': '+str(error)
                (dest/'failure-traceback.txt').write_text(traceback.format_exc(),encoding='utf-8')
    bridge.need(bridge.verify_package(package)['status']=='VERIFIED','ORIGINAL_PACKAGE_CHANGED')
    report['original_package_unchanged']=True
    report['implementation']=[bridge.artifact(HERE/name) for name in ('engine_bridge.py','verify_engine.py')]
    bridge.write_new(receipt_path,report)
    print(json.dumps({'status':report['status'],'supplied_tests':report['supplied_tests'],
        'authored_checks':report.get('authored_checks',{}),'error':report.get('error'),
        'receipt':bridge.artifact(receipt_path)},allow_nan=False))
    return 0 if report['status']=='PASS' else 1


if __name__=='__main__':raise SystemExit(main())
