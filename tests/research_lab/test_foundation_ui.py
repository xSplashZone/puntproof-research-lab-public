"""Authored integration checks: truth in saved evidence, not football validity claims."""
import json
from pathlib import Path
import tempfile
import unittest
from core import Store
from app_view import owner_state
from foundation_report import feature_health, audit_saved_laws, population_transition, law_statistics, owner_foundation
from publish_testing import public_state, public_foundation


class FoundationEvidenceTests(unittest.TestCase):
    def test_signed_law_crps_and_zero_mass_are_not_clipped(self):
        law={'support':[-2,0,4],'pmf':[.25,.5,.25]}
        value=law_statistics(law,0)
        self.assertAlmostEqual(value['crps'],.375)
        self.assertEqual(value['probabilities']['negative'],.25)
        self.assertEqual(value['probabilities']['zero'],.5)
        self.assertEqual(value['predicted_mean'],.5)

    def test_probability_failure_not_hidden_by_reasonable_interval_coverage(self):
        key=[2024,5,'game','person','team','receiving']
        report=audit_saved_laws([{'identity':key,'candidate':{'support':[-2,0,4],'pmf':[.25,.5,.25]}}],
            [{'identity':key,'actual':0,'high_usage':True,'unknown_history_count':1,'position':'WR',
              'frozen_control_scores':{'F':{'absolute_error':0,'crps':0}}}])
        self.assertEqual(report['matched_F']['n'],1)
        self.assertGreater(report['matched_F']['candidate_mae'],report['matched_F']['F_mae'])
        self.assertEqual(report['slices']['all']['events']['zero']['observed'],1)
        self.assertEqual(report['slices']['all']['coverage80'],1)
        self.assertEqual(report['probability_status'],'EXPERIMENTAL_UNVALIDATED')
        self.assertEqual(report['mismatches'],[])

    def test_invalid_saved_law_and_mismatched_identity_fail(self):
        with self.assertRaisesRegex(ValueError,'INVALID_SAVED_LAW'):
            law_statistics({'support':[0,1],'pmf':[.5,.4]},1)
        with self.assertRaisesRegex(ValueError,'LAW_SCORE_POPULATION_MISMATCH'):
            audit_saved_laws([], [{'identity':[1], 'actual':0}])

    def test_feature_presence_is_not_usable_variation(self):
        result=feature_health({'train':[{'context':None,'dnp':0,'yards':-2},{'context':None,'dnp':0,'yards':4}], 'forward':[]},['context','dnp','yards'])
        self.assertEqual(result['train']['context']['status'],'ALL_UNKNOWN')
        self.assertEqual(result['train']['dnp']['status'],'CONSTANT')
        self.assertEqual(result['train']['yards']['status'],'VARIES')
        self.assertEqual(result['forward']['yards']['status'],'NO_ROWS')

    def test_actual_population_changes_do_not_masquerade_as_model_gains(self):
        report=population_transition([{'identity':[1]},{'identity':[2]}],[{'identity':[2]},{'identity':[3]}])
        self.assertEqual([report[k] for k in ('before','after','intersection','newly_admitted','dropped')],[2,2,1,1,1])

    def test_sqlite_owner_readback_latest_version_and_matched_negative_result(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory))
            store.append('continuous_model','fit',{'recipe_id':'HGB','channel':'receiving',
                'frozen_controls':{'same_population_n':12,'candidate':{'mae':4,'crps':2},'F':{'mae':3,'crps':1}}})
            store.append('continuous_benchmark','seal',{'manifest':{'sha256':'a'},'forecasts':[{}, {}, {}]})
            for version,scored,unknown in [(1,1,2),(2,2,0)]:
                store.append('continuous_evaluation',str(version),{'forecast_manifest':{'sha256':'a'},
                    'evaluation_version':version,'scored_n':scored,'unknown_n':unknown,'verified_dnp_n':int(version==2)})
            store.append('experiment_result','comparison',{'classification':'REAL'})
            store.set_setting('manager_refresh_status',{'reason':'Approved manager bridge active'})
            state=owner_state(store,{})
            foundation=state['home']['foundation']
            self.assertEqual(foundation['matched_F'][0]['status'],'WORSE_THAN_F')
            self.assertEqual(foundation['observation'],{'issued':3,'scored':2,'pending':0,'unknown':0,'verified_dnp':1})
            self.assertNotIn('experiment',[issue['id'] for issue in state['attention']])
            text=json.dumps(state)
            self.assertNotIn('storage-only',text)
            self.assertNotIn('needs the bounded approval',text)
            state['home']['foundation']['dataset_health']={'status':'PARTIAL','raw_private_rows':'SECRET_RAW_ROW'}
            state['home']['foundation']['report_links']={'private':'SECRET_PRIVATE_PATH'}
            public=public_state(state,'authored')
            self.assertNotIn('SECRET_',json.dumps(public))
            self.assertEqual(public['home']['foundation']['matched_F'][0]['F_mae'],3)

