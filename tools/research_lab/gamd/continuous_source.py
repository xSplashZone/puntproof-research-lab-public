"""Field-level repairs and an independent dated future menu, in a new private lane."""
from collections import defaultdict,Counter
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import csv,gzip,io,json
from gamd import licensed_source as ls
FIELDS={'receiving':('targets','receptions','receiving_yards'),'rushing':('carries','rushing_yards')}
def category_proofs(projection):
 contexts={(x['game_id'],x['team']):x for x in projection['data']['team_context']};groups=defaultdict(list)
 for r in projection['data']['records']:groups[r['game_id'],r['team']].append(r)
 result={}
 for key,rows in groups.items():
  for ch,fields in FIELDS.items():
   candidates=[r for r in rows if ch=='receiving' or r['position'] in ('RB','FB')]
   explicit={r['gsis']:{f:ls.integer(r.get('original_values',{}).get(f)) for f in fields} for r in candidates if any(ls.integer(r.get('original_values',{}).get(f)) is not None for f in fields)}
   totals={f:contexts.get(key,{}).get(f) for f in fields}
   valid=bool(explicit) and all(v is not None for p in explicit.values() for v in p.values()) and all(v is not None and sum(p[f] for p in explicit.values())==v for f,v in totals.items())
   result[(*key,ch)]={'complete_reconciled':valid,'players':explicit,'team_totals':totals,'basis':'ORIGINAL_PBP_RECONCILED_CATEGORY_AND_EXPLICIT_PLAYER_SUM'}
 return result

