"""Authored control canaries only; no sports sources, models or providers."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from nfl_model import research_lab_gate as gate

class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name).resolve()
    def tearDown(self):
        self.temp.cleanup()
    def write(self,path,value):
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True)
        b=gate.encoded(value);p.write_bytes(b)
        return {'path':path,'sha256':hashlib.sha256(b).hexdigest()}
    def events(self,operation,count):
        states=gate.FORWARD_STATES if operation==gate.FORWARD else gate.MODEL_STATES
        rows=[]
        for i,state in enumerate(states[:count]):
            r={'sequence':i,'state':state,'previous_hash':rows[-1]['sha256'] if rows else None,'at':'2026-10-01T00:00:00Z'}
            r['sha256']=hashlib.sha256(gate.encoded(r)).hexdigest();rows.append(r)
        return rows
    def put_events(self,manifest,events):
        directory=self.root/manifest['execution_directory']/'lifecycle';directory.mkdir(parents=True,exist_ok=True)
        for i,e in enumerate(events):(directory/f'{i:03}.json').write_bytes(gate.encoded(e))
    def reseal(self,events):
        for i,e in enumerate(events):
            e['previous_hash']=events[i-1]['sha256'] if i else None
            e['sha256']=hashlib.sha256(gate.encoded({k:v for k,v in e.items() if k!='sha256'})).hexdigest()
    def test_reference_paths_and_hashes_closed(self):
        for name in ('../outside','C:/outside','/outside','data//x','data/./x','data\\x'):
            with self.subTest(name=name),self.assertRaises(ValueError):gate.reference(self.root,{'path':name,'sha256':'a'*64})
        r=self.write('data/x.json',{'v':1});self.assertEqual(gate.value(self.root,r),{'v':1})
        (self.root/r['path']).write_text('{}')
        with self.assertRaisesRegex(ValueError,'HASH_DRIFT'):gate.checked(self.root,r)
    def test_committed_binding_rejects_uncommitted_bytes(self):
        r=self.write('code.py',{'authored':True})
        with patch.object(gate.subprocess,'run') as run:
            run.return_value.returncode=0;run.return_value.stdout=b'different'
            with self.assertRaisesRegex(ValueError,'COMMITTED_BINDING'):gate.checked(self.root,r,committed=True)
    def test_lifecycle_order_hash_and_duplicate_are_closed(self):
        rows=self.events(gate.FORWARD,len(gate.FORWARD_STATES))
        self.assertEqual(gate.validate_lifecycle_events(rows,gate.FORWARD),'AUTHORITY_CLOSED')
        bad=copy.deepcopy(rows);bad[2]['state']='RESPONSE_ACCESS_ENABLED'
        with self.assertRaisesRegex(ValueError,'LIFECYCLE_ORDER'):gate.validate_lifecycle_events(bad,gate.FORWARD)
        bad=copy.deepcopy(rows);bad[2]['at']='2026-10-02T00:00:00Z'
        with self.assertRaisesRegex(ValueError,'LIFECYCLE_HASH'):gate.validate_lifecycle_events(bad,gate.FORWARD)
        with self.assertRaises(ValueError):gate.validate_lifecycle_events(rows+[rows[-1]],gate.FORWARD)
    def test_live_charter_precedence_and_no_inferred_recurring_grant(self):
        self.assertIsNone(gate.scheduled_execution(self.root,gate.SOURCE))
        token=gate._ACTIVE.set({'root':self.root,'operation':gate.SOURCE})
        try:
            (self.root/'TASK_CHARTER.json').write_text('{}')
            with patch.object(gate,'consumer_for',side_effect=AssertionError('must not inspect recurring binding')):
                self.assertIsNone(gate.scheduled_execution(self.root,gate.SOURCE))
        finally:gate._ACTIVE.reset(token)
    def test_source_read_needs_exact_scope_before_access(self):
        source=self.root/'authored.json';source.write_text('{"synthetic":true}')
        sha=hashlib.sha256(source.read_bytes()).hexdigest()
        row={'path':str(source),'sha256':sha,'dataset':'player_stats','season':2026,'permission':'ADMITTED_RETAINED_SOURCE','protected':False,'provider_calls':0}
        policy=self.write('policy.json',{'retained_inputs':[row],'retained_partitions':[]})
        with self.assertRaisesRegex(ValueError,'SOURCE_CONTEXT_REQUIRED'):gate.require_retained_source(self.root,source,sha,'player_stats',2026)
        token=gate._ACTIVE.set({'root':self.root,'operation':gate.SOURCE,'source_policy':policy,'manifest':{'retained_inputs':[]}})
        try:
            self.assertEqual(gate.require_retained_source(self.root,source,sha,'player_stats',2026),row)
            with self.assertRaisesRegex(ValueError,'SOURCE_SEASON_DENIED'):gate.require_retained_source(self.root,source,sha,'player_stats',2025)
            with self.assertRaises(ValueError):gate.require_retained_source(self.root,source,sha,'pbp',2026)
            source.write_text('changed')
            with self.assertRaisesRegex(ValueError,'SOURCE_BYTES_CHANGED'):gate.require_retained_source(self.root,source,sha,'player_stats',2026)
        finally:gate._ACTIVE.reset(token)
    def test_dynamic_snapshot_requires_unchanged_producer_metadata(self):
        import sqlite3
        archive=self.root/'manager';archive.mkdir()
        source=archive/'record.json';source.write_text('{"synthetic":true}')
        sha=hashlib.sha256(source.read_bytes()).hexdigest()
        dbpath=archive/'acquisitions.sqlite3'
        row={'id':'fixture-snapshot','source':'rosters','dataset':'rosters','season':2026,'acquired':'2026-10-01T00:00:00Z','raw_path':str(source),'raw_hash':sha,'records_path':str(source),'records_hash':sha}
        c=sqlite3.connect(dbpath);c.execute('CREATE TABLE snapshots(id TEXT,source TEXT,dataset TEXT,season INTEGER,acquired TEXT,raw_path TEXT,raw_hash TEXT,records_path TEXT,records_hash TEXT)');c.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?,?,?,?)',tuple(row.values()));c.commit();c.close()
        witness=self.write('witness.json',{'schema_version':'puntproof-lab-producer-metadata-witness-v1','source':'rosters','dataset':'rosters','season':2026,'metadata_database':str(dbpath),'snapshot_id':row['id'],'row':row})
        rule={'id':'rosters-2026','dataset':'rosters','source_id':'rosters','season':2026,'permission':'ADMITTED_RETAINED_SOURCE','archive_root':str(archive),'metadata_database':str(dbpath),'allowed_fields':['gsis_id'],'clock_semantics':'OBSERVED_ONLY'}
        policy=self.write('policy.json',{'retained_inputs':[],'retained_partitions':[rule]})
        item={'path':str(source),'sha256':sha,'dataset':'rosters','season':2026,'partition_id':rule['id'],'metadata_witness':witness,'stored_field':'records_path'}
        token=gate._ACTIVE.set({'root':self.root,'operation':gate.SOURCE,'source_policy':policy,'manifest':{'retained_inputs':[item]}})
        try:
            self.assertEqual(gate.require_retained_source(self.root,source,sha,'rosters',2026)['clock_semantics'],'OBSERVED_ONLY')
            c=sqlite3.connect(dbpath);c.execute("UPDATE snapshots SET acquired='2026-10-02T00:00:00Z'");c.commit();c.close()
            with self.assertRaisesRegex(ValueError,'SOURCE_METADATA_ROW_CHANGED'):gate.require_retained_source(self.root,source,sha,'rosters',2026)
        finally:gate._ACTIVE.reset(token)
    def test_forecast_clock_and_phase(self):
        m={'operation':gate.FORWARD,'execution_directory':'data/private/research-lab/executions/test','first_kickoff':'2000-01-01T00:00:00Z'}
        self.put_events(m,self.events(gate.FORWARD,2))
        token=gate._ACTIVE.set({'root':self.root,'operation':gate.FORWARD,'manifest':m})
        try:
            with self.assertRaisesRegex(ValueError,'KICKOFF_PASSED'):gate.require_phase('forecast')
            with self.assertRaisesRegex(ValueError,'CAPABILITY_PHASE_DENIED'):gate.require_phase('response')
        finally:gate._ACTIVE.reset(token)
    def test_whole_week_completion_and_seal_integrity(self):
        manifest={'operation':gate.REPLAY,'week':7,'execution_directory':'data/private/research-lab/executions/test','fixtures':[{'game_id':'AUTHORED_GAME','kickoff':'2020-01-01T00:00:00Z'}]}
        sealed=self.write('data/private/research-lab/sources/sealed.json',{'authored_forecasts':[1,2]})
        source=self.write('data/private/research-lab/sources/final.json',{'authored_final':True})
        completion={'status':'VERIFIED_COMPLETE','season':2026,'week':7,'all_fixtures_complete':True,'fixtures':[{'game_id':'AUTHORED_GAME','kickoff':'2020-01-01T00:00:00Z','completed_at':'2020-01-01T05:00:00Z','final':True,'source_ref':source}]}
        cref=self.write('completion.json',completion)
        events=self.events(gate.REPLAY,6);events[3]['evidence']=[sealed];events[5]['completion']=cref;self.reseal(events);self.put_events(manifest,events)
        token=gate._ACTIVE.set({'root':self.root,'operation':gate.REPLAY,'manifest':manifest})
        try:
            self.assertEqual(gate.require_phase('response')['capability'],'response')
            (self.root/sealed['path']).write_text('tampered')
            with self.assertRaisesRegex(ValueError,'HASH_DRIFT'):gate.require_phase('response')
        finally:gate._ACTIVE.reset(token)
    def test_unknown_readiness_and_missing_canary_block(self):
        consumer={'implementation':[{'path':'code','sha256':'a'*64}]};ref={'path':'consumer','sha256':'b'*64};mref={'path':'manifest','sha256':'c'*64};manifest={'week':7}
        ready={'schema_version':'puntproof-lab-runner-readiness-v1','status':'UNKNOWN','operation':gate.SOURCE,'consumer':ref,'manifest':mref,'implementation':consumer['implementation']}
        r=self.write('ready.json',ready)
        with self.assertRaisesRegex(ValueError,'RUNNER_NOT_READY'):gate._readiness(self.root,gate.SOURCE,consumer,ref,mref,manifest,r)
        ready.update(status='READY',season=2026,week=7,attempt_consumed=False,target_week_witness=self.write('witness.json',{'season':2026,'week':7,'status':'PASS','records':1,'provider_calls':0}),authored_adapter_checks=[self.write('canary.json',{'status':'PASS','same_adapter':True,'provider_calls':0,'real_outcomes_opened':0,'passed_defects':list(gate.DEFECTS[:-1])})])
        r=self.write('ready.json',ready)
        with self.assertRaisesRegex(ValueError,'KNOWN_DEFECT_CANARY_MISSING'):gate._readiness(self.root,gate.SOURCE,consumer,ref,mref,manifest,r)

if __name__=='__main__':unittest.main(verbosity=2)
