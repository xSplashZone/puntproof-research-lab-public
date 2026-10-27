"""Game-specific custody export of existing outputs. No model or provider calls."""
from __future__ import annotations
import argparse, csv, hashlib, io, json, math, os, zipfile
from pathlib import Path
from datetime import datetime, timezone
try:
    from .core import Store, digest, encoded, now, parse_time
except ImportError:
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parent))
    from core import Store, digest, encoded, now, parse_time
GAME='2026_07_DAL_PHI'
KICKOFF='2026-10-27T00:15:00+00:00'
NAME='Dallas at Philadelphia — Week 7 pregame shadow observation'
MANIFEST='data/private/research-lab/executions/forecast-15f52689a64971e205456cb5/MANIFEST.json'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def ref(root,path):
    p=Path(path);p=p if p.is_absolute() else root/p
    if not p.resolve().is_relative_to(root.resolve()):raise ValueError('Reference outside Lab')
    return {'path':p.relative_to(root).as_posix(),'sha256':sha(p)}
def verified(root,r):
    p=(root/r['path']).resolve()
    if not p.is_relative_to(root.resolve()):raise ValueError('Reference outside Lab')
    if sha(p)!=r['sha256']:raise ValueError('Custody mismatch: '+r['path'])
    return p

def probabilities(row):
    xs,ps=row['support'],row['pmf']
    if not xs or len(xs)!=len(ps) or xs!=sorted(set(xs)):raise ValueError('Invalid support')
    if any(not math.isfinite(p) or p<0 for p in ps) or abs(sum(ps)-1)>1e-8:raise ValueError('Invalid probabilities')
    mean=sum(x*p for x,p in zip(xs,ps))
    if abs(mean-row['mean'])>1e-7:raise ValueError('Mean differs from frozen law')
    def q(v):
        total=0
        for x,p in zip(xs,ps):
            total+=p
            if total>=v:return x
        return xs[-1]
    thresholds=[]
    for n in range(math.floor(min(xs))-1,math.ceil(max(xs))+1):
        for line in (float(n),n+.5):
            under=sum(p for x,p in zip(xs,ps) if x<line)
            push=sum(p for x,p in zip(xs,ps) if x==line)
            over=sum(p for x,p in zip(xs,ps) if x>line)
            thresholds.append({'line':line,'under':under,'push':push,'over':over,'choice':'OVER' if over>under else 'UNDER' if under>over else 'NO_EDGE'})
    return {'support':xs,'pmf':ps,'thresholds':thresholds,'tails':'Outside support: probabilities are exactly zero/one as implied by the complete PMF.',
            'uncertainty':{'q05':q(.05),'q25':q(.25),'q75':q(.75),'q95':q(.95),'standard_deviation':math.sqrt(sum(p*(x-mean)**2 for x,p in zip(xs,ps)))}}