class FoundationComparisonBasisTests(unittest.TestCase):
    def test_same_recipe_results_distinguish_verified_data_revision_and_export_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory))
            for attempt,error in [('old-attempt',4),('new-attempt',3.5)]:
                store.append('continuous_model',attempt,{'recipe_id':'individual_context_receiving','channel':'receiving',
                    'projection_file_sha256':'SECRET_PRIVATE_PROJECTION',
                    'frozen_controls':{'same_population_n':12,'candidate':{'mae':error},'F':{'mae':3}}})
            pending=owner_foundation(store)['matched_F']
            self.assertEqual(len(pending),2)
            self.assertEqual({r['comparison_basis'] for r in pending},{'Saved result — dataset shown in evidence'})
            store.set_setting('foundation_report',{'controlled_attempt_ids':['new-attempt'],
                'probability_audit':[{'recipe':'individual_context_receiving','channel':'receiving',
                    'comparison_basis':'Foundation repaired data','attempt':'SECRET_PRIVATE_ATTEMPT','all':{'n':12,'events':{}}}]})
            owner=owner_foundation(store)
            by_id={r['id']:r for r in owner['matched_F']}
            self.assertEqual(by_id['new-attempt']['comparison_basis'],'Foundation repaired data')
            self.assertEqual(by_id['old-attempt']['comparison_basis'],'Earlier saved result')
            self.assertEqual(by_id['new-attempt']['recipe'],by_id['old-attempt']['recipe'])
            self.assertEqual(by_id['new-attempt']['n'],12)
            self.assertEqual(by_id['new-attempt']['F_mae'],3)
            public=public_foundation(owner)
            self.assertEqual({r['comparison_basis'] for r in public['matched_F']},{'Foundation repaired data','Earlier saved result'})
            self.assertEqual(public['probability_audit'][0]['comparison_basis'],'Foundation repaired data')
            self.assertNotIn('SECRET_',json.dumps(public))
            self.assertNotIn('controlled_attempt_ids',public)
            self.assertTrue(all('id' not in r and 'projection_sha256' not in r for r in public['matched_F']))

    def test_unverified_or_unsafe_basis_is_not_inferred_or_publicly_copied(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory))
            store.append('continuous_model','new-attempt',{'recipe_id':'individual_context_receiving','channel':'receiving',
                'frozen_controls':{'same_population_n':2,'candidate':{'mae':4},'F':{'mae':3}}})
            store.set_setting('foundation_report',{'controlled_attempt_ids':'new-attempt'})
            self.assertEqual(owner_foundation(store)['matched_F'][0]['comparison_basis'],'Saved result — dataset shown in evidence')
            public=public_foundation({'matched_F':[{'comparison_basis':'SECRET_PRIVATE_PATH'}],
                'probability_audit':[{'comparison_basis':'SECRET_PRIVATE_PATH'}]})
            self.assertNotIn('SECRET_',json.dumps(public))
            self.assertEqual(public['matched_F'][0]['comparison_basis'],'Saved result — dataset shown in evidence')
            self.assertEqual(public['probability_audit'][0]['comparison_basis'],'Saved result — dataset shown in evidence')


    def test_failed_independent_verification_excludes_headlines_but_preserves_history(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory))
            for attempt in ('good-result','SECRET_INVALID_ATTEMPT'):
                store.append('continuous_model',attempt,{'recipe_id':'individual_context_receiving','channel':'receiving',
                    'frozen_controls':{'same_population_n':12,'candidate':{'mae':2},'F':{'mae':3}}})
            before=[r['record_hash'] for r in store.records('continuous_model')]
            store.set_setting('foundation_report',{'controlled_attempt_ids':['SECRET_INVALID_ATTEMPT'],
                'invalid_attempt_ids':['SECRET_INVALID_ATTEMPT'],'probability_metrics_verified':False,
                'probability_audit':[{'attempt':'SECRET_INVALID_ATTEMPT','recipe':'invalid headline','probability_metrics_verified':False},
                                     {'attempt':'good-result','recipe':'verified headline','probability_metrics_verified':True,'all':{'n':12}}]})
            owner=owner_foundation(store)
            self.assertEqual([r['id'] for r in owner['matched_F']],['good-result'])
            self.assertEqual([r['recipe'] for r in owner['probability_audit']],['verified headline'])
            self.assertEqual(owner['evidence_warnings']['invalid_result_count'],1)
            self.assertEqual(owner['evidence_warnings']['invalid_attempt_ids'],['SECRET_INVALID_ATTEMPT'])
            public=public_foundation(owner)
            self.assertEqual(public['evidence_warnings'],{'invalid_result_count':1})
            self.assertNotIn('SECRET_',json.dumps(public))
            self.assertEqual(len(store.records('continuous_model')),2)
            self.assertEqual([r['record_hash'] for r in store.records('continuous_model')],before)
            report=store.get_setting('foundation_report')
            report['probability_audit'][0]['probability_metrics_verified']=True
            store.set_setting('foundation_report',report)
            candidate_valid=owner_foundation(store)
            self.assertEqual([r['id'] for r in candidate_valid['matched_F']],['good-result'])
            self.assertEqual(len(candidate_valid['probability_audit']),2,
                'A bad F comparison must not hide independently verified candidate probability metrics')

    def test_verified_report_with_no_invalid_results_keeps_all_comparisons(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory))
            store.append('continuous_model','valid',{'recipe_id':'HGB','channel':'rushing',
                'frozen_controls':{'same_population_n':3,'candidate':{'mae':4},'F':{'mae':3}}})
            store.set_setting('foundation_report',{'invalid_attempt_ids':[],'probability_metrics_verified':True})
            owner=owner_foundation(store)
            self.assertEqual(len(owner['matched_F']),1)
            self.assertEqual(owner['matched_F'][0]['status'],'WORSE_THAN_F')
            self.assertEqual(owner['evidence_warnings']['invalid_result_count'],0)
            self.assertEqual(public_foundation(owner)['evidence_warnings'],{'invalid_result_count':0})


