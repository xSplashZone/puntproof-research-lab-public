"""Authored persistence recovery controls; never reads supplied models or NFL data."""
import contextlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from core import Store
from v3_runtime import ScientificRuntime,LifecycleJournal,gate,write_once,ref,sha

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
        self.store=Store(self.root/'data/private/research-lab');self.runtime=ScientificRuntime(self.store,self.root)
        self.origin={'operation':gate.REPLAY,'season':2026,'week':7,
            'execution_directory':'data/private/research-lab/executions/original',
            'classification':'EXPOSED_RETROSPECTIVE_DEVELOPMENT','cutoff':'2020-01-01T00:00:00Z',
            'as_of':'2020-01-01T00:00:00Z','channels':['receiving','rushing'],
            'fixtures':[{'game_id':'AUTHORED','kickoff':'2020-01-01T00:00:00Z'}]}
        self.runtime.engine.mkdir(parents=True);write_once(self.runtime.engine/'PACKAGE_MANIFEST.json',{'authored':True})
        seeds={c:ref(self.root,write_once(self.root/'authored'/c,{'authored':'not a model'})) for c in self.origin['channels']}
        self.origin['model_artifacts']=seeds
        self.origin['channel_inputs']={c:{'input_root':{'path':'authored','files':{}},'fixtures':seeds[c]} for c in seeds}
        self.origin_ref=ref(self.root,write_once(self.root/self.origin['execution_directory']/'MANIFEST.json',self.origin))
        self.runtime._lease(self.origin,'forecast')
        self.journal=LifecycleJournal(self.root,self.origin)
        self.journal.append('PRETASK_VALID');self.journal.append('INPUTS_VERIFIED')
        self.prefix=[p.read_bytes() for p in sorted(self.journal.directory.glob('*.json'))]
        contract={'refs':{'original_manifest':self.origin_ref},'original_execution_directory':self.origin['execution_directory'],
                  'original_lease':ref(self.root,self.root/self.origin['execution_directory']/'FORECAST_LEASE.json')}
        cref=ref(self.root,write_once(self.root/'contract.json',contract))
        self.child=dict(self.origin,execution_directory='data/private/research-lab/executions/child',engineering_recovery=cref)
        self.child_ref=ref(self.root,write_once(self.root/self.child['execution_directory']/'MANIFEST.json',self.child))
        self.claim=self.root/self.origin['execution_directory']/'RECOVERY_CLAIM.json'
    def tearDown(self):self.tmp.cleanup()
    @contextlib.contextmanager
    def authority(self,*args,**kwargs):yield self.child
    def metadata(self,root,r,**kw):return json.loads((Path(root)/r['path']).read_bytes())
    def directory(self,root,m):return Path(root)/(self.origin['execution_directory'] if m.get('engineering_recovery') else m['execution_directory'])/'lifecycle'
    def fake_bridge(self,request):
        out=Path(request['output_dir']);out.mkdir(parents=True)
        (out/'session.joblib').write_bytes(b'authored not a model')
        (out/'forecast.npz').write_bytes(b'authored not a numerical archive')
        row={'season':2026,'week':7,'game_id':'AUTHORED','gsis':'AUTHORED','team':'AAA','opponent':'BBB',
             'channel':request['channel'],'support':[0,1],'pmf':[.5,.5]}
        write_once(out/'predictions.json',{'rows':[row]})
        result={'artifacts':{k:{'path':str(out/n),'sha256':sha(out/n)} for k,n in
                    [('session','session.joblib'),('forecast','forecast.npz'),('predictions','predictions.json')]}}
        result['_receipt_ref']=ref(self.root,write_once(out/'RESULT.json',{'authored':True}))
        return result
    @contextlib.contextmanager
    def patches(self,bridge=None):
        with (patch.object(gate,'execution_authority',self.authority),patch.object(gate,'value',side_effect=self.metadata),
              patch.object(gate,'lifecycle_directory',side_effect=self.directory),
              patch.object(gate,'require_forecast_recovery',return_value={'authored':True}),
              patch.object(self.runtime,'_invoke',side_effect=bridge or self.fake_bridge) as invoke):
            yield invoke
    def test_same_lease_and_journal_continue_once_with_exact_two_actions(self):
        with self.patches() as invoke:
            result=self.runtime.recover_forecast(self.child_ref,{})
            self.assertEqual(result['status'],'FORECAST_SEALED')
            self.assertEqual([(x.args[0]['channel'],x.args[0]['action']) for x in invoke.call_args_list],
                             [('receiving','recover_forecast'),('rushing','forecast')])
            with self.assertRaisesRegex(ValueError,'UNCONSUMED'):self.runtime.recover_forecast(self.child_ref,{})
        self.assertEqual(self.journal.phase(),'ATTEMPT_CONSUMED')
        self.assertEqual([p.read_bytes() for p in sorted(self.journal.directory.glob('*.json'))[:2]],self.prefix)
        self.assertEqual(len(self.store.rows('SELECT * FROM lab_scientific_leases')),1)
        self.assertEqual(self.store.rows('SELECT * FROM lab_scientific_leases')[0]['execution_directory'],self.origin['execution_directory'])
        self.assertEqual(len(self.store.records('forecast')),2)
        self.assertFalse((self.root/self.origin['execution_directory']/'rushing').exists())
    def test_exact_successor_claim_preserves_the_preinference_failure_claim(self):
        previous=write_once(self.claim,{'authored':'preserved rejected pre-inference claim'})
        previous_bytes=previous.read_bytes()
        contract=self.metadata(self.root,self.child['engineering_recovery'])
        contract['claim_filename']='RECOVERY_CLAIM_TYPE_REPAIR.json'
        new_ref=ref(self.root,write_once(self.root/'type-contract.json',contract))
        self.child=dict(self.child,engineering_recovery=new_ref,
                        execution_directory='data/private/research-lab/executions/type-child')
        self.child_ref=ref(self.root,write_once(self.root/self.child['execution_directory']/'MANIFEST.json',self.child))
        with self.patches():self.runtime.recover_forecast(self.child_ref,{})
        self.assertEqual(previous.read_bytes(),previous_bytes)
        self.assertTrue((self.claim.parent/'RECOVERY_CLAIM_TYPE_REPAIR.json').exists())
        self.assertEqual(len(self.store.rows('SELECT * FROM lab_scientific_leases')),1)
    def test_recovery_failure_never_releases_claim_or_lease_and_cannot_retry(self):
        with self.patches(bridge=lambda r:(_ for _ in ()).throw(ValueError('authored parity failure'))) as invoke:
            with self.assertRaisesRegex(ValueError,'parity failure'):self.runtime.recover_forecast(self.child_ref,{})
            with self.assertRaisesRegex(ValueError,'ALREADY_CLAIMED'):self.runtime.recover_forecast(self.child_ref,{})
            self.assertEqual(invoke.call_count,1)
        self.assertTrue(self.claim.exists())
        self.assertEqual(self.journal.phase(),'INPUTS_VERIFIED')
        self.assertEqual(len(self.store.rows('SELECT * FROM lab_scientific_leases')),1)
        self.assertEqual(self.store.records('forecast'),[])
    def test_recovery_requires_original_lease_identity(self):
        with self.store.connect() as db:db.execute("UPDATE lab_scientific_leases SET execution_directory='other'")
        with self.patches() as invoke:
            with self.assertRaisesRegex(ValueError,'ORIGINAL_GLOBAL_LEASE'):self.runtime.recover_forecast(self.child_ref,{})
            invoke.assert_not_called()
        self.assertFalse(self.claim.exists())
    def test_status_uses_saved_actual_execution_instead_of_legacy_missing_plan(self):
        self.store.set_setting('active_forecast_plan',{'manifest':self.origin_ref,'action':'settle'})
        for state in ('FORECAST_PACKAGES_SEALED','GLOBAL_EXECUTION_SEAL_VERIFIED','ATTEMPT_CONSUMED'):self.journal.append(state)
        self.store.set_setting('operation_status',{'settlement':{'reason':'Missing verified Week 7 player_stats, pbp.'}})
        with patch.object(gate,'consumer_for',return_value=({'expires_at':'2027-01-18'},{})):
            result=self.runtime.status(refresh=True)
        self.assertEqual(result['status'],'FORECAST_SEALED_AWAITING_OUTCOMES')
        self.assertEqual(result['reason'],'Missing verified Week 7 player_stats, pbp.')
        self.assertFalse(result['ready'])

if __name__=='__main__':unittest.main()
