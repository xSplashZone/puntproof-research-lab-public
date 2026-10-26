"""Read-only package custody, model registry and retained aggregate import.

This module never imports supplied Python, unpickles models, reads case-level
outcomes, reruns scores, downloads sources, or turns proxy lines into real odds.
"""
from __future__ import annotations
import csv
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import zipfile

DEFAULT_STARTER_ROOT = Path(__file__).resolve().parents[2]/'scratch/research-lab-starter'
ENGINE_REL = Path('engine/PuntProof_Adaptive_V3')
COMPARISON_REL = Path('comparison/puntproof_2026_backtest')
AGGREGATES = ('overall.csv','seasons.csv','subgroups.csv','uncertainty.csv','probabilities.csv','upper_tail_probabilities.csv','weekly.csv')
SCOPE = 'EXPOSED_REVISED_HISTORY_RETAINED_RESULTS_NOT_PROSPECTIVE'
MAX_MANIFEST = 8_000_000
MAX_AGGREGATE = 4_000_000


def _sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def _read_json(path, maximum=MAX_MANIFEST):
    if path.stat().st_size > maximum: raise ValueError('METADATA_SIZE_LIMIT')
    return json.loads(path.read_text(encoding='utf-8-sig'),parse_constant=lambda x: (_ for _ in ()).throw(ValueError('NONFINITE_JSON')))


def _safe(root, relative):
    if not isinstance(relative,str) or not relative or '\\' in relative or ':' in relative:
        raise ValueError('UNSAFE_PACKAGE_PATH')
    p=PurePosixPath(relative)
    if p.is_absolute() or '..' in p.parts or '.' in p.parts:
        raise ValueError('UNSAFE_PACKAGE_PATH')
    target=root.joinpath(*p.parts)
    for item in [target,*target.parents]:
        if item==root.parent: break
        if item.is_symlink() or (item.exists() and getattr(item.stat(),'st_file_attributes',0)&0x400):
            raise ValueError('LINKED_PACKAGE_PATH')
        if item==root: break
    if not target.resolve().is_relative_to(root.resolve()): raise ValueError('PACKAGE_PATH_ESCAPE')
    return target