class PublicSnapshotCacheTests(unittest.TestCase):
    def test_saved_snapshot_hash_versions_request_and_preserves_git_source_bytes(self):
        import hashlib
        import zipfile
        from unittest.mock import patch
        import publish_testing as publisher
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'authored-repository'
            blobs={
                'tools/research_lab/static/index.html':b'<html><head><link href="/style.css"></head><body><script src="/app.js"></script></body></html>',
                'tools/research_lab/static/style.css':b'body { color: #123; }\n',
                'tools/research_lab/static/app.js':b"async function request(url,options={}){return url;}\nasync function act(action,fields={}){return action;}\n// Connected to your local Lab\n",
            }
            for relative,data in blobs.items():
                target=root/relative;target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(b'LOCAL UNCOMMITTED CONTENT MUST NOT BE EXPORTED')
            commit='a'*40
            head_reads=[]
            def git_output(command,**kwargs):
                self.assertEqual(kwargs['cwd'],root)
                if command[-2:]==['rev-parse','HEAD']:
                    head_reads.append(True)
                    return (commit if len(head_reads)==1 else 'b'*40)+'\n'
                self.assertEqual(command[-2],'show')
                revision,relative=command[-1].split(':',1)
                self.assertEqual(revision,commit)
                return blobs[relative]
            state={'home':{'counts':{'forecasts_generated':1}},'gamd':{}}
            references=[]
            with patch.object(publisher,'ROOT',root),patch.object(publisher.subprocess,'check_output',side_effect=git_output):
                for version in (1,2):
                    state['home']['counts']['forecasts_generated']=version
                    destination=Path(directory)/('export-'+str(version))
                    head_reads.clear()
                    publisher.build(destination,state,'Authored – fixed timestamp')
                    self.assertEqual(len(head_reads),1,'Custody must retain the commit used for the exported blobs')
                    dist=destination/'dist';saved=(dist/'snapshot.json').read_bytes()
                    expected=(json.dumps(publisher.public_state(state,'Authored – fixed timestamp'),indent=2,ensure_ascii=False)+'\n').encode('utf-8')
                    self.assertEqual(saved,expected,'The hashed and written snapshot bytes must be identical')
                    revision=hashlib.sha256(saved).hexdigest()[:16]
                    app=(dist/'app.js').read_text(encoding='utf-8')
                    self.assertIn("url='./snapshot.json?v="+revision+"';",app)
                    references.append(revision)
                    index=(dist/'index.html').read_text(encoding='utf-8')
                    for asset in ('app.js','style.css'):
                        asset_hash=hashlib.sha256((dist/asset).read_bytes()).hexdigest()[:16]
                        self.assertIn('./'+asset+'?v='+asset_hash,index)
                    with zipfile.ZipFile(dist/'source/research-lab-source.zip') as archive:
                        self.assertEqual(archive.read('tools/research_lab/static/app.js'),blobs['tools/research_lab/static/app.js'])
                    self.assertNotIn('LOCAL UNCOMMITTED',app)
                    custody=json.loads((dist/'source/export-custody.json').read_bytes())
                    self.assertTrue(custody['canonical_committed_blobs'])
                    self.assertEqual(custody['private_source_commit'],commit)
            self.assertNotEqual(*references,'Changed public state must receive a different snapshot cache key')


