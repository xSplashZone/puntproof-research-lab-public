import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from paired_worker import candidate_spec,RECIPES
from experiments import validate_spec,ExperimentRegistry,evaluate_paired
from core import Store
from manager_bridge import dispatch_one,run_request

class PairedWorkerTests(unittest.TestCase):
    def plan(self):return {'forecast_manifest':{'path':'authored','sha256':'a'*64},'specification':{'path':'authored-spec','sha256':'b'*64},'week':7}
    def test_two_complete_specs_and_different_recipes(self):
        specs=[candidate_spec(self.plan(),n,'c'*64) for n in (1,2)]
        self.assertNotEqual(validate_spec(specs[0]),validate_spec(specs[1]))
        self.assertEqual([s['mechanism'] for s in specs],list(RECIPES))
        for s in specs:self.assertEqual(s['max_trials'],1);self.assertEqual(s['max_seconds'],300)
    def test_failed_attempt_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d));reg=ExperimentRegistry(store);spec=candidate_spec(self.plan(),1,'c'*64)
            w={'status':'VERIFIED_COMPLETE','fingerprint':'c'*64};review={'status':'NEW_UNCONSUMED_MECHANISM','consumed_equivalent':False,'fingerprint':'c'*64}
            attempt=reg.reserve(spec,whole_week_receipt=w,duplicate_review=review);reg.finish(attempt,{'decision':'FAILED','reason':'authored interruption'})
            with self.assertRaisesRegex(ValueError,'ALREADY_CONSUMED'):ExperimentRegistry(Store(Path(d))).reserve(spec,whole_week_receipt=w,duplicate_review=review)
    def test_no_source_grant_means_no_manager_import_or_call(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'config').mkdir();(root/'config/source_registry.json').write_text('{}')
            with self.assertRaisesRegex(PermissionError,'NOT_REGISTERED'):run_request(dict(source='pbp',season=2026,weeks=[7]),root,root)
    def test_component_source_has_no_fit_predict_or_forecast_call(self):
        import ast,inspect,paired_worker
        calls=[n.func.attr for n in ast.walk(ast.parse(inspect.getsource(paired_worker.export_components))) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
        self.assertFalse(set(calls)&{'fit','predict','forecast_week','settle_week','save_new'})

class PairedGateTests(unittest.TestCase):
    def setup_gate(self):
        from nfl_model import research_lab_gate as g
        from nfl_model.research_lab_pair_gate import POLICY,validate_pair_manifest
        ref=lambda p:dict(path=p,sha256='a'*64)
        root=Path.cwd();cref=ref(g.RUNTIME_CONSUMERS[g.DEVELOPMENT]);policy=ref(POLICY);mr=ref(g.RUNTIME+'executions/paired-authored/MANIFEST.json')
        origin_ref=ref(g.RUNTIME+'executions/forecast-authored/MANIFEST.json');origin=dict(season=2026,week=7,operation=g.REPLAY,consumer=ref('authored-old-consumer'),execution_directory=g.RUNTIME+'executions/forecast-authored')
        pending={c:ref(origin['execution_directory']+'/'+c+'/forecast/session.joblib') for c in ('receiving','rushing')}
        baseline={c:ref(origin['execution_directory']+'/'+c+'/forecast/predictions.json') for c in pending}
        spec_ref=ref('authored-spec');pt=ref('authored-pretask')
        m=dict(purpose='PAIRED_COMPARISON',operation=g.DEVELOPMENT,consumer=cref,policy=policy,season=2026,week=7,recipes=list(RECIPES),phase='PREPARE_SEALED_COMPONENTS',provider_calls=0,cost_cents=0,protected_access=False,fit=False,forecast_manifest=origin_ref,pending_sessions=pending,baseline=baseline,specification=spec_ref,pretask_satisfiability=pt)
        spec=dict(recipes=list(RECIPES),forecast_manifest=origin_ref,frozen=True,seed=20261027,max_seconds=300,duplicate_review=dict(status='NEW_UNCONSUMED_MECHANISM',consumed_equivalent=False,experiment_index=ref('authored-index')))
        docs={policy['path']:dict(recipes=list(RECIPES),maximum_candidates_per_week=2,fit=False,provider_calls=0),mr['path']:m,origin_ref['path']:origin,spec_ref['path']:spec}
        ev=[dict(state='FORECAST_PACKAGES_SEALED',pending_sessions=pending,evidence=list(pending.values())+list(baseline.values())),dict(state='GLOBAL_EXECUTION_SEAL_VERIFIED',evidence=list(baseline.values())),dict(state='ATTEMPT_CONSUMED')]
        return g,validate_pair_manifest,root,dict(automatic_paired_policy=policy),cref,mr,m,docs,ev
    def check(self,mutate=None):
        g,validate,root,c,cr,mr,m,docs,ev=self.setup_gate()
        if mutate:mutate(m,ev)
        with patch.object(g,'value',side_effect=lambda root,ref,**kw:docs[ref['path']]),patch.object(g,'checked',return_value=b'authored'),patch.object(g,'_events',return_value=(ev,'ATTEMPT_CONSUMED')):
            return validate(root,c,cr,mr)
    def test_exact_sealed_component_preparation(self):self.assertEqual(self.check()[0]['week'],7)
    def test_new_fit_denied(self):
        with self.assertRaisesRegex(ValueError,'PAIRED_CAPABILITIES'):self.check(lambda m,e:m.update(fit=True))
    def test_unsealed_population_denied(self):
        with self.assertRaisesRegex(ValueError,'ORIGINAL_SEAL'):self.check(lambda m,e:e.pop(1))
    def test_incomplete_week_cannot_evaluate(self):
        with self.assertRaisesRegex(ValueError,'COMPLETED_LEARNING'):self.check(lambda m,e:m.update(phase='EVALUATE_COMPLETE_WEEK'))
    def test_historical_backcasting_denied(self):
        with self.assertRaisesRegex(ValueError,'PAIRED_TARGET'):self.check(lambda m,e:m.update(week=3))
