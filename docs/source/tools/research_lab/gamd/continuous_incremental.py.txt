"""New already-admitted manager imports trigger new immutable source versions."""
import json
from core import digest

def imports(store):
 if store is None:return []
 latest={}
 for r in store.rows("SELECT * FROM records WHERE kind='source_import' ORDER BY recorded_at"):
  body=json.loads(r['body'])
  if digest([r['kind'],r['id'],r['revision'],body,r['previous_hash'],r['recorded_at']])!=r['hash']:raise ValueError('SOURCE_IMPORT_RECORD_HASH')
  ds=body.get('dataset')
  if ds not in ('schedule','rosters','player_stats','pbp') or body.get('validation')!='ACTUAL_PAYLOAD_READ_AND_HASH_VERIFIED':continue
  if body.get('through_period',[None])[0]!=2026:continue
  latest[ds]={'dataset':ds,'artifact':body['artifact'],'manifest':body['manifest'],'record_hash':r['hash'],'observed_at':body['source_clock']}
 return [latest[k] for k in sorted(latest)]

def project(root,store,m,previous,snaps,stats,conflicts):
 from nfl_model import continuous_gamd_gate as g
 from gamd.continuous_source import future_cohort,repair,forward_history
 from source_adapter import prepare_retained_inputs,instant,_complete
 declared=m.get('manager_imports',[]);current=imports(store)
 if not declared:return None
 # Every declared immutable producer record must still exist, even if a fresher import arrived.
 known={r['hash'] for r in store.rows("SELECT hash FROM records WHERE kind='source_import'")}
 if any(x['record_hash'] not in known for x in declared):raise ValueError('UNKNOWN_SOURCE_IMPORT')
 data={}
 for item in declared:
  manifest=g.read(root,item['manifest'])
  if manifest['operation']!='PUNTPROOF_RESEARCH_LAB_SOURCE_SYNC_V1':raise ValueError('UNADMITTED_SOURCE_IMPORT_OPERATION')
  rows=g.read(root,item['artifact'])['records']
  if any(int(r.get('season',0))!=2026 for r in rows):raise ValueError('NON2026_INCREMENTAL_INPUT')
  data[item['dataset']]=rows
 if set(data)!={'schedule','rosters','player_stats','pbp'}:return None
 rosters=[{**r,'_observed_at':r.get('_source',{}).get('observed_at') or next(x['observed_at'] for x in declared if x['dataset']=='rosters'),'_source':r.get('_source',{})} for r in data['rosters']]
 first=future_cohort(data['schedule'],rosters,[],m['as_of'])
 if not first['fixtures']:return None
 week=first['fixtures'][0]['week'];prepared=prepare_retained_inputs(data['schedule'],data['rosters'],data['player_stats'],data['pbp'],cutoff=m['as_of'],target_period=(2026,week),_settlement=True)
 repaired=repair(prepared,previous,snaps,stats,conflicts);history=forward_history(repaired['history'])
 complete={f['game_id'] for f in prepared['schedule'] if _complete(f,instant(m['as_of']))}
 verified=[r for r in history if r['game_id'] in complete]
 cohort=future_cohort(data['schedule'],rosters,history,m['as_of'])
 return {'cohort':cohort,'verified_completed':verified,'completed_games':sorted(complete),'imports':declared}
