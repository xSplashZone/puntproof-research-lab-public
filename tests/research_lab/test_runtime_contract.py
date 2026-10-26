"""Authored runtime/bridge protocol checks; no supplied model imports or loads."""
import contextlib
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from core import Store
from v3_runtime import ScientificRuntime,LifecycleJournal,gate,write_once,ref,sha


class RuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name).resolve()
        self.store=Store(self.root/'data/private/research-lab');self.runtime=ScientificRuntime(self.store,self.root)
    def tearDown(self):self.temp.cleanup()

    def contract(self,action='forecast'):
        out=self.root/action;out.mkdir()
        request={'action':action,'channel':'receiving','period':[2026,7],
            'package_manifest_sha256':'a'*64,'parent_session':{'through_period':[2026,6]}}
        req=self.root/(action+'-request.json');write_once(req,request)
        names={'forecast':{'session':'session.joblib','forecast':'forecast.npz','predictions':'predictions.json'},
               'settle':{'session':'session.joblib'},'build_features':{'features':'features.npz'}}[action]
        artifacts={}
        for key,name in names.items():
            path=out/name;path.write_bytes(b'authored boundary bytes, not a real model')
            artifacts[key]={'path':str(path),'sha256':sha(path)}
        result={'schema_version':'puntproof-engine-bridge-result-v1','status':'PASS','action':action,
            'channel':'receiving','period':[2026,7],'request_sha256':sha(req),'engine_manifest_sha256':'a'*64,
            'provider_calls':0,'production':False,'artifacts':artifacts}
        if action in ('forecast','settle'):
            result.update(through_period=[2026,6] if action=='forecast' else [2026,7],
                pending_period=[2026,7] if action=='forecast' else None,
                receipt={'action':'FORECAST_SEALED' if action=='forecast' else 'SETTLED_AND_LEARNED','period':[2026,7]})
        return request,result,req,out

    def test_actual_bridge_artifact_shapes_all_actions(self):
        for action in ('forecast','settle','build_features'):
            self.runtime._validate_bridge_result(*self.contract(action))

    def test_foreign_receipts_and_capabilities_rejected(self):
        request,result,req,out=self.contract()
        for field,value in [('schema_version','other'),('action','settle'),('channel','rushing'),
            ('period',[2026,8]),('request_sha256','0'*64),('engine_manifest_sha256','0'*64),
            ('provider_calls',1),('provider_calls',False),('production',True),('through_period',[2026,7]),
            ('pending_period',None),('receipt',{'action':'SETTLED_AND_LEARNED','period':[2026,7]})]:
            with self.subTest(field=field),self.assertRaises(ValueError):
                self.runtime._validate_bridge_result(request,dict(result,**{field:value}),req,out)

    def test_artifact_omission_extra_renaming_and_mutation_rejected(self):
        request,result,req,out=self.contract()
        for artifacts in ({},dict(result['artifacts'],extra=result['artifacts']['session']),
            dict(result['artifacts'],session=result['artifacts']['forecast'])):
            with self.assertRaises(ValueError):
                self.runtime._validate_bridge_result(request,dict(result,artifacts=artifacts),req,out)
        (out/'session.joblib').write_bytes(b'changed authored bytes')
        with self.assertRaisesRegex(ValueError,'OUTPUT_CUSTODY'):
            self.runtime._validate_bridge_result(request,result,req,out)

    def test_invalid_response_never_reaches_scoring_or_learning(self):
        manifest={'operation':gate.REPLAY,'week':7,'season':2026,
            'execution_directory':'data/private/research-lab/executions/authored-contract',
            'classification':'EXPOSED_RETROSPECTIVE_DEVELOPMENT',
            'fixtures':[{'game_id':'AUTHORED','kickoff':'2020-01-01T00:00:00Z'}]}
        directory=self.root/manifest['execution_directory']
        mp=directory/'MANIFEST.json';write_once(mp,manifest)
        journal=LifecycleJournal(self.root,manifest)
        for state in ('PRETASK_VALID','INPUTS_VERIFIED','FORECAST_PACKAGES_SEALED','GLOBAL_EXECUTION_SEAL_VERIFIED','ATTEMPT_CONSUMED'):
            journal.append(state)
        outcomes={}
        for channel in ('receiving','rushing'):
            identity={'season':2026,'week':7,'game_id':'AUTHORED','gsis':'00-0000001',
                'team':'AAA','opponent':'BBB','position':'RB','channel':channel}
            write_once(directory/channel/'forecast'/'predictions.json',{'rows':[dict(identity,support=[0,1],pmf=[.5,.5])]})
            path=self.store.root/'sources'/(channel+'-outcomes.json')
            # This must fail while responses are open, before declaring them complete.
            write_once(path,[dict(identity,status='UNKNOWN',yards=0,opportunities=0)])
            outcomes[channel]=ref(self.root,path)
        source=self.store.root/'sources'/'final.json';write_once(source,{'authored':True})
        completion=self.store.root/'sources'/'completion.json'
        write_once(completion,{'status':'VERIFIED_COMPLETE','season':2026,'week':7,'all_fixtures_complete':True,
            'outcomes':outcomes,'fixtures':[{'game_id':'AUTHORED','kickoff':'2020-01-01T00:00:00Z',
                'completed_at':'2020-01-01T04:00:00Z','final':True,'source_ref':ref(self.root,source)}]})
        @contextlib.contextmanager
        def authored_authority(*args):yield manifest
        with (patch.object(gate,'execution_authority',authored_authority),patch.object(gate,'require_phase') as phase,
             patch.object(gate,'require_response_artifact'),patch('v3_runtime.score_rows') as score,
             patch.object(self.runtime,'_invoke') as invoke):
            with self.assertRaisesRegex(ValueError,'UNKNOWN_IS_NOT_ZERO'):
                self.runtime.settle(ref(self.root,mp),{},ref(self.root,completion))
            score.assert_not_called();invoke.assert_not_called()
            self.assertNotIn('score',[call.args[0] for call in phase.call_args_list])
            self.assertNotIn('learn',[call.args[0] for call in phase.call_args_list])
        self.assertEqual(journal.phase(),'RESPONSE_ACCESS_ENABLED')
        self.assertFalse((directory/'SCORES.json').exists())
        self.assertEqual(self.store.records('performance'),[])


if __name__=='__main__':unittest.main(verbosity=2)
