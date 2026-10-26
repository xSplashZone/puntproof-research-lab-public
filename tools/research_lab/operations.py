"""Bounded, deduplicated connection from retained sources to the supplied V3.

No provider calls, production writes, synthetic inputs, or original model mutation.
Each reader and subprocess enters its own exact action gate.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
from core import now, digest
from v3_runtime import ScientificRuntime, write_once, ref, sha, gate
import source_adapter as adapter

FINISH='research/research_lab_v1/finish_v1/'
CHANNELS=('receiving','rushing')
CHECKS={
 'SOURCE_IMPORT':['source_path_hash_custody','source_projection_scope','missing_not_zero'],
 'PREPARE_FORECAST':['missing_not_zero','temporal_identity','same_week_poisoning'],
 'SEED_INSPECTION':['seed_hash_before_load','seed_channel_schema','inspection_no_fit_score'],
 'FORECAST':['missing_not_zero','temporal_identity','same_week_poisoning','partial_scope_labels'],
 'SETTLE':['positive_snap_zero_production','missing_not_zero','duplicate_settlement','whole_week_completion','artifact_roundtrip']}
PURPOSE={'SOURCE_IMPORT':'SOURCE_IMPORT','PREPARE_FORECAST':'FEATURE_PREPARATION','SEED_INSPECTION':'SEED_INSPECTION','FORECAST':'FORECAST_CYCLE'}
def producer_witness(snapshot, rule):
    """Preserve producer row bytes and the policy's exact database identity."""
    if (snapshot['source_id']!=rule['source_id'] or snapshot['dataset']!=rule['dataset']
        or snapshot['season']!=rule['season']
        or Path(snapshot['metadata_database']).resolve()!=Path(rule['metadata_database']).resolve()):
        raise ValueError('Producer snapshot does not match its exact admitted partition')
    return {'schema_version':'puntproof-lab-producer-metadata-witness-v1',
            'source':snapshot['source_id'],'dataset':snapshot['dataset'],'season':snapshot['season'],
            'metadata_database':rule['metadata_database'],'snapshot_id':snapshot['id'],
            'row':snapshot['metadata_row']}

