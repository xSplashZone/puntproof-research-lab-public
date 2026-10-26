"""Authored post-seal response custody canaries, with no real source/model access."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from nfl_model import research_lab_gate as gate

class ResponseCustodyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.manifest={'operation':gate.REPLAY,'execution_directory':gate.RUNTIME+'executions/authored',
            'week':7,'fixtures':[{'game_id':'authored-game','kickoff':'2026-10-01T00:00:00Z'}]}
        self.source=self.write(gate.RUNTIME+'sources/final.json',{'authored':True})
        self.outcomes={c:self.write(gate.RUNTIME+'sources/'+c+'.json',{'rows':[],'authored':True}) for c in ('receiving','rushing')}
        self.complete=self.write(gate.RUNTIME+'sources/completion.json',{'status':'VERIFIED_COMPLETE','season':2026,'week':7,'all_fixtures_complete':True,'outcomes':self.outcomes,
            'fixtures':[{'game_id':'authored-game','kickoff':'2026-10-01T00:00:00Z','completed_at':'2026-10-01T04:00:00Z','final':True,'source_ref':self.source}]})
        self.token=gate._ACTIVE.set({'root':self.root,'operation':gate.REPLAY,'manifest':self.manifest})
    def tearDown(self):gate._ACTIVE.reset(self.token);self.temp.cleanup()
    def write(self,path,value):
        p=self.root/path;p.parent.mkdir(parents=True,exist_ok=True);raw=gate.encoded(value);p.write_bytes(raw)
        return {'path':path,'sha256':hashlib.sha256(raw).hexdigest()}
    def lifecycle(self,count=6,outcomes=None):
        directory=self.root/self.manifest['execution_directory']/'lifecycle';directory.mkdir(parents=True,exist_ok=True)
        previous=None
        for i,state in enumerate(gate.MODEL_STATES[:count]):
            row={'sequence':i,'state':state,'previous_hash':previous,'at':'2026-10-01T04:00:00Z'}
            if state=='GLOBAL_EXECUTION_SEAL_VERIFIED':row['evidence']=[self.source]
            if state=='RESPONSE_ACCESS_ENABLED':row.update(completion=self.complete,outcomes=outcomes or self.outcomes)
            row['sha256']=hashlib.sha256(gate.encoded(row)).hexdigest();previous=row['sha256']
            (directory/f'{i:03}.json').write_bytes(gate.encoded(row))
    def test_response_requires_seal_and_exact_completion_outcome_binding(self):
        self.lifecycle();ref=self.outcomes['receiving']
        self.assertEqual(gate.require_response_artifact(self.root,self.root/ref['path'],ref['sha256'],'receiving'),ref)
        wrong=self.write(gate.RUNTIME+'sources/other.json',{'rows':[{'authored':True}]})
        self.lifecycle(outcomes={'receiving':wrong,'rushing':self.outcomes['rushing']})
        with self.assertRaisesRegex(ValueError,'COMPLETION_OUTCOME_BINDING'):
            gate.require_response_artifact(self.root,self.root/wrong['path'],wrong['sha256'],'receiving')
    def test_response_bytes_and_channel_cannot_be_substituted(self):
        self.lifecycle();ref=self.outcomes['receiving']
        with self.assertRaisesRegex(ValueError,'RESPONSE_ARTIFACT_CUSTODY'):
            gate.require_response_artifact(self.root,self.root/ref['path'],ref['sha256'],'rushing')
        (self.root/ref['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'HASH_DRIFT'):
            gate.require_response_artifact(self.root,self.root/ref['path'],ref['sha256'],'receiving')
    def test_response_cannot_open_at_consumption_before_access_event(self):
        self.lifecycle(count=5);ref=self.outcomes['receiving']
        with self.assertRaisesRegex(ValueError,'RESPONSE_ARTIFACT_PHASE'):
            gate.require_response_artifact(self.root,self.root/ref['path'],ref['sha256'],'receiving')

if __name__=='__main__':unittest.main()
