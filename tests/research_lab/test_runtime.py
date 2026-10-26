import contextlib
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from core import Store
from v3_runtime import ScientificRuntime,LifecycleJournal,write_once,ref,gate,validate_execution_clock

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.store=Store(self.root/'data/private/research-lab');self.runtime=ScientificRuntime(self.store,self.root)
        self.m={'operation':gate.REPLAY,'week':7,'season':2026,'execution_directory':'data/private/research-lab/executions/authored','classification':'EXPOSED_RETROSPECTIVE_DEVELOPMENT','cutoff':'2020-01-01T00:00:00Z','as_of':'2020-01-01T00:00:00Z','fixtures':[{'game_id':'AUTHORED','kickoff':'2020-01-01T00:00:00Z'}]}
    def tearDown(self):self.temp.cleanup()
    def test_immutable_journal_and_lease(self):
        j=LifecycleJournal(self.root,self.m);j.append('PRETASK_VALID');j.append('INPUTS_VERIFIED')
        with self.assertRaises(ValueError):j.append('SCORING_COMPLETE')
        self.runtime._lease(self.m,'forecast')
        with self.assertRaisesRegex(ValueError,'ALREADY_CONSUMED'):self.runtime._lease(self.m,'forecast')
        with self.assertRaisesRegex(ValueError,'ALREADY_CONSUMED'):self.runtime._lease(dict(self.m,execution_directory='data/private/research-lab/executions/renamed'),'forecast')
        p=j.directory/'001.json';data=json.loads(p.read_bytes());data['at']='2026-01-01T01:00:00Z';p.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError,'LIFECYCLE_HASH'):j.records()
    def test_write_once_cannot_change_and_escape_denied(self):
        p=self.root/'x.json';write_once(p,{'v':1});write_once(p,{'v':1})
        with self.assertRaises(ValueError):write_once(p,{'v':2})
        with self.assertRaises(ValueError):LifecycleJournal(self.root,dict(self.m,execution_directory='../outside'))
    def test_future_observation_clock_denied(self):
        with self.assertRaisesRegex(ValueError,'FUTURE_EXECUTION'):
            validate_execution_clock(dict(self.m,as_of='2099-01-01T00:00:00Z'))
        validate_execution_clock(self.m)
    def test_no_manifest_no_scientific_access(self):
        s=self.runtime.status();self.assertFalse(s['ready'])
        with self.assertRaises(PermissionError):self.runtime.run_current()
    def test_same_orchestrator_full_authored_cycle_and_duplicate_denial(self):
        # The engine subprocess is authored here, never a real model or result.
        self.runtime.engine.mkdir(parents=True);write_once(self.runtime.engine/'PACKAGE_MANIFEST.json',{'authored':True})
        seeds={}
        for c in ('receiving','rushing'):
            p=self.root/'seeds'/f'{c}.bin';p.parent.mkdir(exist_ok=True);p.write_bytes(b'authored not a pickle');seeds[c]=ref(self.root,p)
        self.m['model_artifacts']=seeds
        self.m['channel_inputs']={c:{'input_root':{'path':'authored','files':{}},'fixtures':{'path':'authored','sha256':'0'*64}} for c in seeds}
        mp=self.root/self.m['execution_directory']/'MANIFEST.json';write_once(mp,self.m);mref=ref(self.root,mp)
        def row(c):return {'season':2026,'week':7,'game_id':'AUTHORED','gsis':'00-0000001','team':'AAA','opponent':'BBB','position':'RB','channel':c,'support':[-1,0,1],'pmf':[.25,.5,.25],'high_usage':True}
        def fake_bridge(request):
            out=Path(request['output_dir']);out.mkdir(parents=True)
            (out/'session.joblib').write_bytes(b'authored session, never unpickled')
            artifacts={'session':{'path':str(out/'session.joblib'),'sha256':hashlib.sha256((out/'session.joblib').read_bytes()).hexdigest()}}
            if request['action']=='forecast':
                (out/'forecast.npz').write_bytes(b'authored forecast, never decoded')
                write_once(out/'predictions.json',{'rows':[row(request['channel'])]})
                for k,n in [('forecast','forecast.npz'),('predictions','predictions.json')]:artifacts[k]={'path':str(out/n),'sha256':hashlib.sha256((out/n).read_bytes()).hexdigest()}
            return {'status':'PASS','artifacts':artifacts}
        @contextlib.contextmanager
        def authored_authority(*args):yield self.m
        consumer={'seed_artifacts':list(seeds.values()),'implementation':[]}
        with patch.object(gate,'execution_authority',authored_authority),patch.object(gate,'require_phase'),patch.object(gate,'require_response_artifact'),patch.object(gate,'consumer_for',return_value=(consumer,{})),patch.object(self.runtime,'_invoke',side_effect=fake_bridge):
            self.assertEqual(self.runtime.forecast(mref,{})['status'],'FORECAST_SEALED')
            self.assertEqual(len(self.store.records('forecast')),2)
            with self.assertRaises(ValueError):self.runtime.forecast(mref,{})
            outcomes={}
            for c in seeds:
                p=self.store.root/'sources'/f'{c}-outcomes.json';write_once(p,[dict({k:v for k,v in row(c).items() if k not in ('support','pmf','high_usage')},status='APPEARED',yards=0,opportunities=1)]);outcomes[c]=ref(self.root,p)
            source=self.store.root/'sources'/'final.json';write_once(source,{'authored':True})
            completion=self.store.root/'sources'/'completion.json';write_once(completion,{'status':'VERIFIED_COMPLETE','season':2026,'week':7,'all_fixtures_complete':True,'outcomes':outcomes,'fixtures':[{'game_id':'AUTHORED','kickoff':'2020-01-01T00:00:00Z','completed_at':'2020-01-01T05:00:00Z','final':True,'source_ref':ref(self.root,source)}]})
            result=self.runtime.settle(mref,{},ref(self.root,completion));self.assertEqual(result['status'],'SETTLED_AND_LEARNED')
            self.assertEqual(len(self.store.records('performance')),2);self.assertEqual(len(self.store.records('model_head')),1)
            with self.assertRaises(ValueError):self.runtime.settle(mref,{},ref(self.root,completion))
        self.assertEqual(LifecycleJournal(self.root,self.m).phase(),'AUTHORITY_CLOSED')