def inspect_package(path):
    """Hash delivered files as opaque bytes; return only installation metadata."""
    root=Path(path)
    result={'path':str(root),'status':'FAILED','files_checked':0,'bytes_checked':0,'failures':[],'engine_executed':False,'joblib_loaded':False}
    try:
        manifest=_safe(root,'PACKAGE_MANIFEST.json')
        data=_read_json(manifest)
        if not isinstance(data,dict): raise ValueError('INVALID_PACKAGE_MANIFEST')
        if data.get('hash_algorithm')!='SHA-256' or not isinstance(data.get('files'),list) or len(data['files'])>10000:
            raise ValueError('INVALID_PACKAGE_MANIFEST')
        names=set();bindings={}
        for row in data['files']:
            if not isinstance(row,dict): raise ValueError('INVALID_MANIFEST_ENTRY')
            name=row.get('path');target=_safe(root,name)
            if name in names: raise ValueError('DUPLICATE_MANIFEST_PATH')
            names.add(name)
            expected=row.get('sha256');size=row.get('bytes')
            if not isinstance(expected,str) or not re.fullmatch('[a-f0-9]{64}',expected) or type(size) is not int or size<0:
                raise ValueError('INVALID_MANIFEST_ENTRY')
            if not target.is_file(): result['failures'].append({'path':name,'reason':'MISSING'});continue
            actual_size=target.stat().st_size
            if actual_size!=size:
                result['failures'].append({'path':name,'reason':'SIZE_MISMATCH'});continue
            actual=_sha(target);result['files_checked']+=1;result['bytes_checked']+=actual_size
            if actual!=expected: result['failures'].append({'path':name,'reason':'HASH_MISMATCH'})
            else: bindings[name]=expected
        # Unlisted code is never trusted by this package boundary.
        extras=[p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.relative_to(root).as_posix() not in names|{'PACKAGE_MANIFEST.json'}]
        if extras: result['failures'].append({'reason':'UNMANIFESTED_FILES','paths':extras[:100]})
        result.update(profile=data.get('profile'),manifest_sha256=_sha(manifest),file_hashes=bindings)
        if result['failures']: return result
        for required in ('LOCK.json','PROTOCOL.json','PACKAGE_SCOPE.json','requirements.txt','outputs/feature_names.json'):
            if required not in bindings: raise ValueError('REQUIRED_METADATA_NOT_BOUND: '+required)
        lock=_read_json(root/'LOCK.json');protocol=_read_json(root/'PROTOCOL.json');scope=_read_json(root/'PACKAGE_SCOPE.json')
        if not all(isinstance(x,dict) for x in (lock,protocol,scope)): raise ValueError('INVALID_ENGINE_METADATA')
        names=_read_json(root/'outputs/feature_names.json')
        if not isinstance(names,list) or len(names)!=len(set(names)) or not all(isinstance(n,str) for n in names):
            raise ValueError('INVALID_FEATURE_SCHEMA')
        requirements=(root/'requirements.txt').read_text(encoding='utf-8').splitlines()
        requirements=[s.strip() for s in requirements if s.strip() and not s.lstrip().startswith('#')]
        if not all(re.fullmatch(r'[A-Za-z0-9_.-]+==[0-9][A-Za-z0-9_.-]*',r) for r in requirements):
            raise ValueError('UNPINNED_OR_UNSAFE_REQUIREMENT')
        result.update(status='VERIFIED',python='3.13 (package specifies no patch)',requirements=requirements,
            feature_count=len(names),feature_schema_sha256=bindings['outputs/feature_names.json'],
            through_period=[2026,lock.get('last_2026_week')],
            historical_replay_available=scope.get('primary_reproduction') is True,
            runtime_learning_available=scope.get('runtime_inference_and_learning') is True,
            history_scope=protocol.get('source_status'),
            policies={channel:{k:v for k,v in selection.items() if k in ('channel','expert1','expert2','policy')} for channel,selection in lock.get('primary',{}).items()},
            sessions=[{'path':name,'sha256':sha} for name,sha in bindings.items() if name.startswith('models/future_sessions/') and name.endswith('.joblib')],
            verification_is_execution_authority=False)
    except (OSError,ValueError,TypeError,KeyError) as error:
        result['failures'].append({'reason':str(error)[:300]})
    return result


def _starter_binding(root,relative):
    manifest=root/'SHA256_MANIFEST.txt'
    if not manifest.is_file() or manifest.stat().st_size>100000: raise ValueError('STARTER_MANIFEST_UNAVAILABLE')
    found=[]
    for line in manifest.read_text(encoding='utf-8-sig').splitlines():
        match=re.fullmatch(r'([a-f0-9]{64})\s+(.+)',line.strip())
        if match and match[2]==relative: found.append(match[1])
    if len(found)!=1: raise ValueError('STARTER_MANIFEST_BINDING_MISSING_OR_DUPLICATED')
    path=_safe(root,relative)
    if not path.is_file() or _sha(path)!=found[0]: raise ValueError('STARTER_ARCHIVE_HASH_MISMATCH')
    return path,found[0]


def _csv_rows(raw):
    if len(raw)>MAX_AGGREGATE: raise ValueError('AGGREGATE_SIZE_LIMIT')
    reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames): raise ValueError('AGGREGATE_SCHEMA_INVALID')
    forbidden={'gsis','player_id','actual_yards','y','game_id','net_units','roi_percent','hypothetical_offered_price'}
    if forbidden.intersection(reader.fieldnames): raise ValueError('CASE_OR_MONEY_TABLE_NOT_ALLOWED')
    for i,row in enumerate(reader):
        if i>=20000 or None in row: raise ValueError('AGGREGATE_ROW_LIMIT_OR_SCHEMA')
        clean={}
        for key,value in row.items():
            if value is None or value.strip()=='': clean[key]=None
            elif re.fullmatch(r'-?\d+',value): clean[key]=int(value)
            elif re.fullmatch(r'-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?',value):
                number=float(value)
                if not math.isfinite(number): raise ValueError('NONFINITE_AGGREGATE')
                clean[key]=number
            else: clean[key]=value
        yield clean


