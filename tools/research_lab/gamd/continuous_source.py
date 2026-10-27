"""Field-level repairs and an independent dated future menu, in a new private lane."""
from collections import defaultdict,Counter
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from pathlib import Path
import csv,gzip,io,json
from gamd import licensed_source as ls
from gamd.continuous_context import REVISION,sources,latest_observation
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
   lineage=sources([r.get('source_lineage',[]) for r in rows],projection.get('source_lineage',[]))
   result[(*key,ch)]={'complete_reconciled':valid,'players':explicit,'team_totals':totals,'basis':'ORIGINAL_PBP_RECONCILED_CATEGORY_AND_EXPLICIT_PLAYER_SUM','category_scope':'WR_TE_RB_FB_ONLY' if ch=='receiving' else 'RB_FB_ONLY_WITH_LATERALS','source_lineage':lineage,'observed_at':latest_observation(lineage),'independently_reconciled_totals':valid}
 return result

def repair(projection,previous,snaps,stats,conflicts):
 """All original decisions, all failed prerequisites; no replacement of old bytes."""
 original={ls.key(r):r for r in projection['data']['records']};baseline=deepcopy(original);proofs=category_proofs(projection)
 verified={(g['game_id'],r['gsis'],r['team']) for r in previous['rows'] for g in r['after']['verified_games']}
 for key,row in baseline.items():
  row['predecessor']={k:deepcopy(row.get(k)) for k in ('state','appeared','observed_at','source_lineage','source_basis','value_states','offense_snaps','snap_share','snap_denominator')}
  row['canonical_revision']=REVISION
  sn=snaps.get(key);st=stats.get(key,{})
  participation_conflict=row.get('state')=='PARTICIPATION_CONFLICT' or (row.get('appeared') is False and sn and (ls.integer(sn.get('offense_snaps')) or 0)>0)
  if participation_conflict:row.update(appeared=None,state='PARTICIPATION_CONFLICT')
  elif (row['game_id'],row['gsis'],row['team']) in verified:row['appeared']=True
  if sn and ls.integer(sn.get('offense_snaps')) is not None and sn['offense_snaps']>=0 and key not in conflicts:
   row['offense_snaps']=sn['offense_snaps'];row['source_lineage']=sources(row.get('source_lineage',[]),sn.get('source'))
  if sn and sn['offense_snaps']>0 and key not in conflicts and not participation_conflict:
   row['appeared']=True
   for f in ls.FIELDS.values():
    value=ls.integer(row.get('original_values',{}).get(f));value=value if value is not None else st.get('values',{}).get(f)
    if row.get(f) is None and value is not None:row[f]=value
   row['offense_snaps']=sn['offense_snaps']
   row['source_lineage']=sources(row.get('source_lineage',[]),sn.get('source'),sn.get('game',{}).get('source'))
   row['source_basis']=sorted(set(row.get('source_basis',[])+['BDL_POSITIVE_OFFENSIVE_SNAPS']))
  # Old derived shares were not based on an independent native-player census.
  row.update(snap_share=None,snap_denominator=None,snap_completeness='NO_INDEPENDENT_CENSUS_WITNESS')
  if row['appeared'] is True:row['state']='WITNESSED_APPEARANCE'
  elif row['appeared'] is False:row['state']='DNP'
  else:row['state']=row.get('state') if row.get('state') not in ('WITNESSED_APPEARANCE','DNP') else 'UNKNOWN_PARTICIPATION'
 repaired=deepcopy(baseline);field_audit=[]
 for key,row in repaired.items():
  old=baseline[key];st=stats.get(key,{});row['field_lineage']={}
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
    lineage=sources(row.get('source_lineage',[]),st.get('source') if b is not None else None,proof.get('source_lineage') if classification.endswith('CATEGORY_ZERO') else None)
    row['field_lineage'][f]={'classification':classification,'sources':lineage,'observed_at':latest_observation(lineage,row.get('observed_at'))}
    if value is not None or source_conflict:row['source_lineage']=sources(row.get('source_lineage',[]),lineage)
    row.setdefault('value_states',{})[f]='UNKNOWN' if value is None else 'ZERO' if value==0 else 'OBSERVED'
    field_audit.append({'season':row['season'],'week':row['week'],'game_id':row['game_id'],'gsis':row['gsis'],'team':row['team'],'channel':ch,'field':f,'baseline_value':before,'new_value':value,'retained_individual':a,'bdl_individual':b,'classification':classification,'appeared':row['appeared'],'context_valid':proof['complete_reconciled'],'category_proof':proof if classification.endswith('CATEGORY_ZERO') else None,'bdl_source':st.get('source'),'source_lineage':row.get('source_lineage',[])})
    if value is not None and row['appeared'] is not True:raise ValueError('VALUE_WITHOUT_OFFENSIVE_APPEARANCE')
  row['field_context_proofs']={ch:proofs[row['game_id'],row['team'],ch] for ch in FIELDS}
  row['source_lineage']=sources(row.get('source_lineage',[]),[p.get('source_lineage',[]) for p in row['field_context_proofs'].values() if p['complete_reconciled']])
  row['observed_at']=latest_observation(row['source_lineage'],row.get('observed_at'))
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

