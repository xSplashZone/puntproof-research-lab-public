"""Year-partitioned, field-closed projection. Execution always requires GAMD admission."""
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
from collections import defaultdict, Counter

FIELDS={
 'schedule':('season','game_type','week','game_id','home_team','away_team','gameday','gametime'),
 'pbp':('season','season_type','week','game_id','home_team','away_team','posteam','receiver_player_id','rusher_player_id','passer_player_id','lateral_receiver_player_id','lateral_rusher_player_id','complete_pass','rush_attempt','pass_attempt','play_type','qb_kneel','qb_spike'),
 'player_stats':('season','season_type','week','player_id','player_name','player_display_name','position','recent_team','team','opponent_team','targets','receptions','receiving_yards','carries','rushing_yards','attempts','completions',),
 'rosters':('season','season_type','game_type','week','gsis_id','team','position','full_name','status')}
POSITIONS={'WR','TE','RB','FB'}

def sha(path):
 with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def number(v):
 if v in (None,'','NA','NaN','nan'):return None
 try:x=float(v)
 except (ValueError,TypeError):return None
 return x if math.isfinite(x) else None

def read_partition(root,entry,*,years):
 """Authenticate one admitted year file, filter period before projecting fields."""
 dataset=entry['dataset'];year=int(entry['season'])
 if dataset not in FIELDS or year not in years or year==2025:raise ValueError('SOURCE_PARTITION_DENIED')
 if set(entry['allowed_fields'])!=set(FIELDS[dataset]):raise ValueError('SOURCE_FIELD_CONTRACT_CHANGED')
 root=Path(root).resolve();p=(root/entry['path']).resolve();p.relative_to(root)
 if p.is_symlink() or sha(p)!=entry['sha256']:raise ValueError('SOURCE_BYTES_CHANGED')
 opener=gzip.open if entry.get('format')=='jsonl.gz' else open
 with opener(p,mode='rt',encoding='utf-8-sig',newline='') as f:
  reader=(json.loads(line) for line in f) if entry.get('format')=='jsonl.gz' else csv.DictReader(f)
  for raw in reader:
   required={'schedule':('game_id','home_team','away_team','gameday'),'pbp':('game_id','home_team','away_team','posteam'),'rosters':('gsis_id','team','position'),'player_stats':('player_id','position','targets','receiving_yards','carries','rushing_yards')}[dataset]
   if not set(required)<=set(raw) or 'week' not in raw:raise ValueError('SOURCE_SCHEMA_MISSING_REQUIRED_FIELDS')
   actual=number(raw.get('season'))
   if actual is None:
    if dataset=='schedule':raise ValueError('SCHEDULE_SEASON_REQUIRED')
    actual=year
   if actual!=year:
    if dataset=='schedule':continue
    raise ValueError('CROSS_SEASON_ROW')
   week=number(raw.get('week'))
   if week is None or not 1<=week<=18:continue
   if not float(week).is_integer():raise ValueError('NONINTEGER_SOURCE_WEEK')
   if year==2026 and week>entry.get('through_week',6):continue
   # Match the existing Data Manager's null representation in raw historical
   # CSVs. A missing GSIS must never become a shared literal "NA" identity.
   row={k:(None if raw.get(k) in (None,'','NA','NaN','nan') else raw.get(k)) for k in FIELDS[dataset]}
   types=[row[k] for k in ('season_type','game_type') if row.get(k) is not None]
   if not types:raise ValueError('EXPLICIT_REGULAR_SEASON_TYPE_REQUIRED')
   if any(v!='REG' for v in types):continue
   row.update(season=year,week=int(week))
   row['_source']={k:entry[k] for k in ('path','sha256','observed_at','dataset','season')}
   yield row
 if sha(p)!=entry['sha256']:raise ValueError('SOURCE_CHANGED_DURING_READ')