class OwnerExecutionActivityTests(unittest.TestCase):
    def test_only_completed_linked_recovery_is_separate_from_retained_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory));store.set_setting('operation_status',{'failures':[{'function':'forecast','reason':'FORWARD_TIMEOUT_NO_SEAL'}]})
            ref=lambda name:{'path':'evidence/'+name,'sha256':'a'*64}
            body={'schema':'PUNTPROOF_FORWARD_RECOVERY_V1','status':'PREPARED','original_manifest':ref('MANIFEST.json'),
                'original_claim':ref('CLAIM.json'),'timeout_evidence':ref('TIMEOUT.json'),'os_standby_evidence':ref('STANDBY.json'),
                'recovery_manifest':ref('RECOVERY_MANIFEST.json'),'recovery_seal':ref('SEAL.json'),
                'original_files_unchanged':True,'old_claim_modified':False,'old_failure_retained':True,'fits':0,'provider_calls':0,
                'input_revision':'same-input','recovered_at':'2026-10-28T00:00:00Z','forecasts':26}
            store.append('continuous_forward_recovery','a'*64,body)
            baseline=owner_state(store,{})
            self.assertFalse(any(r['id']=='forward_recovery' for r in baseline['function_status']))
            store.append('continuous_forward_recovery','a'*64,{**body,'status':'COMPLETED','old_claim_modified':True})
            self.assertFalse(any(r['id']=='forward_recovery' for r in owner_state(store,{})['function_status']))
            store.append('continuous_forward_recovery','a'*64,{**body,'status':'COMPLETED'})
            current=owner_state(store,{})
            recovery=next(r for r in current['function_status'] if r['id']=='forward_recovery')
            self.assertEqual(recovery['status'],'COMPLETE');self.assertIn('26 forecasts saved',recovery['summary'])
            self.assertIn('not a fit or a learning update',recovery['summary'])
            self.assertEqual(current['home']['counts'],baseline['home']['counts'])
            self.assertEqual([r['id'] for r in current['attention']],[r['id'] for r in baseline['attention']])
            self.assertEqual(store.get_setting('operation_status')['failures'][0]['reason'],'FORWARD_TIMEOUT_NO_SEAL')

    def test_actual_activity_overrides_stale_idle_without_inventing_results(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory));store.set_setting('worker_status','IDLE')
            store.set_setting('continuous_runtime',{'status':'IDLE','next_action':'No supported work'})
            store.set_setting('operation_status',{'summary':'Waiting for new records','failures':[{'function':'source','reason':'Retained source still incomplete'}]})
            idle=owner_state(store,{})
            before=[dict(r) for r in store.rows('SELECT * FROM settings ORDER BY key')]
            busy=owner_state(store,{},execution_busy=True)
            self.assertEqual(busy['home']['worker']['label'],'Working')
            self.assertTrue(busy['home']['worker']['execution_busy'])
            self.assertEqual(busy['home']['worker']['saved_status'],'IDLE')
            self.assertEqual(busy['home']['next']['label'],'Current work is running')
            self.assertEqual(busy['home']['counts'],idle['home']['counts'])
            self.assertEqual(busy['home']['foundation']['observation'],idle['home']['foundation']['observation'])
            self.assertEqual([r['id'] for r in busy['attention']],[r['id'] for r in idle['attention']])
            self.assertEqual(before,[dict(r) for r in store.rows('SELECT * FROM settings ORDER BY key')])
            store.set_setting('worker_paused',True)
            paused=owner_state(store,{},execution_busy=True)
            self.assertEqual(paused['home']['worker']['label'],'Finishing current work')
            self.assertTrue(paused['home']['worker']['paused']);self.assertIn('paused',paused['home']['next']['reason'])
            store.set_setting('worker_paused',False);store.set_setting('worker_status','ERROR:retained failure')
            failed=owner_state(store,{},execution_busy=True)
            self.assertEqual(failed['home']['worker']['saved_status'],'ERROR:retained failure')
            self.assertIn('error still needs investigation',failed['home']['worker']['detail'])
            self.assertEqual(failed['home']['counts'],idle['home']['counts'])

    def test_application_state_samples_existing_lock_without_starting_worker(self):
        from threading import Lock
        from types import SimpleNamespace
        from unittest.mock import patch
        from server import Application
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory));store.set_setting('worker_status','IDLE')
            raw={'status':'SEALED_PREGAME_WITH_GAPS','forecasts':2,'seals':1,'games':1,'current_horizon_seals':1,
                 'problems':[{'reason':'FORWARD_TIMEOUT_NO_SEAL'}]}
            store.set_setting('continuous_forward',raw)
            for number,size in ((1,2),(2,1)):
                store.append('continuous_benchmark','seal-'+str(number),{'manifest':{'sha256':str(number)*64},
                    'forecasts':[{'identity':[2026,8,'game','player','TEAM','receiving']} for _ in range(size)]})
            ref=lambda name:{'path':'evidence/'+name,'sha256':'a'*64}
            store.append('continuous_forward_recovery','a'*64,{'schema':'PUNTPROOF_FORWARD_RECOVERY_V1','status':'COMPLETED',
                'original_manifest':ref('MANIFEST.json'),'original_claim':ref('CLAIM.json'),
                'timeout_evidence':ref('TIMEOUT.json'),'os_standby_evidence':ref('STANDBY.json'),
                'recovery_manifest':ref('RECOVERY_MANIFEST.json'),'recovery_seal':ref('SEAL.json'),
                'original_files_unchanged':True,'old_claim_modified':False,'old_failure_retained':True,
                'fits':0,'provider_calls':0,'input_revision':'same-input','recovered_at':'2026-10-28T00:00:00Z','forecasts':1})
            app=Application.__new__(Application);app.store=store;app.source_repo=Path(directory)
            lock=Lock();app.worker=SimpleNamespace(run_lock=lock,runtime=SimpleNamespace(status=lambda:{'status':'READY','registered':True,'installed':True}))
            original_status={'continuous':{'forward':raw}}
            app.gamd=SimpleNamespace(status=lambda:original_status)
            with patch('game_shadow.observation_views',return_value=[]),patch('gamd.forward.status',return_value={}):
                idle=app.state()
                lock.acquire()
                try:busy=app.state()
                finally:lock.release()
                resumed=app.state()
            self.assertFalse(idle['home']['worker']['execution_busy']);self.assertTrue(busy['home']['worker']['execution_busy'])
            self.assertFalse(resumed['home']['worker']['execution_busy'])
            self.assertEqual(busy['home']['worker']['label'],'Working')
            self.assertEqual(idle['home']['counts'],busy['home']['counts'])
            self.assertEqual(busy['home']['counts'],resumed['home']['counts'])
            forward=busy['gamd']['continuous']['forward']
            self.assertEqual((forward['forecasts'],forward['seals'],forward['games']),(3,2,1))
            self.assertEqual(forward['forecasts'],busy['home']['foundation']['observation']['issued'])
            self.assertEqual(forward['count_basis'],'ALL_SAVED_IMMUTABLE_FORECAST_VERSIONS')
            self.assertEqual((forward['completed_recoveries'],forward['recovered_forecasts']),(1,1))
            self.assertEqual(forward['batch_snapshot'],raw);self.assertEqual(forward['problems'],raw['problems'])
            self.assertEqual(forward['current_horizon_seals'],1)
            self.assertEqual(store.get_setting('continuous_forward'),raw);self.assertEqual(original_status,{'continuous':{'forward':raw}})


if __name__=='__main__':unittest.main()