def canonical_calendar(rows,schedule,rosters):
 """Retain missing cells between known membership/history endpoints, including transfers."""
 rows=deepcopy(rows);groups=defaultdict(list);dated=defaultdict(set);roster_refs=defaultdict(list)
 for r in rows:groups[r['season'],r['gsis'],r['team'],r['channel']].append(r)
 for r in rosters:
  key=(r['season'],r['week'],r.get('gsis_id'))
  dated[key].add(ls.team(r.get('team')));roster_refs[key].append(r.get('_source',{}))
 existing={(r['game_id'],r['gsis'],r['team'],r['channel']) for r in rows}
 for (year,gs,tm,ch),known in groups.items():
  anchors=[r['week'] for r in known]+[r['week'] for r in rosters if r['season']==year and r.get('gsis_id')==gs and ls.team(r.get('team'))==tm]
  first,last=min(anchors),max(anchors)
  template=known[0]
  for f in schedule:
   if f['season']!=year or not first<=f['week']<=last or tm not in (f['home_team'],f['away_team']):continue
   key=f['game_id'],gs,tm,ch
   if key in existing:continue
   membership=dated.get((year,f['week'],gs),set())
   if membership and tm not in membership:continue
   lineage=sources(f.get('_source'),roster_refs.get((year,f['week'],gs),[]),template.get('provenance',[]))
   row={k:template[k] for k in ('gsis','team','channel','position')}
   row.update(season=year,week=f['week'],game_id=f['game_id'],opponent=f['away_team'] if tm==f['home_team'] else f['home_team'],appeared=None,opportunity=None,yards=None,
    participation_state='UNKNOWN_PARTICIPATION' if membership=={tm} else 'UNKNOWN_MEMBERSHIP',source_observed_at=latest_observation(lineage),provenance=lineage,canonical_revision=REVISION,calendar_basis='SCHEDULE_BETWEEN_KNOWN_TEAM_HISTORY_ENDPOINTS')
   rows.append(row);existing.add(key)
 return sorted(rows,key=lambda r:(r['season'],r['week'],r['game_id'],r['gsis'],r['team'],r['channel']))

def training_context(rows,contexts=()):
 """Join an independent team table; nullable individual fields stay untouched."""
 rows=deepcopy(rows);index={(r['game_id'],r['team'],r['channel']):r for r in contexts}
 for row in rows:
  proof=index.get((row['game_id'],row['team'],row['channel'])) or row.get('context_proof',{})
  fields=FIELDS[row['channel']];valid=proof.get('complete_reconciled') is True and proof.get('independently_reconciled_totals') is True
  if row['channel']=='receiving' and proof.get('category_scope')=='WR_TE_RB_FB_ONLY':valid=False
  totals=proof.get('team_totals',{})
  valid=valid and all(ls.integer(totals.get(f)) is not None for f in fields) and totals[fields[0]]>=0
  compact={k:v for k,v in proof.items() if k!='players'}
  if proof:compact['context_record_sha256']=proof.get('context_record_sha256') or ls.digest(ls.dump(proof).encode())
  row.update(team_context_valid=bool(valid),team_opportunities=totals.get(fields[0]) if valid else None,team_yards=totals.get(fields[-1]) if valid else None,context_proof=compact,canonical_revision=REVISION)
  row.setdefault('predecessor',{'participation_state':row.get('participation_state'),'source_observed_at':row.get('source_observed_at'),'provenance':deepcopy(row.get('provenance',[]))})
  row['provenance']=sources(row.get('provenance',[]),row.get('bdl_evidence',[]),proof.get('source_lineage',[]) if valid else [])
  row['source_observed_at']=latest_observation(row['provenance'],row.get('source_observed_at'))
  if row.get('appeared') is True and row.get('participation_state') in ('UNKNOWN_PARTICIPATION','UNSUPPORTED_PRIOR_PARTICIPATION','UNKNOWN'):
   row['participation_state']='WITNESSED_APPEARANCE'
  row.update(snap_share=None,snap_denominator=None,snap_completeness='NO_INDEPENDENT_CENSUS_WITNESS')
 return rows