def repair(projection,previous,snaps,stats,conflicts):
 """All original decisions, all failed prerequisites; no replacement of old bytes."""
 original={ls.key(r):r for r in projection['data']['records']};baseline=deepcopy(original);proofs=category_proofs(projection)
 verified={(g['game_id'],r['gsis'],r['team']) for r in previous['rows'] for g in r['after']['verified_games']}
 for key,row in baseline.items():
  if (row['game_id'],row['gsis'],row['team']) in verified:row['appeared']=True
  sn=snaps.get(key);st=stats.get(key,{})
  if sn and sn['offense_snaps']>0 and key not in conflicts:
   for f in ls.FIELDS.values():
    value=ls.integer(row.get('original_values',{}).get(f));value=value if value is not None else st.get('values',{}).get(f)
    if row.get(f) is None and value is not None:row[f]=value
   row.update({f:sn[f] for f in ('offense_snaps','snap_share','snap_denominator')})
 repaired=deepcopy(baseline);field_audit=[]
 for key,row in repaired.items():
  old=baseline[key];st=stats.get(key,{})
  for ch,fields in FIELDS.items():
   if ch=='rushing' and row['position'] not in ('RB','FB'):continue
   proof=proofs[row['game_id'],row['team'],ch]
   row[ch+'_context_valid']=proof['complete_reconciled']
   for f in fields:
    a=ls.integer(row.get('original_values',{}).get(f));b=st.get('values',{}).get(f);before=old.get(f);value=before;classification='EXPLICIT_NUMERIC_ZERO' if before==0 else 'VERIFIED_INDIVIDUAL_VALUE' if before is not None else 'PRODUCTION_UNAVAILABLE'
    source_conflict=a is not None and b is not None and a!=b
    if source_conflict:
     # Do not pick the preferred source after seeing outcomes; quarantine this field.
     value=None;classification='INDIVIDUAL_SOURCE_CONFLICT'
    elif row['appeared'] is True and value is None:
     if a is not None:value=a;classification='ADAPTER_REMOVED_VALID_INDIVIDUAL'
     elif b is not None:value=b;classification='BDL_EXPLICIT_FIELD_NOT_INTEGRATED'
     elif proof['complete_reconciled'] and row['gsis'] not in proof['players']:
      value=0;classification='POSITIVE_APPEARANCE_COMPLETE_RECONCILED_CATEGORY_ZERO'
    elif row['appeared'] is False:classification='VERIFIED_DNP';value=None
    elif row['appeared'] is None:classification='UNKNOWN_APPEARANCE';value=None
    row[f]=value
    field_audit.append({'season':row['season'],'week':row['week'],'game_id':row['game_id'],'gsis':row['gsis'],'team':row['team'],'channel':ch,'field':f,'baseline_value':before,'new_value':value,'retained_individual':a,'bdl_individual':b,'classification':classification,'appeared':row['appeared'],'context_valid':proof['complete_reconciled'],'category_proof':proof if classification.endswith('CATEGORY_ZERO') else None,'bdl_source':st.get('source'),'source_lineage':row.get('source_lineage',[])})
    if value is not None and row['appeared'] is not True:raise ValueError('VALUE_WITHOUT_OFFENSIVE_APPEARANCE')
  row['field_context_proofs']={ch:proofs[row['game_id'],row['team'],ch] for ch in FIELDS}
 history=defaultdict(list)
 for row in repaired.values():history[row['gsis'],row['team']].append(row)
 ledger=[];cross=Counter();prior_usage=[]
 for old in previous['rows']:
  h=sorted(history[old['gsis'],old['team']],key=lambda r:(r['season'],r['week']));known=[r for r in h if r['appeared'] is True];fail=[]
  if old['frozen_reason'] in ('UNRESOLVED_PRIOR_ROSTER_MEMBERSHIP','UNRESOLVED_PRIOR_FIXTURE_IDENTITY'):fail.append(old['frozen_reason'])
  if any(r['appeared'] is None for r in h):fail.append('UNRESOLVED_PRIOR_PARTICIPATION')
  if len(known)<2:fail.append('TOO_FEW_SAME_TEAM_APPEARANCES')
  if any(r.get(f) is None for r in known for f in FIELDS[old['channel']]):fail.append('UNKNOWN_RECENT_CHANNEL_VALUES')
  if any(ls.position(r['position'])!=ls.position(old['position']) for r in known):fail.append('DATED_POSITION_CHANGE')
  result=fail[0] if fail else 'ELIGIBLE';supported=sum(all(r.get(f) is not None for f in FIELDS[old['channel']]) for r in known)
  item={k:old[k] for k in ('season','week','game_id','gsis','team','channel','name','position')};item.update(original_reason=old['frozen_reason'],bdl_reason=old['source_repaired_reason'],new_reason=result,failed_prerequisites=fail,individual_eligible=not fail,original_issued=old['frozen_decision']=='ELIGIBLE',unknown_games=[r['game_id'] for r in h if r['appeared'] is None],context_masked_games=[r['game_id'] for r in h if not r.get(old['channel']+'_context_valid')],verified_appearances=len(known),verified_channel_games=supported,gamd_partial_history_eligible=supported>=2,original_prior_usage=old['before']['prior_usage_mean'],availability='UNKNOWN',offered_prop=None)
  ledger.append(item);cross[old['frozen_reason'],old['source_repaired_reason'],result]+=1
  if old['before']['prior_usage_mean'] is not None and old['before']['prior_usage_mean']>=(5 if old['channel']=='receiving' else 10):prior_usage.append(item)
 targeted={(x['gsis'],x['team'],x['channel']) for x in previous['rows'] if x['source_repaired_reason']=='UNKNOWN_RECENT_CHANNEL_VALUES'}
 field65=[x for x in field_audit if (x['gsis'],x['team'],x['channel']) in targeted and x['appeared'] is True and x['baseline_value'] is None]
 return {'rows':ledger,'field_audit':field_audit,'priority_65':field65,'history':list(repaired.values()),'category_proofs':[{'game_id':k[0],'team':k[1],'channel':k[2],**v} for k,v in proofs.items()],
 'summary':{'original_decisions':len(ledger),'original_issued':sum(x['original_issued'] for x in ledger),'previous_eligible':previous['summary']['source_repaired_eligible'],'new_eligible':sum(x['individual_eligible'] for x in ledger),'priority_cases':len(targeted),'priority_missing_fields':len(field65),'priority_repaired_fields':sum(x['new_value'] is not None for x in field65),'reasons':dict(Counter(x['new_reason'] for x in ledger)),'gamd_supported':sum(x['gamd_partial_history_eligible'] for x in ledger),'fixed_prior_usage_n':len(prior_usage),'fixed_prior_usage_eligible':sum(x['individual_eligible'] for x in prior_usage),'verified_offered_prop_population':None},
 'transition_matrix':[{'original':k[0],'after_bdl':k[1],'after_field_repair':k[2],'count':v} for k,v in sorted(cross.items())],'provider_calls':0,'original_forecasts_modified':0,'classification':'RETROSPECTIVE_SOURCE_REPAIR_NOT_FORECAST_REISSUE'}

def training_context(rows):
 """Conservative current historical projection; no fake complete team totals."""
 rows=deepcopy(rows);groups=defaultdict(list)
 for row in rows:groups[row['season'],row['week'],row['game_id'],row['team'],row['channel']].append(row)
 for group in groups.values():
  # A complete retained player census does not independently certify team totals.
  for row in group:row.update(team_context_valid=False,team_opportunities=None,team_yards=None)
 return rows

