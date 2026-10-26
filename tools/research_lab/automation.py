"""Finite orchestration and coverage evidence; no implicit provider or model authority."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import csv
import hashlib
import io
import json
from pathlib import Path
from core import now

CATEGORIES = {
 'TOO_FEW_SAME_TEAM_APPEARANCES':'inadequate_history',
 'UNRESOLVED_PRIOR_PARTICIPATION':'unknown_participation',
 'UNRESOLVED_PRIOR_FIXTURE_IDENTITY':'no_valid_identity',
 'UNRESOLVED_PRIOR_ROSTER_MEMBERSHIP':'join_defect',
 'UNKNOWN_RECENT_CHANNEL_VALUES':'unsupported_source',
 'DATED_POSITION_CHANGE':'incompatible_role_history',
}

def decision_audit(projection, *, projection_ref):
    """Explain the sealed construction; never expand or alter its population.

    Cohorts use prior appearances/usage only. No target outcomes are consumed.
    The 783 denominator is channel-applicable candidates, not every roster role.
    """
    history=defaultdict(list)
    for row in projection['data']['records']:
        history[row['gsis'],row['team']].append(row)
    decisions=[]
    for channel,fixtures in projection['fixtures'].items():
        decisions.extend(dict(f,decision='ELIGIBLE',reason='ELIGIBLE',category='eligible') for f in fixtures)
    decisions.extend(dict(x['fixture'],decision='EXCLUDED',reason=x['reason'],category=CATEGORIES.get(x['reason'],'other')) for x in projection['exclusions'])
    seen=set()
    for row in decisions:
        key=tuple(row.get(k) for k in ('season','week','game_id','gsis','team','channel'))
        if key in seen:raise ValueError('DUPLICATE_CANDIDATE_DECISION')
        seen.add(key)
        h=sorted((x for x in history[row['gsis'],row['team']] if (x['season'],x['week'])<(row['season'],row['week'])),key=lambda x:(x['season'],x['week']))
        known=[x for x in h if x.get('appeared') is True]
        field='targets' if row['channel']=='receiving' else 'carries'
        usage=[x.get(field) for x in known[-4:]]
        usage=[float(v) for v in usage if v is not None]
        row.update(prior_rows=len(h),verified_appearances=len(known),unknown_appearances=sum(x.get('appeared') is None for x in h),
          prior_game_ids=[x['game_id'] for x in h],last_prior_week=max((x['week'] for x in h),default=None),
          prior_usage_mean=sum(usage)/len(usage) if usage else None,prior_usage_cases=len(usage),
          evidence_projection=projection_ref,cutoff_selection='STRICTLY_PRIOR_ONLY')
        row['usage_cohort']='unknown' if not usage else ('higher_prior_usage' if row['prior_usage_mean'] >= (5 if row['channel']=='receiving' else 10) else 'other_known_prior_usage')
    cohorts=[]
    for channel in ('receiving','rushing'):
        for group in ('higher_prior_usage','other_known_prior_usage','unknown'):
            rows=[x for x in decisions if x['channel']==channel and x['usage_cohort']==group]
            cohorts.append(dict(channel=channel,cohort=group,candidates=len(rows),eligible=sum(x['decision']=='ELIGIBLE' for x in rows),excluded=sum(x['decision']=='EXCLUDED' for x in rows)))
    return dict(schema_version='puntproof-candidate-decisions-v1',rows=decisions,total=len(decisions),eligible=sum(x['decision']=='ELIGIBLE' for x in decisions),excluded=sum(x['decision']=='EXCLUDED' for x in decisions),
      reasons=dict(Counter(x['reason'] for x in decisions if x['decision']=='EXCLUDED')),categories=dict(Counter(x['category'] for x in decisions if x['decision']=='EXCLUDED')),cohorts=cohorts,
      cohort_rule='Last four verified prior same-team appearances: receiving >=5 targets, rushing >=10 carries; unknown is separate. Descriptive coverage only; no performance selection.',
      offered_prop_population=None,offered_prop_reason='No admitted timestamped offered-prop population',
      history_scope='2026 only; fresh 2021-2025 payloads are outside current Lab source permission.',
      input_equivalence='NOT_ESTABLISHED: snaps unknown and older source history omitted. The 228 column count is not an input-equivalence or forecast-quality certificate.',
      denominator='Channel-applicable roster decisions only. Non-RB/FB rushing combinations were never in the 783 candidate denominator.',projection=projection_ref)

def decision_csv(audit):
    fields=['season','week','game_id','gsis','name','team','position','channel','decision','reason','category','prior_rows','verified_appearances','unknown_appearances','last_prior_week','prior_usage_mean','prior_usage_cases','usage_cohort','prior_game_ids']
    out=io.StringIO(newline='');w=csv.DictWriter(out,fieldnames=fields,extrasaction='ignore');w.writeheader()
    for row in audit['rows']:w.writerow({**row,'prior_game_ids':'|'.join(row['prior_game_ids'])})
    return out.getvalue().encode('utf-8-sig')

def source_demands(imports,period):
    rows=[];by={x['dataset']:x for x in imports}
    for source in ('player_stats','pbp','rosters'):
        item=by.get(source,{})
        covered=period[1] in item.get('weeks',[])
        rows.append(dict(source=source,season=period[0],weeks=[period[1]],state='VALIDATED_USABLE' if covered else 'PUBLICATION_UNKNOWN',
          imported_records=item.get('rows',0),source_observed_at=item.get('source_clock'),last_import=item.get('imported_at'),
          next_action='Verify whole-week finality and issued-player participation' if covered else 'Request this exact partition through Data Manager',
          action_owner='Lab' if covered else 'Owner: approve the prepared manager bridge',
          publication_evidence=None,reason=None if covered else 'Missing target-week import; no upstream publication check has been authorised.'))
    return rows

class Automation:
    def __init__(self,operations):self.operations=operations;self.store=operations.store;self.root=operations.root
    def coverage(self):
        from operations import ref,write_once
        from nfl_model import research_lab_gate as gate
        preparations=self.store.records('source_preparation')
        if not preparations:return {'status':'WAITING','reason':'No retained source preparation'}
        # Explain the original issued preparation, not a newly widened population.
        issued={(f['season'],f['week']) for f in self.store.records('forecast')}
        applicable=[p for p in preparations if tuple(p['period']) in issued]
        if not applicable:return {'status':'WAITING','reason':'No issued population to explain'}
        p=min(applicable,key=lambda x:x['recorded_at']);key=p['projection']['sha256']
        old=self.store.latest('coverage_audit',key)
        if old:return {'status':'UNCHANGED',**old}
        mr,rr=self.operations._plan('PREPARE_FORECAST',gate.SOURCE,p['period'][1],{'population':'Exact previously issued candidate construction','projection':p['projection']},inputs=[p['projection']],identity={'coverage':key})
        with gate.execution_authority(self.root,gate.SOURCE,mr,rr,capability='PREPARE_FORECAST'):
            result=decision_audit(gate.value(self.root,p['projection']),projection_ref=p['projection'])
        directory=self.store.root/'sources'/('coverage-'+key[:24]);detail=ref(self.root,write_once(directory/'DECISIONS.json',result))
        csv_path=self.store.root/'exports'/('candidate-decisions-'+key[:16]+'.csv');csv_path.parent.mkdir(parents=True,exist_ok=True)
        content=decision_csv(result)
        if csv_path.exists():
            if csv_path.read_bytes()!=content:raise ValueError('DECISION_EXPORT_CUSTODY')
        else:
            with csv_path.open('xb') as f:f.write(content);f.flush()
        for row in result['rows']:
            row_id=hashlib.sha256(json.dumps([key,row['game_id'],row['gsis'],row['team'],row['channel']]).encode()).hexdigest()
            self.store.append('candidate_decision',row_id,row,immutable=True)
        summary={k:v for k,v in result.items() if k!='rows'}
        summary.update(artifact=detail,download='/api/download/'+csv_path.name,completed_at=now(),classification='SOURCE_COVERAGE_DIAGNOSTIC_NOT_MODEL_EXPERIMENT')
        self.store.append('coverage_audit',key,summary,immutable=True)
        return {'status':'COMPLETE',**summary}
    def status(self,period):
        imports=list(self.operations._latest_imports().values())
        demands=source_demands(imports,period)
        result=dict(at=now(),demands=demands,refresh=dict(status='PERMISSION_REQUIRED',provider_calls=0,
          interface='manager_service.Service.run([source], [2026], trigger="research_lab", required_by_source={source: [week]})',
          reason='Lab policy permits retained imports only and explicitly sets provider_calls=0. Prepared bridge preserves manager needs/reuse checks, disabled-source settings and rate limits.'),
          experiment=dict(status='WAITING_FOR_ELIGIBLE_PAIRED_INPUTS',executed=len(self.store.records('experiment_result')),
          reason='No completed matched baseline/candidate forecast-and-outcome set is admitted. End-of-Week-6 seeds cannot forecast earlier weeks. DEVELOPMENT also requires an exact one-shot charter.',
          next_action='Bind an eligible sealed paired candidate and complete whole-week outcomes to the existing finite executor; automatic one-shot preparation must pass the same gates.'),
          schedule=dict(state='STALE_RETAINED_VERSION',observed_at=next((x.get('source_clock') for x in imports if x['dataset']=='schedule'),None),
          reason='Retained schedule identity is not verified current finality. Settlement additionally requires explicit game-end evidence.'),
          research_policy=dict(max_candidates_per_week=2,max_attempts_each=1,max_seconds_each=300,protected_access=False,new_fit_authorized=False,pre2026_source_authorized=False))
        refresh=self.store.get_setting('manager_refresh_status')
        if refresh:
            result['refresh']=dict(refresh,reason=refresh.get('reason','Requests use the existing Data Manager; saved imports and upstream downloads are separately recorded.'))
        research=self.store.get_setting('paired_research_status')
        if research:result['experiment']=dict(research,reason=research.get('reason','Finite paired execution uses original sealed component distributions.'))
        requests=self.store.get_setting('manager_request_last',{})
        for demand in demands:
            previous=requests.get(demand['source'],{})
            demand.update(last_request=previous.get('at'),next_eligible_epoch=previous.get('epoch',0)+21600 if previous else None)
            demand['action_owner']='Lab automatic worker'
            demand['next_action']='Import new manager version, verify finality, then settle once' if demand['state']!='VALIDATED_USABLE' else 'Check exact finality and issued-player participation'
        self.store.set_setting('automation_status',result);return result
