"""Authored tests for independent capabilities, truthful counts and recovery."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from core import Store
from operations import Operations
from app_view import owner_state
from worker import Worker
from v3_runtime import ScientificRuntime

class IndependentOperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.store=Store(self.root/'data/private/research-lab')
        self.ops=Operations(self.store,self.root,self.root)
    def tearDown(self):self.tmp.cleanup()
    def test_source_failure_does_not_prevent_seed_inspection(self):
        with patch.object(self.ops,'inspect_seeds',return_value={'status':'SEEDS_LOADED'}) as seeds,patch.object(self.ops,'import_sources',side_effect=ValueError('source unavailable')),patch.object(self.ops,'next_period',side_effect=ValueError('authored no period')):
            result=self.ops.run_ready()
        seeds.assert_called_once()
        self.assertEqual(next(x for x in result['steps'] if x['function']=='seed')['status'],'SEEDS_LOADED')
        self.assertEqual(result['failures'][0]['function'],'source')
    def test_seed_failure_does_not_prevent_source_import(self):
        with patch.object(self.ops,'inspect_seeds',side_effect=ValueError('seed incompatible')),patch.object(self.ops,'import_sources',return_value={'status':'IMPORTED','rows':4}) as imported,patch.object(self.ops,'next_period',side_effect=ValueError('no loaded seed')):
            result=self.ops.run_ready()
        imported.assert_called_once()
        self.assertEqual(next(x for x in result['steps'] if x['function']=='source')['rows'],4)
        self.assertEqual(result['failures'][0]['function'],'seed')
    def test_actual_period_is_required_not_the_supplied_filename(self):
        with self.assertRaisesRegex(ValueError,'Both saved seeds'):self.ops.next_period()
        for c in ('receiving','rushing'):
            self.store.append('seed_inspection',c,{'channel':c,'through_period':[2026,8]})
        self.assertEqual(self.ops.next_period(),[2026,9])
        self.store.append('model_head','9',{'period':[2026,9]})
        self.assertEqual(self.ops.next_period(),[2026,10])
    def test_previous_comparisons_and_synthetic_runs_never_count_as_real_work(self):
        self.store.append('comparison','imported',{'n':9000})
        for kind in ('forecast','source_import','seed_inspection','performance','model_head','experiment_result'):
            self.store.append(kind,'synthetic',{'classification':'SYNTHETIC_ENGINEERING','rows':100,'cohorts':{'all':{'n':100}}})
        self.store.append('performance','comparison',{'classification':'IMPORTED_COMPARISON','cohorts':{'all':{'n':9000}}})
        self.store.append('experiment','proposal',{'status':'PROPOSED'})
        state=owner_state(self.store,{'checked_at':'2026-10-26T00:00:00Z'})
        self.assertEqual(state['home']['counts'],dict(seeds_loaded=0,source_records_validated=0,forecasts_generated=0,forecasts_settled=0,learning_updates=0,candidates_tested=0))
        self.assertEqual(state['home']['performance']['summary'],'Not measured yet')
    def test_source_versions_count_latest_verified_rows_not_all_revisions(self):
        for key,count in [('old',4),('new',7)]:
            self.store.append('source_import',key,{'dataset':'player_stats','rows':count})
        state=owner_state(self.store,{})
        self.assertEqual(state['home']['counts']['source_records_validated'],7)
    def test_resource_wait_names_actual_condition(self):
        self.store.set_setting('worker_status','WAITING_RESOURCES')
        self.store.set_setting('worker_resources',{'reasons':['CPU_BUSY'],'cpu_percent':90})
        state=owner_state(self.store,{})
        self.assertIn('CPU usage',state['home']['worker']['detail'])
        self.assertEqual(state['home']['worker']['resource_measurements']['cpu_percent'],90)
    def test_stopped_and_running_workers_do_not_reuse_old_resource_waits(self):
        self.store.set_setting('worker_resources',{'reasons':['CPU_BUSY'],'cpu_percent':90})
        self.store.set_setting('worker_retry_at','2026-10-27T00:00:00Z')
        self.store.set_setting('operation_current_step','Loading a saved engine')
        for status,label in [('STOPPED','Stopped'),('RUNNING:connect','Working'),('IDLE','Watching for new data')]:
            with self.subTest(status=status):
                self.store.set_setting('worker_status',status)
                worker=owner_state(self.store,{})['home']['worker']
                self.assertEqual(worker['label'],label)
                self.assertNotIn('CPU usage',worker['detail'])
                self.assertEqual(worker['resource_measurements']['cpu_percent'],90)
                if status=='STOPPED':
                    self.assertIn('No automatic checks',worker['detail'])
                    self.assertIsNone(worker['retry_at'])
        self.store.set_setting('worker_status','STOPPED')
        self.store.set_setting('worker_paused',True)
        self.assertEqual(owner_state(self.store,{})['home']['worker']['label'],'Stopped')
    def test_operation_failures_group_only_identical_explicit_reasons_without_losing_evidence(self):
        failures=[{'function':'source','reason':'exact missing binding','summary':'Source failed','blocks':['Source import'],'checked_at':'2026-10-26T01:00:00Z','evidence':{'path':'a'}},
                  {'function':'forecast','reason':'exact missing binding','summary':'Forecast failed','blocks':['Forecasting'],'owner_required':True,'evidence':{'path':'b'}},
                  {'function':'settlement','reason':'different missing binding','blocks':['Settlement']},
                  {'function':'seed','summary':'same generic summary'},
                  {'function':'seed','summary':'same generic summary'}]
        self.store.set_setting('operation_status',{'failures':failures,'checked_at':'2026-10-26T02:00:00Z'})
        issues=[i for i in owner_state(self.store,{})['attention'] if i['id'].startswith('operation-')]
        self.assertEqual(len(issues),4)
        grouped=next(i for i in issues if len(i['technical']['failures'])==2)
        self.assertEqual(grouped['technical']['failures'],failures[:2])
        self.assertEqual(grouped['blocks'],['Source import','Forecasting'])
        self.assertTrue(grouped['owner_required'])
        self.assertEqual([c['at'] for c in grouped['checks']],['2026-10-26T01:00:00Z','2026-10-26T02:00:00Z'])
        self.assertEqual(grouped['summary'],'exact missing binding')
    def test_acquisition_and_candidate_prerequisites_do_not_block_existing_inputs(self):
        self.store.append('source_import','stats',{'dataset':'player_stats','rows':7,'through_period':[2026,6]})
        self.store.append('forecast','real',{'classification':'EXPOSED_RETROSPECTIVE_DEVELOPMENT','season':2026,'week':7})
        state=owner_state(self.store,{})
        acquisition=next(i for i in state['attention'] if i['id']=='acquisition')
        self.assertEqual(acquisition['blocks'],['Acquisition of newer inputs'])
        self.assertEqual(acquisition['technical']['mode'],'READ_ONLY_POLLING')
        self.assertFalse(acquisition['technical']['provider_refresh_connector'])
        self.assertFalse(acquisition['technical']['provider_acquisition_grant'])
        self.assertIn('Existing records remain usable',acquisition['summary'])
        self.assertTrue(acquisition['owner_required'])
        candidate=next(i for i in state['attention'] if i['id']=='experiment')
        for required in ('frozen, approved and unconsumed','matched sealed control and candidate predictions','complete chronological outcomes'):
            self.assertIn(required,candidate['summary'])
        for function in ('source','forecast'):
            status=next(f for f in state['function_status'] if f['id']==function)
            self.assertEqual(status['status'],'COMPLETE')
            self.assertNotIn('acquisition',status['blocker_ids'])
    def test_second_process_cannot_start_an_overlapping_connection(self):
        from worker import ProcessLock
        held=ProcessLock(self.store.root/'.operations.lock')
        try:self.assertEqual(self.ops.run_ready()['status'],'BUSY')
        finally:held.close()
        self.assertFalse(self.ops.lock.locked())
    def test_pipeline_queue_is_deduplicated_by_evidence(self):
        worker=Worker(self.store,self.root)
        a=worker.enqueue('connect',evidence_hash='same')
        b=worker.enqueue('connect',evidence_hash='same')
        self.assertEqual(a,b)
        self.assertEqual(a['status'],'QUEUED')
    def test_backup_retains_real_execution_and_source_custody(self):
        for folder in ('sources','executions','admission'):
            p=self.store.root/folder/'authored.json';p.parent.mkdir();p.write_text('{"authored":true}',encoding='utf-8')
        archive=self.store.backup()
        restored=Store.restore(archive,self.root/'restored')
        for folder in ('sources','executions','admission'):
            self.assertTrue((restored.root/folder/'authored.json').is_file())

    def test_generated_action_plans_match_exact_gate_schema(self):
        from operations import FINISH,CHECKS
        from v3_runtime import gate,write_once,ref
        checks=write_once(self.root/FINISH/'ADAPTER_CHECKS.json',{'status':'PASS','same_adapter':True,'provider_calls':0,'real_outcomes_opened':0,
                            'passed_defects':sorted({v for values in CHECKS.values() for v in values}),'code_sha256':{}})
        write_once(self.root/FINISH/'REVIEW.json',{'authored':'not an execution review'})
        cref={'path':'research/authored/CONSUMER.json','sha256':'0'*64}
        consumer={'implementation':[],'source_policy':{'path':'research/authored/POLICY.json','sha256':'1'*64}}
        seeds={c:ref(self.root,write_once(self.root/'authored'/c,{'authored':'not a model'})) for c in ('receiving','rushing')}
        consumer['seed_channels']=seeds
        for capability,operation,week,extra in [
            ('SOURCE_IMPORT',gate.SOURCE,None,{'retained_inputs':[{'authored':True}]}),
            ('SEED_INSPECTION',gate.REPLAY,None,{'channels':['receiving','rushing'],'model_artifacts':seeds})]:
            with patch.object(gate,'consumer_for',return_value=(consumer,cref)),patch.object(self.ops,'_checks',return_value=ref(self.root,checks)):
                mr,rr=self.ops._plan(capability,operation,week,{'population':'Authored only','records':2},extra=extra)
            m,_,_=gate._finish_manifest(self.root,operation,consumer,cref,{gate.FAMILY:[mr]})
            ready=gate._finish_readiness(self.root,operation,consumer,cref,mr,m,rr)
            self.assertEqual(ready['capability'],capability)
            self.assertEqual(len(gate.value(self.root,m['pretask_satisfiability'])['predicates'][0]['known_defects']),len(CHECKS[capability]))

    def test_producer_witness_preserves_exact_policy_spelling_and_raw_row(self):
        from operations import producer_witness
        db=self.root/'producer'/'acquisitions.sqlite3'
        row={'id':'native','records_path':'native\\records.gz'}
        snapshot={'id':'native','source_id':'player_stats','dataset':'player_stats','season':2026,
                  'metadata_database':str(db),'metadata_row':row}
        rule={'source_id':'player_stats','dataset':'player_stats','season':2026,'metadata_database':db.as_posix()}
        witness=producer_witness(snapshot,rule)
        self.assertEqual(witness['metadata_database'],rule['metadata_database'])
        self.assertIs(witness['row'],row)
        self.assertEqual(snapshot['metadata_database'],str(db))
        with self.assertRaisesRegex(ValueError,'exact admitted partition'):
            producer_witness(snapshot,dict(rule,metadata_database=str(self.root/'other.sqlite3')))
    def test_check_receipt_follows_current_consumer_directory(self):
        import json
        from v3_runtime import gate,write_once
        old=self.root/'research/research_lab_v1/finish_v1/ADAPTER_CHECKS.json'
        new=self.root/'research/research_lab_v1/finish_v1/input_repair_v1/ADAPTER_CHECKS.json'
        write_once(old,{'status':'FAIL'})
        write_once(new,{'status':'PASS','code_sha256':{}})
        current={'path':'research/research_lab_v1/finish_v1/input_repair_v1/SOURCE_CONSUMER.json','sha256':'0'*64}
        with patch.object(gate,'consumer_for',return_value=({},current)),patch.object(gate,'value',side_effect=lambda root,r,**kw:json.loads((root/r['path']).read_bytes())):
            actual=self.ops._checks()
        self.assertEqual(actual['path'],new.relative_to(self.root).as_posix())
    def test_changed_source_consumer_invalidates_both_preparation_caches(self):
        from v3_runtime import gate
        imports={name:{'artifact':{'path':name,'sha256':'a'*64}} for name in ('schedule','rosters','player_stats','pbp')}
        source_keys=[];feature_keys=[]
        with patch.object(self.ops,'next_period',return_value=[2026,7]),patch.object(self.ops,'_latest_imports',return_value=imports):
            for h in ('a','b'):
                with patch.object(gate,'consumer_for',return_value=({},dict(path='research/consumer.json',sha256=h*64))),patch.object(self.store,'latest',side_effect=lambda kind,key:source_keys.append(key) or {'stored':True}):
                    self.ops.prepare_forecast()
                with patch.object(gate,'consumer_for',return_value=({},dict(path='research/consumer.json',sha256=h*64))),patch.object(self.store,'latest',side_effect=lambda kind,key:feature_keys.append(key) or {'stored':True}):
                    self.ops.build_features({'period':[2026,7],'record_hash':'same'})
        self.assertNotEqual(source_keys[0],source_keys[1])
        self.assertNotEqual(feature_keys[0],feature_keys[1])