def forward_history(repaired):
 result=[]
 for row in repaired:
  for ch,fields in FIELDS.items():
   if ch=='rushing' and row['position'] not in ('RB','FB'):continue
   context=row['field_context_proofs'][ch];op,yard=(fields[0],fields[-1]);copy={k:row[k] for k in ('season','week','game_id','gsis','team','opponent','position')}
   copy.update(channel=ch,opportunity=row.get(op),yards=row.get(yard),appeared=row['appeared'],participation_state=row.get('state','UNKNOWN'),source_observed_at=row['observed_at'],provenance=row['source_lineage'],team_context_valid=context['complete_reconciled'],team_opportunities=context['team_totals'].get(op),team_yards=context['team_totals'].get(yard));result.append(copy)
 return result

def future_cohort(schedule,rosters,history,cutoff):
 from gamd.continuous_features import build_target_features
 from gamd.licensed_source import clock
 from source_adapter import schedule_timezone
 cut=clock(cutoff);fixtures=[]
 schedule=[dict(r,season=ls.integer(r['season']),week=ls.integer(r['week'])) for r in schedule]
 if any(r['season'] is None or r['week'] is None for r in schedule):raise ValueError('INVALID_SCHEDULE_PERIOD')
 for r in schedule:
  if ls.integer(r['season'])!=2026:continue
  r=dict(r,season=2026,week=int(r['week']))
  at=datetime.fromisoformat(r['gameday']+'T'+r['gametime']).replace(tzinfo=schedule_timezone()).astimezone(timezone.utc)
  if at>cut:fixtures.append({**r,'kickoff_at':at.isoformat()})
 fixtures.sort(key=lambda x:x['kickoff_at']);fixtures=fixtures[:1]
 latest={};ambiguous=set();excluded=[]
 for r in rosters:
  if ls.integer(r.get('season'))!=2026 or r.get('position') not in ('WR','TE','RB','FB') or not r.get('gsis_id'):continue
  if r.get('game_type',r.get('season_type'))!='REG' or clock(r['_observed_at'])>cut:continue
  gs=r['gsis_id'];rank=(int(r['week']),r['_observed_at'])
  if gs not in latest or rank>latest[gs][0]:latest[gs]=(rank,r);ambiguous.discard(gs)
  elif rank==latest[gs][0] and r['team']!=latest[gs][1]['team']:ambiguous.add(gs)
 targets=[]
 for f in fixtures:
  for gs,(rank,roster) in sorted(latest.items()):
   tm=ls.team(roster['team'])
   if tm not in (f['home_team'],f['away_team']):continue
   if gs in ambiguous:excluded.append({'gsis':gs,'reason':'AMBIGUOUS_CURRENT_TEAM','game_id':f['game_id']});continue
   age=(cut-clock(roster['_observed_at'])).total_seconds()/86400
   if age>14:excluded.append({'gsis':gs,'reason':'ROSTER_OBSERVATION_OLDER_THAN_14_DAYS','game_id':f['game_id']});continue
   for ch in ('receiving','rushing') if roster['position'] in ('RB','FB') else ('receiving',):
    target={'season':2026,'week':f['week'],'game_id':f['game_id'],'gsis':gs,'team':tm,'opponent':f['away_team'] if tm==f['home_team'] else f['home_team'],'position':roster['position'],'name':roster.get('full_name') or roster.get('player_name'),'channel':ch,'appeared':None,'opportunity':None,'yards':None,'participation_state':'PREGAME_AVAILABILITY_UNKNOWN','source_observed_at':roster['_observed_at'],'provenance':[roster['_source']],'kickoff_at':f['kickoff_at'],'availability':'UNKNOWN','roster_observation_age_days':age}
    # Preserve a calendar placeholder for an unfinished/missing prior game, instead of dropping it from workload denominators.
    existing={(x['game_id'],x['gsis'],x['team'],x['channel']) for x in history}
    for prior in schedule:
     if int(prior['week'])>=f['week'] or int(prior['week'])<rank[0] or tm not in (prior['home_team'],prior['away_team']):continue
     k=(prior['game_id'],gs,tm,ch)
     if k not in existing:history.append({**{k:v for k,v in target.items() if k not in ('kickoff_at','name','availability','roster_observation_age_days')},'week':prior['week'],'game_id':prior['game_id'],'opponent':prior['away_team'] if tm==prior['home_team'] else prior['home_team'],'participation_state':'UNVERIFIED_PRIOR_GAME_PLACEHOLDER','team_context_valid':False,'team_opportunities':None,'team_yards':None});existing.add(k)
    built=build_target_features(history,target,cutoff,allowed_history_seasons=(2026,))
    if built['verified_history_count']<2:excluded.append({'gsis':gs,'channel':ch,'game_id':f['game_id'],'reason':'FEWER_THAN_TWO_VERIFIED_PRIOR_CHANNEL_GAMES'});continue
    target.update(unknown_history_count=built['unknown_history_count'],verified_history_count=built['verified_history_count']);targets.append(target)
 return {'fixtures':fixtures,'targets':targets,'exclusions':excluded,'source_cutoff':cutoff,'population_scope':'INDEPENDENT_DATED_ROSTER_COHORT','future_appearance_selection':False,'availability':'UNKNOWN','history':history}