def seal(root,store):
    if store.records('game_observation'):raise ValueError('An observation already exists; preserve it')
    started=now();kickoff=parse_time(KICKOFF)
    all_rows=store.rows("SELECT * FROM records WHERE kind='forecast' ORDER BY id,revision")
    selected=[]
    for r in all_rows:
        body=json.loads(r['body'])
        if body.get('game_id')!=GAME:continue
        if r['hash']!=digest([r['kind'],r['id'],r['revision'],body,r['previous_hash'],r['recorded_at']]):raise ValueError('Forecast record hash mismatch')
        if body['team'] not in ('DAL','PHI') or body['opponent'] not in ('DAL','PHI') or body['team']==body['opponent']:raise ValueError('Team identity mismatch')
        if any(body[k]!=body['fixture'][k] for k in ('game_id','gsis','team','opponent','channel','season','week')):raise ValueError('Fixture identity mismatch')
        selected.append({'id':r['id'],'record_hash':r['hash'],'recorded_at':r['recorded_at'],'original':body,'distribution':probabilities(body),
                         'expected_opportunities':{'value':body['workload'],'unit':'targets' if body['channel']=='receiving' else 'carries'},
                         'availability':{'active':None,'injury':None,'starting_qb':None,'role_change':None,'status':'UNKNOWN_NO_ADMITTED_CURRENT_AVAILABILITY_RECORD'}})
    if not selected:raise ValueError('No existing game forecasts')
    identities=[(x['original']['gsis'],x['original']['team'],x['original']['channel']) for x in selected]
    if len(set(identities))!=len(identities):raise ValueError('Duplicate forecast population')
    m=json.loads((root/MANIFEST).read_bytes());sr=json.loads(verified(root,m['source_receipt']).read_bytes());fp=json.loads(verified(root,m['feature_preparation']).read_bytes())
    actual_fp=json.loads(verified(root,fp['actual_preparation']).read_bytes())
    fixture=next(x for x in m['fixtures'] if x['game_id']==GAME)
    if parse_time(fixture['kickoff'])!=kickoff:raise ValueError('Kickoff disagreement')
    refs=[ref(root,MANIFEST),m['source_receipt'],m['feature_preparation'],fp['actual_preparation'],*m['input_files'],*m['model_artifacts'].values()]
    for x in selected:refs.append(x['original']['forecast_artifact'])
    for channel in ('receiving','rushing'):
        p=root/m['execution_directory']/channel/'forecast/COMPLETE.json';complete=json.loads(p.read_bytes());refs.append(ref(root,p));refs.append(complete['predictions'])
        original_rows=json.loads(verified(root,complete['predictions']).read_bytes())['rows']
        for x in [v for v in selected if v['original']['channel']==channel]:
            match=[z for z in original_rows if z['game_id']==GAME and z['gsis']==x['original']['gsis'] and z['team']==x['original']['team']]
            if len(match)!=1 or any(match[0][k]!=x['original'][k] for k in ('support','pmf','mean','median','workload')):raise ValueError('Stored output differs from sealed archive export')
    journal_dir=root/'data/private/research-lab/executions/forecast-197e45ac0a0edbf39a617cf4/lifecycle'
    events=[];previous=None
    for p in sorted(journal_dir.glob('*.json')):
        e=json.loads(p.read_bytes());copy=dict(e);recorded_hash=copy.pop('sha256')
        if hashlib.sha256((json.dumps(copy,sort_keys=True,indent=2,allow_nan=False)+'\n').encode()).hexdigest()!=recorded_hash or e['previous_hash']!=previous:raise ValueError('Broken original lifecycle chain')
        previous=recorded_hash;events.append(e);refs.append(ref(root,p))
    global_seal=next(x for x in events if x['state']=='GLOBAL_EXECUTION_SEAL_VERIFIED')
    for r in refs:verified(root,r)
    seeds=store.records('seed_inspection')
    seed_times_valid=all(parse_time(s['recorded_at'])<kickoff for s in seeds) and len(seeds)==2
    checks={'original_forecasts_before_kickoff':all(parse_time(x['original']['sealed_at'])<kickoff and parse_time(x['recorded_at'])<kickoff for x in selected),
            'global_seal_before_kickoff':parse_time(global_seal['at'])<kickoff,'source_observed_before_kickoff':parse_time(sr['latest_observed_at'])<kickoff,
            'feature_cutoff_before_kickoff':parse_time(m['cutoff'])<kickoff and parse_time(actual_fp['checked_at'])<kickoff,
            'history_through_week6':sr['coverage']['history_through']==[2026,6],
            'no_target_outcomes_in_preparation':fp['real_outcomes_opened']==0 and actual_fp['real_outcomes_opened']==0,
            'saved_seeds_inspected_before_kickoff':seed_times_valid,'original_outputs_equal':True,'identities_match':True,'custody_verified':True}
    decisions=[d for d in store.records('candidate_decision') if d['game_id']==GAME]
    seal_at=now();checks['evaluation_sealed_before_kickoff']=parse_time(seal_at)<kickoff
    classification='PROSPECTIVE_GAME_SPECIFIC_OBSERVATION' if all(checks.values()) else 'RETROSPECTIVE_GAME_SPECIFIC_OBSERVATION'
    body={'schema_version':'puntproof-game-shadow-v1','id':GAME,'name':NAME,'game_id':GAME,'kickoff':KICKOFF,'started_at':started,'sealed_at':seal_at,
          'classification':classification,'original_week_classification':m['classification'],'timing_checks':checks,'source_cutoff':m['cutoff'],'latest_source_observation':sr['latest_observed_at'],
          'original_global_seal_at':global_seal['at'],'original_manifest':ref(root,MANIFEST),'model_artifacts':m['model_artifacts'],'forecasts':selected,'candidate_decisions':decisions,
          'excluded_count':sum(d['decision']=='EXCLUDED' for d in decisions),'evidence':list({r['path']:r for r in refs}.values()),
          'frozen_F':{'status':'NOT_AVAILABLE_IN_EXISTING_LAB_FORECASTS','predictions':[]},'deployed_model':{'status':'UNVERIFIED_SEPARATE_FROM_V3_AND_F'},
          'offers':[],'betting':{'status':'ODDS_FREE_NO_MATCHED_PREGAME_OFFERS','paper_roi':None},
          'evaluation_protocol':{'point_metrics':['absolute_error_mean','absolute_error_median','squared_error_mean'],'probability_metrics':['CRPS','threshold_Brier','threshold_log_loss','PIT_midpoint','80_and_90_percent_interval_coverage'],
            'threshold_grid':'All integer and half-integer lines across each sealed support; diagnostic choices are not bookmaker bets. Aggregate Brier across fixed common 9.5 to 149.5 by 10 receiving, 9.5 to 99.5 by 10 rushing.',
            'eligibility':'Use only existing authorised whole-week reconciled observed outcomes; DNP and unknown remain separate, never zero-filled.',
            'learning':'Existing whole-week sequential process once only; this observation never calls settle/fit/forecast or creates another lease.',
            'corrections':'Retain first result and append correction versions if the admitted output authority publishes corrections; never alter sealed forecasts.',
            'inference':'One game, correlated player/channel observations. Descriptive calibration only; no improvement, significance or profitable-strategy claim.'},
          'limitations':['Conditional on appearance; current inactives, injuries, starting quarterbacks and role changes are unknown.','Narrow 2026-only history and missing snaps; original V3 input equivalence is not established.','Prospective designation applies only to this game observation; original Week7 retrospective labels remain unchanged.','Local clock and hash-chain custody; not an independent trusted timestamp service.']}
    folder=store.root/'executions'/('game-shadow-'+GAME);folder.mkdir(exist_ok=False)
    manifest=folder/'MANIFEST.json'
    with manifest.open('xb') as f:f.write(encoded(body));f.flush();os.fsync(f.fileno())
    receipt={'sealed_at':now(),'manifest':ref(root,manifest),'before_kickoff':parse_time(now())<kickoff,'forecast_count':len(selected),'classification':classification}
    if not receipt['before_kickoff'] and classification.startswith('PROSPECTIVE'):raise RuntimeError('Seal completed too late; retain package as unissued')
    with (folder/'SEAL.json').open('xb') as f:f.write(encoded(receipt));f.flush();os.fsync(f.fileno())
    csvbuf=io.StringIO(newline='');writer=csv.writer(csvbuf);writer.writerow(['player','gsis','team','channel','mean_yards','median_yards','expected_opportunities','q05','q95','original_sealed_at'])
    for x in selected:
        r=x['original'];u=x['distribution']['uncertainty'];writer.writerow([r['name'],r['gsis'],r['team'],r['channel'],r['mean'],r['median'],r['workload'],u['q05'],u['q95'],r['sealed_at']])
    csvname='DAL-PHI-2026-W7-shadow.csv';zipname='DAL-PHI-2026-W7-shadow.zip'
    (store.root/'exports'/csvname).write_text(csvbuf.getvalue(),encoding='utf-8')
    with zipfile.ZipFile(store.root/'exports'/zipname,'x',zipfile.ZIP_DEFLATED) as z:
        z.write(manifest,'MANIFEST.json');z.write(folder/'SEAL.json','SEAL.json');z.writestr(csvname,csvbuf.getvalue())
    summary={k:body[k] for k in ('id','name','game_id','kickoff','sealed_at','classification','original_week_classification','timing_checks','source_cutoff','latest_source_observation','original_global_seal_at','excluded_count','limitations','frozen_F','deployed_model','betting')}
    summary.update(manifest=receipt['manifest'],forecast_ids=[x['id'] for x in selected],forecast_count=len(selected),download='/api/download/'+zipname,csv_download='/api/download/'+csvname,status='SEALED_AWAITING_VERIFIED_OUTCOMES')
    store.append('game_observation',GAME,summary,immutable=True);store.audit('GAME_OBSERVATION_SEALED',receipt)
    return summary

