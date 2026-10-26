"""Read-only metadata integration and authenticated, bounded metadata exchange.

No adapter in this module opens raw football responses, performs model execution,
requests a provider, or invokes the Data Manager's mutating Store constructor.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid

UTC = dt.timezone.utc
SCHEMA = 'puntproof-research-lab-metadata-v1'
MAX_ENVELOPE_BYTES = 2_000_000
MAX_RECORDS = 2000
_EXCHANGE_LOCK = threading.RLock()
ALLOWED_KINDS = {'source', 'integration'}
# Closed field list: export/import is deliberately not a generic data-admission path.
FIELDS = {
    'source_id', 'name', 'source', 'dataset', 'season', 'week', 'status', 'mode',
    'snapshot_count', 'run_count', 'successful_runs', 'retained_rows_across_versions',
    'latest_observed_at', 'latest_started_at', 'latest_ended_at', 'latest_ingested_at',
    'coverage', 'permissions', 'license_status', 'model_use', 'raw_sha256',
    'records_sha256', 'source_version', 'producer', 'checked_at', 'schema_version',
    'database_bytes', 'table_count', 'note', 'source_repo', 'table', 'read_only',
    'integration_id', 'record_count', 'fixture_metadata_count', 'protected',
    'archive', 'version', 'path', 'sha256', 'bytes', 'label', 'code', 'detail',
}
SECRETS = re.compile(r'(?i)(?:gh[pousr]_[A-Za-z0-9_]{15,}|github_pat_[A-Za-z0-9_]+|Bearer\s+\S+|(?:api[_-]?key|password|authorization|access[_-]?token)\s*[:=]\s*\S+)')

def utc_now():
    return dt.datetime.now(UTC).isoformat(timespec='seconds')

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('utf-8')

def sha256(value):
    return hashlib.sha256(value).hexdigest()

def _safe_text(value, limit=800):
    return SECRETS.sub('[REDACTED]', str(value))[:limit]

def _inside(root, relative):
    root = Path(root).resolve()
    result = root / relative
    # Do not follow junctions/symlinks into other projects or credential stores.
    if not result.resolve().is_relative_to(root):
        raise ValueError('Metadata path escapes source repository')
    for part in (result, *result.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise ValueError('Linked metadata path refused')
        if part == root:
            break
    return result

def _readonly_database(path):
    """A short live WAL reader, never a write connection or exclusive store lock."""
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1.0)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('PRAGMA busy_timeout=1000')
    # Authorizer refuses every SQL write even if a caller accidentally adds one.
    denied = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
              sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE,
              sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}
    db.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action in denied else sqlite3.SQLITE_OK)
    return db

def inventory(source_repo):
    """Inventory known metadata columns only; never return settings or payloads."""
    repo = Path(source_repo).resolve()
    checked = utc_now()
    result = {'checked_at': checked, 'source_repo': str(repo), 'mode': 'METADATA_ONLY',
              'read_only': True, 'sources': [], 'databases': [], 'gaps': [],
              'model_use': False, 'fixture_metadata_count': 0}
    path = _inside(repo, 'data/private/puntproof/data-manager/acquisitions.sqlite3')
    if not path.is_file():
        result['gaps'].append({'code': 'DATA_ACCESS', 'detail': 'Known Data Manager acquisitions database is absent; no substitute was used.'})
        result['status'] = 'UNAVAILABLE'
        return result
    info = {'path': str(path), 'database_bytes': path.stat().st_size, 'status': 'UNREAD', 'read_only': True}
    result['databases'].append(info)
    db = None
    try:
        db = _readonly_database(path)
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        info['table_count'] = len(tables)
        info['schema_version'] = int(db.execute('PRAGMA user_version').fetchone()[0])
        if info['schema_version'] != 1:
            result['gaps'].append({'code': 'SCHEMA_DRIFT', 'detail': 'Data Manager user_version differs from admitted metadata adapter version 1.'})
            info['status'] = result['status'] = 'SCHEMA_BLOCKED'
            return result
        expected = {
            'snapshots': {'source','dataset','season','acquired','records'},
            'runs': {'source','dataset','season','started','ended','status'},
            'av_captures': {'provider','feed','observed'},
            'av_games': {'game_id'},
        }
        for table, columns in expected.items():
            if table in tables:
                actual = {r[1] for r in db.execute('PRAGMA table_info("' + table + '")')}
                if not columns <= actual:
                    result['gaps'].append({'code': 'SCHEMA_DRIFT', 'detail': 'Metadata columns missing from ' + table})
                    tables.remove(table)
        if 'snapshots' in tables:
            rows = db.execute('SELECT source,dataset,season,COUNT(*) AS snapshot_count,SUM(records) AS retained_rows_across_versions,MAX(acquired) AS latest_observed_at FROM snapshots GROUP BY source,dataset,season ORDER BY source,dataset,season LIMIT 2001').fetchall()
            if len(rows) > MAX_RECORDS:
                result['gaps'].append({'code':'METADATA_LIMIT','detail':'Metadata group limit reached; remaining groups are not projected.'})
            for row in rows[:MAX_RECORDS]:
                item = dict(row)
                item.update(source_id='manager:' + ':'.join(str(item[k]) for k in ('source','dataset','season')),
                            name=_safe_text(str(item['source']) + ' / ' + str(item['dataset'])),
                            status='RETAINED_METADATA', mode='METADATA_ONLY', read_only=True,
                            protected=item['season'] == 2025, permissions='METADATA_ONLY_NO_MODEL_ADMISSION',
                            license_status='CONSULT_ACTIVE_SOURCE_REGISTRY', model_use=False,
                            checked_at=checked, table='snapshots',
                            note='Counts include retained revisions; they are not unique player/game coverage. Payloads were not opened.')
                # Source and dataset are metadata strings, still sanitize unexpected content.
                for key in ('source','dataset','source_id'):
                    item[key] = _safe_text(item[key])
                result['sources'].append(item)
        if 'runs' in tables:
            rows = db.execute("SELECT source,dataset,season,COUNT(*) AS run_count,SUM(CASE WHEN status IN ('NEW','UNCHANGED') THEN 1 ELSE 0 END) AS successful_runs,MAX(started) AS latest_started_at,MAX(ended) AS latest_ended_at FROM runs GROUP BY source,dataset,season ORDER BY source,dataset,season LIMIT 2000").fetchall()
            lookup = {x['source_id']: x for x in result['sources']}
            for row in rows:
                item = dict(row)
                sid = 'manager:' + ':'.join(_safe_text(item[k]) for k in ('source','dataset','season'))
                if sid in lookup:
                    lookup[sid].update({k: item[k] for k in ('run_count','successful_runs','latest_started_at','latest_ended_at')})
                else:
                    result['sources'].append({'source_id':sid, 'source':_safe_text(item['source']), 'dataset':_safe_text(item['dataset']),
                        'season':item['season'], 'status':'NO_RETAINED_SNAPSHOT', 'mode':'METADATA_ONLY', 'model_use':False,
                        'run_count':item['run_count'], 'successful_runs':item['successful_runs'], 'latest_started_at':item['latest_started_at'],
                        'latest_ended_at':item['latest_ended_at'], 'permissions':'METADATA_ONLY_NO_MODEL_ADMISSION', 'checked_at':checked})
        if 'av_captures' in tables:
            rows = db.execute('SELECT provider,feed,COUNT(*) AS snapshot_count,MAX(observed) AS latest_observed_at FROM av_captures GROUP BY provider,feed LIMIT 2000').fetchall()
            for row in rows:
                result['sources'].append({'source_id':'availability:' + _safe_text(row['provider']) + ':' + _safe_text(row['feed']),
                    'source':_safe_text(row['provider']), 'dataset':_safe_text(row['feed']), 'season':2026,
                    'snapshot_count':row['snapshot_count'], 'latest_observed_at':row['latest_observed_at'],
                    'status':'RETAINED_METADATA', 'mode':'METADATA_ONLY', 'model_use':False,
                    'permissions':'METADATA_ONLY_NO_MODEL_ADMISSION', 'checked_at':checked, 'table':'av_captures'})
        if 'av_games' in tables:
            result['fixture_metadata_count'] = int(db.execute('SELECT COUNT(*) FROM av_games').fetchone()[0])
        info['status'] = 'READ_ONLY_VERIFIED'
        result['status'] = 'METADATA_CONNECTED'
        result['gaps'].append({'code':'SOURCE_ADMISSION_REQUIRED',
            'detail':'Fixture/player/feature/outcome/odds payload round trip is blocked pending exact source and consumer admission. Metadata connection is not live research-data integration.'})
        if not result['sources']:
            result['gaps'].append({'code':'NO_RETAINED_METADATA','detail':'Metadata query succeeded with zero source groups; this is distinct from a failed fetch.'})
    except sqlite3.Error as error:
        info['status'] = result['status'] = 'DATA_ACCESS_BLOCKED'
        result['gaps'].append({'code':'DATA_ACCESS','detail':'Read-only metadata database unavailable: ' + _safe_text(error, 200)})
    finally:
        if db is not None:
            db.close()
    return result

def sync_metadata(store, source_repo, *, as_of=None):
    report = inventory(source_repo)
    changed = 0
    for item in report['sources']:
        body = dict(item)
        # Stable ingestion key prevents a new version solely because worker polled.
        body.pop('checked_at', None)
        old = store.latest('source', item['source_id'])
        old_body = {k:v for k,v in (old or {}).items() if k not in {'id','revision','record_hash','recorded_at'}}
        if old_body != body:
            store.append('source', item['source_id'], body)
            changed += 1
    summary = {k:v for k,v in report.items() if k not in {'sources','databases','gaps'}}
    summary.update(integration_id='data_manager', record_count=len(report['sources']),
                   permissions='METADATA_ONLY_NO_MODEL_ADMISSION', note='Only explicitly named metadata columns queried; no source response opened.')
    store.append('integration', 'data_manager', summary)
    for gap in report['gaps']:
        store.alert(gap['code'], 'P1', gap['code'].replace('_',' ').title(), gap['detail'], entity='data_manager')
    moment = as_of or dt.datetime.now(UTC)
    if isinstance(moment, str):
        moment = dt.datetime.fromisoformat(moment.replace('Z','+00:00'))
    if not isinstance(moment, dt.datetime) or moment.tzinfo is None:
        raise ValueError('Freshness comparison requires an aware as-of clock')
    moment = moment.astimezone(UTC)
    # NFL's January/February fixtures belong to the preceding regular-season year.
    current_season = moment.year if moment.month >= 7 else moment.year - 1
    threshold = store.get_setting('source_stale_hours', 72)
    if type(threshold) not in (int,float) or not math.isfinite(threshold) or not 1 <= threshold <= 8760:
        store.alert('SOURCE_FRESHNESS_CONFIG','P1','Source freshness configuration is invalid',
            {'expected':'source_stale_hours between 1 and 8760 hours','actual':'Invalid configuration; 72-hour default used for this check'},entity='data_manager')
        threshold = 72
    for item in report['sources']:
        sid = item['source_id']
        if item['status'] == 'NO_RETAINED_SNAPSHOT':
            store.alert('RUN_LABEL_UNMATCHED','P2','Run label has no exact snapshot-label match',
                {'source_id':sid,'season':item.get('season'),'expected':'Verified metadata alias or exact source/dataset snapshot match',
                 'actual':'No exact label match; descriptive run labels may differ from canonical dataset keys. Whole-source absence is not established',
                 'next_action':'Reconcile retained metadata aliases before declaring a source gap or fetching data.'},entity=sid)
            continue
        if item.get('season') != current_season:
            continue
        clock = item.get('latest_observed_at')
        semantics = ('Data Manager acquisition clock only; this does not authenticate historical pregame availability'
                     if item.get('table') == 'snapshots' else
                     'Retained capture observation clock only; source publication/event/cutoff clocks remain distinct')
        detail = {'source_id':sid,'season':current_season,'as_of':moment.isoformat(),
                  'source_observed_at':clock,'maximum_age_hours':threshold,'clock_semantics':semantics}
        if clock in (None,''):
            store.alert('SOURCE_CLOCK_UNKNOWN','P1','Current-season source freshness is unknown',
                {**detail,'expected':'An explicit timezone-aware observation/acquisition clock','actual':'No usable clock is retained'},entity=sid)
            continue
        try:
            observed = dt.datetime.fromisoformat(clock.replace('Z','+00:00'))
            if observed.tzinfo is None:
                raise ValueError('Timezone missing')
            age = (moment-observed.astimezone(UTC)).total_seconds()/3600
        except (ValueError,TypeError,AttributeError):
            store.alert('SOURCE_CLOCK_INVALID','P1','Current-season source clock is malformed',
                {**detail,'expected':'An explicit timezone-aware ISO8601 clock','actual':'Clock cannot be interpreted without guessing'},entity=sid)
            continue
        if age < -5/60:
            store.alert('SOURCE_CLOCK_FUTURE','P1','Current-season source clock is in the future',
                {**detail,'actual_age_hours':round(age,3),'expected':'Observation no later than the current clock plus five minutes'},entity=sid)
        elif age > threshold:
            store.alert('SOURCE_STALE','P1','Current-season source metadata is stale',
                {**detail,'actual_age_hours':round(age,3),'expected':'Observation within the configured freshness window',
                 'actual':'The retained metadata is older than the limit. This is a source-age warning, not evidence of no betting edge.'},entity=sid)
    # Alerts are not automatically resolved by a later poll; verified resolution
    # requires the explicit diagnostic/backfill process.
    store.set_setting('integration_last_sync', report['checked_at'])
    store.set_setting('last_metadata_sync', report['checked_at'])
    store.audit('METADATA_SYNC', {'status':report['status'], 'source_groups':len(report['sources']), 'revisions_added':changed, 'raw_responses_opened':0, 'provider_calls':0})
    return {**report, 'revisions_added':changed}

def _validate_record(item):
    if not isinstance(item, dict) or set(item) != {'kind','id','body'}:
        raise ValueError('Invalid metadata record envelope')
    if item['kind'] not in ALLOWED_KINDS:
        raise ValueError('Scientific/payload exchange not admitted')
    if not isinstance(item['id'], str) or not re.fullmatch(r'[A-Za-z0-9_:. /-]{1,240}', item['id']):
        raise ValueError('Invalid metadata identifier')
    body = item['body']
    if not isinstance(body, dict) or set(body) - FIELDS:
        raise ValueError('Unapproved metadata fields')
    if body.get('model_use') is not False or body.get('mode') != 'METADATA_ONLY':
        raise ValueError('Metadata exchange cannot grant model admission')
    for key, value in body.items():
        if value is not None and type(value) not in (str, int, float, bool):
            raise ValueError('Only scalar metadata is exchanged')
        if isinstance(value,str) and (len(value)>2000 or SECRETS.search(value)):
            raise ValueError('Secret-bearing or oversized metadata refused')
    canonical(body)  # Reject NaN/infinity.

def export_signed(store, key, key_id='local'):
    if not isinstance(key, bytes) or len(key) < 32:
        raise ValueError('Exchange key requires at least 32 random bytes')
    records = []
    for kind in sorted(ALLOWED_KINDS):
        for record in store.records(kind):
            body = {k:v for k,v in record.items() if k in FIELDS}
            body.update(mode='METADATA_ONLY', model_use=False)
            item = {'kind':kind, 'id':record['id'], 'body':body}
            _validate_record(item)
            records.append(item)
    if len(records) > MAX_RECORDS:
        raise ValueError('Metadata record count limit exceeded')
    payload = {'schema':SCHEMA, 'key_id':str(key_id), 'created_at':utc_now(), 'records':records}
    envelope = {'payload':payload, 'payload_sha256':sha256(canonical(payload))}
    envelope['signature'] = hmac.new(key, canonical(envelope), hashlib.sha256).hexdigest()
    if len(canonical(envelope)) > MAX_ENVELOPE_BYTES:
        raise ValueError('Metadata exchange size limit exceeded')
    store.audit('METADATA_EXPORT', {'records':len(records), 'payload_sha256':envelope['payload_sha256'], 'key_id':str(key_id)})
    return envelope

def import_signed(store, envelope, key, key_id='local'):
    with _EXCHANGE_LOCK:
        return _import_signed(store,envelope,key,key_id)

def _import_signed(store, envelope, key, key_id='local'):
    if not isinstance(key,bytes) or len(key)<32:
        raise ValueError('Exchange key requires at least 32 random bytes')
    if not isinstance(envelope,dict) or set(envelope) != {'payload','payload_sha256','signature'}:
        raise ValueError('Invalid signed envelope')
    raw = canonical(envelope)
    if len(raw)>MAX_ENVELOPE_BYTES:
        raise ValueError('Metadata exchange size limit exceeded')
    unsigned = {'payload':envelope['payload'], 'payload_sha256':envelope['payload_sha256']}
    expected = hmac.new(key, canonical(unsigned), hashlib.sha256).hexdigest()
    if not isinstance(envelope['signature'],str) or not hmac.compare_digest(expected,envelope['signature']):
        raise ValueError('Metadata signature verification failed')
    payload = envelope['payload']
    if not isinstance(payload,dict) or set(payload)!={'schema','key_id','created_at','records'} or payload['schema']!=SCHEMA or payload['key_id']!=key_id:
        raise ValueError('Metadata schema or trusted key identity mismatch')
    if envelope['payload_sha256'] != sha256(canonical(payload)):
        raise ValueError('Metadata payload hash mismatch')
    stamp = dt.datetime.fromisoformat(payload['created_at'].replace('Z','+00:00'))
    if stamp.tzinfo is None or stamp > dt.datetime.now(UTC)+dt.timedelta(minutes=5):
        raise ValueError('Invalid exchange observation clock')
    records = payload['records']
    if not isinstance(records,list) or len(records)>MAX_RECORDS:
        raise ValueError('Invalid metadata record count')
    seen = set()
    for item in records:
        _validate_record(item)
        identity = (item['kind'],item['id'])
        if identity in seen:
            raise ValueError('Duplicate metadata identity')
        seen.add(identity)
    # Validate everything before applying any record. Store append is idempotent;
    # interruption before the immutable receipt is safe to resume with same envelope.
    digest = sha256(raw)
    if store.latest('exchange_receipt',digest):
        return {'status':'UNCHANGED', 'records':len(records), 'sha256':digest}
    previous = store.latest('exchange_state', key_id)
    if previous and stamp < dt.datetime.fromisoformat(previous['created_at'].replace('Z','+00:00')):
        raise ValueError('Older signed export cannot replace newer metadata')
    directory = Path(store.root)/'exchanges'
    directory.mkdir(exist_ok=True)
    path = directory/(digest+'.json')
    if path.exists():
        if path.read_bytes()!=raw:
            raise ValueError('Immutable exchange collision')
    else:
        temporary=directory/(digest+'.'+uuid.uuid4().hex+'.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            # Atomic create without replacing an existing content-addressed file.
            try:
                os.link(temporary,path)
            except FileExistsError:
                if path.read_bytes()!=raw:
                    raise ValueError('Immutable exchange collision')
        finally:
            temporary.unlink(missing_ok=True)
    for item in records:
        store.append(item['kind'],item['id'],item['body'])
    store.append('exchange_state',key_id,{'created_at':payload['created_at'],'payload_sha256':envelope['payload_sha256']})
    store.append('exchange_receipt',digest, {'payload_sha256':envelope['payload_sha256'],'records':len(records),'key_id':key_id,'mode':'METADATA_ONLY','created_at':payload['created_at']}, immutable=True)
    store.audit('METADATA_IMPORT', {'sha256':digest,'records':len(records),'model_admission':False})
    return {'status':'IMPORTED','records':len(records),'sha256':digest}

def local_exchange_key(store):
    """Private local authentication material. Never include this file in exports."""
    path = Path(store.root)/'.metadata-exchange.key'
    if path.is_symlink():
        raise ValueError('Linked exchange key refused')
    try:
        with path.open('xb') as stream:
            stream.write(os.urandom(32)); stream.flush(); os.fsync(stream.fileno())
        if os.name != 'nt':
            path.chmod(0o600)
    except FileExistsError:
        pass
    key = path.read_bytes()
    if len(key)!=32:
        raise ValueError('Invalid local exchange key length')
    return key
