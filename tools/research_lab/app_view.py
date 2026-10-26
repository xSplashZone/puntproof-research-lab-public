"""Owner-facing state derived only from persistent actual-operation records."""
from core import now
from hashlib import sha256

def actual_records(rows):
    """Exclude explicitly labelled validation/reference records from real activity."""
    reference_labels={'IMPORTED_COMPARISON','HISTORICAL_COMPARISON'}
    return [r for r in rows if not any('SYNTHETIC' in str(r.get(k,'')).upper() or str(r.get(k,'')).upper() in reference_labels for k in ('classification','scope'))]

def latest_by(rows, key):
    result={}
    for row in rows:
        if row.get(key) not in result or row.get('recorded_at','')>result[row[key]].get('recorded_at',''):
            result[row.get(key)]=row
    return list(result.values())

def owner_state(store, runtime):
    imports=latest_by(actual_records(store.records('source_import')),'dataset')
    seeds=latest_by(actual_records(store.records('seed_inspection')),'channel')
    forecasts=actual_records(store.records('forecast'))
    performance=actual_records(store.records('performance'))
    heads=actual_records(store.records('model_head'))
    candidates=actual_records(store.records('experiment_result'))
    operation=store.get_setting('operation_status',{})
    source_count=sum(x.get('rows',0) for x in imports)
    through=max([x.get('through_period',[2026,0]) for x in seeds]+[h['period'] for h in heads],default=None)
    period=operation.get('next_period') or ([through[0],through[1]+1] if through else None)
    week=f"Week {period[1]}" if period else 'the next week'
    settled=sum(p.get('cohorts',{}).get('all',{}).get('n',0) for p in performance)
    counts={'seeds_loaded':len(seeds),'source_records_validated':source_count,'forecasts_generated':len(forecasts),
            'forecasts_settled':settled,'learning_updates':len(heads),'candidates_tested':len(candidates)}
    paused=store.get_setting('worker_paused',False)
    resources=store.get_setting('worker_resources',{})
    status=store.get_setting('worker_status','STARTING')
    waits={'WAITING_FOR_IDLE':'Waiting until the computer has been idle long enough',
           'AVAILABLE_RAM_BELOW_RESERVE':'Waiting for enough free memory','CPU_BUSY':'Waiting for CPU usage to fall',
           'PROCESS_MEMORY_LIMIT':'The app reached its memory limit','DISK_BELOW_RESERVE':'Disk space is below the reserve',
           'IDLE_STATE_UNKNOWN':'Unable to measure computer idle time','PAUSED_BY_USER':'Paused by you'}
    reasons=[waits.get(x,'Unable to measure '+x.split(':')[-1].replace('_',' ')) for x in resources.get('reasons',[])] if status=='WAITING_RESOURCES' and not paused else []
    if status=='STOPPED': worker_label='Stopped'
    elif status=='STOPPING': worker_label='Stopping'
    elif paused: worker_label='Paused by you'
    elif status.startswith('RUNNING:'): worker_label='Working'
    elif status=='WAITING_RESOURCES': worker_label='Waiting for resources'
    elif status.startswith(('FAILED','ERROR')): worker_label='Needs investigation'
    elif status=='STARTING': worker_label='Starting'
    else: worker_label='Watching for new data'
    if status=='STOPPED': detail='The background worker is stopped. No automatic checks are running.'
    elif status=='STOPPING': detail='The current bounded operation is finishing before the worker stops.'
    elif paused: detail='Automatic research is paused until you resume it.'
    elif reasons: detail='; '.join(reasons)
    elif status.startswith('RUNNING:'): detail=store.get_setting('operation_current_step') or 'Running the current research operation.'
    else: detail=operation.get('summary','Checking retained data for supported work')
    worker={'label':worker_label,'detail':detail,
            'retry_at':None if status in ('STOPPED','STOPPING') or paused else store.get_setting('worker_retry_at') or store.get_setting('worker_next_run'),
            'resource_measurements':resources}
    checked=operation.get('checked_at') or runtime.get('checked_at') or now()
    attention=[]
    def issue(i,title,summary,blocks,kind='automatic',owner=False,source='Local Research Lab',technical=None):
        item={'id':i,'title':title,'summary':summary,'blocks':blocks,'source':source,'period':period,
              'status':'OPEN','owner_required':owner,'resolution_kind':kind,'checked_at':checked,
              'checks':[{'summary':summary,'result':'Unresolved','at':checked}],
              'action':{'label':'Refresh and continue','action':'run_ready','payload':{},'enabled':kind=='automatic','reason':None if kind=='automatic' else summary},
              'alert_ids':[],'technical':technical or {}}
        attention.append(item);return item
    if len(seeds)<2:
        issue('seed','Saved engines have not both been verified','The app still needs to load both original saved engines and check their actual saved periods.',['Seed verification','Forecasting'],'automatic')
    if not imports:
        issue('sources','Retained football records have not been imported','Available source metadata has been found; football payloads must pass the permitted reader.',['Source import','Forecasting'],'automatic')
    failures=operation.get('failures',[])
    failure_groups={}
    for index,failure in enumerate(failures):
        reason=failure.get('reason')
        # Only identical explicit reasons share a cause. Missing reasons stay separate.
        key=('reason',reason) if isinstance(reason,str) and reason else ('missing',index)
        failure_groups.setdefault(key,[]).append(failure)
    function_labels={'seed':'Seed verification','source':'Source import','forecast':'Forecasting','settlement':'Settlement and learning',
                     'player_stats':'Source import','pbp':'Source import','rosters':'Source import','schedule':'Source import'}
    for key,members in failure_groups.items():
        first=members[0]
        shared=len(members)>1
        identifier=sha256(str(key).encode('utf-8')).hexdigest()[:16]
        blocks=list(dict.fromkeys(b for f in members for b in f.get('blocks',[function_labels.get(f.get('function'),f.get('function','Research'))])))
        kinds=list(dict.fromkeys(f.get('resolution_kind','code repair') for f in members))
        item=issue('operation-'+identifier,'Several operations need the same repair' if shared else first.get('title','An operation needs repair'),
                   first['reason'] if shared else first.get('summary',first.get('reason','Inspect the execution details')),
                   blocks,kinds[0] if len(kinds)==1 else 'repair',any(f.get('owner_required',False) for f in members),
                   technical={'reason':first.get('reason'),'failures':members})
        item['checks']=[{'summary':f.get('summary',f.get('reason','Inspect the execution details')),
                         'function':f.get('function'),'result':'Unresolved','at':f.get('checked_at') or f.get('recorded_at') or checked} for f in members]
    if forecasts and not heads:
        imp={x['dataset']:x for x in imports}
        available=', '.join(f"{name.replace('_',' ')} through Week {r.get('through_period',[2026,0])[1]}" for name,r in imp.items() if name in ('player_stats','pbp'))
        issue('results',f'Waiting for complete {week} results',
              (operation.get('settlement',{}).get('reason') or (available+'. ' if available else ''))+' Settlement and learning require complete final-game and participation evidence for the issued population. Forecasts remain saved.',
              ['Settlement','Learning','Measured forecast performance'],source='Retained player statistics and play-by-play')
    issue('experiment','Development experiments need a complete eligible evaluation',
          'An exact frozen, approved and unconsumed candidate specification, matched sealed control and candidate predictions, and complete chronological outcomes are required. Knowledge-linked proposals are saved separately; none count as tested candidates.',
          ['Development experiments'],'specification',False)
    if not store.bets():
        issue('betting','Betting returns are not measured','No genuine timestamped offer and verified settled wager records are connected. Ordinary forecasts do not depend on these records.',
              ['Genuine betting ROI'],'configuration',True,source='Bookmaker offers and wager settlements')
    acquisition=issue('acquisition','Newer inputs use the existing Data Manager',
          'The exact Data Manager bridge is prepared. Its provider request authority needs the bounded approval in AUTOMATION_PERMISSION_DELTA.md; retained imports continue independently. Existing records remain usable.',
          ['Acquisition of newer inputs'],'source connection',True,source='Existing Data Manager archive',
          technical={'mode':'READ_ONLY_POLLING','provider_refresh_connector':False,'prepared_connector_state':'PREPARED_AWAITING_AUTHORITY','provider_acquisition_grant':False})
    automatic=store.get_setting('manager_refresh_status')
    if automatic:
        acquisition.update(owner_required=False,resolution_kind='automatic',summary=automatic.get('reason','Automatic refresh requests use the approved Data Manager bridge. Each source has a six-hour minimum demand interval; imported outcomes still require finality and identity checks.'))
        acquisition['technical'].update(provider_refresh_connector=True,provider_acquisition_grant=True)
    issue('history','Exact past-run reproduction is unavailable','The runtime package does not include the original full feature matrix and historical checkpoints.',
          ['Exact historical reproduction'],'missing artifact',False,source='Supplied V3 runtime package')
    issue('snap','Snap coverage is not admitted for this model','BALLDONTLIE archive access is storage-only and its player crosswalk is unverified. PFR has a separate rights restriction. Optional snap features remain unknown; unsupported player histories are excluded.',
          ['Snap-dependent features','Complete player coverage'],'permission',False,source='BALLDONTLIE and PFR')
    issue('production','Production model identity is unverified','A read-only feed status confirmed configuration, but did not establish the deployed model identity. Comparisons against frozen research remain available.',
          ['Claims about beating production'],'configuration',False,source='Production metadata')
    # Retain consequential incidents. Old global/metadata duplicates belong to the
    # appropriate root cause, with every original incident still inspectable.
    groups={'MODEL_AUTHORITY':'seed' if not seeds else 'sources','NO_MARKET_OFFERS':'betting',
            'PRODUCTION_IDENTITY':'production','SCIENTIFIC_EXECUTION_BLOCKED':'experiment',
            'SOURCE_NOT_READY':'results' if forecasts else 'sources','SOURCE_READINESS':'results' if forecasts else 'sources'}
    for alert in store.alerts():
        if alert['status']=='RESOLVED':continue
        code=alert['code'];target=groups.get(code)
        if code in ('SOURCE_ALIAS_MISMATCH','NO_RETAINED_SOURCE','SOURCE_METADATA_STALE'):target='sources'
        match=next((x for x in attention if x['id']==target),None)
        if match:match['alert_ids'].append(alert['id'])
        elif alert['severity']=='P0' or code in ('WORKER_JOB_FAILURE','WORKER_LOOP_FAILURE','V3_EXECUTION_QUARANTINED','WORKER_TIME_BUDGET'):
            item=issue('alert-'+alert['id'],alert['title'],
                       alert['detail'].get('actual',alert['detail'].get('next_action','Inspect technical evidence')) if isinstance(alert['detail'],dict) else alert['detail'],
                       ['Affected operation'],'code repair',True,technical=alert)
            item['alert_ids']=[alert['id']]
            item['action']={'label':'Export fix pack','action':'fixpack','payload':{'alert_id':alert['id']},'enabled':True}
    ready_seeds=len(seeds)==2
    functions=[
        ('historical','Previous research','AVAILABLE','Supplied historical comparisons are available.'),
        ('source','Source import','COMPLETE' if imports else 'PENDING',f'{source_count:,} real records validated from {len(imports)} retained datasets.' if imports else 'Import permitted retained payloads.'),
        ('seed','Saved engine','COMPLETE' if ready_seeds else 'PENDING',f'{len(seeds)} saved engines actually loaded.'),
        ('forecast','Forecasting','COMPLETE' if forecasts else 'PENDING',f'{len(forecasts):,} real-input forecasts saved.' if forecasts else operation.get('forecast_reason','Connect compatible prior inputs.')),
        ('learning','Settlement and learning','COMPLETE' if heads else 'WAITING',f'{len(heads)} learning updates completed.' if heads else f'Awaiting complete {week} outcomes after forecast sealing.'),
        ('experiment','Development experiments','COMPLETE' if candidates else 'WAITING',f'{len(candidates)} candidates tested.'),
        ('betting','Genuine betting returns','NOT_MEASURED','Exact offers and settlements are required.'),
        ('reproduction','Exact historical reproduction','BLOCKED','Original full matrix and checkpoints are absent.')]
    function_status=[dict(id=i,label=l,status=t,summary=m,blocker_ids=[a['id'] for a in attention if any(l.lower() in b.lower() for b in a['blocks'])]) for i,l,t,m in functions]
    def last(rows,kind):
        if not rows:return None
        r=max(rows,key=lambda x:x.get('recorded_at',x.get('sealed_at','')))
        p=r.get('period') or ([r.get('season',2026),r['week']] if r.get('week') else None)
        return {'summary':kind,'period':p,'at':r.get('sealed_at',r.get('recorded_at'))}
    if failures:headline='Some operations need attention'
    elif len(seeds)<2 or not imports:headline='Setup incomplete — connecting real data'
    elif forecasts and not heads:headline=f'Waiting for {week} results'
    elif heads:headline='Learning from completed games'
    else:headline=f'Preparing {week} forecasts'
    next_label=operation.get('next_action') or ('Load the saved engines' if not ready_seeds else 'Import retained football records' if not imports else f'Generate {week} forecasts' if not forecasts else f'Import complete {week} outcomes')
    summary=operation.get('summary') or ('The saved engine and permitted records are being connected.' if not forecasts else 'Real-input predictions are saved. Learning will advance only after the complete next week is verified.')
    home={'headline':headline,'summary':summary,'counts':counts,'worker':worker,
          'last_real':{'refresh':last([x for x in store.records('manager_refresh') if x.get('status')=='MANAGER_COMPLETED' and (x.get('last_run') or {}).get('status') in ('NEW','UNCHANGED','PARTIAL')],'Data Manager refresh completed'),'forecast':last(forecasts,'Generated real-input forecasts'),'learning':last(heads,'Settled and learned from a complete week'),'experiment':last(candidates,'Completed a development candidate')},
          'next':{'label':next_label,'reason':operation.get('next_reason',summary),'period':period},
          'primary_action':{'label':'Resume research' if paused else 'Refresh and continue','action':'resume' if paused else 'run_ready','payload':{},'enabled':True},
          'performance':{'summary':'Measured forecast performance' if settled else 'Not measured yet',
                         'reason':f'{settled} verified outcomes scored.' if settled else 'Real forecasts need verified outcomes before error and calibration can be measured. Betting returns require genuine offers and settlements.',
                         'metrics':[{'label':'Verified outcomes','value':settled,'unit':'records'}] if settled else []}}
    return {'home':home,'function_status':function_status,'attention':attention,'source_imports':imports,'seed_inspections':seeds}