def _import_aggregate(store,raw,source,sha,table,package_hash,scope=SCOPE):
    count=0
    for index,row in enumerate(_csv_rows(raw)):
        body={'table':table,'source_file':source,'source_sha256':sha,'package_sha256':package_hash,
              'scope':scope,'metrics':row,'row_number':index+2,'recomputed':False,
              'betting_roi_established':False,'same_population':'As defined in supplied retained comparison; not independently recertified by this import'}
        identifier=hashlib.sha256((sha+':'+str(index)).encode()).hexdigest()[:32]
        store.append('comparison',identifier,body,immutable=True);count+=1
    return count


def _production_models(source_repo):
    if source_repo is None: return []
    runtime=Path(source_repo)/'data/private/puntproof/data-manager/weekly-releases/runtime'
    checks=[('production-team','lib/schedule-mean-model.ts',r"SCHEDULE_MODEL\s*=\s*['\"]([^'\"]+)"),
            ('production-player-edge','scripts/build-weekly-player-edge.py',r"release=\{['\"]model['\"]:\s*['\"]([^'\"]+)")]
    records=[]
    for key,relative,pattern in checks:
        p=_safe(runtime,relative)
        if not p.is_file() or p.stat().st_size>2_000_000: continue
        raw=p.read_bytes();match=re.search(pattern,raw.decode('utf-8'))
        if match: records.append((key,dict(id=key,name=match[1],lane='PUBLIC_PRODUCTION',status='RETAINED_CODE_ONLY_DEPLOYMENT_UNVERIFIED',
            source_path=str(p),source_sha256=hashlib.sha256(raw).hexdigest(),deployed_version_verified=False,
            public_promotion_allowed=False,metrics=None,description='Recipe identifier read from retained production runtime; actual deployed release requires authenticated readback.')))
    return records


