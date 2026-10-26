"""Isolated, gated adapter for the unchanged supplied Adaptive V3 engine.

The CLI is the real-data entrypoint. Imports are inert: no package import, model
load, source read, network or fit occurs until the corresponding admission gate.
"""
from __future__ import annotations
import argparse
import copy
from contextlib import contextmanager
import hashlib
import importlib
import importlib.metadata
import json
import math
import os
from numbers import Integral
from pathlib import Path
import re
import socket
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_MANIFEST_SHA256 = '016609ef2c03fc4d20523e81bed8ffbaed85cccd9a98d75e507775219e267fed'
ENGINE_PINS = {'numpy':'2.3.5','pandas':'2.2.3','scipy':'1.17.0','scikit-learn':'1.8.0','lightgbm':'4.6.0','joblib':'1.5.3','catboost':'1.2.8'}
INPUT_FILES = ('revised-source-01/DATA.json','context-source-02/RBFB_CONTEXT.json')
FIXTURE_KEYS = {'season','week','game_id','gsis','team','opponent','position','channel'}


def need(ok, message):
    if not ok:
        raise ValueError('ENGINE_BRIDGE:'+message)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def _pairs(items):
    result = {}
    for key,value in items:
        need(key not in result,'DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'),object_pairs_hook=_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))


