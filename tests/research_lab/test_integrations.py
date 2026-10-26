"""Focused integration/worker checks with authored metadata only."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    from tools.research_lab.core import Store
    from tools.research_lab.integrations import inventory, sync_metadata, export_signed, import_signed, local_exchange_key
    from tools.research_lab.worker import Worker, ProcessLock
except ImportError:
    from core import Store
    from integrations import inventory, sync_metadata, export_signed, import_signed, local_exchange_key
    from worker import Worker, ProcessLock

class LabIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.repo=self.root/'source'; self.repo.mkdir()
        self.store=Store(self.root/'lab')
    def tearDown(self):
        self.temp.cleanup()
    def source_database(self):
        path=self.repo/'data/private/puntproof/data-manager/acquisitions.sqlite3'
        path.parent.mkdir(parents=True)
        db=sqlite3.connect(path)
        db.executescript('''
            CREATE TABLE settings(key TEXT,value TEXT);
            INSERT INTO settings VALUES('api_key','TOP_SECRET_NEVER_EXPORT');
            CREATE TABLE snapshots(source TEXT,dataset TEXT,season INTEGER,acquired TEXT,records INTEGER,metadata TEXT,raw_path TEXT);
            INSERT INTO snapshots VALUES('authored','weekly',2026,'2026-10-25T01:00:00Z',12,'SECRET_RAW_METADATA','never_open_raw.json');
            INSERT INTO snapshots VALUES('authored','weekly',2025,'2026-10-25T01:00:00Z',99,'PROTECTED_RESPONSE','protected.json');
            CREATE TABLE runs(source TEXT,dataset TEXT,season INTEGER,started TEXT,ended TEXT,status TEXT,error TEXT);
            INSERT INTO runs VALUES('authored','weekly',2026,'2026-10-25T00:00:00Z','2026-10-25T01:00:00Z','NEW','password=NO_PRINT');
            CREATE TABLE av_captures(provider TEXT,feed TEXT,observed TEXT,metadata TEXT);
            INSERT INTO av_captures VALUES('authored','fixture','2026-10-25T01:00:00Z','SECRET_FIXTURE_PAYLOAD');
            CREATE TABLE av_games(game_id TEXT,record TEXT);
            INSERT INTO av_games VALUES('TEST_GAME','SECRET_OUTCOME');
            PRAGMA user_version=1;
        ''')
        db.close()
        return path
    def test_metadata_read_only_preserves_database_and_excludes_secret_payloads(self):
        path=self.source_database(); before=hashlib.sha256(path.read_bytes()).hexdigest()
        result=inventory(self.repo)
        self.assertEqual(result['status'],'METADATA_CONNECTED')
        self.assertTrue(result['read_only'])
        self.assertEqual(result['fixture_metadata_count'],1)
        self.assertEqual(len(result['sources']),3)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
        encoded=json.dumps(result)
        for forbidden in ('TOP_SECRET','SECRET_RAW','PROTECTED_RESPONSE','SECRET_FIXTURE','SECRET_OUTCOME','NO_PRINT','never_open_raw'):
            self.assertNotIn(forbidden,encoded)
        self.assertTrue(next(x for x in result['sources'] if x.get('season')==2025)['protected'])
    def test_missing_source_is_not_an_empty_success(self):
        result=inventory(self.repo)
        self.assertEqual(result['status'],'UNAVAILABLE')
        self.assertEqual(result['gaps'][0]['code'],'DATA_ACCESS')
    def test_schema_drift_blocks_payload_projection(self):
        path=self.source_database()
        db=sqlite3.connect(path)
        try:
            db.execute('PRAGMA user_version=99'); db.commit()
        finally:
            db.close()
        result=inventory(self.repo)
        self.assertEqual(result['status'],'SCHEMA_BLOCKED')
        self.assertEqual(result['sources'],[])
    def test_sync_is_idempotent_on_unchanged_metadata(self):
        self.source_database()
        first=sync_metadata(self.store,self.repo)
        second=sync_metadata(self.store,self.repo)
        self.assertEqual(first['revisions_added'],3)
        self.assertEqual(second['revisions_added'],0)
        self.assertEqual(len(self.store.records('source')),3)
    def test_signed_import_round_trip_tamper_and_duplicate(self):
        self.store.append('source','fixture:source',{'source_id':'fixture:source','status':'AUTHORED_METADATA','mode':'METADATA_ONLY','model_use':False})
        key=b'k'*32; envelope=export_signed(self.store,key)
        target=Store(self.root/'target')
        self.assertEqual(import_signed(target,envelope,key)['status'],'IMPORTED')
        self.assertEqual(import_signed(target,envelope,key)['status'],'UNCHANGED')
        self.assertEqual(len(target.records('source')),1)
        changed=json.loads(json.dumps(envelope)); changed['payload']['records'][0]['body']['status']='ALTERED'
        with self.assertRaisesRegex(ValueError,'signature'):
            import_signed(target,changed,key)
        self.assertEqual(len(target.records('source')),1)
    def test_signed_import_rejects_science_and_secret_fields_before_write(self):
        import hmac
        try:
            from tools.research_lab.integrations import canonical
        except ImportError:
            from integrations import canonical
        self.store.append('source','fixture',{'source_id':'fixture','mode':'METADATA_ONLY','model_use':False})
        key=b'k'*32; envelope=export_signed(self.store,key)
        envelope['payload']['records'][0]['kind']='outcome'
        envelope['payload_sha256']=hashlib.sha256(canonical(envelope['payload'])).hexdigest()
        unsigned={k:v for k,v in envelope.items() if k!='signature'}
        envelope['signature']=hmac.new(key,canonical(unsigned),hashlib.sha256).hexdigest()
        target=Store(self.root/'target')
        with self.assertRaisesRegex(ValueError,'not admitted'):
            import_signed(target,envelope,key)
        self.assertEqual(target.records('outcome'),[])
        self.store.append('source','secret',{'source_id':'secret','mode':'METADATA_ONLY','model_use':False,'note':'password=do-not-export'})
        with self.assertRaisesRegex(ValueError,'Secret'):
            export_signed(self.store,key)
    def test_older_signed_export_is_rejected_after_newer_import(self):
        import hmac
        try:
            from tools.research_lab.integrations import canonical
        except ImportError:
            from integrations import canonical
        self.store.append('source','fixture',{'source_id':'fixture','mode':'METADATA_ONLY','model_use':False})
        key=b'k'*32; envelope=export_signed(self.store,key)
        target=Store(self.root/'target')
        import_signed(target,envelope,key)
        older=json.loads(json.dumps(envelope))
        older['payload']['created_at']='2020-01-01T00:00:00+00:00'
        older['payload_sha256']=hashlib.sha256(canonical(older['payload'])).hexdigest()
        unsigned={k:v for k,v in older.items() if k!='signature'}
        older['signature']=hmac.new(key,canonical(unsigned),hashlib.sha256).hexdigest()
        with self.assertRaisesRegex(ValueError,'Older signed export'):
            import_signed(target,older,key)

    def test_interrupted_signed_import_resumes_without_duplicate_revisions(self):
        self.store.append('source','fixture',{'source_id':'fixture','mode':'METADATA_ONLY','model_use':False})
        key=b'k'*32; envelope=export_signed(self.store,key)
        target=Store(self.root/'target')
        original_append=target.append
        def crash_before_receipt(kind,record_id,body,immutable=False):
            if kind=='exchange_receipt':raise OSError('Authored interrupted receipt')
            return original_append(kind,record_id,body,immutable=immutable)
        with patch.object(target,'append',side_effect=crash_before_receipt):
            with self.assertRaises(OSError):import_signed(target,envelope,key)
        self.assertEqual(import_signed(target,envelope,key)['status'],'IMPORTED')
        self.assertEqual(target.latest('source','fixture')['revision'],1)
        self.assertEqual(len(list((target.root/'exchanges').glob('*.json'))),1)
        self.assertEqual(list((target.root/'exchanges').glob('*.tmp')),[])

    def test_staleness_flags_only_current_season_and_preserves_source_revision(self):
        path=self.source_database()
        db=sqlite3.connect(path)
        try:
            db.execute("UPDATE snapshots SET season=2023,acquired='2023-09-01T00:00:00Z' WHERE season=2025")
            db.execute("INSERT INTO snapshots VALUES('authored','weekly',2024,'2024-09-01T00:00:00Z',10,'UNOPENED','unused')")
            db.commit()
        finally:db.close()
        as_of=dt.datetime(2026,10,30,tzinfo=dt.timezone.utc)
        first=sync_metadata(self.store,self.repo,as_of=as_of)
        stale=[x for x in self.store.alerts() if x['code']=='SOURCE_STALE']
        self.assertEqual(len(stale),2)
        self.assertTrue(all(x['detail']['season']==2026 for x in stale))
        self.assertFalse(any(x['entity'].endswith((':2023',':2024')) for x in stale))
        second=sync_metadata(self.store,self.repo,as_of=as_of+dt.timedelta(hours=1))
        self.assertEqual(second['revisions_added'],0)
        self.assertIn('does not authenticate historical pregame',next(x['detail']['clock_semantics'] for x in stale if x['entity']=='manager:authored:weekly:2026'))
        self.store.set_setting('source_stale_hours',200)
        sync_metadata(self.store,self.repo,as_of=as_of)
        # A fresher threshold/poll is not a tested backfill-impact resolution.
        self.assertTrue(all(x['status']!='RESOLVED' for x in self.store.alerts() if x['code']=='SOURCE_STALE'))

    def test_unknown_malformed_clock_and_no_snapshot_are_distinct(self):
        path=self.source_database()
        db=sqlite3.connect(path)
        try:
            db.execute('UPDATE snapshots SET acquired=NULL WHERE season=2026')
            db.execute("UPDATE av_captures SET observed='not-a-clock'")
            db.execute("INSERT INTO runs VALUES('missing','injuries',2026,'2026-10-25T00:00:00Z',NULL,'FAILED','UNOPENED')")
            db.commit()
        finally:db.close()
        sync_metadata(self.store,self.repo,as_of=dt.datetime(2026,10,30,tzinfo=dt.timezone.utc))
        alerts=self.store.alerts(); codes={x['code'] for x in alerts}
        self.assertTrue({'SOURCE_CLOCK_UNKNOWN','SOURCE_CLOCK_INVALID','RUN_LABEL_UNMATCHED'}<=codes)
        self.assertNotIn('SOURCE_STALE',codes)
        self.assertTrue(all(x['severity']=='P1' for x in alerts if x['code'] in {'SOURCE_CLOCK_UNKNOWN','SOURCE_CLOCK_INVALID'}))

    def test_key_is_persistent_and_missing_from_export(self):
        key=local_exchange_key(self.store)
        self.assertEqual(len(key),32)
        self.assertEqual(local_exchange_key(self.store),key)
        self.assertNotIn(key.hex(),json.dumps(export_signed(self.store,key)))

class LabWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        self.store=Store(self.root/'lab'); self.repo=self.root/'source'; self.repo.mkdir()
        self.worker=Worker(self.store,self.repo)
        self.store.set_setting('worker_next_epoch',time.time()+86400)
        self.good={'disk_free_mb':10000,'available_ram_mb':10000,'process_memory_mb':50,'cpu_percent':1,'idle_seconds':10000}
        self.worker.sampler.sample=lambda root:dict(self.good)
    def tearDown(self):
        self.worker.stop(); self.temp.cleanup()
    def test_job_queue_idempotence_and_science_denial(self):
        a=self.worker.enqueue('diagnostics',evidence_hash='same')
        b=self.worker.enqueue('diagnostics',evidence_hash='same')
        self.assertEqual(a,b)
        self.assertEqual(self.worker.enqueue('forecast',evidence_hash='same')['status'],'BLOCKED')
        with self.assertRaises(ValueError):self.worker.enqueue('shell')
        with self.assertRaises(ValueError):self.worker.enqueue('metadata',{'command':'unsafe'})
    def test_resource_limits_and_pause_do_not_consume_jobs(self):
        jid=self.worker.enqueue('diagnostics')['id']
        self.good['disk_free_mb']=1
        result=self.worker.run_once()
        self.assertEqual(result['status'],'WAITING_RESOURCES')
        row=self.store.rows('SELECT * FROM lab_jobs WHERE id=?',(jid,))[0]
        self.assertEqual(row['attempts'],0)
        self.good['disk_free_mb']=10000
        self.store.set_setting('worker_paused',True)
        self.assertIn('PAUSED_BY_USER',self.worker.run_once(force=True)['reasons'])
    def test_crash_recovery_retry_and_completion(self):
        jid=self.worker.enqueue('diagnostics')['id']
        with self.store.connect() as db:
            db.execute("UPDATE lab_jobs SET status='RUNNING',attempts=1 WHERE id=?",(jid,))
        lock=ProcessLock(self.store.root/'.worker.lock')
        try:self.assertEqual(self.worker.recover(),1)
        finally:lock.close()
        self.worker._handle=lambda kind:{'checks':[],'status':'PASS'}
        result=self.worker.run_once()
        self.assertEqual(result['status'],'SUCCEEDED')
        row=self.store.rows('SELECT * FROM lab_jobs WHERE id=?',(jid,))[0]
        self.assertEqual(row['attempts'],2)
        self.assertEqual(row['status'],'SUCCEEDED')
    def test_failure_budget_stops_retries_and_redacts_exception(self):
        jid=self.worker.enqueue('diagnostics')['id']
        def fail(kind):raise RuntimeError('password=SECRET_SHOULD_NOT_APPEAR')
        self.worker._handle=fail
        for attempt in range(3):
            with self.store.connect() as db:db.execute('UPDATE lab_jobs SET next_at=0 WHERE id=?',(jid,))
            result=self.worker.run_once()
        row=self.store.rows('SELECT * FROM lab_jobs WHERE id=?',(jid,))[0]
        self.assertEqual(row['status'],'FAILED')
        self.assertEqual(row['attempts'],3)
        self.assertNotIn('SECRET',row['error'])
        events=self.store.rows('SELECT * FROM audit WHERE action=?',('WORKER_JOB_FAILED',))
        self.assertEqual(len(events),3)
    def test_os_lock_prevents_second_worker(self):
        lock=ProcessLock(self.store.root/'.worker.lock')
        try:
            with self.assertRaisesRegex(RuntimeError,'already running'):
                ProcessLock(self.store.root/'.worker.lock')
        finally:lock.close()
        later=ProcessLock(self.store.root/'.worker.lock'); later.close()
    def test_proposal_only_on_changed_metadata_no_scientific_execution(self):
        first=self.worker._proposal(); second=self.worker._proposal()
        self.assertEqual(first['status'],'PROPOSAL_SAVED')
        self.assertEqual(second['status'],'UNCHANGED')
        record=self.store.latest('experiment',first['experiment_id'])
        self.assertEqual(record['fits'],0)
        self.assertEqual(record['status'],'BLOCKED')
        self.assertEqual(record['decision'],'No verified improvement')
    def test_worker_proposals_reference_frozen_knowledge_concepts(self):
        try:
            from tools.research_lab.knowledge import seed_knowledge
        except ImportError:
            from knowledge import seed_knowledge
        seed_knowledge(self.store)
        result=self.worker._proposal()
        self.assertEqual(result['knowledge_concept_ids'],['snap_share','zero_signed','measurement_revision','late_news','market_settlement'])
        self.assertEqual(len(result['knowledge_proposal_ids']),5)
        for identity in result['knowledge_proposal_ids']:
            proposal=self.store.latest('experiment',identity)
            self.assertEqual(proposal['status'],'BLOCKED')
            self.assertEqual(proposal['trials_attempted'],0)
            self.assertTrue(proposal['concept_sha256'])
        count=len(self.store.records('experiment'))
        self.worker._proposal()
        self.assertEqual(len(self.store.records('experiment')),count)

    def test_idle_return_does_not_nest_write_transaction(self):
        result=self.worker.run_once()
        self.assertEqual(result['status'],'IDLE')

if __name__=='__main__':
    unittest.main()
