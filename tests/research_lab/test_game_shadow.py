import sys, tempfile, unittest, json, hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/research_lab'))
from game_shadow import probabilities,evaluate_scored_outputs,refresh_evaluations,observation_views,verified,ref
from core import Store,encoded
from monitoring import metrics

class GameShadowTests(unittest.TestCase):
    def fixture(self):
        f=dict(season=2026,week=7,game_id='AUTHORED',gsis='p1',team='DAL',opponent='PHI',channel='receiving',name='Authored Player',support=[-1,0,1],pmf=[.25,.5,.25],mean=0.,median=0,high_usage=True)
        return {'game_id':'AUTHORED','classification':'PROSPECTIVE_GAME_SPECIFIC_OBSERVATION','forecasts':[{'original':f,'distribution':probabilities(f)}]},f
    def test_signed_distribution_thresholds_push_and_tails(self):
        m,f=self.fixture();d=probabilities(f);zero=next(t for t in d['thresholds'] if t['line']==0)
        self.assertEqual((zero['under'],zero['push'],zero['over']),(.25,.5,.25))
        half=next(t for t in d['thresholds'] if t['line']==.5)
        self.assertEqual((half['under'],half['push'],half['over']),(.75,0,.25))
        self.assertTrue(all(abs(t['over']+t['push']+t['under']-1)<1e-12 for t in d['thresholds']))
        with self.assertRaises(ValueError):probabilities(dict(f,mean=100))
        with self.assertRaises(ValueError):probabilities(dict(f,pmf=[.5,.5,.5]))
    def test_actual_zero_is_scored_but_absent_is_never_zero(self):
        m,f=self.fixture();identity=[2026,7,'AUTHORED','p1','DAL','receiving']
        r=evaluate_scored_outputs(m,[{'identity':identity,**metrics(f['support'],f['pmf'],0)}])
        self.assertEqual(r['scored'],1);self.assertEqual(r['rows'][0]['actual_yards'],0);self.assertAlmostEqual(r['rows'][0]['crps'],.125);self.assertIsNone(r['paper_roi'])
        missing=evaluate_scored_outputs(m,[]);self.assertEqual(missing['scored'],0);self.assertIsNone(missing['mean_absolute_error']);self.assertEqual(len(missing['missing_players']),1)
    def test_wrong_identity_and_mean_do_not_enter_result(self):
        m,f=self.fixture();row={'identity':[2026,7,'OTHER','p1','DAL','receiving'],**metrics(f['support'],f['pmf'],1)}
        self.assertEqual(evaluate_scored_outputs(m,[row])['scored'],0)
        row['identity'][2]='AUTHORED';row['mean']=12
        with self.assertRaises(ValueError):evaluate_scored_outputs(m,[row])
        with self.assertRaises(ValueError):evaluate_scored_outputs(m,[row,row])
    def test_fixed_grid_covers_outside_support_and_observed_surprise(self):
        m,f=self.fixture();row={'identity':[2026,7,'AUTHORED','p1','DAL','receiving'],**metrics(f['support'],f['pmf'],20)}
        r=evaluate_scored_outputs(m,[row])['rows'][0]
        self.assertEqual(len(r['fixed_thresholds']),15);self.assertEqual(r['fixed_thresholds'][0]['brier'],1);self.assertEqual(r['covered90'],0);self.assertEqual(r['surprise'],'Outside sealed 90% prediction interval')
        self.assertGreater(r['threshold_log_loss'],0)
    def test_verified_reference_rejects_changes_and_escape(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'x.json';p.write_text('{}');r=ref(root,p);verified(root,r);p.write_text('{"changed":true}')
            with self.assertRaises(ValueError):verified(root,r)
            with self.assertRaises(ValueError):verified(root,{'path':'../other.json','sha256':'0'*64})
    def test_automatic_report_waits_for_closed_learning_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);store=Store(root/'store');m,f=self.fixture();(root/'source').mkdir();orig=root/'source/MANIFEST.json';orig.write_text('{}');m['original_manifest']=ref(root,orig)
            manifest=root/'OBSERVATION.json';manifest.write_bytes(encoded(m));obs={'id':'AUTHORED','name':'Authored','game_id':'AUTHORED','manifest':ref(root,manifest),'forecast_ids':[]}
            store.append('game_observation','AUTHORED',obs,immutable=True)
            self.assertEqual(refresh_evaluations(store,root)['completed'],[])
            scores={'receiving':{'rows':[{'identity':[2026,7,'AUTHORED','p1','DAL','receiving'],**metrics(f['support'],f['pmf'],1)}]},'rushing':{'rows':[]}}
            sp=root/'source/SCORES.json';sp.write_bytes(encoded(scores));closed=root/'CLOSED.json';closed.write_bytes(encoded({'state':'AUTHORITY_CLOSED'}))
            store.append('performance','authored',{'season':2026,'week':7,'source':ref(root,sp)},immutable=True)
            store.append('model_head','authored',{'manifest':ref(root,orig),'closed_lifecycle':ref(root,closed)},immutable=True)
            (root/'TASK_CHARTER.json').write_text('{}');self.assertEqual(refresh_evaluations(store,root)['status'],'WAITING_FOR_MAINTENANCE_CLOSEOUT');(root/'TASK_CHARTER.json').unlink()
            self.assertEqual(len(refresh_evaluations(store,root)['completed']),1);self.assertEqual(refresh_evaluations(store,root)['completed'],[])
            self.assertEqual(len(store.records('game_evaluation')),1);self.assertEqual(len(store.records('model_head')),1)
            self.assertEqual(observation_views(store,root)[0]['evaluation']['scored'],1)
            self.assertTrue((store.root/'exports').glob('*.json'))

if __name__=='__main__':unittest.main()