def write_new(path,value):
    path = Path(path)
    raw = (json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()
    with path.open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    return artifact(path)


def artifact(path,root=ROOT):
    path = Path(path).resolve()
    return {'path':path.relative_to(Path(root).resolve()).as_posix(),'sha256':sha(path)}


def safe_path(root,path):
    root = Path(root).resolve(); path = Path(path)
    path = path if path.is_absolute() else root/path
    need(path.resolve().is_relative_to(root) and path.resolve()!=root,'PATH_ESCAPE')
    for part in (path,*path.parents):
        need(not part.is_symlink() and not getattr(part,'is_junction',lambda:False)(),'LINKED_PATH')
        if part==root: break
    return path.resolve()


@contextmanager
def no_network():
    """Deny Python socket calls, including DNS; an accidental-use guard, not an OS sandbox."""
    original = {name:getattr(socket,name) for name in ('socket','create_connection','getaddrinfo')}
    class OfflineSocket(original['socket']):
        def connect(self,*args,**kwargs): raise RuntimeError('ENGINE_NETWORK_DENIED')
        def connect_ex(self,*args,**kwargs): raise RuntimeError('ENGINE_NETWORK_DENIED')
        def sendto(self,*args,**kwargs): raise RuntimeError('ENGINE_NETWORK_DENIED')
    def denied(*args,**kwargs): raise RuntimeError('ENGINE_NETWORK_DENIED')
    socket.socket = OfflineSocket; socket.create_connection = denied; socket.getaddrinfo = denied
    try: yield
    finally:
        for name,value in original.items(): setattr(socket,name,value)


def runtime_versions():
    need(sys.version_info[:3]==(3,13,17),'ISOLATED_PYTHON_3_13_17_REQUIRED')
    actual = {name:importlib.metadata.version(name) for name in ENGINE_PINS}
    need(actual==ENGINE_PINS,'ENGINE_DEPENDENCY_PIN_MISMATCH')
    return actual


def verify_package(package,expected=PACKAGE_MANIFEST_SHA256):
    """Opaque package hashes before importing any delivered Python or loading joblib."""
    need(expected==PACKAGE_MANIFEST_SHA256,'UNRECOGNIZED_PACKAGE_MANIFEST')
    package = Path(package).resolve()
    need(sha(package/'PACKAGE_MANIFEST.json')==expected,'PACKAGE_MANIFEST_CHANGED')
    if str(Path(__file__).parent) not in sys.path: sys.path.insert(0,str(Path(__file__).parent))
    from package_adapter import inspect_package
    result = inspect_package(package)
    need(result['status']=='VERIFIED' and result['manifest_sha256']==expected,'PACKAGE_INTEGRITY_FAILED')
    return result


def import_engine(package):
    package = Path(package).resolve(); source = package/'src'
    os.environ['OMP_NUM_THREADS']='2'; os.environ['OPENBLAS_NUM_THREADS']='2'; os.environ['MKL_NUM_THREADS']='2'
    os.environ['PYTHONDONTWRITEBYTECODE']='1'; sys.dont_write_bytecode=True
    for name in ('prepare','base_features','models','adaptive','learning_session','inference_cli'):
        if name in sys.modules:
            origin = Path(sys.modules[name].__file__).resolve()
            need(origin.is_relative_to(source),'FOREIGN_ENGINE_MODULE:'+name)
    if str(source) not in sys.path: sys.path.insert(0,str(source))
    return {name:importlib.import_module(name) for name in ('prepare','base_features','learning_session','inference_cli')}


def validate_fixtures(fixtures,period,channel=None):
    need(isinstance(period,(list,tuple)) and len(period)==2 and all(type(x) is int for x in period),'PERIOD')
    need(isinstance(fixtures,list) and fixtures,'EMPTY_FIXTURES')
    keys = set()
    for row in fixtures:
        need(isinstance(row,dict) and FIXTURE_KEYS<=set(row)<=FIXTURE_KEYS|{'name'},'FIXTURE_METADATA_ONLY')
        need(type(row['season']) is int and type(row['week']) is int and [row['season'],row['week']]==list(period),'FIXTURE_PERIOD')
        need(row['channel'] in ('receiving','rushing') and row['position'] in ('WR','TE','RB','FB'),'FIXTURE_CHANNEL_POSITION')
        need(channel is None or row['channel']==channel,'MIXED_CHANNEL')
        need(row['channel']!='rushing' or row['position'] in ('RB','FB'),'RBFB_RUSHING_ONLY')
        need(all(isinstance(row[k],str) and row[k] for k in ('game_id','gsis','team','opponent','position','channel')),'FIXTURE_IDENTITY')
        need(re.fullmatch(r'\d{2}-\d{7}',row['gsis']) is not None,'GSIS_FORMAT')
        identity = tuple(row[k] for k in ('season','game_id','gsis','team','channel'))
        need(identity not in keys,'DUPLICATE_FIXTURE'); keys.add(identity)
    return fixtures


def validate_outcomes(outcomes,fixtures,period):
    need(isinstance(outcomes,list),'OUTCOME_LIST')
    expected = {tuple(f[k] for k in ('season','game_id','gsis','team','channel')) for f in fixtures}
    found = set()
    for row in outcomes:
        need(isinstance(row,dict) and {'season','week','game_id','gsis','team','channel','status'}<=set(row),'OUTCOME_SCHEMA')
        need(set(row)<=FIXTURE_KEYS|{'name','status','yards','opportunities'},'UNEXPECTED_OUTCOME_FIELDS')
        need([row['season'],row['week']]==list(period),'OUTCOME_PERIOD')
        key = tuple(row[k] for k in ('season','game_id','gsis','team','channel'))
        need(key not in found,'DUPLICATE_OUTCOME'); found.add(key)
        status = row['status']
        need(status in ('APPEARED','NONAPPEARANCE','UNKNOWN'),'OUTCOME_STATUS')
        if status=='APPEARED':
            for name in ('yards','opportunities'):
                v = row.get(name)
                need(type(v) in (int,float) and math.isfinite(v) and v==int(v),'KNOWN_INTEGER_LABEL')
            need(row['opportunities']>=0,'NEGATIVE_OPPORTUNITIES')
        else:
            need(row.get('yards') is None and row.get('opportunities') is None,'UNKNOWN_IS_NOT_ZERO')
    need(found==expected,'EXACT_SETTLEMENT_CENSUS')
    return outcomes


def build_features(package,input_root,fixtures,period):
    """Caller admits inputs first; synthetic verification uses authored data only."""
    validate_fixtures(fixtures,period)
    modules = import_engine(package)
    prepare = modules['prepare']; prepare.INPUT = Path(input_root).resolve()
    d,records,contexts,defenses,team_index,posoff,posdef = prepare.load()
    base = modules['base_features'].FeatureStore(d['records'],d['team_context'],d['advanced'])
    names = read_json(Path(package)/'outputs/feature_names.json')
    rows=[]; eligible=[]; exclusions=[]; histories=[]
    for fixture in fixtures:
        target = {k:v for k,v in fixture.items() if k!='channel'}
        features,reason = base.build(target,fixture['channel'])
        if reason:
            exclusions.append({'fixture':fixture,'status':'INSUFFICIENT_DATA','reason':reason}); continue
        need(tuple(features['max_history_week'])<tuple(period),'NONPRIOR_HISTORY')
        row = prepare.build_row({'target':target,'channel':fixture['channel'],'features':features},
            records,contexts,defenses,team_index,posoff,posdef,d['_global_players'])
        need(list(row)==names and len(names)==228,'EXACT_228_FEATURE_SCHEMA')
        rows.append([row[n] for n in names]); eligible.append(fixture)
        histories.append({'fixture':fixture,'max_history_week':list(features['max_history_week'])})
    import numpy as np
    x=np.asarray(rows,dtype=np.float32).reshape((-1,len(names)))
    need(not np.isinf(x).any(),'INFINITE_FEATURE')
    return {'x':x,'names':names,'fixtures':eligible,'exclusions':exclusions,'histories':histories}


def _bound_input(root,ref,manifest,gate):
    path=safe_path(root,ref['path'])
    normalized={'path':path.relative_to(root).as_posix(),'sha256':ref['sha256']}
    need(normalized in manifest['input_files'],'INPUT_NOT_ADMITTED')
    gate.checked(root,normalized)
    return path


def _inputs(root,request,manifest,gate):
    reference = request['fixtures']; path = _bound_input(root,reference,manifest,gate)
    fixtures = read_json(path)
    validate_fixtures(fixtures,request['period'],request['channel'])
    expected_games = {str(f['game_id']) for f in manifest['fixtures']}
    need({f['game_id'] for f in fixtures}<=expected_games,'FIXTURE_OUTSIDE_ADMITTED_WEEK')
    source = request['input_root']; directory = safe_path(root,source['path'])
    need(isinstance(source.get('files'),dict) and set(source['files'])==set(INPUT_FILES),'CANONICAL_SOURCE_FILES')
    for name in INPUT_FILES:
        file=safe_path(directory,name)
        ref={'path':file.relative_to(root).as_posix(),'sha256':source['files'][name]}
        _bound_input(root,ref,manifest,gate); gate.checked(root,ref)
    return directory,fixtures


def _fsync(path):
    with Path(path).open('r+b') as stream: os.fsync(stream.fileno())



def _recovery_inputs(root,request,manifest,gate):
    """Validate the committed, exclusively claimed recovery before package imports."""
    refs=gate.require_forecast_recovery(root,request['channel'])
    need(request.get('recovery')==refs,'RECOVERY_REFERENCES')
    need(set(refs)=={'original_archive','original_request','original_manifest','original_failure'},'RECOVERY_REFERENCE_SET')
    paths={}
    for name,ref in refs.items():
        need(set(ref)=={'path','sha256'},'RECOVERY_REFERENCE_SCHEMA')
        path=safe_path(root,ref['path']); gate.checked(root,ref)
        need(sha(path)==ref['sha256'],'RECOVERY_REFERENCE_CHANGED')
        paths[name]=path
    original=read_json(paths['original_request'])
    origin=read_json(paths['original_manifest'])
    failure=read_json(paths['original_failure'])
    need(original['action']=='forecast' and original['channel']=='receiving','RECOVERY_ORIGINAL_ACTION')
    for key in ('schema_version','operation','channel','period','engine_root','package_manifest_sha256',
                'parent_session','input_root','fixtures'):
        need(original[key]==request[key],'RECOVERY_REQUEST_CHANGED:'+key)
    defaults=[25,25.5,50,50.5,75,75.5]
    need(original.get('thresholds',defaults)==request.get('thresholds',defaults),'RECOVERY_THRESHOLDS_CHANGED')
    need(original['authority']['execution_manifest']==refs['original_manifest'],'RECOVERY_ORIGINAL_MANIFEST')
    for key in ('season','week','cutoff','as_of','classification','fixtures'):
        need(origin[key]==manifest[key],'RECOVERY_MANIFEST_CHANGED:'+key)
    leaf=safe_path(root,original['output_dir'])
    need(paths['original_archive']==leaf/'forecast.npz' and paths['original_failure']==leaf/'FAILED.json',
         'RECOVERY_ORIGINAL_OUTPUT_PATH')
    need(leaf.is_relative_to(safe_path(root,origin['execution_directory'])),'RECOVERY_ORIGINAL_SCOPE')
    need(all(not (leaf/name).exists() for name in ('session.joblib','predictions.json','COMPLETE.json','RESULT.json')),
         'RECOVERY_COMPLETED_OUTPUT_EXISTS')
    need(failure.get('status')=='QUARANTINED' and failure.get('action')=='forecast'
         and failure.get('channel')==request['channel'] and failure.get('period')==request['period']
         and failure.get('retry_authorized') is False,'RECOVERY_FAILURE_STATE')
    return paths,refs


def bitwise_array_equal(a,b):
    """Require dtype, shape and actual bytes, including signed zero and NaN bits."""
    return (a.dtype.str==b.dtype.str and a.shape==b.shape
            and not a.dtype.hasobject and not b.dtype.hasobject
            and a.tobytes(order='C')==b.tobytes(order='C'))


def recover_forecast_state(session,archive_path,archive_sha256,copy_path,prepared,period,modules):
    """One exact deterministic inference reconstruction; never forecast/refit/update.

    This recovers lost expert laws from the unchanged seed. Archive agreement
    validates the recomputation; the component laws were not stored in the archive.
    Admission and exclusive recovery claim are mandatory at the CLI caller.
    """
    import numpy as np
    native=modules['learning_session']
    period=tuple(period); archive_path=Path(archive_path); copy_path=Path(copy_path)
    need(session.pending is None,'RECOVERY_PENDING_ALREADY_EXISTS')
    session._ensure_period(period)
    need(session.selection['channel']=='receiving' and session.selection['policy']=='adaptive',
         'RECOVERY_LOCKED_RECEIVING_POLICY')
    names=[session.selection['expert1'],session.selection['expert2']]
    need(names==['Qlong_online','RFlong_online'],'RECOVERY_LOCKED_EXPERTS')
    need([session.configs[name].get('kind') for name in names]==['quantile','forest'],
         'RECOVERY_EXPERT_KINDS')
    need(type(session.models[names[0]]) is native.QuantileModel
         and type(session.models[names[1]]) is native.LeafDistributionForest,'RECOVERY_EXPERT_TYPES')
    need(session.feature_names==prepared['names'],'RECOVERY_FEATURE_NAMES')
    need(all(tuple(p)<period for p in session.history_periods),'RECOVERY_FUTURE_HISTORY')
    for name in names:
        cut=tuple(session.models[name].train_cutoff); config=session.configs[name]
        due=cut[0]!=period[0] or (config.get('update')=='4weeks' and period[1] in (1,5,9,13,17) and cut!=period)
        need(not due,'RECOVERY_REFIT_REQUIRED')
    need(sha(archive_path)==archive_sha256,'RECOVERY_ARCHIVE_CHANGED')
    expected={'support','pmf','raw_pmf','workload','weights','feature_matrix','fixture_json'}
    with np.load(archive_path,allow_pickle=False) as archive:
        need(set(archive.files)==expected,'RECOVERY_ARCHIVE_SCHEMA')
        values={name:archive[name] for name in expected}
    fixture_json=values['fixture_json']
    need(fixture_json.shape==() and fixture_json.dtype.kind=='U','RECOVERY_FIXTURE_JSON')
    fixtures=json.loads(str(fixture_json.item()),object_pairs_hook=_pairs,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))
    validate_fixtures(fixtures,list(period),'receiving')
    need(fixtures==prepared['fixtures'],'RECOVERY_FIXTURES_CHANGED')
    x=values['feature_matrix']
    need(x.dtype==np.dtype('float32') and x.shape==(len(fixtures),len(session.feature_names))
         and not np.isinf(x).any(),'RECOVERY_FEATURE_SCHEMA')
    need(bitwise_array_equal(x,np.asarray(prepared['x'],dtype=np.float32)),'RECOVERY_FEATURE_BYTES_CHANGED')
    keys=[native.identity(f) for f in fixtures]
    # These are the exact operations/order in unchanged ResearchSession.forecast_week.
    # No current outcomes, model fitting, online updates or forecast_week call occurs.
    laws=[session.models[name].predict(x) for name in names]
    high=x[:,session.feature_names.index('base_opportunities_12')]>=3
    groups=np.array([('RB' if f['position']=='FB' else f['position'])+'_'+str(bool(h)) for f,h in zip(fixtures,high)])
    weights=session.mixer.weights(groups,period)
    support,pmf=native.mix_distributions([(laws[0][0],laws[0][1]),(laws[1][0],laws[1][1])],
        np.column_stack([weights,1-weights]))
    pmf/=pmf.sum(1,keepdims=True); raw=pmf.copy()
    workload=weights*laws[0][2]+(1-weights)*laws[1][2]
    computed={'support':support,'pmf':pmf,'raw_pmf':raw,'workload':workload,'weights':weights}
    for name,value in computed.items():
        need(bitwise_array_equal(value,values[name]),'RECOVERY_BITWISE_MISMATCH:'+name)
    need(np.isfinite(pmf).all() and (pmf>=0).all() and np.all(np.abs(pmf.sum(1)-1)<1e-8),'RECOVERY_PMF_INVARIANT')
    need(sha(archive_path)==archive_sha256,'RECOVERY_ARCHIVE_CHANGED')
    # The original remains untouched. The exact copy belongs to the new gated
    # execution directory so the native pending archive remains settlement-ready.
    with archive_path.open('rb') as source,copy_path.open('xb') as target:
        shutil.copyfileobj(source,target); target.flush(); os.fsync(target.fileno())
    need(sha(copy_path)==archive_sha256 and sha(archive_path)==archive_sha256,'RECOVERY_COPY_HASH')
    session.pending={'period':period,'keys':keys,'fixtures':copy.deepcopy(fixtures),'x':x.copy(),
        'groups':groups,'laws':laws,'support':support.copy(),'raw':raw,
        'archive_sha256':archive_sha256,'archive_path':str(copy_path)}
    receipt={'action':'FORECAST_SEALED','period':list(period),'sha256':archive_sha256,
        'numerical_forecasts':len(x),'source_state_through':list(session.through_period),'new_fits':[]}
    session.receipts.append(receipt)
    return {'support':values['support'],'pmf':values['pmf'],'workload':values['workload'],
        'weights':values['weights'],'receipt':receipt,
        'recovery':{'method':'SAME_ATTEMPT_DETERMINISTIC_EXPERT_INFERENCE_RECONSTRUCTION',
            'component_laws':'REDERIVED_FROM_IDENTICAL_IMMUTABLE_SEED_NOT_STORED_IN_ORIGINAL_ARCHIVE',
            'expert_predict_calls':2,'forecast_week_calls':0,'fit_calls':0,'update_calls':0,
            'target_outcomes_opened':0,'bitwise_arrays':list(computed),'archive_copy_sha256':archive_sha256}}