def evaluate_scored_outputs(manifest, scored_rows):
    """Report already-scored admitted outputs; never read a source response."""
    scoremap={tuple(r['identity']):r for r in scored_rows}
    if len(scoremap)!=len(scored_rows):raise ValueError('Duplicate scored identity')
    rows=[];missing=[]
    for item in manifest['forecasts']:
        f=item['original'];key=(f['season'],f['week'],f['game_id'],f['gsis'],f['team'],f['channel'])
        scored=scoremap.get(key)
        if scored is None:
            missing.append({'name':f['name'],'gsis':f['gsis'],'channel':f['channel'],'status':'NO_ELIGIBLE_SCORED_OUTCOME','reason':'No matching appeared-player score in the authorised whole-week output. Nonappearance and unresolved response must not be replaced by zero.'});continue
        if abs(scored['mean']-f['mean'])>1e-7:raise ValueError('Scored mean differs from sealed prediction')
        raw_y=scored['mean']-scored['error'];y=round(raw_y)
        if abs(y-raw_y)>1e-7:raise ValueError('Scored outcome is not an integer yardage')
        xs,ps=f['support'],f['pmf'];cdf=0;quantiles={}
        for x,pr in zip(xs,ps):
            cdf+=pr
            for q in (.05,.1,.9,.95):
                if q not in quantiles and cdf>=q:quantiles[q]=x
        all_thresholds=[];fixed=[]
        fixed_lines={n+.5 for n in range(9,150 if f['channel']=='receiving' else 100,10)}
        for t in item['distribution']['thresholds']:
            over=int(y>t['line']);push=y==t['line'];prob=t['over'];clipped=min(1-1e-15,max(1e-15,prob))
            r={**t,'actual_relation':'PUSH' if push else 'OVER' if over else 'UNDER','choice_correct':None if push or t['choice']=='NO_EDGE' else int(t['choice']==('OVER' if over else 'UNDER')),'brier':(prob-over)**2,'log_loss':-(over*math.log(clipped)+(1-over)*math.log(1-clipped))}
            all_thresholds.append(r)
        # Evaluate the fixed grid even where a line is outside this player's support.
        for line in sorted(fixed_lines):
            prob=sum(pr for x,pr in zip(xs,ps) if x>line);target=int(y>line);cp=min(1-1e-15,max(1e-15,prob))
            fixed.append({'line':line,'probability_over':prob,'observed_over':target,'brier':(prob-target)**2,'log_loss':-(target*math.log(cp)+(1-target)*math.log(1-cp))})
        lower=sum(pr for x,pr in zip(xs,ps) if x<y);mass=sum(pr for x,pr in zip(xs,ps) if x==y)
        rows.append({'name':f['name'],'gsis':f['gsis'],'channel':f['channel'],'team':f['team'],'actual_yards':y,'mean':f['mean'],'median':f['median'],'absolute_error_mean':abs(f['mean']-y),'absolute_error_median':abs(f['median']-y),'squared_error_mean':(f['mean']-y)**2,'crps':scored['crps'],'covered80':int(quantiles[.1]<=y<=quantiles[.9]),'covered90':int(quantiles[.05]<=y<=quantiles[.95]),'pit_midpoint':lower+mass/2,'threshold_brier':sum(r['brier'] for r in fixed)/len(fixed),'threshold_log_loss':sum(r['log_loss'] for r in fixed)/len(fixed),'fixed_thresholds':fixed,'all_thresholds':all_thresholds,'high_usage':f.get('high_usage'),'surprise':'Outside sealed 90% prediction interval' if not quantiles[.05]<=y<=quantiles[.95] else 'Within sealed 90% prediction interval','causal_explanation':'QB, injury and role causes are unverified; yardage deviation alone does not establish a cause.'})
    n=len(rows);bins=[]
    for lo in range(0,10):
        vals=[t for r in rows for t in r['fixed_thresholds'] if lo/10<=t['probability_over']<((lo+1)/10 if lo<9 else 1.0000001)]
        bins.append({'lower':lo/10,'upper':(lo+1)/10,'n':len(vals),'mean_probability':sum(v['probability_over'] for v in vals)/len(vals) if vals else None,'observed_frequency':sum(v['observed_over'] for v in vals)/len(vals) if vals else None})
    return {'game_id':manifest['game_id'],'classification':manifest['classification'],'rows':rows,'missing_players':missing,'issued':len(manifest['forecasts']),'scored':n,'mean_absolute_error':sum(r['absolute_error_mean'] for r in rows)/n if n else None,'mean_crps':sum(r['crps'] for r in rows)/n if n else None,'calibration_bins':bins,'paper_roi':None,'betting_status':'NO_SEALED_MATCHED_OFFERS','learning':'Already handled once by the original whole-week sequential process. No second model update.', 'limitation':'One game; correlated players and thresholds. Descriptive calibration only, no independent improvement claim.'}

