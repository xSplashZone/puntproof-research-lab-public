"""Licensed-data lane within the existing coordinator, shared lock and finite registry."""
from pathlib import Path
import json,statistics
from core import now,digest
from .registry import write_once

def read_status(store):return store.get_setting('licensed_bdl_runtime',{'status':'PENDING_ADMISSION','legal':'PROVIDER_PERMITS_MODELLING','next_action':'Verify committed licensed source bindings.'})

def publish_source(c,receipt):
 from nfl_model import licensed_bdl_gate as g
 from .coverage import runtime_summary
 root=c.root;store=c.store;report=g.read(root,receipt['coverage_json']);exports=store.root/'exports';exports.mkdir(exist_ok=True)
 links={}
 for kind,ref in [('json',receipt['coverage_json']),('csv',receipt['coverage_csv'])]:
  dest=exports/('LICENSED_BDL_COVERAGE-'+ref['sha256'][:16]+'.'+kind);raw=g.checked(root,ref)
  if dest.exists():g.need(dest.read_bytes()==raw,'EXPORT_CUSTODY')
  else:dest.write_bytes(raw)
  links[kind]='/api/download/'+dest.name
 record={**report,'status':'COMPLETE','source_repairs_completed':report['summary']['source_recovered_candidates'],'unknown_candidate_decisions_remaining':report['summary']['candidates_with_unknown_after'],'download':links['csv'],'json_download':links['json'],'artifacts':{'report':receipt['coverage_json'],'csv':receipt['coverage_csv']},'completed_at':receipt['recorded_at']}
 if not store.latest('coverage_reconciliation',receipt['projection']['sha256']):store.append('coverage_reconciliation',receipt['projection']['sha256'],record,immutable=True)
 summary={k:v for k,v in receipt.items() if k not in ('source_manifest','catalogue','source_policy')}
 store.set_setting('licensed_bdl_source',summary)
 # Resolve only the exact saved format defect after authenticated backfill succeeds.
 for alert in store.alerts():
  if alert['code']=='LICENSED_BDL_INTEGRATION' and alert['status']!='RESOLVED' and alert['detail'].get('reason')=='LICENSED_SOURCE:REQUEST_SEASON_SCOPE':
   evidence={'source_receipt':g.ref(root,root/g.PRIVATE/'source/RECEIPT.json'),'backfill_impact_verified':True,'original_forecasts_modified':0,'at':now()}
   with store.connect() as db:db.execute("UPDATE alerts SET status='RESOLVED',resolution=? WHERE id=?",(json.dumps(evidence,sort_keys=True),alert['id']))
   store.audit('licensed_source_format_resolved',{'alert':alert['id'],**evidence})

 return summary

def terminal(c,candidate,mr):
 from nfl_model import licensed_bdl_gate as g
 out=c.root/g.PRIVATE/'attempts'/candidate['id'];v=json.loads((out/'TERMINAL.json').read_bytes());spec=candidate['plan']
 g.need(v['manifest_sha256']==mr['sha256'] and v['projection_file_sha256']==spec['projection']['sha256'] and v['recipe_id']==spec['recipe_id'] and v['completed_folds']==1 and v['status'] in ('COMPLETED','REJECTED','INCONCLUSIVE'),'TERMINAL_IDENTITY')
 for a in [*v['artifacts'],v['result_artifact']]:
  p=Path(a['path']).resolve();g.need(p.is_relative_to((out/'artifacts').resolve()),'ARTIFACT_PATH');g.checked(c.root,{'path':p.relative_to(c.root).as_posix(),'sha256':a['sha256']})
 retained=json.loads(Path(v['result_artifact']['path']).read_bytes());g.need(retained=={k:x for k,x in v.items() if k not in ('result_artifact','manifest_sha256')},'RESULT_ROUNDTRIP')
 return v

def ablation(c):
 from nfl_model import licensed_bdl_gate as g
 from .trainer import summarize_scores,paired_week_interval
 runs=[r for r in c.registry.attempts() if r['plan'].get('campaign')==g.CAMPAIGN];results=[]
 for ch in ('receiving','rushing'):
  selected={r['plan']['include_snaps']:r for r in runs if r['plan']['channel']==ch and r.get('result')}
  if set(selected)!={True,False}:continue
  scores={}
  for snap,r in selected.items():
   refs=[a for a in r['result']['artifacts'] if Path(a['path']).name=='SCORES.jsonl'];a=refs[0];p=Path(a['path']);raw=g.checked(c.root,{'path':p.relative_to(c.root).as_posix(),'sha256':a['sha256']});scores[snap]={tuple(s['identity']):s for s in map(json.loads,raw.splitlines())}
  g.need(set(scores[True])==set(scores[False]),'ABLATION_SAME_POPULATION')
  paired=[]
  for k,s in scores[True].items():
   b=scores[False][k];g.need(s['actual']==b['actual'] and s['high_usage']==b['high_usage'],'ABLATION_LABELS_COHORT');paired.append({**s,'baseline_score':b})
  # Existing conservative six-trial interval retained, disclosed; no tuning.
  results.append({'channel':ch,'n':len(paired),'with_snaps':summarize_scores(paired),'without_snaps':summarize_scores(list(scores[False].values())),'paired_mae':paired_week_interval(paired),'paired_crps':paired_week_interval(paired,metric='crps'),'higher_usage_with':summarize_scores([r for r in paired if r['high_usage']]),'higher_usage_without':summarize_scores([r['baseline_score'] for r in paired if r['high_usage']]),'interpretation':'Negative paired delta favours snaps; exposed revised-history only. Existing conservative six-trial interval used; no promotion.'})
 if results:
  body={'campaign':g.CAMPAIGN,'comparisons':results,'provider_calls':0,'promotion':False,'genuine_roi':None};p=c.store.root/'exports'/('LICENSED_BDL_ABLATION-'+digest(body)[:16]+'.json');write_once(p,body);c.store.set_setting('licensed_bdl_ablation',{**body,'download':'/api/download/'+p.name})
 return results