def validate_consumer_binding(root,request,consumer,gate):
    binding=consumer['engine_package']
    package=safe_path(root,request['engine_root'])
    need(package==safe_path(root,binding['root']),'UNBOUND_ENGINE_ROOT')
    need(binding['package_manifest']['sha256']==request['package_manifest_sha256']==PACKAGE_MANIFEST_SHA256,'ENGINE_CONSUMER_MANIFEST')
    need(gate.reference(root,binding['package_manifest'])==package/'PACKAGE_MANIFEST.json','ENGINE_MANIFEST_PATH')
    for name,relative in (('package_manifest','PACKAGE_MANIFEST.json'),('lock','LOCK.json'),('protocol','PROTOCOL.json'),('feature_schema','outputs/feature_names.json')):
        need(gate.reference(root,binding[name])==package/relative,'ENGINE_METADATA_PATH')
        gate.checked(root,binding[name])
    parent=request.get('parent_session')
    if parent:
        ref={'path':safe_path(root,parent['path']).relative_to(root).as_posix(),'sha256':parent['sha256']}
        if ref in consumer['seed_artifacts']:
            need(ref==consumer['seed_channels'][request['channel']],'SWAPPED_SEED_CHANNEL')
    return package



def inspect_seed_metadata(session,channel):
    """Inspect trusted in-memory state only; never predict, refit or save it."""
    def period_value(value):
        need(len(value)==2 and all(isinstance(n,Integral) and not isinstance(n,bool) for n in value),'SEED_PERIOD')
        return tuple(int(n) for n in value)
    through=list(period_value(session.through_period))
    periods=[period_value(p) for p in session.history_periods]
    need(session.selection['channel']==channel,'SESSION_CHANNEL')
    names=list(session.feature_names)
    need(len(names)==228 and len(set(names))==228,'SESSION_FEATURE_SCHEMA')
    shape=[int(n) for n in session.history_x.shape]
    need(len(shape)==2 and shape[1]==len(names),'SEED_HISTORY_SHAPE')
    n=shape[0]
    need(all(len(x)==n for x in (session.history_y,session.history_workload,session.history_periods,session.history_keys)),'SEED_HISTORY_LENGTH')
    need(all(p<=tuple(through) for p in periods),'SEED_FUTURE_HISTORY')
    experts=[session.selection['expert1'],session.selection['expert2']]
    need(all(name in session.models and name in session.configs for name in experts),'SEED_EXPERTS')
    next_period=[through[0],through[1]+1] if through[1]<18 else None
    cutoffs={name:list(period_value(session.models[name].train_cutoff)) for name in experts}
    refit={name:bool(next_period and (cutoffs[name][0]!=next_period[0] or
        session.configs[name].get('update')=='4weeks' and next_period[1] in (1,5,9,13,17) and cutoffs[name]!=next_period)) for name in experts}
    pending=session.pending
    need(pending is None or isinstance(pending,dict),'SEED_PENDING_SCHEMA')
    return dict(through_period=through,next_period=next_period,
        pending_period=list(period_value(pending['period'])) if pending else None,
        feature_count=len(names),feature_names=names,history_rows=n,history_shape=shape,
        history_max_period=list(max(periods)) if len(periods) else None,
        selected_experts=experts,expert_train_cutoffs=cutoffs,refit_due_next=refit,
        fits=0,numerical_forecasts=0,training_rows_added=0,seed_mutated=False,
        classification='TRUSTED_SEED_INSPECTION_NOT_FORECAST_OR_PERFORMANCE')