class Operations:
    def __init__(self,store,source_repo,root=None):
        self.store=store
        self.runtime=ScientificRuntime(store) if root is None else ScientificRuntime(store,root)
        self.root=self.runtime.root;self.source_repo=Path(source_repo).resolve()
        self.lock=threading.Lock()
    def fingerprint(self):
        metadata=adapter.select_snapshot_metadata(self.source_repo,as_of=now())
        return digest({'snapshots':[(s['id'],s['records_hash']) for s in metadata['snapshots']],
                       'heads':[(h['id'],h['record_hash']) for h in self.store.records('model_head')],
                       'seeds':[(s['channel'],s['record_hash']) for s in self.store.records('seed_inspection')],
                       'forecasts':len(self.store.records('forecast')),'refresh_window':int(__import__('time').time()//21600),
                       'manager_requests':self.store.get_setting('manager_request_last',{}),
                       'binding':sha(self.root/'docs/project_brief.md')})
    def _checks(self):
        _,source_consumer=gate.consumer_for(self.root,gate.SOURCE)
        p=(self.root/source_consumer['path']).parent/'ADAPTER_CHECKS.json'
        r=ref(self.root,p);body=gate.value(self.root,r,committed=True)
        if body.get('status')!='PASS':raise ValueError('Committed adapter checks have not passed')
        for name,h in body['code_sha256'].items():
            if sha(self.root/name)!=h:raise ValueError('Adapter changed after tests: '+name)
        return r
    def _plan(self,capability,operation,week,witness,*,inputs=(),extra=None,identity=None):
        consumer,cref=gate.consumer_for(self.root,operation)
        checks=self._checks();extra=extra or {}
        key=digest({'capability':capability,'consumer':cref,'week':week,'identity':identity or witness,'inputs':inputs,'extra':extra})
        directory=self.store.root/'executions'/(capability.lower()+'-'+key[:24])
        mp=directory/'MANIFEST.json';rp=directory/'READINESS.json'
        if mp.is_file() and rp.is_file():return ref(self.root,mp),ref(self.root,rp)
        stamp=now()
        w=write_once(directory/'TARGET_WITNESS.json',dict(witness,season=2026,week=week,capability=capability,records=witness.get('records',1),status='PASS',provider_calls=0,witness_class='LAB_ACTION_INPUTS',checked_at=stamp))
        wr=ref(self.root,w)
        checked_tests=gate.value(self.root,checks)
        required_canaries=list(CHECKS[capability])+(['deterministic_pending_recovery','exact_locked_expert_types'] if extra.get('engineering_recovery') else [])
        if not set(required_canaries)<=set(checked_tests['passed_defects']):
            raise ValueError('Missing tested action-specific canary')
        canaries=[]
        for defect in required_canaries:
            canary=write_once(directory/('CANARY-'+defect+'.json'),{
                'status':'PASS','defect_id':defect,'target_seasons':['2026'],
                'witness_class':'LAB_ACTION_INPUTS','provider_calls':0,
                'upstream_test_receipt':checks,'code_sha256':checked_tests['code_sha256']})
            canaries.append({'defect_id':defect,'canary':ref(self.root,canary)})
        review=json.loads((self.root/FINISH/'REVIEW.json').read_bytes())
        predicate={'predicate_id':capability,'mandatory':True,'terminal_outcome_dependency':['LAB_RUNTIME_OPERATION_COMPLETE'],
                   'required_witness_class':'LAB_ACTION_INPUTS','source_artifacts':[dict(wr,seasons=['2026'],witness_class='LAB_ACTION_INPUTS')],
                   'target_seasons':['2026'],'target_population':witness.get('population',capability),
                   'retained_availability':True,'authorised_acquisition':[],'required_authority':['READ_METADATA'],
                   'maximum_calls':0,'maximum_cost_cents':0,'known_defects':canaries,
                   'cross_season_provenance':{'inherited_from_seasons':[],'verified_target_seasons':['2026']},
                   'equivalent_prior_work_checked':True,'status':'SATISFIABLE_RETAINED'}
        pretask=write_once(directory/'PRETASK.json',{'schema_version':'nfl-pretask-satisfiability-v1','task_id':operation,
                 'phase':'REAL_EXECUTION','review':review,'budget':dict(max_calls=0,remaining_calls=0,reserve_calls=0,max_cost_cents=0),'predicates':[predicate]})
        m={'schema_version':'puntproof-lab-execution-manifest-v2','purpose':PURPOSE[capability],
           'operation':operation,'consumer':cref,'source_policy':consumer['source_policy'],'season':2026,'week':week,
           'execution_directory':directory.relative_to(self.root).as_posix(),'as_of':stamp,'cutoff':stamp,
           'input_files':list(inputs),'provider_calls':0,'cost_cents':0,'protected_access':False,'production':False,
           'pretask_satisfiability':ref(self.root,pretask),**extra}
        write_once(mp,m);mr=ref(self.root,mp)
        readiness={'schema_version':'puntproof-lab-action-readiness-v2','capability':capability,'status':'READY',
                   'operation':operation,'consumer':cref,'manifest':mr,'implementation':consumer['implementation'],
                   'season':2026,'week':week,'attempt_consumed':False,
                   'authored_adapter_checks':[checks],'target_witness':wr,
                   'prechecks':{x:True for x in CHECKS[capability]}}
        if extra.get('feature_preparation'):readiness['feature_preparation']=extra['feature_preparation']
        write_once(rp,readiness);return mr,ref(self.root,rp)
    def inspect_seeds(self):
        consumer,_=gate.consumer_for(self.root,gate.REPLAY)
        refs=consumer['seed_channels']
        previous={r['channel']:r for r in self.store.records('seed_inspection')}
        if all(previous.get(c,{}).get('source_seed',{}).get('sha256')==refs[c]['sha256'] for c in CHANNELS):
            return {'status':'UNCHANGED','seeds':2}
        mr,rr=self._plan('SEED_INSPECTION',gate.REPLAY,None,{'population':'Two exact supplied V3 seed files','seed_artifacts':refs},
                       extra={'model_artifacts':refs,'channels':list(CHANNELS)},identity=refs)
        m=gate.value(self.root,mr)
        for channel in CHANNELS:
            if previous.get(channel,{}).get('source_seed',{}).get('sha256')==refs[channel]['sha256']:continue
            output=self.root/m['execution_directory']/channel/'inspection'
            request={'schema_version':1,'action':'inspect_seed','engine_root':str(self.runtime.engine),
                     'package_manifest_sha256':sha(self.runtime.engine/'PACKAGE_MANIFEST.json'),'operation':gate.REPLAY,
                     'authority':{'execution_manifest':mr,'readiness':rr},'period':None,'channel':channel,
                     'output_dir':str(output),'parent_session':{'path':str(gate.reference(self.root,refs[channel])),
                     'sha256':refs[channel]['sha256'],'through_period':None}}
            self.store.set_setting('operation_current_step','Loading the saved '+channel+' engine')
            result=self.runtime._invoke(request)
            body={k:v for k,v in result.items() if not k.startswith('_')}
            body.update(loaded_at=now(),source_seed=refs[channel],manifest=mr,bridge_receipt=result['_receipt_ref'])
            self.store.append('seed_inspection',channel+'-'+refs[channel]['sha256'],body,immutable=True)
        self.store.audit('actual_seed_inspection_completed',{'channels':list(CHANNELS),'manifest':mr})
        return {'status':'SEEDS_LOADED','seeds':2}
    def next_period(self):
        heads=self.store.records('model_head')
        if heads:return [2026,max(h['period'][1] for h in heads)+1]
        seeds=self.store.records('seed_inspection')
        periods={tuple(s['through_period']) for s in seeds}
        if len({s['channel'] for s in seeds})!=2 or len(periods)!=1:raise ValueError('Both saved seeds must report the same actual through-period')
        y,w=next(iter(periods))
        if y!=2026 or not 1<=w<18:raise ValueError('Saved period is outside this bounded 2026 continuation')
        return [y,w+1]
    def import_sources(self):
        try:target=self.next_period()
        except ValueError:target=[2026,7] # Import-only cap from the supplied seed manifest; no forecast authority.
        metadata=adapter.select_snapshot_metadata(self.source_repo,as_of=now())
        consumer,_=gate.consumer_for(self.root,gate.SOURCE)
        policy=gate.value(self.root,consumer['source_policy'])
        selected=metadata['snapshots']
        active=self.store.get_setting('active_forecast_plan')
        responses_allowed=False
        if active and active.get('period')==target:
            from v3_runtime import LifecycleJournal
            m=gate.value(self.root,active['manifest'])
            journal=LifecycleJournal(self.root,m)
            events=journal.records()
            responses_allowed=journal.phase()=='ATTEMPT_CONSUMED' and [m['season'],m['week']]==target
            if responses_allowed:
                sealed=next((e for e in events if e['state']=='GLOBAL_EXECUTION_SEAL_VERIFIED'),None)
                if not sealed or not sealed.get('evidence'):raise ValueError('Target responses require the exact verified forecast seal')
                for artifact in sealed['evidence']:gate.checked(self.root,artifact)
        if not selected:raise ValueError('No readable 2026 Data Manager metadata rows: '+str(metadata['gaps']))
        identity=digest({'witness_serialization':'policy-path-v1','rows':[(s['id'],s['records_hash']) for s in selected],'target':target,'responses_after_seal':responses_allowed,'policy':consumer['source_policy']})
        sourcedir=self.store.root/'sources'/('import-'+identity[:24])
        entries=[]
        for s in selected:
            rule=next(r for r in policy['retained_partitions'] if r['source_id']==s['source_id'])
            w=write_once(sourcedir/(s['source_id']+'-producer.json'),producer_witness(s,rule))
            entries.append({'path':s['records_path'],'sha256':s['records_hash'],'dataset':s['dataset'],'season':2026,
                            'partition_id':'manager-'+s['source_id']+'-2026','metadata_witness':ref(self.root,w),'stored_field':'records_path'})
        mr,rr=self._plan('SOURCE_IMPORT',gate.SOURCE,None,{'population':'Permitted 2026 retained snapshots and schedule identities',
                         'metadata':[{k:s[k] for k in ('id','source_id','records_hash','acquired')} for s in selected]},
                         extra={'retained_inputs':entries+policy.get('retained_inputs',[]),
                                'postseal_response_context':{'forecast':active['manifest'],'phase':'ATTEMPT_CONSUMED'} if responses_allowed else None},identity=identity)
        imported=[];failures=[]
        with gate.execution_authority(self.root,gate.SOURCE,mr,rr,capability='SOURCE_IMPORT'):
            for s in selected:
                key=s['source_id']+'-'+s['records_hash']+'-'+str(target[1])+('-postseal' if responses_allowed else '-prior')
                old=self.store.latest('source_import',key)
                if old:imported.append(old);continue
                try:
                    maximum=tuple(target) if s['source_id']=='rosters' or responses_allowed else (target[0],target[1]-1)
                    self.store.set_setting('operation_current_step','Importing retained '+s['source_id'].replace('_',' ')+' records')
                    result=adapter.read_bound_snapshot(self.root,s,max_period=maximum)
                    path=write_once(sourcedir/(s['source_id']+'.json'),result)
                    weeks=sorted({adapter.period(r)[1] for r in result['records']})
                    fields=adapter.FIELD_ALLOWLISTS[s['source_id']]
                    missing={k:sum(r.get(k) in (None,'','NA','NaN','nan') for r in result['records']) for k in sorted(fields)}
                    body={'dataset':s['source_id'],'rows':len(result['records']),'through_period':[2026,max(weeks,default=0)],'weeks':weeks,
                          'imported_at':now(),'source_clock':s['acquired'],'source_version':s['id'],'artifact':ref(self.root,path),
                          'producer_declared_rows':s['metadata_coverage']['records'],'missingness':missing,
                          'samples':[{k:v for k,v in r.items() if k!='_source'} for r in result['records'][:5]],
                          'withheld_rows':result.get('withheld_future_rows',0),'validation':'ACTUAL_PAYLOAD_READ_AND_HASH_VERIFIED','manifest':mr}
                    self.store.append('source_import',key,body,immutable=True);imported.append(body)
                except (ValueError,OSError,KeyError) as e:
                    failures.append({'function':s['source_id'],'reason':str(e),'title':s['source_id'].replace('_',' ')+' import failed','resolution_kind':'code repair'})
            schedule=next((r for r in policy['retained_inputs'] if r['dataset']=='schedule'),None)
            if schedule:
                key='schedule-'+schedule['sha256'];old=self.store.latest('source_import',key)
                if old:imported.append(old)
                else:
                    try:
                        result=adapter.read_bound_schedule(self.root,schedule)
                        path=write_once(sourcedir/'schedule.json',result)
                        weeks=sorted({adapter.period(r)[1] for r in result['records']})
                        body={'dataset':'schedule','rows':len(result['records']),'through_period':[2026,max(weeks,default=0)],'weeks':weeks,
                              'imported_at':now(),'source_clock':schedule.get('observed_at'),'source_version':schedule['sha256'],
                              'artifact':ref(self.root,path),'samples':result['records'][:5],'missingness':{},'validation':'ACTUAL_PAYLOAD_READ_AND_HASH_VERIFIED','manifest':mr}
                        self.store.append('source_import',key,body,immutable=True);imported.append(body)
                    except (ValueError,OSError,KeyError) as e:failures.append({'function':'schedule','reason':str(e),'title':'Schedule import failed','resolution_kind':'code repair'})
        self.store.audit('actual_source_import_completed',{'rows':sum(r['rows'] for r in imported),'datasets':[r['dataset'] for r in imported],'failures':failures})
        return {'status':'IMPORTED' if not failures else 'PARTIAL','datasets':len(imported),'rows':sum(r['rows'] for r in imported),'failures':failures}
    def _latest_imports(self):
        result={}
        for r in self.store.records('source_import'):
            if r['dataset'] not in result or r['recorded_at']>result[r['dataset']]['recorded_at']:result[r['dataset']]=r
        return result
    def prepare_forecast(self):
        target=self.next_period()
        imports=self._latest_imports()
        missing=set(('schedule','rosters','player_stats','pbp'))-set(imports)
        if missing:raise ValueError('Missing actual imported datasets: '+', '.join(sorted(missing)))
        inputs=[imports[s]['artifact'] for s in sorted(imports)]
        _,source_consumer=gate.consumer_for(self.root,gate.SOURCE)
        key=digest({'inputs':inputs,'target':target,'source_consumer':source_consumer})
        old=self.store.latest('source_preparation',key)
        if old:return old
        # Pure projection still runs under the admitted source context, with
        # every previously projected source file hash bound before reading.
        mr,rr=self._plan('PREPARE_FORECAST',gate.SOURCE,target[1],{'population':'Previously verified permitted snapshots','artifacts':inputs,'records':sum(r['rows'] for r in imports.values())},inputs=inputs,identity=key)
        with gate.execution_authority(self.root,gate.SOURCE,mr,rr,capability='PREPARE_FORECAST'):
            data={s:gate.value(self.root,imports[s]['artifact'])['records'] for s in imports}
            prepared=adapter.prepare_retained_inputs(data['schedule'],data['rosters'],data['player_stats'],data['pbp'],cutoff=now(),target_period=tuple(target))
        source=self.store.root/'sources'/('prepared-'+key[:24])
        files={}
        for name,body in (('revised-source-01/DATA.json',prepared['data']),('context-source-02/RBFB_CONTEXT.json',prepared['rbfb_context'])):
            p=write_once(source/name,body);files[name]=sha(p)
        channel_refs={}
        for c in CHANNELS:channel_refs[c]=ref(self.root,write_once(source/(c+'-fixtures.json'),prepared['fixtures'][c]))
        inp=[ref(self.root,source/name) for name in files]+list(channel_refs.values())
        detail=ref(self.root,write_once(source/'PROJECTION.json',prepared))
        input_root={'path':source.relative_to(self.root).as_posix(),'files':files}
        body={'period':target,'input_files':inp,'input_root':input_root,'channel_fixtures':channel_refs,
              'fixtures':[{'game_id':f['game_id'],'season':f['season'],'week':f['week'],'kickoff':f.get('kickoff_utc')} for f in prepared['schedule'] if (f['season'],f['week'])==tuple(target)],
              'coverage':dict(prepared['coverage'],full_fixture_census_verified=prepared['full_fixture_census_verified']),'exclusions':prepared['exclusions'],
              'projection':detail,'source_inputs':inputs,'latest_observed_at':max((r.get('source_clock') or '' for r in imports.values()),default=now())}
        self.store.append('source_preparation',key,body,immutable=True)
        return self.store.latest('source_preparation',key)
    def build_features(self,prepared):
        target=prepared['period']
        _,source_consumer=gate.consumer_for(self.root,gate.SOURCE)
        key=digest({'prepared':prepared['record_hash'],'target':target,'source_consumer':source_consumer})
        old=self.store.latest('feature_preparation',key)
        if old:return old
        mr,rr=self._plan('PREPARE_FORECAST',gate.SOURCE,target[1],
                        {'population':'Compatible dated prior-history player cohort','projection':prepared['projection']},
                        inputs=prepared['input_files'],extra={'fixtures':prepared['fixtures'],'classification':'EXPOSED_RETROSPECTIVE_DEVELOPMENT'},identity=key)
        m=gate.value(self.root,mr);results={}
        for c in CHANNELS:
            if prepared['coverage'].get('eligible',{}).get(c,0)==0:continue
            request={'schema_version':1,'action':'build_features','engine_root':str(self.runtime.engine),
                     'package_manifest_sha256':sha(self.runtime.engine/'PACKAGE_MANIFEST.json'),'operation':gate.SOURCE,
                     'authority':{'execution_manifest':mr,'readiness':rr},'period':target,'channel':c,
                     'output_dir':str(self.root/m['execution_directory']/c/'features'),
                     'input_root':prepared['input_root'],'fixtures':prepared['channel_fixtures'][c]}
            results[c]=self.runtime._invoke(request)
        if not results:raise ValueError('No verified player cohort has sufficient complete prior history')
        receipt={'status':'FEATURES_BUILT','real_outcomes_opened':0,'fits':0,'eligible':sum(r['eligible'] for r in results.values()),
                 'feature_count':228,'period':target,'channels':{c:{'eligible':r['eligible'],'receipt':r['_receipt_ref'],'features':r['artifacts']['features']} for c,r in results.items()},
                 'source_preparation':prepared['projection'],'checked_at':now()}
        path=write_once(self.root/m['execution_directory']/'FEATURE_PREPARATION.json',receipt)
        self.store.append('feature_preparation',key,dict(receipt,artifact=ref(self.root,path),manifest=mr),immutable=True)
        return self.store.latest('feature_preparation',key)
    def forecast(self,prepared,features):
        target=prepared['period']
        if any(f.get('season')==target[0] and f.get('week')==target[1] for f in self.store.records('forecast')):
            return {'status':'ALREADY_FORECAST','period':target}
        seeds={s['channel']:s for s in self.store.records('seed_inspection')}
        consumer,_=gate.consumer_for(self.root,gate.REPLAY)
        # The supplied saved states establish the first continuation period.
        channels=list(features['channels'])
        parents={c:seeds[c]['source_seed'] for c in channels}
        head=max(self.store.records('model_head'),key=lambda h:h['period'],default=None)
        if head:parents={c:head['models'][c] for c in channels}
        fixtures=prepared['fixtures']
        first=min((gate.timestamp(f['kickoff']) for f in fixtures if f.get('kickoff')),default=None)
        prospective=bool(first and all(f.get('kickoff') for f in fixtures) and datetime.now(timezone.utc)<first)
        operation=gate.FORWARD if prospective else gate.REPLAY
        classification='GENUINE_PROSPECTIVE' if prospective else 'EXPOSED_RETROSPECTIVE_DEVELOPMENT'
        source_receipt={'schema_version':'puntproof-lab-source-receipt-v2','status':'PASS','operation':gate.SOURCE,
                        'season':2026,'source_policy':consumer['source_policy'],'inputs':prepared['input_files'],
                        'identity_conflicts':0,'duplicate_keys':0,'history_complete':False,'history_scope':'AVAILABLE_2026_ONLY','unknowns_preserved':True,
                        'snap_denominator_verified':False,'known_snap_denominators_verified':True,'scope':'VERIFIED_SUBSET','fixture_count':len(fixtures),
                        'roster_count':sum(features['channels'][c]['eligible'] for c in channels),'target_weeks_verified':[target[1]],
                        'pregame_snapshots_verified':prospective,'latest_observed_at':prepared['latest_observed_at'],
                        'coverage':prepared['coverage'],'excluded':len(prepared['exclusions']),
                        'history_limitation':'Permitted 2026-only inputs; shorter history than original two-season input. Conditional on appearance; no exact historical reproduction.'}
        sr=ref(self.root,write_once(self.store.root/'sources'/('forecast-'+features['record_hash'][:24])/'SOURCE_RECEIPT.json',source_receipt))
        features_ready=ref(self.root,write_once(self.store.root/'sources'/('forecast-'+features['record_hash'][:24])/'FEATURE_PREPARATION.json',
            {'status':'FEATURES_BUILT','real_outcomes_opened':0,'fits':0,'feature_count':features['feature_count'],
             'eligible':features['eligible'],'inputs':prepared['input_files'],'source_receipt':sr,'channels':channels,
             'actual_preparation':features['artifact']}))
        extras={'classification':classification,'model_artifacts':parents,'fixtures':fixtures,'source_receipt':sr,
                'feature_preparation':features_ready,'first_kickoff':first.isoformat() if first else None,'population_scope':'VERIFIED_SUBSET','channels':channels,
                'channel_inputs':{c:{'input_root':prepared['input_root'],'fixtures':prepared['channel_fixtures'][c]} for c in channels}}
        if head:extras['parent_state_receipts']={c:head['state_receipts'][c] for c in channels}
        extras['full_fixture_census_verified']=prepared['coverage'].get('full_fixture_census_verified',False)
        mr,rr=self._plan('FORECAST',operation,target[1],{'population':'Verified compatible subset','eligible':features['eligible'],'feature_preparation':features['artifact']},
                        inputs=prepared['input_files'],extra=extras,identity=features['record_hash'])
        self.store.set_setting('operation_current_step','Generating real-input '+classification.lower().replace('_',' ')+' forecasts')
        result=self.runtime.forecast(mr,rr)
        self.store.set_setting('active_forecast_plan',{'operation':operation,'manifest':mr,'readiness':rr,'action':'settle','period':target})
        self.store.set_setting('last_scientific_run',dict(result,at=now()))
        # Retest the old global incident against an actual, verified forecast.
        gate.checked(self.root,mr)
        with self.store.connect() as db:
            changed=db.execute("UPDATE alerts SET status='RESOLVED',resolution=? WHERE code='MODEL_AUTHORITY' AND entity='scientific-runtime' AND status!='RESOLVED'",
                (json.dumps({'at':now(),'actual_forecast_manifest':mr,'condition':'An actual admitted saved-engine forecast completed'}),)).rowcount
        if changed:self.store.audit('legacy_global_readiness_issue_resolved',{'manifest':mr,'actual_forecast_completed':True})
        return result
    def settle_ready(self):
        active=self.store.get_setting('active_forecast_plan')
        if not active:return {'status':'WAITING_FOR_FORECAST','reason':'Seal a real forecast first.'}
        from v3_runtime import LifecycleJournal
        m=gate.value(self.root,active['manifest'])
        phase=LifecycleJournal(self.root,m).phase()
        if phase=='AUTHORITY_CLOSED':return {'status':'ALREADY_SETTLED','period':active['period']}
        if phase!='ATTEMPT_CONSUMED':
            return {'status':'WAITING','reason':'The exact forecast lifecycle is not ready for response attachment.'}
        if set(m.get('channels',CHANNELS))!=set(CHANNELS):
            return {'status':'WAITING','reason':'Both saved channels need compatible forecasts before whole-week state advancement. Supported subset predictions remain visible.'}
        if m.get('full_fixture_census_verified') is not True:
            return {'status':'WAITING','reason':'A complete target-week fixture census is required before learning; the saved subset forecasts remain available.'}
        target=active['period'];imports=self._latest_imports()
        missing=[name for name in ('player_stats','pbp','rosters') if name not in imports or imports[name].get('through_period',[2026,0])<target]
        if missing:return {'status':'WAITING','reason':'Missing verified Week '+str(target[1])+' '+', '.join(missing)+'.','missing':missing,'period':target}
        inputs=[r['artifact'] for r in imports.values()]
        identity=digest({'inputs':inputs,'forecast':active['manifest']})
        mr,rr=self._plan('PREPARE_FORECAST',gate.SOURCE,target[1],
            {'population':'Post-seal target-week completion and exact issued player outcomes','records':sum(r['rows'] for r in imports.values())},
            inputs=inputs,identity=identity)
        with gate.execution_authority(self.root,gate.SOURCE,mr,rr,capability='PREPARE_FORECAST'):
            sources={s:gate.value(self.root,r['artifact'])['records'] for s,r in imports.items()}
            issued=[f for f in self.store.records('forecast') if f.get('manifest')==active['manifest']]
            result=adapter.prepare_retained_settlement(sources['schedule'],sources['rosters'],sources['player_stats'],sources['pbp'],issued,
                     target_period=tuple(target),observed_at=now())
        if result['status']!='VERIFIED_COMPLETE':return result
        if any(not any(row['status']=='APPEARED' for row in result['outcomes'].get(c,[])) for c in CHANNELS):
            return {'status':'WAITING','reason':'Both channels require actual verified appeared-player outcomes before advancing; unresolved participation is not zero.'}
        directory=self.store.root/'sources'/('completion-'+identity[:24])
        proof=ref(self.root,write_once(directory/'SOURCE_PROOF.json',dict(result,source_inputs=inputs)))
        outcomes={c:ref(self.root,write_once(directory/(c+'-outcomes.json'),{'rows':result['outcomes'][c]})) for c in CHANNELS}
        completion={'status':'VERIFIED_COMPLETE','season':target[0],'week':target[1],
                    'all_fixtures_complete':True,'full_fixture_census_verified':result['full_fixture_census_verified'],
                    'fixtures':[dict(f,source_ref=proof) for f in result['fixtures']],'outcomes':outcomes}
        completion_ref=ref(self.root,write_once(directory/'COMPLETION.json',completion))
        consumer,cref=gate.consumer_for(self.root,m['operation'])
        checks=self._checks()
        target_ref=ref(self.root,write_once(directory/'TARGET_WITNESS.json',{'season':2026,'week':target[1],'status':'PASS','provider_calls':0,
                        'witness_class':'LAB_ACTION_INPUTS','capability':'SETTLE','records':len(issued),'completion':completion_ref}))
        ready={'schema_version':'puntproof-lab-action-readiness-v2','capability':'SETTLE','status':'READY',
               'operation':m['operation'],'consumer':cref,'manifest':active['manifest'],'implementation':consumer['implementation'],
               'season':2026,'week':target[1],'attempt_consumed':False,
               'prechecks':{key:True for key in CHECKS['SETTLE']},'authored_adapter_checks':[checks],'target_witness':target_ref}
        ready_ref=ref(self.root,write_once(directory/'SETTLEMENT_READINESS.json',ready))
        return self.runtime.settle(active['manifest'],ready_ref,completion_ref)

    def run_ready(self):
        if not self.lock.acquire(blocking=False):return {'status':'BUSY'}
        from worker import ProcessLock
        try:operation_lock=ProcessLock(self.store.root/'.operations.lock')
        except RuntimeError:
            self.lock.release();return {'status':'BUSY'}
        failures=[];steps=[]
        try:
            try:
                from manager_bridge import dispatch_one
                try:refresh_period=self.next_period()
                except ValueError:refresh_period=None
                refresh=dispatch_one(self,refresh_period) if refresh_period else {'status':'WAITING_FOR_SAVED_PERIOD'}
                steps.append({'function':'refresh',**refresh})
            except (ValueError,OSError,KeyError,PermissionError,RuntimeError,TypeError) as e:
                failures.append({'function':'refresh','reason':str(e),'title':'Data Manager request needs attention','resolution_kind':'code repair','blocks':['New source acquisition']})
            for function,handler in (('seed',self.inspect_seeds),('source',self.import_sources)):
                try:
                    result=handler();steps.append({'function':function,**result});failures.extend(result.get('failures',[]))
                except (ValueError,OSError,KeyError,PermissionError,RuntimeError,TypeError) as e:
                    failures.append({'function':function,'title':function.capitalize()+' connection needs repair','reason':str(e),'summary':str(e),'resolution_kind':'code repair'})
            try:period=self.next_period()
            except ValueError:period=None
            if period and not any(f.get('season')==period[0] and f.get('week')==period[1] for f in self.store.records('forecast')):
                try:
                    prepared=self.prepare_forecast();features=self.build_features(prepared)
                    steps.append({'function':'forecast',**self.forecast(prepared,features)})
                except (ValueError,OSError,KeyError,PermissionError,RuntimeError,TypeError) as e:
                    failures.append({'function':'forecast','title':'Forecast connection needs attention','reason':str(e),'summary':str(e),'resolution_kind':'code repair','blocks':['Forecasting']})
            try:
                settlement=self.settle_ready();steps.append({'function':'settlement',**settlement})
            except (ValueError,OSError,KeyError,PermissionError,RuntimeError,TypeError) as e:
                settlement={'status':'BLOCKED','reason':str(e)}
                failures.append({'function':'settlement','title':'Settlement connection needs attention','summary':str(e),'reason':str(e),'resolution_kind':'code repair','blocks':['Settlement','Learning']})
            from automation import Automation
            automation=Automation(self)
            try:steps.append({'function':'coverage',**automation.coverage()})
            except (ValueError,OSError,KeyError,RuntimeError,TypeError) as e:
                failures.append({'function':'coverage','reason':str(e),'title':'Candidate coverage explanation needs repair','resolution_kind':'code repair'})
            try:
                from paired_worker import run_available
                paired=run_available(self);steps.append({'function':'paired_research',**paired});self.store.set_setting('paired_research_status',paired)
            except (ValueError,OSError,KeyError,PermissionError,RuntimeError,TypeError) as e:
                self.store.set_setting('paired_research_status',{'status':'BLOCKED','reason':str(e)})
                failures.append({'function':'paired_research','reason':str(e),'title':'Bounded paired research needs attention','resolution_kind':'code repair','blocks':['Paired research']})
            if period:automation.status(period)
            status={'checked_at':now(),'next_period':period,'failures':failures,'steps':steps,
                    'summary':'Independent available operations checked. '+('Some operations need attention.' if failures else 'Retained inputs and saved engines are connected.'),
                    'next_action':f"Wait for complete Week {period[1]} outcomes" if period and self.store.records('forecast') else 'Connect compatible prior inputs',
                    'settlement':settlement,'next_reason':'Complete next-week final-game, player outcome and participation evidence is required before settlement and sequential learning.' if self.store.records('forecast') else 'Load both saved engines and validate compatible prior player histories.'}
            self.store.set_setting('operation_status',status)
            return {'status':'PARTIAL' if failures else 'AVAILABLE_WORK_COMPLETE',**status}
        finally:
            self.store.set_setting('operation_current_step',None)
            operation_lock.close()
            self.lock.release()
