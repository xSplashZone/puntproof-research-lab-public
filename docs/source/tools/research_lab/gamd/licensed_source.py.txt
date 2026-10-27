"""Authenticated retained BDL projection. No network, mutation or fitting on import."""
from collections import defaultdict,Counter
from datetime import datetime,timezone
from pathlib import Path
import csv,gzip,hashlib,json,math,re,unicodedata
FIELDS={'receiving_targets':'targets','receptions':'receptions','receiving_yards':'receiving_yards','rushing_attempts':'carries','rushing_yards':'rushing_yards'}
DEN='MAX_INDIVIDUAL_OFFENSIVE_SNAPS_TEAM_GAME'
def need(ok,code):
 if not ok:raise ValueError('LICENSED_SOURCE:'+code)
def digest(raw):return hashlib.sha256(raw).hexdigest()
def dump(x):return json.dumps(x,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def team(x):
 x=x.get('abbreviation') if isinstance(x,dict) else x
 return {'LAR':'LA','JAC':'JAX','WSH':'WAS','OAK':'LV','SD':'LAC'}.get(x,x)
def name(x):
 x=''.join(c for c in unicodedata.normalize('NFKD',str(x)) if not unicodedata.combining(c)).casefold()
 x=re.sub(r'\b(jr|sr|ii|iii|iv)\.?$','',x).strip();return re.sub('[^a-z0-9]','',x)
def position(x):return 'RB' if x=='FB' else x
def integer(x):
 if x is None or x in ('','NA','nan'):return None
 try:v=float(x)
 except (TypeError,ValueError):return None
 return int(v) if math.isfinite(v) and v.is_integer() and not isinstance(x,bool) else None
def clock(x):return datetime.fromisoformat(x.replace('Z','+00:00'))
def key(r):return tuple(r[k] for k in ('season','week','game_id','gsis','team'))
def verified_bytes(base,ref):
 p=(Path(base)/ref['path']).resolve();need(p.is_relative_to(Path(base).resolve()) and not p.is_symlink(),'PATH_ESCAPE')
 raw=p.read_bytes();need(digest(raw)==ref['sha256'],'RAW_CUSTODY');return raw

def read_partition(base,part):
 """Decode only an admitted nonprotected REG partition, authenticate every page."""
 need(part['season'] in (2023,2024,2026) and part['phase']==2,'PROTECTED_OR_NONREG_PARTITION')
 need(part['endpoint'] in ('/nfl/v1/games','/nfl/v1/stats','/nfl/v1/snap_counts') and part['state']=='EXHAUSTED','PARTITION_NOT_COMPLETE')
 pages=part['page_records'];need(len(pages)==part['pages'] and sum(p['rows'] for p in pages)==part['rows'],'PAGE_CENSUS')
 cursor=None;seen=set()
 for p in pages:
  need(p['status']==200 and p['cursor_in']==cursor,'HTTP_OR_CURSOR_CENSUS')
  meta=json.loads(verified_bytes(base,{'path':p['meta_path'],'sha256':p['meta_sha']}))
  need(meta['status']==200 and meta['endpoint']==part['endpoint'] and meta['raw_sha']==p['raw_sha'] and meta['gzip_sha']==p['gzip_sha'],'RAW_METADATA_MISMATCH')
  need((meta['query'].get('season_type')==2 if part['endpoint']=='/nfl/v1/snap_counts' else meta['query'].get('season_types[]')==[2]) and meta['query'].get('seasons[]')==[part['season']],'REQUEST_SEASON_SCOPE')
  compressed=verified_bytes(base,{'path':p['raw_path'],'sha256':p['gzip_sha']});raw=gzip.decompress(compressed)
  need(len(raw)<=128*1024*1024 and digest(raw)==p['raw_sha'],'UNCOMPRESSED_CUSTODY')
  doc=json.loads(raw);rows=doc['data'];need(isinstance(rows,list) and len(rows)==p['rows'],'PAGE_ROW_CENSUS')
  hashes=sorted(digest(dump(r).encode()) for r in rows);need(digest(dump(hashes).encode())==p['rows_sha'],'ROW_CUSTODY')
  next_cursor=doc.get('meta',{}).get('next_cursor');need((dump(next_cursor) if next_cursor is not None else None)==p['cursor_out'],'CURSOR_RESPONSE')
  if next_cursor is None:need('next_cursor' in doc.get('meta',{}) or (len(rows)<meta['query']['per_page'] and doc['meta'].get('prev_cursor')==meta['query'].get('cursor')),'TERMINAL_PAGE_WITNESS')
  cursor=p['cursor_out'];need(cursor is None or cursor not in seen,'CURSOR_LOOP');seen.add(cursor)
  for row in rows:yield row,{'path':(Path(base)/p['raw_path']).as_posix(),'sha256':p['gzip_sha'],'raw_sha256':p['raw_sha'],'observed_at':p['retrieved_at'],'request_id':p['request_id']}
 need(cursor is None,'PAGINATION_NOT_EXHAUSTED')

def fixture(native,schedules,year):
 need(native.get('season')==year and not native.get('postseason'),'GAME_SEASON_PHASE')
 w=native.get('week');need(type(w) is int and 1<=w<=18,'GAME_WEEK')
 if year==2026 and w>=7:return None
 home,away=team(native.get('home_team')),team(native.get('visitor_team'));gid=f'{year}_{w:02d}_{away}_{home}'
 s=schedules.get(gid);need(s and home==s['home_team'] and away==s['away_team'],'GAME_TEAM_IDENTITY')
 need(native.get('date') and abs((clock(native['date'])-clock(s['kickoff_utc'])).total_seconds())<=3600,'GAME_DATE_IDENTITY')
 final=native.get('status_state') in ('post','completed','final') or native.get('status') in ('Final','Final/OT','Final/2OT')
 need(final,'GAME_NOT_VERIFIED_FINAL')
 return {**s,'native_game_id':native['id'],'completed':True}

def make_index(rows):
 index=defaultdict(dict)
 for r in rows:
  if not r.get('name') or not r.get('gsis'):continue
  k=(r['season'],r['week'],r['game_id'],r['team'],position(r['position']),name(r['name']))
  index[k][r['gsis']]=r
 return index

def match(native,game,index):
 player=native.get('player') or {};tm=team(native.get('team'));need(tm in (game['home_team'],game['away_team']),'ROW_TEAM_OUTSIDE_GAME')
 pos=player.get('position_abbreviation') or player.get('position');full=(str(player.get('first_name') or '')+' '+str(player.get('last_name') or '')).strip()
 k=(game['season'],game['week'],game['game_id'],tm,position(pos),name(full));matches=index.get(k,{})
 need(len(matches)==1,'AMBIGUOUS_IDENTITY' if len(matches)>1 else 'NO_UNIQUE_DATED_NAME_POSITION_TEAM')
 found=next(iter(matches.values()));need(type(player.get('id')) is int,'NATIVE_PLAYER_ID_REQUIRED')
 return {**{k:found[k] for k in ('season','week','game_id','gsis','team','position','name')},'native_player_id':player['id'],'native_game_id':game['native_game_id'],'identity_state':'EXACT_DATED_ROSTER_GSIS','birthdate_evidence':None,'match_basis':'UNIQUE_FULL_NAME_POSITION_DATED_TEAM_GAME; native ID consistency checked across all admitted games'}

def snap_values(rows,*,complete_censuses=None):
 """Individual counts do not establish a complete offensive-player census.

 Optional witnesses must come from an independently authenticated full census,
 not pagination, a minimum row count or this capture's own list of players.
 No current retained catalogue supplies one; this pure function grants no
 source certification. The admitted caller authenticates a witness's source.
 """
 groups=defaultdict(dict);bad=set();captures=defaultdict(list)
 for r in rows:
  k=(r['game']['id'],team(r.get('team')));pid=r.get('player',{}).get('id');n=integer(r.get('offense_snaps'))
  captures[k].append(r)
  if pid is None or n is None or n<0:bad.add(k);continue
  if pid in groups[k] and groups[k][pid]!=n:bad.add(k)
  groups[k][pid]=n
 result={}
 for k,players in groups.items():
  witness=(complete_censuses or {}).get(k,{});capture_sha=digest(dump(captures[k]).encode())
  complete=bool(players) and k not in bad and max(players.values())>0 and (
   witness.get('basis')=='INDEPENDENT_COMPLETE_OFFENSIVE_PARTICIPANT_CENSUS' and
   witness.get('capture_sha256')==capture_sha and
   re.fullmatch('[0-9a-f]{64}',witness.get('source_sha256','')) is not None and
   witness.get('source_sha256')!=capture_sha and
   set(witness.get('native_player_ids',[]))==set(players))
  result[k]={'snap_denominator':max(players.values()) if complete else None,
   'observed_max_offense_snaps':max(players.values()) if players else None,
   'native_players':len(players),'team_game_complete':complete,
   'snap_denominator_definition':DEN,'completeness_basis':witness if complete else None,
   'capture_sha256':capture_sha,'conflicted_capture':k in bad}
 return result

def execute(root,mr):
 from nfl_model import licensed_bdl_gate as gate
 from gamd.source import read_partition as read_nflverse
 from gamd.registry import write_once
 from coverage_review import reconcile_coverage,reconciliation_csv,reconciliation_json
 from source_adapter import schedule_timezone
 root=Path(root)
 with gate.authority(root,gate.SOURCE,mr) as m:
  consumer,_=gate.consumer_for(root,gate.SOURCE);catalog=gate.read(root,consumer['catalogue'],True);out=root/m['output'];need(not (out/'RECEIPT.json').exists(),'SOURCE_ALREADY_COMPLETED')
  original=gate.read(root,catalog['original_projection']);decisions=gate.read(root,catalog['original_decisions'])
  historical=[json.loads(line) for line in gate.checked(root,catalog['historical_projection']).splitlines()]
  # The existing projection is explicitly admitted; numerical2025 rows are absent.
  need(all(r['season'] in (2021,2022,2023,2024) for r in historical),'PROJECTION_PROTECTED_SEASON')
  historical=[r for r in historical if r['season'] in (2023,2024)]
  schedules={}
  for e in catalog['schedules']:
   for r in read_nflverse(e['source_root'],e,years=(2023,2024,2026)):
    if r['season']==2026 and r['week']>=7:continue
    local=datetime.fromisoformat(r['gameday']+'T'+r['gametime']).replace(tzinfo=schedule_timezone());r['kickoff_utc']=local.astimezone(timezone.utc).isoformat();schedules[r['game_id']]=r
  identities=list(historical)
  for e in catalog['identity_rosters']:
   raw=verified_bytes(e['source_root'],{'path':e['path'],'sha256':e['sha256']})
   for line in gzip.decompress(raw).splitlines():
    r=json.loads(line);y=integer(r.get('season'));w=integer(r.get('week'));tm=team(r.get('team'))
    if y!=2026 or w is None or w>=7 or r.get('game_type')!='REG':continue
    matches=[s for s in schedules.values() if s['season']==y and s['week']==w and tm in (s['home_team'],s['away_team'])]
    if len(matches)==1 and r.get('gsis_id') and r.get('position') in ('WR','TE','RB','FB'):
     identities.append({'season':y,'week':w,'game_id':matches[0]['game_id'],'team':tm,'gsis':r['gsis_id'],'position':r['position'],'name':r.get('full_name') or r.get('player_name')})
  index=make_index(identities);games={};payloads={'stats':[],'snap_counts':[]};quarantine=[];page_count=0
  for part in catalog['partitions']:
   ds=part['endpoint'].rsplit('/',1)[-1];page_count+=len(part['page_records'])
   for raw,src in read_partition(catalog['source_root'],part):
    ng=raw if ds=='games' else raw.get('game',{})
    if part['season']==2026 and (integer(ng.get('week')) or 99)>=7:continue
    try:
     game=fixture(ng,schedules,part['season'])
     if game is None:continue
     need(clock(src['observed_at'])>clock(game['kickoff_utc']),'COMPLETION_CLOCK')
     if ds=='games':games[ng['id']]={**game,'completion_observed_at':src['observed_at'],'source':src}
     else:payloads[ds].append((raw,src,game))
    except ValueError as exc:quarantine.append({'dataset':ds,'native_game_id':ng.get('id'),'code':str(exc)})
  # Every source row requires the separate completed game census, not just nested status.
  census=snap_values([raw for raw,src,g in payloads['snap_counts'] if raw['game']['id'] in games])
  mapped=[];native_map=defaultdict(set)
  for ds,values in payloads.items():
   for raw,src,g in values:
    pid=(raw.get('player') or {}).get('id');pos=(raw.get('player') or {}).get('position_abbreviation') or (raw.get('player') or {}).get('position')
    if pos not in ('WR','TE','RB','FB'):continue
    try:
     need(g['native_game_id'] in games,'NO_INDEPENDENT_FINAL_GAME_CENSUS');found=match(raw,g,index);native_map[pid].add(found['gsis']);mapped.append((ds,raw,src,g,found))
    except ValueError as exc:quarantine.append({'dataset':ds,'native_player_id':pid,'native_game_id':g['native_game_id'],'name':str((raw.get('player') or {}).get('first_name',''))+' '+str((raw.get('player') or {}).get('last_name','')),'code':str(exc)})
  inconsistent={pid for pid,gs in native_map.items() if len(gs)!=1};crosswalk={};snaps={};stats={};conflicts=set()
  for ds,raw,src,g,found in mapped:
   k=key(found)
   if found['native_player_id'] in inconsistent:quarantine.append({'identity':list(k),'code':'NATIVE_ID_MULTIPLE_GSIS'});continue
   crosswalk[k]={**found,'game_source':games[g['native_game_id']]['source'],'source':src}
   value={**found,'source':src,'game':games[g['native_game_id']]}
   if ds=='snap_counts':
    den=census.get((g['native_game_id'],found['team']),{});off=integer(raw.get('offense_snaps'))
    if off is None or off<0:quarantine.append({'identity':list(k),'code':'INVALID_INDIVIDUAL_SNAP_COUNT'});continue
    denominator=den.get('snap_denominator')
    value.update(den,offense_snaps=off,snap_share=off/denominator if denominator and off<=denominator else None,provider_offense_snap_pct=raw.get('offense_snap_pct'));target=snaps
   else:
    value['values']={dst:integer(raw.get(srcfield)) for srcfield,dst in FIELDS.items()};target=stats
   if k in target and {x:v for x,v in value.items() if x!='source'}!={x:v for x,v in target[k].items() if x!='source'}:conflicts.add(k)
   else:target[k]=value
  for k in conflicts:snaps.pop(k,None);stats.pop(k,None);quarantine.append({'identity':list(k),'code':'DUPLICATE_NATIVE_ROW_CONFLICT'})
  # Reconcile known values conservatively. A discrepant box score never replaces the original.
  merged=[];field_counts=Counter();resolved=Counter()
  for old in historical:
   row=dict(old);k=key(old);sn=snaps.get(k);st=stats.get(k);op,yard=('targets','receiving_yards') if row['channel']=='receiving' else ('carries','rushing_yards')
   row.update(offense_snaps=None,snap_share=None,snap_denominator=None)
   if sn:
    row.update({f:sn[f] for f in ('offense_snaps','snap_share','snap_denominator')});field_counts[str(row['season'])+'_snap_rows']+=1
    if sn['offense_snaps']>0 and row['appeared'] is None:row.update(appeared=True,participation_state='BDL_VERIFIED_OFFENSIVE_SNAPS');resolved[str(row['season'])+'_appearances']+=1
   if st and row['appeared'] is True:
    if any(old.get(dst) is not None and st['values'].get(src) is not None and old[dst]!=st['values'][src] for src,dst in ((op,'opportunity'),(yard,'yards'))):quarantine.append({'identity':list(k),'channel':row['channel'],'code':'STAT_CONFLICT_WITH_ADMITTED_LABEL'})
    else:
     for src,dst in ((op,'opportunity'),(yard,'yards')):
      if row[dst] is None and st['values'].get(src) is not None:row[dst]=st['values'][src];resolved[str(row['season'])+'_'+dst]+=1
   row['bdl_evidence']=[v['source'] for v in (sn,st) if v];merged.append(row)
  # Read original2024 B/F laws without replaying models or touching protected2025.
  controls={};control_count=0
  for entry in catalog.get('frozen_control_partitions',[]):
   need(entry['season']==2024 and 5<=entry['week']<=18,'CONTROL_SEASON')
   seal=json.loads(verified_bytes(entry['source_root'],entry['seal']));need(seal['forecasts']==entry['forecasts'],'CONTROL_ORIGINAL_SEAL')
   for line in gzip.decompress(verified_bytes(entry['source_root'],entry['forecasts'])).splitlines():
    f=json.loads(line);target=f['target'];need(target['season']==2024 and target['week']==entry['week'],'CONTROL_TARGET_PERIOD')
    if f['candidate'] not in ('B','F') or f['status']!='READY':continue
    history=f.get('history_keys',[])
    need(history and all((int(k[0]),int(str(k[1]).split('_')[1]))<(2024,entry['week']) for k in history),'CONTROL_LOOKAHEAD')
    ck=(*key(target),f['channel']);bucket=controls.setdefault(ck,{})
    need(f['candidate'] not in bucket,'CONTROL_DUPLICATE')
    bucket[f['candidate']]={'forecast':f['forecast'],'source':entry['forecasts'],'seal':entry['seal'],'original_seal_at':seal['at'],'historical_pregame_certified':False};control_count+=1
  for row in merged:row['frozen_controls']=controls.get((*key(row),row['channel']),{})
  # Coverage proofs may use explicitly stored original stats even when appearance was unknown.
  proofs=[];admissions={};oldhistory={key(r):r for r in original['data']['records'] if r['season']==2026 and r['week']<7}
  for k,sn in snaps.items():
   if k not in oldhistory or sn['offense_snaps']<=0:continue
   old=oldhistory[k];values={f:sn[f] for f in ('offense_snaps','snap_denominator','snap_denominator_definition','team_game_complete')};st=stats.get(k)
   explicit={f:integer(v) for f,v in old.get('original_values',{}).items() if f in FIELDS.values() and integer(v) is not None}
   conflict=False
   for f,v in (st or {}).get('values',{}).items():
    if v is None:continue
    if f in explicit and explicit[f]!=v:conflict=True
    if old.get(f) is not None and old[f]!=v:conflict=True
    explicit.setdefault(f,v)
   if conflict:quarantine.append({'identity':list(k),'code':'COVERAGE_STAT_CONFLICT'});continue
   if explicit:values.update(explicit_player_stats=explicit,explicit_stats_ref=({'path':st['source']['path'],'sha256':st['source']['sha256']} if st else catalog['original_projection']))
   sr={'path':sn['source']['path'],'sha256':sn['source']['sha256']};game=sn['game'];proof={**{f:sn[f] for f in ('season','week','game_id','gsis','team')},'kind':'offensive_snaps','source_id':'licensed_balldontlie','dataset':'snap_counts','source_ref':sr,'values':values,'observed_at':sn['source']['observed_at'],'identity_state':sn['identity_state'],'completed':True,'completion_observed_at':game['completion_observed_at'],'kickoff_utc':game['kickoff_utc']};proofs.append(proof)
   admissions[sr['sha256']]={'artifact':sr,'source_id':'licensed_balldontlie','dataset':'snap_counts','rights_owner':'BALLDONTLIE','licence_lane':gate.FAMILY,'model_use':True,'upstream_sources':['BALLDONTLIE_API'],'seasons':[2026],'allowed_fields':['offense_snaps','snap_denominator','snap_denominator_definition','team_game_complete','explicit_player_stats','explicit_stats_ref'],'evidence_types':['offensive_snaps','explicit_player_stats'],'authority':{'operation':gate.SOURCE,'consumer':consumer_path(consumer,gate),'manifest':mr,'readiness':m['readiness'],'source_policy':consumer['source_policy'],'semantics':consumer['foundations']['certificate']}}
  report=reconcile_coverage(decisions,original,original_ref=catalog['original_decisions'],projection_ref=catalog['original_projection'],original_cutoff=catalog['original_cutoff'],research_cutoff=m['as_of'],evidence_rows=proofs,admissions=list(admissions.values()))
  out.mkdir(parents=True,exist_ok=True)
  def lines(n,rows):
   p=out/n
   with p.open('xb') as f:
    for row in rows:f.write((dump(row)+'\n').encode())
   return gate.ref(root,p)
  projection=lines('ROWS.jsonl',merged);cross=lines('CROSSWALK.jsonl',list(crosswalk.values()));quar=lines('QUARANTINE.jsonl',quarantine);snref=lines('SNAPS.jsonl',list(snaps.values()))
  for n,raw in [('COVERAGE.json',reconciliation_json(report)),('COVERAGE.csv',reconciliation_csv(report))]:
   with (out/n).open('xb') as f:f.write(raw)
  from gamd.licensed_train import build
  split_rows={}
  for ch in ('receiving','rushing'):
   ex=build(merged,ch,include_snaps=True)['examples'];split_rows[ch]={'train':sum(x['season']==2023 for x in ex),'calibration':sum(x['season']==2024 and x['week']<=4 for x in ex),'evaluation':sum(x['season']==2024 and x['week']>=5 for x in ex)}
  receipt={'status':'COMPLETE','source_manifest':mr,'projection':projection,'source_policy':consumer['source_policy'],'catalogue':consumer['catalogue'],'source_pages_loaded':page_count,'source_partitions_loaded':len(catalog['partitions']),'source_rows':{ds:len(v) for ds,v in payloads.items()},'year_coverage':[{'season':y,'status':('Protected numerical season excluded by repository authority' if y==2025 else 'BDL not retained; existing nflverse history remains separately available' if y in (2021,2022) else 'Retained private research; Week1-6 only' if y==2026 else 'Retained private research'),'snap_rows':sum(k[0]==y for k in snaps) if y in (2023,2024,2026) else None,'stats_rows':sum(k[0]==y for k in stats) if y in (2023,2024,2026) else None} for y in range(2021,2027)],'frozen_control_forecasts_projected':control_count,'crosswalk_rows':len(crosswalk),'native_players_mapped':len(set(v['native_player_id'] for v in crosswalk.values())),'crosswalk':cross,'quarantine':quar,'quarantine_counts':dict(Counter(q['code'] for q in quarantine)),'snap_rows':len(snaps),'snap_artifact':snref,'field_coverage':dict(field_counts),'recovered_history':dict(resolved),'rows':len(merged),'split_rows':split_rows,'coverage_summary':report['summary'],'coverage_json':gate.ref(root,out/'COVERAGE.json'),'coverage_csv':gate.ref(root,out/'COVERAGE.csv'),'seasons':[2023,2024],'provider_calls':0,'original_forecasts_modified':0,'recorded_at':gate.now(),'pregame_seal':catalog['pregame_seal'],'classification':'EXPOSED_REVISED_HISTORY_NOT_PROSPECTIVE','identity_limitation':'Birthdates unavailable; no name-only or ambiguous matches admitted.','denominator':DEN}
  for f in ('original_decisions','original_projection','pregame_seal','historical_projection'):gate.checked(root,catalog[f])
  write_once(out/'RECEIPT.json',receipt);return receipt

def consumer_path(consumer,gate):return gate.BASE+'SOURCE_CONSUMER.json'