def _execute_admitted(root,request,manifest,gate,consumer=None):
    action=request['action']; channel=request['channel']; period=request['period']
    need(action in ('inspect_seed','build_features','forecast','recover_forecast','settle'),'ACTION')
    need(channel in ('receiving','rushing'),'CHANNEL')
    need((period is None and manifest['week'] is None) if action=='inspect_seed' else period==[manifest['season'],manifest['week']],'MANIFEST_PERIOD')
    if action=='build_features' and request.get('operation')==getattr(gate,'SOURCE',None):
        from nfl_model import stage
        gate.require_active_source(root,stage.current_authority())
    else:
        gate.require_phase('inspect' if action=='inspect_seed' else 'learn' if action=='settle' else 'forecast')
    need(consumer is not None,'CONSUMER_REQUIRED')
    validate_consumer_binding(root,request,consumer,gate)
    recovery_inputs=_recovery_inputs(root,request,manifest,gate) if action=='recover_forecast' else None
    thresholds=request.get('thresholds',[25,25.5,50,50.5,75,75.5])
    need(isinstance(thresholds,list) and len(thresholds)<=100 and all(type(t) in (int,float) and math.isfinite(t) for t in thresholds),'THRESHOLDS')
    package=safe_path(root,request['engine_root'])
    package_info=verify_package(package,request['package_manifest_sha256'])
    versions=runtime_versions()
    dest=safe_path(root,request['output_dir'])
    scope=(root/'data/private/research-lab/sessions') if action=='settle' else root/manifest['execution_directory']
    need(dest.is_relative_to(scope) and dest!=scope,'OUTPUT_SCOPE')
    need(not dest.exists(),'OUTPUT_ALREADY_EXISTS')
    parent=None
    if action!='build_features':
        parent_ref=request['parent_session']
        need(set(parent_ref)=={'path','sha256','through_period'},'PARENT_SESSION_SCHEMA')
        parent=safe_path(root,parent_ref['path'])
        gate.require_model_artifact(root,parent,parent_ref['sha256'],channel)
    if action=='settle':
        response=request['outcomes']; response_path=safe_path(root,response['path'])
        gate.require_response_artifact(root,response_path,response['sha256'],channel)
    elif action!='inspect_seed':
        input_root,fixtures=_inputs(root,request,manifest,gate)
    # All authority/hash/path checks precede package imports and model deserialization.
    modules=import_engine(package)
    import numpy as np
    import joblib
    prepared=None
    if action not in ('settle','inspect_seed'):
        prepared=build_features(package,input_root,fixtures,period)
        need(prepared['fixtures'],'NO_FEATURE_ELIGIBLE_FIXTURES')
    session=None
    if parent is not None:
        session=joblib.load(parent)
        need(isinstance(session,modules['learning_session'].ResearchSession),'SESSION_CLASS')
        need(session.selection['channel']==channel,'SESSION_CHANNEL')
        if action!='inspect_seed':
            need(list(session.through_period)==request['parent_session']['through_period'],'PARENT_PERIOD')
        need(session.feature_names==read_json(package/'outputs/feature_names.json'),'SESSION_FEATURE_SCHEMA')
    if action=='settle':
        need(session.pending is not None and list(session.pending['period'])==period,'PENDING_PERIOD')
        pending_path=safe_path(root,session.pending['archive_path'])
        need(pending_path.is_relative_to(root/manifest['execution_directory']),'PENDING_ARCHIVE_SCOPE')
        response_body=read_json(response_path)
        outcome_rows=response_body['rows'] if isinstance(response_body,dict) else response_body
        outcomes=validate_outcomes(outcome_rows,session.pending['fixtures'],period)
        need(isinstance(request.get('provenance'),dict) and request['provenance'],'OUTCOME_PROVENANCE_REQUIRED')
    dest.mkdir(parents=True,exist_ok=False)
    write_new(dest/'STARTED.json',{'action':action,'period':period,'channel':channel,'request_sha256':request['_request_sha256']})
    result={'schema_version':'puntproof-engine-bridge-result-v1','status':'PASS','action':action,'period':period,
        'channel':channel,'engine_manifest_sha256':package_info['manifest_sha256'],'runtime_versions':versions,
        'provider_calls':0,'network_guard':'PYTHON_SOCKET_DENIAL','production':False,'request_sha256':request['_request_sha256']}
    try:
        if action=='inspect_seed':
            result.update(inspect_seed_metadata(session,channel))
            result['source_seed']={'path':str(parent),'sha256':request['parent_session']['sha256']}
        elif action=='build_features':
            path=dest/'features.npz'
            with path.open('xb') as stream:
                np.savez_compressed(stream,x=prepared['x'],names=np.asarray(prepared['names']))
                stream.flush(); os.fsync(stream.fileno())
            result.update(features=artifact(path),feature_count=len(prepared['names']),eligible=len(prepared['fixtures']),
                fixtures=prepared['fixtures'],exclusions=prepared['exclusions'],histories=prepared['histories'],fits=0)
        elif action in ('forecast','recover_forecast'):
            if action=='recover_forecast':
                paths,refs=recovery_inputs
                forecast=recover_forecast_state(session,paths['original_archive'],refs['original_archive']['sha256'],
                    dest/'forecast.npz',prepared,period,modules)
                result['recovery']=dict(forecast['recovery'],original_references=refs)
            else:
                forecast=session.forecast_week(period,prepared['x'],prepared['fixtures'],dest/'forecast.npz')
            # Persist native pending laws before summaries or other postprocessing.
            saved=session.save_new(dest/'session.joblib')
            _fsync(dest/'session.joblib'); _fsync(dest/'forecast.npz')
            thresholds=request.get('thresholds',[25,25.5,50,50.5,75,75.5])
            need(isinstance(thresholds,list) and len(thresholds)<=100 and all(type(t) in (int,float) and math.isfinite(t) for t in thresholds),'THRESHOLDS')
            predictions=[]; rows=[]
            for i,fixture in enumerate(prepared['fixtures']):
                s=forecast['support']; p=forecast['pmf'][i]
                need(np.isfinite(p).all() and (p>=0).all() and abs(float(p.sum())-1)<1e-8,'PMF_INVARIANT')
                summary=modules['inference_cli'].summary(s,p,float(forecast['workload'][i]),thresholds)
                cdf=np.cumsum(p)
                summary['interval_50']=[int(s[np.searchsorted(cdf,q)]) for q in (.25,.75)]
                summary['interval_95']=[int(s[np.searchsorted(cdf,q)]) for q in (.025,.975)]
                rows.append(dict(fixture,fixture=fixture,support=s.tolist(),pmf=p.tolist(),mean=summary['mean_yards'],
                    median=summary['median_yards'],workload=float(forecast['workload'][i]),
                    high_usage=bool(prepared['x'][i,prepared['names'].index('base_opportunities_12')]>=(3 if channel=='receiving' else 8)),
                    forecast=summary,expert1_weight=float(forecast['weights'][i])))
                predictions.append({'fixture':fixture,'status':'CONDITIONAL_RESEARCH_FORECAST','forecast':summary,
                    'expert1_weight':float(forecast['weights'][i]),'availability':'UNKNOWN_NOT_MODELLED'})
            predictions_ref=write_new(dest/'predictions.json',{'rows':rows,'forecasts':predictions,'exclusions':prepared['exclusions'],
                'classification':manifest['classification'],'conditional_on_appearance':True})
            result.update(new_session=artifact(dest/'session.joblib'),archive=artifact(dest/'forecast.npz'),
                predictions=predictions_ref,receipt=forecast['receipt'],numerical_forecasts=len(predictions),
                exclusions=prepared['exclusions'],feature_count=len(prepared['names']),
                fits=len(forecast['receipt']['new_fits']),through_period=saved['through_period'],pending_period=period)
        else:
            receipt=session.settle_week(period,outcomes,request['provenance'])
            saved=session.save_new(dest/'session.joblib'); _fsync(dest/'session.joblib')
            result.update(new_session=artifact(dest/'session.joblib'),receipt=receipt,through_period=saved['through_period'],
                pending_period=None,fits=0,training_rows_added=receipt['training_rows_added'])
        result['artifacts']={}
        for key,name in (('session','session.joblib'),('forecast','forecast.npz'),('predictions','predictions.json'),('features','features.npz')):
            if (dest/name).is_file(): result['artifacts'][key]={'path':str((dest/name).resolve()),'sha256':sha(dest/name)}
        need(parent is None or sha(parent)==request['parent_session']['sha256'],'PARENT_MUTATED')
        write_new(dest/'COMPLETE.json',result)
        return result
    except Exception as error:
        write_new(dest/'FAILED.json',{'status':'QUARANTINED','action':action,'period':period,'channel':channel,
            'error':type(error).__name__+': '+str(error),'retry_authorized':False})
        raise