def forward_history(repaired):
 result=[]
 for row in repaired:
  for ch,fields in FIELDS.items():
   if ch=='rushing' and row['position'] not in ('RB','FB'):continue
   context=row['field_context_proofs'][ch];op,yard=(fields[0],fields[-1]);copy={k:row[k] for k in ('season','week','game_id','gsis','team','opponent','position')}
   copy.update(channel=ch,opportunity=row.get(op),yards=row.get(yard),appeared=row['appeared'],participation_state=row.get('state','UNKNOWN'),source_observed_at=row['observed_at'],provenance=row['source_lineage'],team_context_valid=context['complete_reconciled'],team_opportunities=context['team_totals'].get(op),team_yards=context['team_totals'].get(yard),context_proof=context,field_lineage=row.get('field_lineage',{}),predecessor=row.get('predecessor',{}),canonical_revision=REVISION);result.append(copy)
 return result

def future_cohort(schedule,rosters,history,cutoff,*,horizon_days=7,contexts=()):
 from gamd.continuous_features import build_target_features
 from gamd.licensed_source import clock
 from source_adapter import schedule_timezone
 from gamd.continuous_features import SCHEMA,feature_contract_sha256
 cut=clock(cutoff);fixtures=[];history=deepcopy(history)
 # Retain exact independently admitted proofs in the fallback lane too.
 context_index={(r['game_id'],r['team'],r['channel']):dict(r['context_proof'],game_id=r['game_id'],team=r['team'],channel=r['channel']) for r in history if r.get('team_context_valid') is True and r.get('context_proof')}
 context_index.update({(r['game_id'],r['team'],r['channel']):r for r in contexts})
 if not isinstance(horizon_days,int) or not 1<=horizon_days<=14:raise ValueError('INVALID_FIXTURE_HORIZON')
 schedule=[dict(r,season=ls.integer(r['season']),week=ls.integer(r['week'])) for r in schedule]
 if any(r['season'] is None or r['week'] is None for r in schedule):raise ValueError('INVALID_SCHEDULE_PERIOD')
 fixture_ids={}
 for r in schedule:
  if ls.integer(r['season'])!=2026:continue
  r=dict(r,season=2026,week=int(r['week']))
  at=datetime.fromisoformat(r['gameday']+'T'+r['gametime']).replace(tzinfo=schedule_timezone()).astimezone(timezone.utc)
  if r.get('game_type',r.get('season_type','REG'))!='REG':continue
  source_clock=r.get('_source',{}).get('observed_at')
  if source_clock and clock(source_clock)>cut:continue
  value={**r,'kickoff_at':at.isoformat()}
  if r['game_id'] in fixture_ids and fixture_ids[r['game_id']]!=value:raise ValueError('CONFLICTING_FIXTURE_IDENTITY')
  fixture_ids[r['game_id']]=value
  if cut<at<=cut+timedelta(days=horizon_days):fixtures.append(value)
 fixtures=sorted({f['game_id']:f for f in fixtures}.values(),key=lambda x:(x['kickoff_at'],x['game_id']))
 retained=[];dated=defaultdict(list);excluded=[]
 for r in rosters:
  if ls.integer(r.get('season'))!=2026 or not r.get('gsis_id'):continue
  observed=r.get('_observed_at') or r.get('_source',{}).get('observed_at')
  if r.get('game_type',r.get('season_type'))!='REG' or not observed or clock(observed)>cut:continue
  w=ls.integer(r.get('week'))
  if w is None:continue
  r=dict(r,week=w,team=ls.team(r['team']),_observed_at=observed)
  retained.append(r);dated[r['week'],r['gsis_id']].append(r)
 targets=[]
 for f in fixtures:
  latest={};ambiguous=set()
  for r in retained:
   if r['week']>f['week']:continue
   gs=r['gsis_id'];rank=(r['week'],clock(r['_observed_at']))
   if gs not in latest or rank>latest[gs][0]:latest[gs]=(rank,r);ambiguous.discard(gs)
   elif rank==latest[gs][0] and (r['team'],r['position'])!=(latest[gs][1]['team'],latest[gs][1]['position']):ambiguous.add(gs)
  for gs,(rank,roster) in sorted(latest.items()):
   tm=ls.team(roster['team'])
   if tm not in (f['home_team'],f['away_team']):continue
   if gs in ambiguous:excluded.append({'gsis':gs,'reason':'AMBIGUOUS_CURRENT_TEAM','game_id':f['game_id']});continue
   if roster.get('position') not in ('WR','TE','RB','FB'):
    excluded.append({'gsis':gs,'reason':'LATEST_DATED_POSITION_OUTSIDE_PLAYER_YARDS_SCOPE','game_id':f['game_id'],'position':roster.get('position')});continue
   age=(cut-clock(roster['_observed_at'])).total_seconds()/86400
   interval=roster.get('validity_verified') is True and roster.get('valid_from') and roster.get('valid_to') and clock(roster['valid_from'])<=clock(f['kickoff_at'])<clock(roster['valid_to'])
   strict=roster['week']==f['week'] or bool(interval)
   membership='VERIFIED_TARGET_WEEK_MEMBERSHIP' if roster['week']==f['week'] else 'VERIFIED_EFFECTIVE_INTERVAL' if interval else 'PROVISIONAL_PRIOR_WEEK_MEMBERSHIP'
   for ch in ('receiving','rushing') if roster['position'] in ('RB','FB') else ('receiving',):
    lineage=sources(roster.get('_source'),f.get('_source'))
    target={'season':2026,'week':f['week'],'game_id':f['game_id'],'gsis':gs,'team':tm,'opponent':f['away_team'] if tm==f['home_team'] else f['home_team'],'position':roster['position'],'name':roster.get('full_name') or roster.get('player_name'),'channel':ch,'appeared':None,'opportunity':None,'yards':None,'participation_state':'PREGAME_AVAILABILITY_UNKNOWN','source_observed_at':latest_observation(lineage,roster['_observed_at']),'provenance':lineage,'kickoff_at':f['kickoff_at'],'availability':'UNKNOWN','roster_observation_age_days':age,'membership_state':membership,'membership_lane':'STRICT' if strict else 'PROVISIONAL','membership_basis':{'effective_week':roster['week'],'target_week':f['week'],'observation_is_not_effective_time':True},'canonical_revision':REVISION}
    # Preserve a calendar placeholder for an unfinished/missing prior game, instead of dropping it from workload denominators.
    existing={(x['game_id'],x['gsis'],x['team'],x['channel']) for x in history}
    anchors=[r['week'] for r in retained if r['gsis_id']==gs and r['team']==tm and r['week']<f['week']]+[r['week'] for r in history if r['season']==2026 and r['gsis']==gs and r['team']==tm and r['week']<f['week']]
    first=min(anchors) if anchors else f['week']
    for prior in schedule:
     if prior['season']!=2026 or int(prior['week'])>=f['week'] or int(prior['week'])<first or tm not in (prior['home_team'],prior['away_team']):continue
     members=dated.get((int(prior['week']),gs),[]);member_teams={r['team'] for r in members}
     if member_teams and tm not in member_teams:continue
     state='UNKNOWN_PARTICIPATION' if member_teams=={tm} else 'UNKNOWN_MEMBERSHIP'
     k=(prior['game_id'],gs,tm,ch)
     if k not in existing:
      placeholder={**{k:v for k,v in target.items() if k not in ('kickoff_at','name','availability','roster_observation_age_days','membership_basis','membership_lane')},'week':prior['week'],'game_id':prior['game_id'],'opponent':prior['away_team'] if tm==prior['home_team'] else prior['home_team'],'participation_state':state,'membership_state':'EXACT_DATED_MEMBERSHIP' if member_teams=={tm} else 'UNKNOWN_MEMBERSHIP','team_context_valid':False,'team_opportunities':None,'team_yards':None}
      # A player's unknown history does not invalidate an independently known
      # team total. Join the same authenticated proof after calendar insertion;
      # individual membership, participation and labels remain unknown.
      proof=context_index.get((prior['game_id'],tm,ch))
      history.extend(training_context([placeholder],[proof] if proof else []));existing.add(k)
    prior_history=[r for r in history if (r['season'],r['week'])<(2026,f['week'])]
    built=build_target_features(prior_history,target,cutoff,allowed_history_seasons=(2026,))
    if built['verified_history_count']<2:excluded.append({'gsis':gs,'channel':ch,'game_id':f['game_id'],'reason':'FEWER_THAN_TWO_VERIFIED_PRIOR_CHANNEL_GAMES','membership_lane':target['membership_lane'],'membership_state':membership});continue
    target.update(unknown_history_count=built['unknown_history_count'],verified_history_count=built['verified_history_count']);targets.append(target)
 boundaries=[clock(f['kickoff_at']) if clock(f['kickoff_at'])<=cut+timedelta(days=horizon_days) else clock(f['kickoff_at'])-timedelta(days=horizon_days) for f in fixture_ids.values() if clock(f['kickoff_at'])>cut]
 return {'fixtures':fixtures,'targets':targets,'exclusions':excluded,'source_cutoff':cutoff,'valid_until':min(boundaries).isoformat() if boundaries else None,'population_scope':'INDEPENDENT_DATED_ROSTER_COHORT','population_definition':'STRICT_TARGET_WEEK_AND_EXPLICIT_PROVISIONAL_MEMBERSHIP','future_appearance_selection':False,'availability':'UNKNOWN','history':history,'horizon_days':horizon_days,'strict_targets':sum(r['membership_lane']=='STRICT' for r in targets),'provisional_targets':sum(r['membership_lane']=='PROVISIONAL' for r in targets),'canonical_revision':REVISION,'feature_schema':SCHEMA,'feature_contract_sha256':feature_contract_sha256()}

