"""Pure participation-first projection and exact, gated retained-source readers.

Import performs no I/O. Metadata selection is not source/model admission. Only
require_retained_source can open a real payload; projection accepts supplied
in-memory rows and never certifies their provider rights or historical vintage.
"""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo
from functools import lru_cache

FIELDS = ('targets','receptions','receiving_yards','carries','rushing_yards')
CHANNEL_FIELDS = {'receiving':FIELDS[:3], 'rushing':FIELDS[3:]}
SKILL = {'WR','TE','RB','FB'}
GSIS = re.compile(r'\d{2}-\d{7}\Z')
HASH = re.compile(r'[a-f0-9]{64}\Z')
SOURCES = ('player_stats','rosters','pbp')
FIELD_ALLOWLISTS = {
 'player_stats':frozenset('season season_type week game_id player_id gsis team recent_team opponent_team position targets receptions receiving_yards carries rushing_yards'.split()),
 'rosters':frozenset('season week game_type team gsis_id gsis position pfr_id pfr_player_id full_name player_name first_name last_name status'.split()),
 'pbp':frozenset('season season_type week game_id play_id home_team away_team posteam defteam play_type game_half desc rush_attempt rusher_player_id rushing_yards lateral_rusher_player_id lateral_rushing_yards qb_scramble qb_kneel two_point_attempt pass_attempt complete_pass receiver_player_id receiving_yards lateral_receiver_player_id lateral_receiving_yards'.split()),
}


def need(ok, reason):
    if not ok:
        raise ValueError(reason)


def instant(value):
    need(isinstance(value,str) and bool(value),'SOURCE_CLOCK_REQUIRED')
    stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
    need(stamp.tzinfo is not None,'SOURCE_CLOCK_NAIVE')
    return stamp.astimezone(timezone.utc)


def team(value):
    return {'LAR':'LA','WSH':'WAS','JAC':'JAX'}.get(value,value)


def number(value, *, count=False):
    if value in (None,'','NA','NaN','nan'):
        return None
    need(not isinstance(value,bool),'BOOLEAN_STATISTIC')
    try:
        n=float(value)
    except (ValueError,TypeError) as exc:
        raise ValueError('INVALID_STATISTIC') from exc
    need(math.isfinite(n) and n.is_integer() and (not count or n>=0),'INVALID_STATISTIC')
    return int(n)


def period(row):
    y=number(row.get('season'),count=True);w=number(row.get('week'),count=True)
    need(y is not None and w is not None and 1<=w<=22,'SOURCE_PERIOD_REQUIRED')
    return y,w


def observation(row):
    source=row.get('_source') or {}
    return row.get('observed_at') or source.get('observed_at') or source.get('acquired')


def visible(row, cutoff):
    clock=observation(row)
    return clock is not None and instant(clock)<=cutoff


def provenance(row):
    src=dict(row.get('_source') or {})
    return dict(source_id=src.get('source_id') or row.get('source_id'),
                source_version=src.get('source_version'),raw_sha256=src.get('raw_sha256'),
                normalized_sha256=src.get('normalized_sha256'),observed_at=observation(row),
                ingested_at=src.get('ingested_at'),published_at=src.get('published_at'),
                available_at=src.get('available_at'),event_at=row.get('event_at'),
                license=src.get('license'),model_permission=src.get('model_permission'))


def select_snapshot_metadata(source_repo, *, as_of, sources=SOURCES):
    """Select exact 2026 metadata rows only; no settings or payload decoding."""
    cutoff=instant(as_of)
    need(cutoff<=datetime.now(timezone.utc),'METADATA_FUTURE_AS_OF')
    need(set(sources)<=set(SOURCES),'UNSUPPORTED_OR_RIGHTS_BLOCKED_SOURCE')
    root=Path(source_repo).resolve()/'data/private/puntproof/data-manager'
    db_path=root/'acquisitions.sqlite3'
    result={'classification':'METADATA_ONLY_NOT_SOURCE_ADMISSION','season':2026,
            'as_of':cutoff.isoformat(),'metadata_database':str(db_path),'snapshots':[],
            'gaps':[],'provider_calls':0,'payloads_opened':0}
    if not db_path.is_file():
        result['gaps'].append({'code':'DATA_ACCESS','detail':'Data Manager metadata database unavailable'})
        return result
    db=None
    try:
        db=sqlite3.connect(db_path.as_uri()+'?mode=ro',uri=True,timeout=1)
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
        columns={r['name'] for r in db.execute('PRAGMA table_info(snapshots)')}
        required={'id','source','dataset','season','acquired','raw_path','raw_hash','records_path','records_hash'}
        need(required<=columns,'MANAGER_SNAPSHOT_SCHEMA')
        for source in sources:
            # ISO instants are compared in Python, not lexically across offsets.
            candidates=db.execute('SELECT id,source,dataset,season,acquired,raw_path,raw_hash,records_path,records_hash FROM snapshots WHERE source=? AND season=2026 ORDER BY acquired DESC,id DESC LIMIT 1001',(source,)).fetchall()
            need(len(candidates)<=1000,'METADATA_VERSION_LIMIT')
            valid=[]
            for row in candidates:
                item=dict(row)
                if instant(item['acquired'])<=cutoff:valid.append(item)
            if not valid:
                result['gaps'].append({'code':'SNAPSHOT_UNAVAILABLE','source':source,'season':2026})
                continue
            row=max(valid,key=lambda r:(instant(r['acquired']),r['id']))
            need(all(HASH.fullmatch(row[k] or '') for k in ('raw_hash','records_hash')),'METADATA_HASH_SCHEMA')
            item=dict(row,source_id=source,metadata_row=dict(row),metadata_database=str(db_path),
                      classification='UNADMITTED_RETAINED_METADATA',stored_field='records_path')
            for key in ('raw_path','records_path'):
                p=Path(row[key]);p=p if p.is_absolute() else root/p
                need(p.resolve().is_relative_to(root) and p.resolve()!=root,'METADATA_PATH_ESCAPE')
                item[key]=str(p.resolve())
            # Read only producer summary metadata for the selected exact version.
            coverage={'declared_weeks':None,'declared_max_week':None,'records':None,
                      'classification':'PRODUCER_METADATA_NOT_COMPLETENESS_CERTIFICATE'}
            if {'records','metadata'}<=columns:
                summary=db.execute('SELECT records,substr(metadata,1,131073) AS metadata FROM snapshots WHERE id=? AND source=? AND season=2026',(row['id'],source)).fetchone()
                need(summary is not None and isinstance(summary['metadata'],str) and len(summary['metadata'])<=131072,'METADATA_SUMMARY_SIZE_LIMIT')
                meta=json.loads(summary['metadata']);validation=meta.get('validation',{})
                need(isinstance(validation,dict),'METADATA_COVERAGE_SCHEMA')
                weeks=validation.get('weeks')
                if weeks is not None:
                    need(isinstance(weeks,list) and len(weeks)<=22,'METADATA_WEEK_SCHEMA')
                    parsed=[number(w,count=True) for w in weeks]
                    need(all(w is not None and 1<=w<=22 for w in parsed),'METADATA_WEEK_SCHEMA')
                    coverage.update(declared_weeks=sorted(set(parsed)),declared_max_week=max(parsed,default=None))
                coverage.update(records=number(summary['records'],count=True),
                    validated=number(validation.get('validated'),count=True),
                    rejected=number(validation.get('rejected'),count=True),
                    duplicate_rows=number(validation.get('duplicate_rows'),count=True))
            item['metadata_coverage']=coverage
            result['snapshots'].append(item)
    except (sqlite3.Error,ValueError,TypeError,AttributeError) as exc:
        result['gaps'].append({'code':'METADATA_BLOCKED','detail':str(exc)})
        result['snapshots']=[]
    finally:
        if db is not None:db.close()
    return result