def execute(request,root=ROOT):
    root=Path(root).resolve()
    need(request.get('schema_version')==1,'REQUEST_VERSION')
    need(request.get('action') in ('inspect_seed','build_features','forecast','recover_forecast','settle'),'ACTION')
    sys.path.insert(0,str(root/'src'))
    from nfl_model import research_lab_gate as gate
    authority=request['authority']
    need(set(authority)=={'execution_manifest','readiness'},'AUTHORITY_REFERENCES')
    with no_network():
        consumer,_=gate.consumer_for(root,request['operation'])
        capability={'inspect_seed':'SEED_INSPECTION','build_features':'PREPARE_FORECAST','forecast':'FORECAST','recover_forecast':'FORECAST','settle':'SETTLE'}[request['action']]
        with gate.execution_authority(root,request['operation'],authority['execution_manifest'],authority['readiness'],capability=capability) as manifest:
            return _execute_admitted(root,request,manifest,gate,consumer)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request',required=True); parser.add_argument('--result',required=True)
    args=parser.parse_args(argv)
    request_path=safe_path(ROOT,args.request); result_path=safe_path(ROOT,args.result)
    need(not result_path.exists(),'RESULT_ALREADY_EXISTS')
    request=read_json(request_path); request['_request_sha256']=sha(request_path)
    result=execute(request)
    need(result_path.parent.exists(),'RESULT_PARENT_MISSING')
    write_new(result_path,result)
    print(json.dumps({'status':'PASS','result':artifact(result_path),'action':request['action']}))


if __name__=='__main__':
    main()