def seed_packages(store,starter_root=DEFAULT_STARTER_ROOT,source_repo=None):
    """Idempotent saved metadata/result import, with explicit actionable gaps."""
    root=Path(starter_root);engine=root/ENGINE_REL;aggregate_rows=0
    receipt=inspect_package(engine)
    engine_archive_hash=None
    try:
        engine_archive,engine_archive_hash=_starter_binding(root,'01_ENGINE/PuntProof_Adaptive_V3_Runtime.zip')
        with zipfile.ZipFile(engine_archive) as archive:
            manifests=[i for i in archive.infolist() if PurePosixPath(i.filename).name=='PACKAGE_MANIFEST.json' and not i.is_dir()]
            if len(manifests)!=1 or manifests[0].file_size>MAX_MANIFEST:
                raise ValueError('ARCHIVED_ENGINE_MANIFEST_INVALID')
            if archive.read(manifests[0])!=(engine/'PACKAGE_MANIFEST.json').read_bytes():
                raise ValueError('EXTRACTED_ENGINE_MANIFEST_NOT_BOUND_TO_ARCHIVE')
    except (OSError,ValueError,zipfile.BadZipFile) as error:
        receipt['status']='FAILED';receipt['failures'].append({'reason':str(error)})
    receipt['archive_sha256']=engine_archive_hash
    receipt_id='adaptive-v3-'+hashlib.sha256(json.dumps(receipt,sort_keys=True).encode()).hexdigest()[:16]
    store.append('package',receipt_id,receipt,immutable=True)
    for model in ('B','F','C','L','K'):
        body=dict(id=model,name=('V2 '+model if model in ('C','L','K') else model),
            lane='FROZEN_CONTROL' if model in ('B','F') else 'RESEARCH_CANDIDATE',
            status='FROZEN_HISTORICAL_REFERENCE',artifact_loaded=False,metrics=None,
            public_promotion_allowed=False,description='Preserved historical model identity; no model execution or new validation performed.')
        store.append('model',model,body,immutable=True)
    v3=dict(id='V3',name='Adaptive V3',lane='RESEARCH_CANDIDATE',status='HASH_VERIFIED_RUNTIME_NOT_EXECUTED' if receipt['status']=='VERIFIED' else 'PACKAGE_INTEGRITY_BLOCKED',
        package_id=receipt_id,artifact_loaded=False,through_period=receipt.get('through_period'),feature_count=receipt.get('feature_count'),
        source_scope=SCOPE,public_promotion_allowed=False,champion=False,description='Locked seeded challenger; no prospective performance or profitability claim.')
    # Different package versions get different records; old model metadata stays immutable.
    store.append('model','V3-'+receipt_id.split('-')[-1],v3,immutable=True)
    if receipt['status']=='VERIFIED':
        for filename in AGGREGATES:
            relative='outputs/readout/'+filename
            if relative not in receipt['file_hashes']: continue
            raw=(engine/relative).read_bytes()
            aggregate_rows+=_import_aggregate(store,raw,str(engine/relative),receipt['file_hashes'][relative],filename,engine_archive_hash)
    else:
        store.alert('V3_PACKAGE_INTEGRITY','P0','Adaptive V3 package verification failed',
            {'expected':'All supplied manifest hashes and sizes agree','actual':receipt['failures'],'next_action':'Restore the exact owner-supplied package; never load an unverified joblib'},'adaptive-v3')
    try:
        archive,archive_hash=_starter_binding(root,'02_BACKTEST/PuntProof_2026_F_vs_V3_Audit_and_Monitor.zip')
        relative='2026_classification_summary.csv';target=_safe(root,(COMPARISON_REL/relative).as_posix())
        with zipfile.ZipFile(archive) as z:
            matches=[info for info in z.infolist() if PurePosixPath(info.filename).name==relative and not info.is_dir()]
            if len(matches)!=1 or matches[0].file_size>MAX_AGGREGATE: raise ValueError('COMPARISON_AGGREGATE_MEMBER_INVALID')
            raw=z.read(matches[0])
        if not target.is_file() or target.read_bytes()!=raw: raise ValueError('EXTRACTED_COMPARISON_AGGREGATE_MISMATCH')
        aggregate_rows+=_import_aggregate(store,raw,str(target),hashlib.sha256(raw).hexdigest(),relative,archive_hash)
        store.append('package','comparison-'+archive_hash[:16],dict(name='2026 F vs Adaptive V3 comparison',status='VERIFIED_RETAINED_AGGREGATES',
            archive_sha256=archive_hash,scope=SCOPE,seasons=[2026],weeks=[1,2,3,4,5,6],
            market_status='PROXY_THRESHOLDS_NO_BOOKMAKER_OFFERS',actual_roi=None,score_recomputed=False),immutable=True)
    except (OSError,ValueError,zipfile.BadZipFile,KeyError) as error:
        store.alert('COMPARISON_IMPORT','P1','Retained comparison import blocked',
            {'actual':str(error),'expected':'Original ZIP hash and exact extracted aggregate table','next_action':'Restore the supplied comparison archive and extraction'},'2026-comparison')
    try:
        for key,body in _production_models(source_repo): store.append('model',key,body,immutable=True)
    except (OSError,ValueError,UnicodeError) as error:
        store.alert('PRODUCTION_METADATA_READ','P1','Retained production recipe metadata unavailable',
            {'actual':str(error)[:300],'next_action':'Provide an unchanged readable retained runtime or signed production export'},'production-metadata')
    if receipt.get('historical_replay_available') is not True:
        store.alert('V3_REPLAY_EVIDENCE_MISSING','P1','Exact historical session replay needs Full Evidence',
            {'expected':['outputs/X.npy','outputs/meta.csv.gz','2024 Week 1 model checkpoints','2024 Week 1/2 forecast NPZs'],
             'actual':'Runtime edition explicitly omits historical reproduction inputs',
             'next_action':'Supply the Full Evidence edition to verify exact historical replay; do not refit substitutes'},'adaptive-v3-replay')
    store.alert('PRODUCTION_VERSION_UNVERIFIED','P1','Current public model needs authenticated readback',
        {'actual':'Only retained production recipe identifiers available','next_action':'Connect an authorized read-only production export with current release/model hashes'},'production')
    return {'package_status':receipt['status'],'aggregate_rows_imported':aggregate_rows,'engine_executed':False,'joblib_loaded':False,'scientific_results_recomputed':False}