def project(partitions):
 """Pure stream projector; unknown no-touch roster games remain unknown."""
 fixtures={};fixture_sources=defaultdict(list);touches=set();rosters={};stats={};problems=[];sources={}
 for dataset,rows in partitions:
  for row in rows:
   y,w=row['season'],row['week'];src=row['_source'];sources[(src['dataset'],src['season'],src['path'],src['sha256'])]=src
   if dataset in ('pbp','schedule'):
    gid=row.get('game_id');home=row.get('home_team');away=row.get('away_team')
    if not gid or not home or not away:continue
    for team,opp in ((home,away),(away,home)):
     key=(y,w,team);v=(gid,opp)
     if key in fixtures and fixtures[key]!=v:raise ValueError('AMBIGUOUS_TEAM_GAME')
     fixtures[key]=v
     if src not in fixture_sources[key]:fixture_sources[key].append(src)
    if dataset=='schedule':continue
    # Positive native recorded involvement, never absence or inferred snaps.
    team=row.get('posteam')
    if team not in (home,away):continue
    for field in ('receiver_player_id','rusher_player_id','passer_player_id','lateral_receiver_player_id','lateral_rusher_player_id'):
     gsis=row.get(field)
     if gsis and row.get('play_type') in ('pass','run') and number(row.get('qb_kneel'))!=1 and number(row.get('qb_spike'))!=1:touches.add((y,w,team,gsis))
   elif dataset=='rosters':
    gsis=row.get('gsis_id');team=row.get('team')
    if not gsis or not team:continue
    key=(y,w,team,gsis)
    if key in rosters and rosters[key]['position']!=row['position']:raise ValueError('DATED_POSITION_CONFLICT')
    rosters[key]=row
   elif dataset=='player_stats':
    gsis=row.get('player_id');team=row.get('recent_team') or row.get('team')
    if not gsis or not team:continue
    key=(y,w,team,gsis)
    if key in stats and any(stats[key].get(k)!=row.get(k) for k in ('targets','receptions','receiving_yards','carries','rushing_yards')):raise ValueError('DUPLICATE_STAT_CONFLICT')
    stats[key]=row
 for key in sorted(set(stats)-set(rosters)):problems.append({'key':list(key),'code':'STAT_IDENTITY_WITHOUT_DATED_ROSTER'})
 for key in sorted(set(touches)-set(rosters)):problems.append({'key':list(key),'code':'PBP_IDENTITY_WITHOUT_DATED_ROSTER'})
 rows=[]
 for key,roster in sorted(rosters.items()):
  y,w,team,gsis=key;position=roster.get('position');fixture=fixtures.get((y,w,team))
  if position not in POSITIONS:continue
  if fixture is None:problems.append({'key':list(key),'code':'ROSTER_WITHOUT_RESOLVED_FIXTURE'});continue
  stat=stats.get(key,{});conflict=bool((stat.get('position') and stat['position']!=position) or (stat.get('opponent_team') and stat['opponent_team']!=fixture[1]))
  positive=any((number(stat.get(k)) or 0)>0 for k in ('targets','receptions','carries','attempts','completions'))
  appeared=True if not conflict and (positive or key in touches) else None
  proof='POSITIVE_RECORDED_WORKLOAD' if positive else ('POSITIVE_PBP_INVOLVEMENT' if key in touches else 'UNSUPPORTED_PRIOR_PARTICIPATION')
  if conflict:proof='DATED_IDENTITY_CONFLICT';problems.append({'key':list(key),'code':proof})
  provenance=[roster['_source']]+([stat['_source']] if stat else [])+fixture_sources[(y,w,team)]
  if key in touches:provenance += [s for s in sources.values() if s['dataset']=='pbp' and s['season']==y]
  for channel,oppfield,yardfield in [('receiving','targets','receiving_yards')]+([('rushing','carries','rushing_yards')] if position in ('RB','FB') else []):
   rows.append(dict(season=y,week=w,game_id=fixture[0],gsis=gsis,team=team,opponent=fixture[1],position=position,channel=channel,
    name=roster.get('full_name'),appeared=appeared,opportunity=number(stat.get(oppfield)) if appeared else None,
    yards=number(stat.get(yardfield)) if appeared else None,participation_state=proof,
    source_observed_at=max(p['observed_at'] for p in provenance),provenance=provenance,
    classification='REVISED_HISTORY_NOT_VERIFIED_AS_PREGAME',verified_active=None))
 from .features import validate_row,identity
 keys=set()
 for row in rows:
  validate_row(row,(2021,2022,2023,2024,2026))
  key=identity(row)
  if key in keys:raise ValueError('DUPLICATE_PROJECTED_PLAYER_GAME_CHANNEL')
  keys.add(key)
 positive_labels={channel:dict(Counter(f"{r['season']}-{r['week']:02d}" for r in rows if r['channel']==channel and r['appeared'] is True and r['opportunity'] is not None and r['yards'] is not None and r['opportunity']>0)) for channel in ('receiving','rushing')}
 labels={channel:dict(Counter(f"{r['season']}-{r['week']:02d}" for r in rows if r['channel']==channel and r['appeared'] is True and r['opportunity'] is not None and r['yards'] is not None and float(r['opportunity']).is_integer() and float(r['yards']).is_integer() and r['opportunity']>=0)) for channel in ('receiving','rushing')}
 # Calendar identities and statistics-bearing games are separate censuses.
 stat_games={(key[0],fixtures[key[:3]][0]) for key in stats if key[:3] in fixtures}
 calendar_games={(key[0],value[0]) for key,value in fixtures.items()}
 return {'rows':rows,'audit':{'rows':len(rows),'fixtures':len(calendar_games),
  'fixtures_by_season':{str(y):sum(season==y for season,gid in stat_games) for y in sorted({r['season'] for r in rows})},
  'calendar_fixtures_by_season':{str(y):sum(season==y for season,gid in calendar_games) for y in sorted({r['season'] for r in rows})},
  'fixture_count_basis':'STATISTICS_BEARING_IDENTITIES; distinct from calendar census; no fabricated completion timestamps',
  'label_rows_by_channel_season_week':labels,'positive_label_rows_by_channel_season_week':positive_labels,'row_schema_validation':'PASS','sources':list(sources.values()),'problems':problems,
  'snap_fields_used':False,'dnp_inferred':False,'unknowns_imputed_zero':False}}


def execute(root,manifest_path):
 # Gate authentication precedes all payload reads. No fallback reader exists.
 from nfl_model import gamd_gate
 with gamd_gate.source_authority(Path(root),Path(manifest_path)) as admitted:
  partitions=((e['dataset'],read_partition(e['source_root'],e,years=admitted['years'])) for e in admitted['inputs'])
  return project(partitions)