def read_bound_snapshot(root, snapshot, *, max_compressed_bytes=64*1024*1024,
                        max_expanded_bytes=256*1024*1024,max_rows=500000,max_period=None):
    """Read a hash-bound normalized gzip JSONL under an active SOURCE context.

    The gate is invoked before even inspecting the source file. It returns the
    permitted fields. No source field outside that allowlist is projected.
    """
    from nfl_model.research_lab_gate import require_retained_source, value
    path=snapshot['records_path'];expected=snapshot['records_hash'];dataset=snapshot['dataset']
    season=snapshot['season'];source=snapshot.get('source_id',snapshot.get('source'))
    admission=require_retained_source(root,path,expected,dataset,season)
    need(season==2026 and source in FIELD_ALLOWLISTS and dataset==source,'SOURCE_SCOPE')
    need(isinstance(admission.get('metadata_witness'),dict),'SOURCE_METADATA_WITNESS_REQUIRED')
    witness=value(root,admission['metadata_witness'])
    need(witness.get('schema_version')=='puntproof-lab-producer-metadata-witness-v1','SOURCE_METADATA_WITNESS_SCHEMA')
    bound=witness.get('row')
    need(isinstance(bound,dict) and snapshot.get('metadata_row')==bound,'SOURCE_SNAPSHOT_METADATA_CHANGED')
    for field in ('id','dataset','season','acquired','raw_hash','records_hash'):
        need(snapshot.get(field)==bound.get(field),'SOURCE_SNAPSHOT_'+field.upper()+'_CHANGED')
    need(source==bound.get('source'),'SOURCE_SNAPSHOT_SOURCE_CHANGED')
    allowed=set(admission.get('allowed_fields',[]))
    need(allowed and allowed<=FIELD_ALLOWLISTS[source],'SOURCE_FIELD_ADMISSION')
    acquired=snapshot.get('acquired')
    instant(acquired)
    p=Path(path)
    need(p.stat().st_size<=max_compressed_bytes,'SOURCE_COMPRESSED_SIZE_LIMIT')
    if max_period is not None:
        need(len(max_period)==2 and max_period[0]==2026 and type(max_period[1]) is int and 1<=max_period[1]<=18,'SOURCE_PERIOD_CAP')
    rows=[];expanded=0;scanned=0;withheld=0
    with p.open('rb') as handle:
        need(hashlib.file_digest(handle,'sha256').hexdigest()==expected,'SOURCE_BYTES_CHANGED')
        handle.seek(0)
        with gzip.GzipFile(fileobj=handle,mode='rb') as stream:
            while True:
                raw=stream.readline(2*1024*1024+1)
                if not raw:break
                expanded+=len(raw)
                need(len(raw)<=2*1024*1024 and expanded<=max_expanded_bytes,'SOURCE_EXPANDED_SIZE_LIMIT')
                need(scanned<max_rows,'SOURCE_ROW_LIMIT');scanned+=1
                native=json.loads(raw,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_SOURCE_JSON')))
                need(isinstance(native,dict),'SOURCE_ROW_OBJECT_REQUIRED')
                need(number(native.get('season'),count=True)==2026,'SOURCE_PARTITION_MISMATCH')
                if max_period is not None and period(native)>tuple(max_period):
                    withheld+=1;continue
                clean={k:v for k,v in native.items() if k in allowed}
                need(number(clean.get('season'),count=True)==2026,'SOURCE_PARTITION_MISMATCH')
                need(all(v is None or type(v) is str for v in clean.values()),'SOURCE_NORMALIZED_STRING_OR_NULL_REQUIRED')
                clean['_source']={'source_id':source,'source_version':snapshot.get('id'),
                    'raw_sha256':snapshot.get('raw_hash'),'normalized_sha256':expected,
                    'observed_at':acquired,'ingested_at':snapshot.get('ingested_at'),
                    'available_at':None,'published_at':None,'license':admission.get('license'),
                    'model_permission':admission.get('permission')}
                rows.append(clean)
        handle.seek(0)
        need(hashlib.file_digest(handle,'sha256').hexdigest()==expected,'SOURCE_CHANGED_DURING_READ')
    return {'records':rows,'source_id':source,'dataset':dataset,'season':season,
            'observed_at':acquired,'sha256':expected,'expanded_bytes':expanded,
            'row_count':len(rows),'scanned_rows':scanned,'withheld_future_rows':withheld,
            'max_period':list(max_period) if max_period else None,'source_certificate_created':False,'provider_calls':0}


def read_bound_json(root,path,sha256,dataset,season,*,max_bytes=2*1024*1024):
    """Exact admitted fixture/manifest JSON; no arbitrary-path ungated reader."""
    from nfl_model.research_lab_gate import require_retained_source
    require_retained_source(root,path,sha256,dataset,season)
    p=Path(path);need(p.stat().st_size<=max_bytes,'BOUND_JSON_SIZE_LIMIT')
    raw=p.read_bytes();need(hashlib.sha256(raw).hexdigest()==sha256,'BOUND_JSON_HASH_CHANGED')
    value=json.loads(raw,parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_SOURCE_JSON')))
    need(isinstance(value,(dict,list)),'BOUND_JSON_SCHEMA')
    return value


def _fixture_map(fixtures,cutoff,classification):
    out={};gaps=[];conflicted=set()
    for raw in fixtures:
        r=dict(raw)
        try:
            y,w=period(r);gid=r.get('game_id');home=team(r.get('home_team'));away=team(r.get('away_team'))
            need(isinstance(gid,str) and bool(gid),'FIXTURE_ID_REQUIRED')
            need(gid not in conflicted,'CONFLICTING_FIXTURE_VERSION')
            need(r.get('identity_verified') is True,'FIXTURE_IDENTITY_NOT_VERIFIED')
            need(home and away and home!=away,'FIXTURE_TEAM_IDENTITY')
            kickoff=instant(r.get('kickoff_utc') or r.get('kickoff'))
            need(visible(r,cutoff),'FIXTURE_OBSERVATION_UNKNOWN_OR_LATE')
            canonical={'season':y,'week':w,'game_id':gid,'home_team':home,'away_team':away,
                       'kickoff_utc':kickoff.isoformat(),'observed_at':observation(r),
                       'completed':r.get('completed') is True,
                       'completion_observed_at':r.get('completion_observed_at'),
                       'snap_complete':r.get('snap_complete') is True,
                       'pbp_complete':r.get('pbp_complete') is True,
                       'source_ref':r.get('source_ref'),'identity_verified':True}
            need(gid not in out or out[gid]==canonical,'CONFLICTING_FIXTURE_VERSION')
            out[gid]=canonical
        except (ValueError,TypeError) as exc:
            gaps.append({'code':str(exc),'game_id':r.get('game_id')})
            if str(exc)=='CONFLICTING_FIXTURE_VERSION':
                conflicted.add(r.get('game_id'));out.pop(r.get('game_id'),None)
    return out,gaps


def _game_key(row,fixtures,fixture_index):
    y,w=period(row);tm=team(row.get('team') or row.get('recent_team'))
    gid=row.get('game_id')
    if not gid:
        options=fixture_index.get((y,w,tm),[])
        need(len(options)==1,'EXACT_FIXTURE_JOIN_UNRESOLVED')
        gid=options[0]
    need(gid in fixtures,'UNVERIFIED_SOURCE_FIXTURE')
    f=fixtures[gid]
    need((f['season'],f['week'])==(y,w) and tm in (f['home_team'],f['away_team']),'SOURCE_FIXTURE_CONFLICT')
    opponent=team(row.get('opponent_team') or row.get('opponent'))
    need(opponent is None or opponent==(f['away_team'] if tm==f['home_team'] else f['home_team']),'SOURCE_OPPONENT_CONFLICT')
    return gid,tm


def _complete(fixture,cutoff):
    clock=fixture.get('completion_observed_at')
    return fixture['completed'] is True and clock is not None and instant(clock)<=cutoff and instant(clock)>=instant(fixture['kickoff_utc'])


def _categories(rows,fixtures,cutoff):
    valid={};gaps=[];conflicted=set()
    for row in rows:
        key=(row.get('game_id'),team(row.get('team')),row.get('channel'))
        try:
            gid,tm,channel=key;need(gid in fixtures and channel in CHANNEL_FIELDS,'CATEGORY_IDENTITY')
            need(tm in (fixtures[gid]['home_team'],fixtures[gid]['away_team']),'CATEGORY_TEAM')
            need(visible(row,cutoff) and _complete(fixtures[gid],cutoff),'CATEGORY_COMPLETION_OR_CLOCK')
            need(row.get('complete') is True and row.get('identity_verified') is True,'CATEGORY_NOT_VERIFIED_COMPLETE')
            fields=CHANNEL_FIELDS[channel];players={}
            need(isinstance(row.get('players'),dict),'CATEGORY_PLAYERS_REQUIRED')
            for gs,data in row['players'].items():
                need(GSIS.fullmatch(gs or '') is not None,'CATEGORY_GSIS')
                vals={k:number(data.get(k),count=not k.endswith('_yards')) for k in fields}
                need(all(v is not None for v in vals.values()),'CATEGORY_UNKNOWN_PLAYER_VALUE')
                players[gs]=vals
            totals={k:number(row.get('totals',{}).get(k),count=not k.endswith('_yards')) for k in fields}
            need(all(v is not None and sum(p[k] for p in players.values())==v for k,v in totals.items()),'CATEGORY_TOTALS_NOT_RECONCILED')
            need(key not in valid and key not in conflicted,'DUPLICATE_COMPLETE_CATEGORY')
            valid[key]={'players':players,'totals':totals,'source':provenance(row)}
        except (ValueError,TypeError) as exc:
            gaps.append({'code':str(exc),'game_id':key[0],'team':key[1]})
            if str(exc)=='DUPLICATE_COMPLETE_CATEGORY':
                conflicted.add(key);valid.pop(key,None)
    return valid,gaps


def project_rbfb(pbp,fixtures,roster_index,*,cutoff,target_period):
    """Credit eligible lateral yards before excluding QB primary rushing."""
    cut=instant(cutoff) if isinstance(cutoff,str) else cutoff
    buckets=defaultdict(lambda:{'carries':0,'yards':0,'unknown':0})
    seen=set();ended=set();counts=Counter();gaps=[]
    for row in pbp:
        try:
            if period(row)>=tuple(target_period):continue
            gid=row.get('game_id');need(gid in fixtures,'PBP_FIXTURE_UNVERIFIED')
            f=fixtures[gid];need(period(row)==period(f),'PBP_FIXTURE_PERIOD')
            need(visible(row,cut),'PBP_CLOCK_UNKNOWN_OR_LATE')
            need(row.get('season_type')=='REG','PBP_PHASE_UNKNOWN')
            need(team(row.get('home_team'))==f['home_team'] and team(row.get('away_team'))==f['away_team'],'PBP_TEAM_MISMATCH')
            pk=(gid,row.get('play_id'));need(pk[1] not in (None,'') and pk not in seen,'DUPLICATE_OR_MISSING_PLAY')
            seen.add(pk)
            if row.get('play_type')=='game_end' or row.get('game_half')=='End of Game' or row.get('desc')=='END GAME':
                ended.add(gid);continue
            yes=lambda key:row.get(key) in (1,'1','1.0',True)
            if row.get('play_type')=='no_play' or yes('two_point_attempt') or yes('qb_kneel'):
                counts['excluded_noneligible_play']+=1;continue
            if not yes('rush_attempt'):
                if row.get('play_type')=='run':
                    for tm in (f['home_team'],f['away_team']):buckets[gid,tm]['unknown']+=1
                    counts['missing_rush_indicator']+=1
                continue
            tm=team(row.get('posteam'));need(tm in (f['home_team'],f['away_team']),'PBP_OFFENSE_IDENTITY')
            bucket=buckets[gid,tm]
            def position(gs):
                rr=roster_index.get((f['season'],f['week'],tm,gs));return rr.get('position') if rr else None
            lateral=row.get('lateral_rusher_player_id')
            if lateral not in (None,'','NA','nan'):
                lp=position(lateral);ly=number(row.get('lateral_rushing_yards'))
                if lp is None or ly is None:bucket['unknown']+=1;counts['unknown_lateral']+=1
                elif lp in ('RB','FB'):bucket['yards']+=ly;counts['eligible_lateral_yards']+=ly
            if yes('qb_scramble'):
                counts['excluded_primary_scramble']+=1;continue
            gs=row.get('rusher_player_id');pos=position(gs)
            if pos is None:bucket['unknown']+=1;counts['unresolved_rusher']+=1;continue
            if pos not in ('RB','FB'):counts['excluded_primary_non_rbfb']+=1;continue
            yards=number(row.get('rushing_yards'))
            if yards is None:bucket['unknown']+=1;continue
            bucket['carries']+=1;bucket['yards']+=yards
        except (ValueError,TypeError) as exc:
            gid=row.get('game_id');gaps.append({'code':str(exc),'game_id':gid})
            if gid in fixtures:
                for tm in (fixtures[gid]['home_team'],fixtures[gid]['away_team']):buckets[gid,tm]['unknown']+=1
    result=[]
    for gid,f in sorted(fixtures.items()):
        if period(f)>=tuple(target_period):continue
        for tm,opponent in ((f['home_team'],f['away_team']),(f['away_team'],f['home_team'])):
            b=buckets[gid,tm];ready=gid in ended and f['pbp_complete'] and _complete(f,cut) and b['unknown']==0
            carries=b['carries'] if ready else None;yards=b['yards'] if ready else None
            result.append(dict(season=f['season'],week=f['week'],game_id=gid,team=tm,opponent=opponent,
                rbfb_eligible_carries=carries,rbfb_eligible_rushing_yards=yards,rb_carries=carries,rb_yards=yards,
                state='READY' if ready else 'UNKNOWN_CONTEXT',unknown_plays=b['unknown'],end_marker=gid in ended))
    return result,dict(counts),gaps


def project_canonical(fixtures,rosters,stats,snaps,pbp,*,cutoff,target_period,
                      classification='RETROSPECTIVE',categories=(),participation=()):
    """Build earlier-only history and full target roster census from typed rows.

    Required fixture witnesses: identity_verified, kickoff_utc, observed_at and,
    for history outcomes, completed=True plus completion_observed_at. Missing
    snap rows, categories, positions or clocks never imply zero or nonappearance.
    snap_complete is an explicit per-fixture completeness witness, not inferred.
    """
    need(classification in ('SYNTHETIC','RETROSPECTIVE','PROSPECTIVE'),'SOURCE_CLASSIFICATION')
    cut=instant(cutoff);target=tuple(target_period)
    need(len(target)==2 and all(type(n) is int for n in target),'TARGET_PERIOD')
    fmap,quarantine=_fixture_map(fixtures,cut,classification)
    fi=defaultdict(list)
    for gid,f in fmap.items():
        for tm in (f['home_team'],f['away_team']):fi[(f['season'],f['week'],tm)].append(gid)
    roster_index={};bad_roster=set();aliases=defaultdict(set)
    for row in rosters:
        try:
            y,w=period(row)
            if (y,w)>target:continue
            need(visible(row,cut),'ROSTER_CLOCK_UNKNOWN_OR_LATE')
            need(row.get('game_type')=='REG','ROSTER_PHASE')
            gs=row.get('gsis') or row.get('gsis_id');tm=team(row.get('team'));pos=row.get('position')
            need(GSIS.fullmatch(gs or '') is not None and tm and pos,'DATED_GSIS_OR_POSITION_REQUIRED')
            k=(y,w,tm,gs)
            if k in roster_index and roster_index[k]['position']!=pos:
                bad_roster.add(k);raise ValueError('CONFLICTING_DATED_POSITION')
            rr=dict(row,gsis=gs,team=tm,position=pos)
            roster_index[k]=rr
            alias=row.get('pfr_id') or row.get('pfr_player_id')
            if alias:aliases[y,w,tm,alias].add(gs)
        except (ValueError,TypeError) as exc:quarantine.append({'code':str(exc),'gsis':row.get('gsis') or row.get('gsis_id')})
    for key in bad_roster:roster_index.pop(key,None)
    statmap={};snapmap={};denominators=defaultdict(list);bad_keys=set();blocked_teams=set()
    for source,rows in (('stats',stats),('snaps',snaps)):
        for row in rows:
            gid=tm=None
            try:
                y,w=period(row)
                if (y,w)>=target:continue  # reject target-week poisoning before reading any number
                need(visible(row,cut),'HISTORY_CLOCK_UNKNOWN_OR_LATE')
                gid,tm=_game_key(row,fmap,fi)
                need(row.get('season_type' if source=='stats' else 'game_type')=='REG','HISTORY_PHASE_UNKNOWN')
                gs=row.get('gsis') or row.get('player_id') or row.get('gsis_id')
                if source=='snaps':
                    off=number(row.get('offense_snaps'),count=True);denominators[gid,tm].append(off)
                    if not gs:
                        options=aliases.get((y,w,tm,row.get('pfr_player_id')),set())
                        need(len(options)==1,'DATED_PROVIDER_ALIAS_UNRESOLVED');gs=next(iter(options))
                need(GSIS.fullmatch(gs or '') is not None,'VERIFIED_GSIS_REQUIRED')
                rk=(y,w,tm,gs);need(rk in roster_index,'DATED_ROSTER_MEMBERSHIP_REQUIRED')
                key=(gid,tm,gs);dest=statmap if source=='stats' else snapmap
                if key in dest:
                    bad_keys.add(key);raise ValueError('DUPLICATE_'+source.upper())
                dest[key]=dict(row,gsis=gs,team=tm,game_id=gid)
            except (ValueError,TypeError) as exc:
                quarantine.append({'code':str(exc),'source':source,'game_id':row.get('game_id')})
                if gid in fmap and tm:blocked_teams.add((gid,tm))
    for k in bad_keys:statmap.pop(k,None);snapmap.pop(k,None)
    category_map,category_gaps=_categories(categories,fmap,cut);quarantine.extend(category_gaps)
    witnessed_keys={(r['game_id'],team(r['team']),r['gsis']) for r in participation if visible(r,cut)}
    records=[];census=[]
    for gid,f in sorted(fmap.items(),key=lambda x:(period(x[1]),x[0])):
        fp=period(f)
        if fp>target:continue
        for (y,w,tm,gs),rr in sorted(roster_index.items()):
            if (y,w)!=fp or tm not in (f['home_team'],f['away_team']) or rr['position'] not in SKILL:continue
            if rr.get('valid_from') and instant(rr['valid_from'])>instant(f['kickoff_utc']):continue
            if rr.get('valid_to') and instant(rr['valid_to'])<=instant(f['kickoff_utc']):continue
            identity=dict(season=y,week=w,game_id=gid,gsis=gs,team=tm,position=rr['position'],
                          opponent=f['away_team'] if tm==f['home_team'] else f['home_team'],
                          kickoff_utc=f['kickoff_utc'],identity_state='EXACT_DATED_ROSTER_GSIS')
            if fp==target:
                future=instant(f['kickoff_utc'])>cut
                census.append(dict(identity,classification=classification,pregame_eligible=future and classification=='PROSPECTIVE',
                                   observed_at=observation(rr),availability='UNKNOWN',source=provenance(rr)))
                continue
            st=statmap.get((gid,tm,gs));sn=snapmap.get((gid,tm,gs));off=number(sn.get('offense_snaps'),count=True) if sn else None
            vals={k:number(st.get(k),count=not k.endswith('_yards')) if st else None for k in FIELDS}
            positive=any(v is not None and v!=0 for v in vals.values())
            witnessed=bool(off is not None and off>0 or positive or (gid,tm,gs) in witnessed_keys)
            dnp=rr.get('did_not_play') is True or (sn or {}).get('did_not_play') is True
            conflict=(off==0 and positive) or (dnp and witnessed) or ((gid,tm,gs) in bad_keys)
            complete=_complete(f,cut)
            states={k:'UNKNOWN' for k in FIELDS};basis=['PBP_PARTICIPATION'] if (gid,tm,gs) in witnessed_keys else []
            if not complete:
                vals=dict.fromkeys(FIELDS);appearance=None;appearance_state='COMPLETION_UNVERIFIED'
            elif dnp and not witnessed:
                vals=dict.fromkeys(FIELDS);appearance=False;appearance_state='DNP'
                states=dict.fromkeys(FIELDS,'DNP')
            elif conflict:
                vals=dict.fromkeys(FIELDS);appearance=None;appearance_state='PARTICIPATION_CONFLICT'
                quarantine.append({'code':appearance_state,'game_id':gid,'gsis':gs})
            elif witnessed:
                appearance=True;appearance_state='WITNESSED_APPEARANCE'
                for channel,fields in CHANNEL_FIELDS.items():
                    category=category_map.get((gid,tm,channel))
                    if category:
                        peer=category['players'].get(gs,dict.fromkeys(fields,0))
                        for field in fields:
                            if vals[field] is not None and vals[field]!=peer[field]:
                                vals[field]=None;states[field]='UNKNOWN';quarantine.append({'code':'PRODUCTION_RECONCILIATION_CONFLICT','game_id':gid,'gsis':gs,'field':field})
                            elif vals[field] is None and off is not None and off>0:
                                vals[field]=peer[field];basis.append('POSITIVE_SNAPS_COMPLETE_CATEGORY')
                    for field in fields:states[field]='UNKNOWN' if vals[field] is None else 'ZERO' if vals[field]==0 else 'OBSERVED'
                if vals['targets'] is not None and vals['receptions'] is not None and vals['receptions']>vals['targets']:
                    for field in CHANNEL_FIELDS['receiving']:vals[field]=None;states[field]='UNKNOWN'
                    quarantine.append({'code':'RECEPTIONS_EXCEED_TARGETS','game_id':gid,'gsis':gs})
            else:
                appearance=None;appearance_state='UNKNOWN_PARTICIPATION';vals=dict.fromkeys(FIELDS)
            denoms=denominators.get((gid,tm),[])
            den=max(denoms) if denoms and all(v is not None for v in denoms) and f['snap_complete'] and (gid,tm) not in blocked_teams else None
            share=off/den if off is not None and den is not None and den>0 else None
            need(share is None or 0<=share<=1,'SNAP_DENOMINATOR_INVALID')
            records.append(dict(identity,appeared=appearance,state=appearance_state,offense_snaps=off,
                snap_share=share,snap_denominator=den,snap_denominator_definition='MAX_INDIVIDUAL_OFFENSIVE_SNAPS_TEAM_GAME',
                observed_at=max(filter(None,[observation(rr),observation(st or {}),observation(sn or {})]),key=instant,default=None),
                available_at=None,classification=classification,value_states=states,source_basis=sorted(set(basis)),
                source_lineage=[provenance(r) for r in (rr,st,sn) if r],original_values={k:(st or {}).get(k) for k in FIELDS},**vals))
    rbfb,rbcounts,rbgaps=project_rbfb(pbp,fmap,roster_index,cutoff=cut,target_period=target);quarantine.extend(rbgaps)
    rbindex={(r['season'],r['week'],r['team']):r for r in rbfb}
    contexts=[];seen_team_week=set()
    for gid,f in sorted(fmap.items(),key=lambda x:(period(x[1]),x[0])):
        if period(f)>=target:continue
        for tm,opponent in ((f['home_team'],f['away_team']),(f['away_team'],f['home_team'])):
            key=(f['season'],f['week'],tm);need(key not in seen_team_week,'DUPLICATE_TEAM_WEEK_FIXTURE');seen_team_week.add(key)
            # An arbitrary subset of appeared players is not a team denominator.
            # Use the independently reconciled full category or retain null.
            category=category_map.get((gid,tm,'receiving'))
            receiving={field:category['totals'][field] if category and (gid,tm) not in blocked_teams else None
                       for field in CHANNEL_FIELDS['receiving']}
            rb=rbindex.get(key,{})
            contexts.append(dict(season=f['season'],week=f['week'],game_id=gid,team=tm,opponent=opponent,
                                 carries=rb.get('rb_carries'),rushing_yards=rb.get('rb_yards'),
                                 rb_carries=rb.get('rb_carries'),rb_yards=rb.get('rb_yards'),**receiving))
    history_max=max((period(r) for r in records if r['appeared'] is True),default=None)
    unknown=sum(r['appeared'] is True and any(r[k] is None for k in FIELDS) for r in records)
    return {'schema_version':'puntproof-lab-canonical-source-v1','classification':classification,
            'records':records,'census':census,'team_context':contexts,'rbfb_context':rbfb,'advanced':[],
            'quarantine':quarantine,'coverage':{'records':len(records),'target_census':len(census),
                'witnessed_appearances':sum(r['appeared'] is True for r in records),
                'unknown_production_appearances':unknown,'history_through':history_max,
                'team_week_contexts':len(contexts),'rbfb_counts':rbcounts},
            'source_certificate_created':False,'ready_for_model':False,
            'admission_note':'Pure projection does not grant source/model admission, completion or prospective certification.'}


def next_week_metadata_readiness(source_repo,*,as_of,through_period=(2026,6)):
    """Stable metadata-only report; the polling clock is stored separately."""
    need(tuple(through_period)[0]==2026 and 1<=tuple(through_period)[1]<18,'NEXT_WEEK_SCOPE')
    inventory=select_snapshot_metadata(source_repo,as_of=as_of)
    next_week=through_period[1]+1
    action='Open Data Manager and review its retained 2026 source status; refresh only through its existing approved acquisition workflow, then rerun metadata sync.'
    blockers=[dict(issue,next_action=action) for issue in inventory['gaps']]
    coverage=[]
    for item in inventory['snapshots']:
        summary=item['metadata_coverage']
        coverage.append(dict(source=item['source_id'],snapshot_id=item['id'],acquired=item['acquired'],season=2026,**summary))
        weeks=summary['declared_weeks']
        if weeks is None:
            blockers.append(dict(code='SOURCE_WEEK_COVERAGE_UNKNOWN',source=item['source_id'],detail='The selected producer metadata does not declare its represented weeks.',next_action=action))
        elif (next_week if item['source_id']=='rosters' else through_period[1]) not in weeks:
            needed=next_week if item['source_id']=='rosters' else through_period[1]
            blockers.append(dict(code='HISTORY_OR_IDENTITY_SOURCE_GAP',source=item['source_id'],required_week=needed,declared_max_week=summary['declared_max_week'],detail='Required earlier history or target dated identity is absent from source metadata.',next_action=action))
    blockers.append(dict(code='SOURCE_PAYLOAD_UNVERIFIED',detail='Retained metadata is available; exact admitted payload import and fixture validation have not been checked here.',next_action='Connect available data to verify the retained records.'))
    dependencies={
        'forecast':'Needs verified earlier history, dated target identities and fixture metadata; target outcomes and snap values are not mandatory.',
        'settlement':'Needs sealed forecasts and verified completed target-week outcomes.',
        'snaps':'PFR remains restricted; independent BDL needs model-use admission and a verified native-ID crosswalk. Unknown snap features remain null.',
        'historical_replay':'Exact historical replay needs the separately missing Full Evidence package.'}
    return dict(schema_version='puntproof-lab-next-week-metadata-v1',classification='METADATA_ONLY',
        through_period=list(through_period),required_next_period=[through_period[0],next_week],
        metadata_database=inventory['metadata_database'],snapshots=inventory['snapshots'],
        source_coverage=coverage,status='SOURCE_NOT_READY',blockers=blockers,dependencies=dependencies,
        payloads_opened=0,provider_calls=0,forecast_issued=False)


def persist_source_readiness(store,source_repo,*,as_of,through_period=(2026,6)):
    report=next_week_metadata_readiness(source_repo,as_of=as_of,through_period=through_period)
    identifier=hashlib.sha256(json.dumps(report,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:24]
    receipt_id='source-readiness-'+identifier
    store.append('receipt',receipt_id,report,immutable=True)
    previous=store.get_setting('last_source_readiness')
    store.set_setting('source_readiness_last_checked',as_of)
    store.set_setting('last_source_readiness',report)
    store.set_setting('source_readiness_receipt_id',receipt_id)
    if previous!=report:
        for issue in report['blockers']:
            entity='next-week-source'+(':'+issue['source'] if issue.get('source') else '')
            store.alert(issue['code'],'P1','Fresh source readiness: '+issue['code'],issue,entity)
    return report


SCHEDULE_FIELDS=frozenset('game_id season game_type week gameday gametime away_team home_team'.split())


def read_bound_schedule(root,ref,*,max_bytes=16*1024*1024,max_rows=100000):
    """Project only the admitted 2026 REG identity columns from retained CSV."""
    from nfl_model.research_lab_gate import require_retained_source
    p=Path(ref['path']);expected=ref['sha256']
    grant=require_retained_source(root,p,expected,'schedule',2026)
    need(grant.get('format')=='CSV_2026_REG_IDENTITY_ONLY' and set(grant['allowed_fields'])==SCHEDULE_FIELDS,'SCHEDULE_FIELD_ADMISSION')
    observed=grant['observed_at'];instant(observed)
    need(p.stat().st_size<=max_bytes,'SCHEDULE_SIZE_LIMIT')
    rows=[];scanned=0
    with p.open('r',encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream);names=reader.fieldnames or []
        need(len(names)==len(set(names)) and SCHEDULE_FIELDS<=set(names),'SCHEDULE_SCHEMA')
        for native in reader:
            scanned+=1;need(scanned<=max_rows,'SCHEDULE_ROW_LIMIT')
            # No other field, including historical outcome/price columns, is
            # projected or used until the exact season and phase match.
            if native.get('season')!='2026' or native.get('game_type')!='REG':continue
            need(None not in native,'SCHEDULE_MALFORMED_ROW')
            clean={k:(None if native.get(k) in ('','NA','NaN','nan') else native.get(k)) for k in SCHEDULE_FIELDS}
            clean['_source']={'source_id':grant.get('source_id'),'source_version':expected,
                'raw_sha256':expected,'normalized_sha256':None,'observed_at':observed,
                'published_at':None,'available_at':None,'model_permission':grant['permission']}
            rows.append(clean)
    with p.open('rb') as stream:need(hashlib.file_digest(stream,'sha256').hexdigest()==expected,'SCHEDULE_CHANGED_DURING_READ')
    return {'records':rows,'row_count':len(rows),'dataset':'schedule','season':2026,
        'sha256':expected,'observed_at':observed,'provider_calls':0,
        'projection':'2026_REG_IDENTITY_ONLY','source_certificate_created':False}



EASTERN_ZONE_SHA256='d7f2206b3a45989fc9ad63d558922532fa7352280d5f87176bf1db79cb1d1fa9'


@lru_cache(maxsize=1)
def schedule_timezone():
    """Use the exact installed V3 tzdata binary, without altering project Python."""
    path=Path(__file__).resolve().parents[2]/'scratch/v3-env/Lib/site-packages/tzdata/zoneinfo/America/New_York'
    need(path.is_file(),'VERIFIED_EASTERN_TIMEZONE_DATA_UNAVAILABLE')
    with path.open('rb') as stream:
        need(hashlib.file_digest(stream,'sha256').hexdigest()==EASTERN_ZONE_SHA256,'EASTERN_TIMEZONE_HASH_CHANGED')
        stream.seek(0)
        return ZoneInfo.from_file(stream,key='America/New_York')

def prepare_retained_inputs(schedule,rosters,stats,pbp,*,cutoff,target_period,_settlement=False):
    """Pure, earlier-only V3 source preparation with explicit cohort exclusions.

    Only a known production or PBP contribution witnesses participation. Missing
    participation cannot be deleted from a player's apparent recent history.
    Snap features stay null; untouched V3 handles those nullable inputs itself.
    No target-week production number is inspected or used by this function.
    """
    target=tuple(target_period);cut=instant(cutoff)
    need(len(target)==2 and target[0]==2026 and type(target[1]) is int and 2<=target[1]<=(19 if _settlement else 18),'TARGET_PERIOD')
    census_check=verify_schedule_census(schedule,target[1])
    fixtures=[];gaps=[]
    for raw in schedule:
        try:
            if period(raw)[0]!=2026 or raw.get('game_type')!='REG' or period(raw)>target:continue
            need(visible(raw,cut),'SCHEDULE_CLOCK_UNKNOWN_OR_LATE')
            y,w=period(raw);home=team(raw.get('home_team'));away=team(raw.get('away_team'))
            need(home and away and home!=away,'SCHEDULE_TEAMS')
            need(raw.get('game_id')==f'{y}_{w:02d}_{away}_{home}','SCHEDULE_CANONICAL_ID')
            need(raw.get('gameday') and raw.get('gametime'),'SCHEDULE_KICKOFF_UNKNOWN')
            local=datetime.fromisoformat(raw['gameday']+'T'+raw['gametime'])
            need(local.tzinfo is None,'SCHEDULE_NATIVE_TIMEZONE')
            kickoff=local.replace(tzinfo=schedule_timezone()).astimezone(timezone.utc)
            fixtures.append(dict(season=y,week=w,game_id=raw['game_id'],home_team=home,away_team=away,
                kickoff_utc=kickoff.isoformat(),observed_at=observation(raw),identity_verified=True,
                completed=False,completion_observed_at=None,pbp_complete=False,snap_complete=False,
                source_ref=provenance(raw)))
        except (ValueError,TypeError,KeyError) as exc:gaps.append({'code':str(exc),'game_id':raw.get('game_id')})
    fmap,fgaps=_fixture_map(fixtures,cut,'RETROSPECTIVE');gaps.extend(fgaps)
    team_week_games=defaultdict(set)
    for gid,f in fmap.items():
        for tm in (f['home_team'],f['away_team']):team_week_games[(*period(f),tm)].add(gid)
    ambiguous={gid for games in team_week_games.values() if len(games)>1 for gid in games}
    unresolved_fixture_periods=set()
    for gid in ambiguous:
        f=fmap.pop(gid)
        unresolved_fixture_periods.update((*period(f),tm) for tm in (f['home_team'],f['away_team']))
        gaps.append({'code':'AMBIGUOUS_TEAM_WEEK_FIXTURE','game_id':gid})
    dated={};conflicts=set()
    for raw in rosters:
        try:
            if period(raw)[0]!=2026 or period(raw)>target or raw.get('game_type')!='REG':continue
            need(visible(raw,cut),'ROSTER_CLOCK_UNKNOWN_OR_LATE')
            gs=raw.get('gsis') or raw.get('gsis_id');tm=team(raw.get('team'));pos=raw.get('position')
            need(GSIS.fullmatch(gs or '') and tm and pos,'DATED_GSIS_OR_POSITION_REQUIRED')
            key=(*period(raw),tm,gs)
            if key in dated and dated[key]['position']!=pos:conflicts.add(key)
            dated[key]=dict(raw,gsis=gs,team=tm)
        except (ValueError,TypeError) as exc:gaps.append({'code':str(exc),'gsis':raw.get('gsis_id')})
    for key in conflicts:dated.pop(key,None)
    participation={};ended={};seen=set();bad_games=set();contributions=defaultdict(lambda:dict(targets=0,receptions=0,receiving_yards=0))
    bad_receiving=set()
    prior_pbp=[]
    for row in pbp:
        try:
            if period(row)[0]!=2026 or period(row)>=target:continue
            if row.get('season_type')!='REG':continue
            gid=row.get('game_id');need(gid in fmap,'PBP_FIXTURE_UNVERIFIED');f=fmap[gid]
            need(period(row)==period(f) and team(row.get('home_team'))==f['home_team'] and team(row.get('away_team'))==f['away_team'],'PBP_FIXTURE_CONFLICT')
            need(visible(row,cut),'PBP_CLOCK_UNKNOWN_OR_LATE')
            pk=(gid,row.get('play_id'));need(pk[1] not in (None,'') and pk not in seen,'PBP_DUPLICATE_PLAY');seen.add(pk)
            prior_pbp.append(row)
            if row.get('play_type')=='game_end' or row.get('game_half')=='End of Game' or row.get('desc')=='END GAME':
                ended[gid]=max(ended.get(gid,observation(row)),observation(row),key=instant);continue
            yes=lambda k:row.get(k) in (1,'1','1.0',True)
            if row.get('play_type')=='no_play' or yes('two_point_attempt'):continue
            tm=team(row.get('posteam'))
            if tm not in (f['home_team'],f['away_team']):continue
            def witness(gs):
                if gs in (None,'','NA','nan'):return False
                need(GSIS.fullmatch(gs or '') is not None,'PBP_GSIS_INVALID')
                participation[gid,tm,gs]=dict(game_id=gid,team=tm,gsis=gs,observed_at=observation(row))
                return True
            if yes('pass_attempt'):
                gs=row.get('receiver_player_id');known=witness(gs)
                if known:
                    z=contributions[gid,tm,gs];z['targets']+=1
                    if yes('complete_pass'):
                        z['receptions']+=1;v=number(row.get('receiving_yards'))
                        if v is None:bad_receiving.add((gid,tm))
                        else:z['receiving_yards']+=v
                elif yes('complete_pass'):bad_receiving.add((gid,tm))
                lateral=row.get('lateral_receiver_player_id')
                if witness(lateral):
                    v=number(row.get('lateral_receiving_yards'))
                    if v is None:bad_receiving.add((gid,tm))
                    else:contributions[gid,tm,lateral]['receiving_yards']+=v
            if yes('rush_attempt'):
                witness(row.get('rusher_player_id'));witness(row.get('lateral_rusher_player_id'))
        except (ValueError,TypeError) as exc:
            gid=row.get('game_id');gaps.append({'code':str(exc),'game_id':gid});bad_games.add(gid)
    for gid,f in fmap.items():
        if gid in ended and gid not in bad_games:
            f.update(completed=True,completion_observed_at=ended[gid])
    fixture_index=defaultdict(list)
    for gid,f in fmap.items():
        for tm in (f['home_team'],f['away_team']):fixture_index[(*period(f),tm)].append(gid)
    statmap={};bad_stats=set()
    for row in stats:
        try:
            if period(row)[0]!=2026 or period(row)>=target or row.get('season_type')!='REG':continue
            gid,tm=_game_key(row,fmap,fixture_index);gs=row.get('gsis') or row.get('player_id')
            need(gid in fmap and period(row)==period(fmap[gid]),'STAT_FIXTURE_UNVERIFIED')
            need(visible(row,cut) and tm in (fmap[gid]['home_team'],fmap[gid]['away_team']),'STAT_CLOCK_OR_TEAM')
            need(GSIS.fullmatch(gs or '') is not None,'STAT_GSIS_INVALID')
            key=(gid,tm,gs);need(key not in statmap,'STAT_DUPLICATE');statmap[key]=dict(row,game_id=gid,team=tm)
        except (ValueError,TypeError) as exc:
            gaps.append({'code':str(exc),'game_id':row.get('game_id')});bad_stats.add(row.get('game_id'))
    categories=[];unresolved_context=set(bad_receiving)
    for gid,f in fmap.items():
        if period(f)>=target:continue
        for tm in (f['home_team'],f['away_team']):
            players={};valid=gid in ended and gid not in bad_games and gid not in bad_stats and (gid,tm) not in bad_receiving
            for (game,side,gs),row in statmap.items():
                if (game,side)!=(gid,tm):continue
                rr=dated.get((*period(f),tm,gs))
                if rr is None:
                    if any(number(row.get(k),count=not k.endswith('_yards')) not in (None,0) for k in FIELDS):valid=False
                    continue
                if rr['position'] not in SKILL:continue
                vals={k:number(row.get(k),count=not k.endswith('_yards')) for k in CHANNEL_FIELDS['receiving']}
                if any(v is None for v in vals.values()):valid=False;continue
                if vals!=contributions.get((gid,tm,gs),dict.fromkeys(CHANNEL_FIELDS['receiving'],0)):valid=False
                players[gs]=vals
            for (game,side,gs),vals in contributions.items():
                if (game,side)!=(gid,tm):continue
                rr=dated.get((*period(f),tm,gs))
                if rr is None or rr['position'] in SKILL and gs not in players:valid=False
            if valid and players:
                categories.append(dict(game_id=gid,team=tm,channel='receiving',complete=True,identity_verified=True,
                    observed_at=ended[gid],players=players,totals={k:sum(p[k] for p in players.values()) for k in CHANNEL_FIELDS['receiving']}))
            else:unresolved_context.add((gid,tm))
    # PBP context remains null unless its own full eligible rushing accounting
    # reconciles with known dated RB/FB player statistics for both teams.
    provisional=[dict(f,pbp_complete=True) for f in fmap.values()]
    rb,_,_=project_rbfb(prior_pbp,{f['game_id']:f for f in provisional},dated,cutoff=cut,target_period=target)
    verified_rb=set()
    for r in rb:
        gid,tm=r['game_id'],r['team'];f=fmap[gid];players=[];valid=r['state']=='READY'
        for (game,side,gs),row in statmap.items():
            if (game,side)!=(gid,tm):continue
            rr=dated.get((*period(f),tm,gs))
            if rr and rr['position'] in ('RB','FB'):
                vals=[number(row.get(k),count=k=='carries') for k in ('carries','rushing_yards')]
                if None in vals:valid=False
                else:players.append(vals)
        if valid and players and sum(v[0] for v in players)==r['rb_carries'] and sum(v[1] for v in players)==r['rb_yards']:verified_rb.add((gid,tm))
    for gid,f in fmap.items():f['pbp_complete']=all((gid,tm) in verified_rb for tm in (f['home_team'],f['away_team']))
    result=project_canonical(list(fmap.values()),list(dated.values()),list(statmap.values()),[],prior_pbp,
        cutoff=cutoff,target_period=target,classification='RETROSPECTIVE',categories=categories,
        participation=list(participation.values()))
    # prepare.load rebuilds receiving/positional totals from appeared records.
    # Mask unverified team-game receiving context explicitly, never let a subset
    # masquerade as the complete denominator consumed by the unchanged builder.
    for row in result['records']:
        if (row['game_id'],row['team']) in unresolved_context:
            for field in CHANNEL_FIELDS['receiving']:row[field]=None;row['value_states'][field]='UNKNOWN'
    histories=defaultdict(list)
    for row in result['records']:histories[row['gsis'],row['team']].append(row)
    fixture_lists={'receiving':[],'rushing':[]};exclusions=[]
    for row in result['census']:
        history=sorted(histories[row['gsis'],row['team']],key=period)
        recent_known=[r for r in history if r['appeared'] is True][-12:]
        if len(recent_known)==12:history=[r for r in history if period(r)>=period(recent_known[0])]
        for channel,fields in CHANNEL_FIELDS.items():
            if channel=='rushing' and row['position'] not in ('RB','FB'):continue
            known=[r for r in history if r['appeared'] is True]
            reason=None
            membership=[key[1] for key in dated if key[0]==2026 and key[2:]==(row['team'],row['gsis']) and key[1]<target[1]]
            first_required=recent_known[0]['week'] if len(recent_known)==12 else min(membership,default=target[1])
            expected_games={f['game_id'] for f in fmap.values() if membership and first_required<=f['week']<target[1] and row['team'] in (f['home_team'],f['away_team'])}
            if any(y==2026 and tm==row['team'] and first_required<=w<target[1] for y,w,tm in unresolved_fixture_periods):reason='UNRESOLVED_PRIOR_FIXTURE_IDENTITY'
            elif expected_games!={r['game_id'] for r in history}:reason='UNRESOLVED_PRIOR_ROSTER_MEMBERSHIP'
            elif any(r['appeared'] is None for r in history):reason='UNRESOLVED_PRIOR_PARTICIPATION'
            elif len(known)<2:reason='TOO_FEW_SAME_TEAM_APPEARANCES'
            elif any(any(r[k] is None for k in fields) for r in known):reason='UNKNOWN_RECENT_CHANNEL_VALUES'
            elif any(('RB' if r['position']=='FB' else r['position'])!=('RB' if row['position']=='FB' else row['position']) for r in known):reason='DATED_POSITION_CHANGE'
            item={k:row[k] for k in ('season','week','game_id','gsis','team','opponent','position')};item['channel']=channel
            rr=dated.get((row['season'],row['week'],row['team'],row['gsis']),{})
            name=rr.get('full_name') or rr.get('player_name')
            if name:item['name']=name
            if reason:exclusions.append({'fixture':item,'reason':reason})
            else:fixture_lists[channel].append(item)
    lineage={json.dumps(provenance(r),sort_keys=True):provenance(r) for group in (schedule,rosters,stats,pbp) for r in group if observation(r)}
    coverage=dict(result['coverage'],source_rows={k:len(v) for k,v in (('schedule',schedule),('rosters',rosters),('player_stats',stats),('pbp',pbp))},
        eligible={k:len(v) for k,v in fixture_lists.items()},excluded=len(exclusions),
        null_snap_records=len(result['records']),verified_receiving_categories=len(categories),
        verified_rbfb_team_games=len(verified_rb),unknown_receiving_team_games=len(unresolved_context))
    return {'schema_version':'puntproof-lab-prepared-inputs-v1','data':{k:result[k] for k in ('records','team_context','advanced')},
        'rbfb_context':result['rbfb_context'],'fixtures':fixture_lists,'schedule':list(fmap.values()),
        'coverage':dict(coverage,full_fixture_census_verified=census_check['verified']),'exclusions':exclusions,'quarantine':gaps+result['quarantine'],
        'full_fixture_census_verified':census_check['verified'] and census_check['target_game_ids']==sorted(f['game_id'] for f in fmap.values() if period(f)==target),
        'fixture_census':census_check,
        'source_lineage':list(lineage.values()),'history_through':result['coverage']['history_through'],
        'classification':'EXPOSED_RETROSPECTIVE_DEVELOPMENT','population_scope':'VERIFIED_SUBSET',
        'history_scope':'AVAILABLE_2026_HISTORY_ONLY_NOT_EXACT_HISTORICAL_REPLAY',
        'source_certificate_created':False,'known_snap_denominators_verified':True,'unknowns_preserved':True,
        'provider_calls':0,'target_outcomes_used':0}

NFL_TEAMS=frozenset('ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LA LAC LV MIA MIN NE NO NYG NYJ PHI PIT SEA SF TB TEN WAS'.split())


def verify_schedule_census(schedule,target_week):
    """Validate the retained regular-season schedule shape, not live freshness."""
    rows=[];keys=set();team_weeks=set();counts=Counter();reason=None
    try:
        for r in schedule:
            if period(r)[0]!=2026 or r.get('game_type')!='REG':continue
            y,w=period(r);away=team(r.get('away_team'));home=team(r.get('home_team'));gid=r.get('game_id')
            need(1<=w<=18 and away in NFL_TEAMS and home in NFL_TEAMS and away!=home,'SCHEDULE_TEAM_OR_WEEK')
            need(gid==f'{y}_{w:02d}_{away}_{home}' and gid not in keys,'SCHEDULE_GAME_DUPLICATE_OR_ID')
            need((w,away) not in team_weeks and (w,home) not in team_weeks,'SCHEDULE_TEAM_WEEK_DUPLICATE')
            keys.add(gid);team_weeks.update(((w,away),(w,home)));counts.update((away,home));rows.append(r)
        need(len(rows)==272 and set(counts)==NFL_TEAMS and all(n==17 for n in counts.values()),'FULL_REGULAR_SEASON_CENSUS_NOT_ESTABLISHED')
    except (ValueError,TypeError):reason='FULL_REGULAR_SEASON_CENSUS_NOT_ESTABLISHED'
    target=sorted(r['game_id'] for r in rows if period(r)==(2026,target_week))
    return {'verified':reason is None and bool(target),'target_game_ids':target,
        'reason':reason,'basis':'RETAINED_2026_REG_272_UNIQUE_GAMES_32_TEAMS_17_EACH','live_schedule_freshness_verified':False}


def prepare_retained_settlement(schedule,rosters,stats,pbp,forecast_rows,*,target_period,observed_at):
    """Project later admitted outcomes for the exact sealed forecast population."""
    target=tuple(target_period);need(target[0]==2026 and 1<=target[1]<=18,'SETTLEMENT_PERIOD')
    cut=instant(observed_at);census=verify_schedule_census(schedule,target[1])
    if not census['verified']:
        return {'status':'WAITING','reason':'Complete retained target-week fixture census is not established.',
            'dependency':'FULL_TARGET_WEEK_FIXTURE_CENSUS','fixtures':[],'outcomes':{},'full_fixture_census_verified':False}
    prepared=prepare_retained_inputs(schedule,rosters,stats,pbp,cutoff=observed_at,
        target_period=(target[0],target[1]+1),_settlement=True)
    fixtures=[f for f in prepared['schedule'] if period(f)==target]
    if sorted(f['game_id'] for f in fixtures)!=census['target_game_ids']:
        return {'status':'WAITING','reason':'Target-week fixture identities or kickoff times are incomplete.',
            'dependency':'VERIFIED_TARGET_WEEK_FIXTURE_IDENTITIES','fixtures':[],'outcomes':{},'full_fixture_census_verified':False}
    missing=[f['game_id'] for f in fixtures if not _complete(f,cut)]
    if missing:
        return {'status':'WAITING','reason':'Completed target-week PBP end markers are missing for '+str(len(missing))+' fixture(s).',
            'dependency':'TARGET_WEEK_COMPLETED_PBP','missing_games':missing,'fixtures':[],
            'outcomes':{},'full_fixture_census_verified':True}
    index={(r['season'],r['game_id'],r['gsis'],r['team']):r for r in prepared['data']['records'] if period(r)==target}
    groups=forecast_rows if isinstance(forecast_rows,dict) else {c:[r for r in forecast_rows if r.get('channel')==c] for c in CHANNEL_FIELDS}
    outcomes={};seen=set()
    for channel,rows in groups.items():
        need(channel in CHANNEL_FIELDS,'SETTLEMENT_CHANNEL');outcomes[channel]=[]
        for forecast in rows:
            f=forecast.get('fixture',forecast)
            need(period(f)==target and f['channel']==channel and f['game_id'] in census['target_game_ids'],'SETTLEMENT_FORECAST_IDENTITY')
            key=(f['season'],f['game_id'],f['gsis'],f['team']);identity=(*key,channel)
            need(identity not in seen,'SETTLEMENT_DUPLICATE_FORECAST');seen.add(identity)
            row=index.get(key);known=bool(row and row['appeared'] is True and all(row[k] is not None for k in CHANNEL_FIELDS[channel]))
            outcome={k:f[k] for k in ('season','week','game_id','gsis','team','opponent','position','channel')}
            outcome.update(status='APPEARED' if known else 'UNKNOWN',
                yards=row['receiving_yards' if channel=='receiving' else 'rushing_yards'] if known else None,
                opportunities=row['targets' if channel=='receiving' else 'carries'] if known else None)
            outcomes[channel].append(outcome)
    return {'status':'VERIFIED_COMPLETE','reason':None,'full_fixture_census_verified':True,
        'fixtures':[{'game_id':f['game_id'],'kickoff':f['kickoff_utc'],'completed_at':f['completion_observed_at'],
                     'completion_clock_semantics':'OBSERVED_COMPLETE_IN_RETAINED_PBP','final':True} for f in fixtures],
        'outcomes':outcomes,'unknown_outcomes':sum(r['status']=='UNKNOWN' for rows in outcomes.values() for r in rows),
        'source_observed_at':observed_at,'provider_calls':0}