def run_next(c):
 from nfl_model import licensed_bdl_gate as g
 from .licensed_preparation import source,fit
 from .limits import load_runtime,run
 root=c.root;store=c.store
 try:
  consumer,_=g.consumer_for(root,g.SOURCE);g.consumer_for(root,g.DEVELOPMENT)
  if (root/'TASK_CHARTER.json').exists():return {'status':'LIVE_CHARTER_ACTIVE'}
  rp=root/g.PRIVATE/'source/RECEIPT.json'
  if rp.exists():receipt=g.read(root,g.ref(root,rp));g.checked(root,receipt['projection']);g.need(receipt['catalogue']==consumer['catalogue'],'RETAINED_SOURCE_CATALOGUE')
  else:
   mp=source(root);store.set_setting('licensed_bdl_runtime',{'status':'LOADING_RETAINED_ARCHIVE','legal':'PROVIDER_PERMITS_MODELLING','internal_policy':'UPDATED','provider_calls':0,'next_action':'Authenticate retained pages and reconcile dated identities.'})
   from .licensed_source import execute
   receipt=execute(root,mp)
  publish_source(c,receipt)
  campaign=g.read(root,consumer['campaign'],True)
  for recipe in campaign['recipes']:
   spec={k:v for k,v in recipe.items() if k!='id'};spec.update(campaign=g.CAMPAIGN,projection=receipt['projection'],data_sha256=receipt['projection']['sha256'],folds=campaign['folds'],seed=campaign['seed']);c.registry.plan(spec)
  # Only this lane's consumed attempts are reconciled; never retried.
  for r in c.registry.attempts():
   if r['plan'].get('campaign')!=g.CAMPAIGN or r['status']!='CLAIMED':continue
   out=root/g.PRIVATE/'attempts'/r['id']
   try:v=terminal(c,r,g.ref(root,out/'MANIFEST.json'));c.registry.finish(r['id'],v['status'],v,elapsed=v['resources']['wall_seconds'])
   except Exception as exc:c.registry.finish(r['id'],'INTERRUPTED',error='Consumed licensed fit retained without retry: '+str(exc))
  candidates=[r for r in c.registry.attempts() if r['plan'].get('campaign')==g.CAMPAIGN and r['status']=='PLANNED']
  if not candidates:
   comparisons=ablation(c);state={'status':'CAMPAIGN_COMPLETE','legal':'PROVIDER_PERMITS_MODELLING','internal_policy':'UPDATED','provider_calls':0,'next_action':'Fixed campaign complete. Review paired snap ablation, remaining identity gaps and coverage export; no automatic refit.','completed_comparisons':len(comparisons)};store.set_setting('licensed_bdl_runtime',state);return state
  r=candidates[0];mr=fit(root,r,receipt);g.require_fit(root,mr);mp=root/mr['path'];value=load_runtime(root)
  c.child_command(mp) # existing exact runtime/Job/nonce probe before consumption
  argv=[value['base_executable']['path'],'-I','-B','-S',str(root/'tools/research_lab/gamd/licensed_child.py'),str(root),str(mp)]
  c.registry.claim(r['id']);nonce=g.claim(root,mr);out=mp.parent
  store.set_setting('licensed_bdl_runtime',{'status':'FITTING','legal':'PROVIDER_PERMITS_MODELLING','internal_policy':'UPDATED','recipe':r['plan']['recipe_id'],'attempt':r['id'],'next_action':'Bounded chronological fit and evaluation running; consumed attempts cannot restart.','provider_calls':0})
  result=run(argv,root,out/'stdout.log',out/'stderr.log',seconds=300,memory_mb=4096,stop=c.stop_event,handshake=('EXECUTE '+nonce+'\n').encode())
  if result['status']=='COMPLETED' and (out/'TERMINAL.json').exists():
   v=terminal(c,r,mr);c.registry.finish(r['id'],v['status'],v,elapsed=result['elapsed_seconds']);store.append('gamd_model',r['id'],{'family':r['plan']['family'],'channel':r['plan']['channel'],'campaign':g.CAMPAIGN,'decision':v['decision'],'artifact_directory':str(out/'artifacts'),'result':v},immutable=True)
  else:c.registry.finish(r['id'],result['status'] if result['status']!='COMPLETED' else 'FAILED',error='Inspect retained child stderr; no retry.',elapsed=result['elapsed_seconds'])
  ablation(c);c.export_ledger();store.set_setting('licensed_bdl_runtime',{'status':'IDLE','last_attempt':r['id'],'legal':'PROVIDER_PERMITS_MODELLING','internal_policy':'UPDATED','provider_calls':0,'next_action':'Next eligible fixed recipe runs automatically under the same resource and pause rules.'});return {'status':'LICENSED_RECIPE_FINISHED','attempt':r['id'],**result}
 except Exception as exc:
  state={'status':'BLOCKED','legal':'PROVIDER_PERMITS_MODELLING','reason':str(exc),'next_action':'Inspect the exact licensed lane prerequisite; this does not revoke provider rights or disable unrelated settlement.','provider_calls':0};store.set_setting('licensed_bdl_runtime',state);store.alert('LICENSED_BDL_INTEGRATION','P1','Licensed-data research needs an engineering check',state,'licensed-bdl');return state