def execute(root,store,mr):
 from nfl_model import continuous_gamd_gate as g
 from gamd.source import read_partition
 root=Path(root)
 with g.authority(root,g.SOURCE,mr) as m:
  c,cr=g.consumer_for(root,g.SOURCE);catalog=g.read(root,c['catalogue'],True);oldcat=g.read(root,catalog['licensed_catalogue'],True);oldreceipt=g.read(root,catalog['licensed_source_receipt']);projection=g.read(root,oldcat['original_projection']);previous=g.read(root,oldreceipt['coverage_json']);out=root/m['output']
  if (out/'RECEIPT.json').exists():return dict(g.read(root,g.ref(root,out/'RECEIPT.json')),receipt=g.ref(root,out/'RECEIPT.json'),cohort=g.ref(root,out/'COHORT.json'),legacy_receipt=g.ref(root,out/'LEGACY_RECEIPT.json'),scheduled_receipt=g.ref(root,out/'SCHEDULED_RECEIPT.json'))
  cross=[json.loads(x) for x in g.checked(root,oldreceipt['crosswalk']).splitlines()];ci={(x['native_game_id'],x['native_player_id'],x['team']):x for x in cross};snaps={ls.key(x):x for x in (json.loads(z) for z in g.checked(root,oldreceipt['snap_artifact']).splitlines())};quarantine=[json.loads(x) for x in g.checked(root,oldreceipt['quarantine']).splitlines()];conflicts={tuple(x['identity']) for x in quarantine if x.get('code')=='COVERAGE_STAT_CONFLICT'};stats={};named=[];partition_receipts=[];native_games={};snap_samples=[]
  for part in oldcat['partitions']:
   if part['season']!=2026:continue
   partition_receipts.append({k:part[k] for k in ('endpoint','season','rows','pages','state')})
   for raw,src in ls.read_partition(oldcat['source_root'],part):
    ng=raw if part['endpoint'].endswith('/games') else raw.get('game',{});pid=raw.get('player',{}).get('id')
    if ng.get('week',99)>=7:continue
    native_games[ng['id']]={'native_game_id':ng['id'],'week':ng['week'],'home':ls.team(ng.get('home_team')),'away':ls.team(ng.get('visitor_team')),'date':ng.get('date'),'source':src}
    if part['endpoint'].endswith('/games'):continue
    ck=(ng['id'],pid,ls.team(raw.get('team')))
    if part['endpoint'].endswith('/stats'):
     match=ci.get(ck)
     if match:stats[ls.key(match)]={'values':{dst:ls.integer(raw.get(sf)) for sf,dst in ls.FIELDS.items()},'source':src}
    elif pid in (476,826,762) or ls.name(str(raw.get('player',{}).get('first_name',''))+' '+str(raw.get('player',{}).get('last_name',''))) in ('saquonbarkley','devontasmith','dallasgoedert'):
     named.append({'native_player_id':pid,'native_game_id':ng['id'],'week':ng['week'],'team':ck[2],'home':ls.team(ng.get('home_team')),'away':ls.team(ng.get('visitor_team')),'date':ng.get('date'),'offense_snaps':raw.get('offense_snaps'),'special_teams_snaps':raw.get('special_teams_snaps'),'source':src})
     snap_samples.append({'row':raw,'source':src})
  report=repair(projection,previous,snaps,stats,conflicts);report.update(original_projection=oldcat['original_projection'],original_decisions=oldcat['original_decisions'],previous_coverage=oldreceipt['coverage_json'],research_cutoff=m['as_of'],source_manifest=mr)
  from gamd.continuous_context import load as load_context
  from gamd.continuous_features import SCHEMA,feature_contract_sha256
  context=load_context(root,catalog,m['as_of'])
  original_historical=[json.loads(x) for x in g.checked(root,oldreceipt['projection']).splitlines()]
  historical=training_context(canonical_calendar(original_historical,context['schedule'],context['rosters']),context['contexts'])
  legacy=g.checked(root,oldcat['historical_projection']);schedule=[];rosters=[]
  for e in oldcat['schedules']:
   if e['season']==2026:schedule.extend(read_partition(e['source_root'],dict(e,through_week=18),years=(2026,)))
  for e in oldcat['identity_rosters']:
   raw=ls.verified_bytes(e['source_root'],{'path':e['path'],'sha256':e['sha256']})
   for line in gzip.decompress(raw).splitlines():
    row=json.loads(line)
    if ls.integer(row.get('season'))==2026:rosters.append({**row,'_observed_at':e['observed_at'],'_source':{k:e[k] for k in ('path','sha256','observed_at')}})
  cohort=future_cohort(schedule,rosters,training_context(forward_history(report['history'])),m['as_of'])
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
   games=[x for x in native_games.values() if x['week']==week and 'PHI' in (x['home'],x['away'])];ids={x['native_game_id'] for x in games};peers=[x for x in named if x['native_game_id'] in ids and x['team']=='PHI'];present=any(x['native_player_id']==pid for x in peers)
   missing.append({'player':name,'native_player_id':pid,'season':2026,'regular_season_type':2,'week':week,'native_game_ids':sorted(ids),'game_dates':sorted({x['date'] for x in games if x['date']}),'game_directions':[{'home':x['home'],'away':x['away']} for x in games],'state':'PRESENT_IN_RAW' if present else 'ABSENT_FROM_SCANNED_RETAINED_PARTITIONS' if ids else 'NATIVE_GAME_ID_UNRESOLVED','normalised_drop':False if not present and ids else None,'offense_absence_semantics':'UNKNOWN; missing is not zero or DNP','repairable_action':'Review the exact retained request versions; only an existing admitted no-charge Data Manager acquisition may refresh the unresolved game/player.'})
  gaps={'exact_gaps':missing,'partition_receipts':partition_receipts,'named_raw_rows':named,'native_games':list(native_games.values()),'source_note':'All pages of the licensed catalogue partitions were searched by exact native game/player identity. This does not claim unlisted archive revisions or upstream completeness. No new request was made.','next_action':'Use the existing Data Manager only if it exposes the necessary admitted no-charge targeted request. No new collector or paid access is created.','provider_calls':0};g.write_once(out/'NAMED_SNAP_GAPS.json',gaps)
  contexts=context['contexts']+(incremental['contexts'] if incremental else [])
  context_ref=lines('TEAM_CONTEXT.jsonl',contexts)
  evidence_ref=lines('MINIMAL_SOURCE_EVIDENCE.jsonl',context['evidence_samples']+(incremental['context_evidence'] if incremental else [])+[{'kind':'licensed_named_snap','sample':x} for x in snap_samples])
  all_rows=historical+hist;ids=[(r['season'],r['week'],r['game_id'],r['gsis'],r['team'],r['channel']) for r in all_rows]
  state_conflicts=sum(r.get('appeared') is True and r.get('participation_state') in ('UNKNOWN','UNKNOWN_PARTICIPATION','UNSUPPORTED_PRIOR_PARTICIPATION') for r in all_rows)
  late=sum(bool(r.get('source_observed_at')) and ls.clock(r['source_observed_at'])>ls.clock(m['as_of']) for r in all_rows)
  health={'canonical_revision':REVISION,'historical_rows':len(historical),'forward_history_rows':len(hist),'calendar_rows_added':len(historical)-len(original_historical),'duplicate_identity_count':len(ids)-len(set(ids)),'state_conflict_count':state_conflicts,'after_cutoff_source_count':late,'context':dict(Counter(r['channel']+('_supported' if r.get('team_context_valid') else '_unknown') for r in all_rows)),'snap_positive_rows':sum((ls.integer(r.get('offense_snaps')) or 0)>0 for r in report['history']),'snap_denominator_witness_count':0,'snap_share_status':'UNAVAILABLE_WITHOUT_INDEPENDENT_COMPLETE_OFFENSIVE_PLAYER_CENSUS','negative_yard_rows':sum(r.get('yards') is not None and r['yards']<0 for r in all_rows),'zero_yard_rows':sum(r.get('yards')==0 for r in all_rows),'verified_dnp_rows':sum(r.get('appeared') is False for r in all_rows),'unknown_membership_rows':sum(r.get('participation_state')=='UNKNOWN_MEMBERSHIP' for r in all_rows),'strict_future_targets':cohort['strict_targets'],'provisional_future_targets':cohort['provisional_targets'],'feature_schema':SCHEMA,'feature_contract_sha256':feature_contract_sha256(),'vintage':'REVISED_HISTORY; no retrospective pregame availability claim'}
  g.need(not state_conflicts and not late and health['duplicate_identity_count']==0,'CANONICAL_IDENTITY_STATE_OR_CLOCK_CONFLICT')
  health['status']='VERIFIED_WITH_SCOPED_GAPS'
  g.write_once(out/'DATASET_HEALTH.json',{'summary':health,'coverage':report['summary'],'transitions':report['transition_matrix'],'context_summary':context['summary'],'context_table':context_ref,'minimal_source_evidence':evidence_ref,'sources':catalog.get('historical_inputs',[]),'original_historical_projection':oldreceipt['projection'],'original_forecasts_modified':0,'provider_calls':0})
  receipt={'status':'COMPLETE','policy':c['policy'],'consumer':cr,'source_manifest':mr,'training_projection':training,'scheduled_training':scheduled,'verified_complete_games':incremental['completed_games'] if incremental else [],'manager_imports':m.get('manager_imports',[]),'legacy_training':legacy_ref,'history':history,'coverage':g.ref(root,out/'COVERAGE_TRANSITIONS.json'),'named_gaps':g.ref(root,out/'NAMED_SNAP_GAPS.json'),'summary':report['summary'],'source_cutoff':m['as_of'],'provider_calls':0,'original_forecasts_modified':0,'training_rows':len(historical),'legacy_training_rows':len(legacy.splitlines()),'scheduled_training_rows':len(historical)+len(incremental['verified_completed'] if incremental else []),'history_rows':len(hist)}
  receipt.update(canonical_revision=REVISION,feature_schema=SCHEMA,feature_contract_sha256=feature_contract_sha256(),dataset_health=health,dataset_health_ref=g.ref(root,out/'DATASET_HEALTH.json'),context_table=context_ref,before_after_coverage=report['summary'],valid_until=cohort.get('valid_until'),cohort_valid_until=cohort.get('valid_until'))
  g.write_once(out/'RECEIPT.json',receipt);rr=g.ref(root,out/'RECEIPT.json');cohort.update(policy=c['policy'],source_receipt=rr,history=history);g.write_once(out/'COHORT.json',cohort)
  # Dedicated legacy recovery receipt, with exact untouched original rows and no new labels.
  g.write_once(out/'LEGACY_RECEIPT.json',dict(receipt,training_projection=legacy_ref,process='LINKED_PREFIT_ENGINEERING_RECOVERY'))
  g.write_once(out/'SCHEDULED_RECEIPT.json',dict(receipt,training_projection=scheduled,process='SCHEDULED_REFIT'))
  return dict(receipt,receipt=rr,cohort=g.ref(root,out/'COHORT.json'),legacy_receipt=g.ref(root,out/'LEGACY_RECEIPT.json'),scheduled_receipt=g.ref(root,out/'SCHEDULED_RECEIPT.json'))