def execute(root,store,mr):
 from nfl_model import continuous_gamd_gate as g
 from gamd.source import read_partition
 root=Path(root)
 with g.authority(root,g.SOURCE,mr) as m:
  c,cr=g.consumer_for(root,g.SOURCE);catalog=g.read(root,c['catalogue'],True);oldcat=g.read(root,catalog['licensed_catalogue'],True);oldreceipt=g.read(root,catalog['licensed_source_receipt']);projection=g.read(root,oldcat['original_projection']);previous=g.read(root,oldreceipt['coverage_json']);out=root/m['output']
  if (out/'RECEIPT.json').exists():return dict(g.read(root,g.ref(root,out/'RECEIPT.json')),receipt=g.ref(root,out/'RECEIPT.json'),cohort=g.ref(root,out/'COHORT.json'),legacy_receipt=g.ref(root,out/'LEGACY_RECEIPT.json'),scheduled_receipt=g.ref(root,out/'SCHEDULED_RECEIPT.json'))
  cross=[json.loads(x) for x in g.checked(root,oldreceipt['crosswalk']).splitlines()];ci={(x['native_game_id'],x['native_player_id'],x['team']):x for x in cross};snaps={ls.key(x):x for x in (json.loads(z) for z in g.checked(root,oldreceipt['snap_artifact']).splitlines())};quarantine=[json.loads(x) for x in g.checked(root,oldreceipt['quarantine']).splitlines()];conflicts={tuple(x['identity']) for x in quarantine if x.get('code')=='COVERAGE_STAT_CONFLICT'};stats={};named=[];partition_receipts=[]
  for part in oldcat['partitions']:
   if part['season']!=2026:continue
   partition_receipts.append({k:part[k] for k in ('endpoint','season','rows','pages','state')})
   if not part['endpoint'].endswith(('/stats','/snap_counts')):continue
   for raw,src in ls.read_partition(oldcat['source_root'],part):
    ng=raw.get('game',{});pid=raw.get('player',{}).get('id')
    if ng.get('week',99)>=7:continue
    ck=(ng['id'],pid,ls.team(raw.get('team')))
    if part['endpoint'].endswith('/stats'):
     match=ci.get(ck)
     if match:stats[ls.key(match)]={'values':{dst:ls.integer(raw.get(sf)) for sf,dst in ls.FIELDS.items()},'source':src}
    elif pid in (476,826,762) or ls.name(str(raw.get('player',{}).get('first_name',''))+' '+str(raw.get('player',{}).get('last_name',''))) in ('saquonbarkley','devontasmith','dallasgoedert'):
     named.append({'native_player_id':pid,'native_game_id':ng['id'],'week':ng['week'],'team':ck[2],'home':ls.team(ng.get('home_team')),'away':ls.team(ng.get('visitor_team')),'date':ng.get('date'),'offense_snaps':raw.get('offense_snaps'),'special_teams_snaps':raw.get('special_teams_snaps'),'source':src})
  report=repair(projection,previous,snaps,stats,conflicts);report.update(original_projection=oldcat['original_projection'],original_decisions=oldcat['original_decisions'],previous_coverage=oldreceipt['coverage_json'],research_cutoff=m['as_of'],source_manifest=mr)
  historical=[json.loads(x) for x in g.checked(root,oldreceipt['projection']).splitlines()];historical=training_context(historical)
  legacy=g.checked(root,oldcat['historical_projection']);schedule=[];rosters=[]
  for e in oldcat['schedules']:
   if e['season']==2026:schedule.extend(read_partition(e['source_root'],dict(e,through_week=18),years=(2026,)))
  for e in oldcat['identity_rosters']:
   raw=ls.verified_bytes(e['source_root'],{'path':e['path'],'sha256':e['sha256']})
   for line in gzip.decompress(raw).splitlines():
    row=json.loads(line)
    if ls.integer(row.get('season'))==2026:rosters.append({**row,'_observed_at':e['observed_at'],'_source':{k:e[k] for k in ('path','sha256','observed_at')}})
  cohort=future_cohort(schedule,rosters,forward_history(report['history']),m['as_of'])
  from gamd.continuous_incremental import project as incremental_project
  incremental=incremental_project(root,store,m,previous,snaps,stats,conflicts)
  if incremental:cohort=incremental['cohort']
  hist=cohort.pop('history')
  def lines(name,rows):
   p=out/name;p.parent.mkdir(parents=True,exist_ok=True)
   raw=b''.join((json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode() for x in rows)
   if p.exists():g.need(p.read_bytes()==raw,'EXISTING_SOURCE_CHANGED')
   else:p.write_bytes(raw)
   return g.ref(root,p)
  scheduled=lines('SCHEDULED_TRAINING.jsonl',historical+training_context(incremental['verified_completed'] if incremental else []))
  training=lines('TRAINING.jsonl',historical);history=lines('FORWARD_HISTORY.jsonl',hist);legacy_ref=lines('LEGACY_TRAINING.jsonl',[json.loads(x) for x in legacy.splitlines()]);g.write_once(out/'COVERAGE_TRANSITIONS.json',report)
  expected=[(476,'Saquon Barkley',5),(476,'Saquon Barkley',6),(826,'DeVonta Smith',4),(826,'DeVonta Smith',5),(762,'Dallas Goedert',3),(762,'Dallas Goedert',4)]
  missing=[]
  for pid,name,week in expected:
   peers=[x for x in named if x['week']==week and x['team']=='PHI'];present=any(x['native_player_id']==pid for x in peers)
   missing.append({'player':name,'native_player_id':pid,'season':2026,'regular_season_type':2,'week':week,'native_game_ids':sorted({x['native_game_id'] for x in peers}),'game_dates':sorted({x['date'] for x in peers if x['date']}),'game_directions':[{'home':x['home'],'away':x['away']} for x in peers[:1]],'state':'PRESENT_IN_RAW' if present else 'ABSENT_FROM_EXHAUSTED_RETAINED_CAPTURE','normalised_drop':False if not present else None,'offense_absence_semantics':'UNKNOWN; missing is not zero or DNP','repairable_action':'Target native game and player in an independently authorised collector; no new request made.'})
  gaps={'exact_gaps':missing,'partition_receipts':partition_receipts,'named_raw_rows':named,'source_note':'Cursor-exhausted retained response, not a guarantee of upstream completeness. No new request was made. Missing snap rows cannot imply DNP.','next_action':'A new targeted BDL game/player request needs an admitted no-charge acquisition capability; this task adds no BDL collector or paid access.','provider_calls':0};g.write_once(out/'NAMED_SNAP_GAPS.json',gaps)
  receipt={'status':'COMPLETE','policy':c['policy'],'consumer':cr,'source_manifest':mr,'training_projection':training,'scheduled_training':scheduled,'verified_complete_games':incremental['completed_games'] if incremental else [],'manager_imports':m.get('manager_imports',[]),'legacy_training':legacy_ref,'history':history,'coverage':g.ref(root,out/'COVERAGE_TRANSITIONS.json'),'named_gaps':g.ref(root,out/'NAMED_SNAP_GAPS.json'),'summary':report['summary'],'source_cutoff':m['as_of'],'provider_calls':0,'original_forecasts_modified':0,'training_rows':len(historical),'legacy_training_rows':len(legacy.splitlines()),'scheduled_training_rows':len(historical)+len(incremental['verified_completed'] if incremental else []),'history_rows':len(hist)}
  g.write_once(out/'RECEIPT.json',receipt);rr=g.ref(root,out/'RECEIPT.json');cohort.update(policy=c['policy'],source_receipt=rr,history=history);g.write_once(out/'COHORT.json',cohort)
  # Dedicated legacy recovery receipt, with exact untouched original rows and no new labels.
  g.write_once(out/'LEGACY_RECEIPT.json',dict(receipt,training_projection=legacy_ref,process='LINKED_PREFIT_ENGINEERING_RECOVERY'))
  g.write_once(out/'SCHEDULED_RECEIPT.json',dict(receipt,training_projection=scheduled,process='SCHEDULED_REFIT'))
  return dict(receipt,receipt=rr,cohort=g.ref(root,out/'COHORT.json'),legacy_receipt=g.ref(root,out/'LEGACY_RECEIPT.json'),scheduled_receipt=g.ref(root,out/'SCHEDULED_RECEIPT.json'))