def refresh_evaluations(store,root):
    root=Path(root)
    if (root/'TASK_CHARTER.json').exists():return {'status':'WAITING_FOR_MAINTENANCE_CLOSEOUT'}
    completed=[]
    for obs in store.records('game_observation'):
        heads=[h for h in store.records('model_head') if h.get('manifest')==json.loads(verified(root,obs['manifest']).read_bytes())['original_manifest']]
        if not heads:continue
        head=heads[-1];closed=json.loads(verified(root,head['closed_lifecycle']).read_bytes())
        if closed.get('state')!='AUTHORITY_CLOSED':raise ValueError('Whole-week lifecycle is not closed')
        manifest=json.loads(verified(root,obs['manifest']).read_bytes())
        perf=[r for r in store.records('performance') if r.get('season')==2026 and r.get('week')==7]
        sources={r['source']['path']:r['source'] for r in perf}
        expected=str(Path(manifest['original_manifest']['path']).parent/'SCORES.json').replace('\\','/')
        if expected not in sources:continue
        score_ref=sources[expected];key=obs['id']+'-'+score_ref['sha256'][:20]
        if store.latest('game_evaluation',key):continue
        scores=json.loads(verified(root,score_ref).read_bytes())
        scored=[r for channel in ('receiving','rushing') for r in scores[channel]['rows'] if r['identity'][2]==obs['game_id']]
        report=evaluate_scored_outputs(manifest,scored)
        report.update(id=key,evaluated_at=now(),manifest=obs['manifest'],scored_output=score_ref,learning_head_hash=head['record_hash'],status='SCORED' if report['scored'] else 'NO_ELIGIBLE_OUTCOMES')
        name='DAL-PHI-2026-W7-evaluation-'+score_ref['sha256'][:20]+'.json';path=store.root/'exports'/name
        if path.exists():
            saved=json.loads(path.read_bytes())
            if saved['manifest']!=obs['manifest'] or saved['scored_output']!=score_ref:raise ValueError('Evaluation export conflict')
            report=saved
        else:
            with path.open('xb') as f:f.write(encoded(report));f.flush();os.fsync(f.fileno())
        report['download']='/api/download/'+name
        store.append('game_evaluation',key,report,immutable=True);store.audit('GAME_OBSERVATION_REPORTED',{'game_id':obs['id'],'score_ref':score_ref,'new_model_updates':0});completed.append(key)
    return {'status':'CHECKED','completed':completed}

def observation_views(store,root):
    result=[]
    for obs in store.records('game_observation'):
        context=store.latest('game_observation_context',obs['id']) or {}
        forecasts=[{k:r.get(k) for k in ('name','gsis','team','channel','mean','median','workload','sealed_at')} for r in store.records('forecast') if r['id'] in obs['forecast_ids']]
        result.append({**obs,'context':context,'forecasts':forecasts,'complete_download':'/api/download/DAL-PHI-2026-W7-complete-pregame.zip','evaluation':next(iter(sorted((r for r in store.records('game_evaluation') if r.get('game_id')==obs['id']),key=lambda r:r['recorded_at'],reverse=True)),None)})
    return result

if __name__=='__main__':
    root=Path(__file__).resolve().parents[2]
    print(json.dumps(seal(root,Store(root/'data/private/research-lab')),indent=2))
