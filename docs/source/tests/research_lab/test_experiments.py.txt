import tempfile
from pathlib import Path
import unittest
from core import Store
from experiments import ExperimentRegistry,validate_spec,evaluate_paired

class ExperimentTests(unittest.TestCase):
    def spec(self,ordinal=1,mechanism='authored mechanism'):
        return dict(schema_version='puntproof-lab-candidate-spec-v1',frozen=True,hypothesis='Authored test only',mechanism=mechanism,features=['authored'],algorithm='SEALED_PAIRED_DISTRIBUTION_COMPARISON',hyperparameters={'fixed':True},seed=71,time_partitions={'prior':'earlier weeks','evaluate':'new whole weeks'},endpoint='paired_crps',matched_population='authored common fixtures',selection_rule='fixed before outcomes',uncertainty_rule='WHOLE_WEEK_97_5_MIN_8_WEEKS',duplicate_review={'path':'authored','sha256':'a'*64},max_trials=1,max_seconds=300,candidate_ordinal=ordinal,provider_calls=0,protected_access=False,week_fingerprint='b'*64,season=2026,week=7,coverage_policy='Exact issued common population; unknown and nonappearance excluded separately',multiple_testing_budget={'candidates':2,'channel_ci':.975},acceptance_rejection_criteria='CRPS upper CI below zero; no MAE/high-usage regression and n>=30 high usage',comparison_controls='Same players/games/timestamps')
    def test_duplicate_and_two_attempt_budget(self):
        with tempfile.TemporaryDirectory() as d:
            registry=ExperimentRegistry(Store(Path(d)))
            w={'status':'VERIFIED_COMPLETE','fingerprint':'b'*64};review={'status':'NEW_UNCONSUMED_MECHANISM','consumed_equivalent':False,'fingerprint':'b'*64}
            first=registry.reserve(self.spec(),whole_week_receipt=w,duplicate_review=review)
            with self.assertRaises(ValueError):registry.reserve(self.spec(),whole_week_receipt=w,duplicate_review=review)
            registry.finish(first,{'decision':'REJECT'})
            with self.assertRaises(ValueError):registry.finish(first,{'decision':'ACCEPT'})
            registry.reserve(self.spec(2,'second authored mechanism'),whole_week_receipt=w,duplicate_review=review)
            with self.assertRaises(ValueError):registry.reserve(self.spec(2,'third'),whole_week_receipt=w,duplicate_review=review)
    def test_known_mechanism_and_unknown_week_block(self):
        with tempfile.TemporaryDirectory() as d:
            registry=ExperimentRegistry(Store(Path(d)))
            with self.assertRaises(ValueError):registry.reserve(self.spec(),whole_week_receipt={'status':'UNKNOWN'},duplicate_review={})
        s=self.spec();s['max_trials']=2
        with self.assertRaises(ValueError):validate_spec(s)
    def test_regression_rejected_not_hidden(self):
        f={'season':2026,'week':7,'game_id':'AUTHORED','gsis':'00-0000001','team':'AAA','channel':'receiving','position':'WR','support':[0,1],'pmf':[1,0]}
        candidate=dict(f,pmf=[0,1]);outcome=dict(f,status='APPEARED',yards=0,opportunities=1)
        result=evaluate_paired(self.spec(),[f],[candidate],[outcome])
        self.assertEqual(result['decision'],'REJECT_REGRESSION');self.assertEqual(result['promotion'],'DISABLED')
