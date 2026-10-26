import unittest
from monitoring import distribution,metrics,score_rows,paired_week_interval

class MonitoringTests(unittest.TestCase):
    def fixture(self):
        f={'season':2026,'week':7,'game_id':'AUTHORED','gsis':'00-0000001','team':'AAA','channel':'receiving','position':'WR','support':[-1,0,1],'pmf':[.25,.5,.25],'high_usage':True}
        return f,dict(f,status='APPEARED',yards=0,opportunities=1)
    def test_signed_crps_and_coherence(self):
        self.assertAlmostEqual(metrics([-1,0,1],[.25,.5,.25],0)['crps'],.125)
        self.assertEqual(distribution([-1,0,1],[.25,.5,.25])['median'],0)
        with self.assertRaises(ValueError):distribution([0,1],[.5,.6])
    def test_unknown_is_not_zero_and_population_exact(self):
        f,o=self.fixture();o['status']='UNKNOWN'
        r=score_rows([f],[o]);self.assertEqual(r['cohorts']['all']['n'],0);self.assertIsNone(r['cohorts']['all']['crps'])
        with self.assertRaises(ValueError):score_rows([f],[])
        with self.assertRaises(ValueError):score_rows([f,f],[o,o])
    def test_scored_cohorts_and_economic_separation(self):
        f,o=self.fixture();r=score_rows([f],[o]);self.assertEqual(r['cohorts']['high_usage']['n'],1);self.assertEqual(r['economic_performance'],'ROI_NOT_ESTABLISHED')
    def test_paired_weeks_low_volume_and_repeatable_interval(self):
        a=[{'identity':[2026,w,'g','p','t','receiving'],'crps':2} for w in range(1,9)]
        b=[dict(x,crps=1) for x in a]
        self.assertEqual(paired_week_interval(a[:1],b[:1])['status'],'INSUFFICIENT_WHOLE_WEEKS')
        r=paired_week_interval(a,b,draws=100);self.assertEqual((r['lower'],r['upper']),(-1,-1))
        with self.assertRaises(ValueError):paired_week_interval(a,b[:-1])


class BettingWarningsTests(unittest.TestCase):
    def test_multileg_rejected_and_loss_warning_is_series_specific(self):
        import tempfile
        from pathlib import Path
        from core import Store
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d))
            bet={'id':'authored','game_id':'AUTHORED','channel':'receiving','mode':'PAPER','placement_status':'PLACED','side':'OVER','line':25,'decimal_odds':2,'stake_units':5,'captured_at':'2020-01-01T00:00:00Z','selected_at':'2020-01-01T00:10:00Z','kickoff':'2020-01-01T01:00:00Z'}
            with self.assertRaisesRegex(ValueError,'Multi-leg'):
                store.record_bet(dict(bet,legs=[{'line':25},{'line':30}]))
            store.record_bet(bet)
            store.settle_bet('authored',status='LOST',evidence_sha256='a'*64,settled_at='2020-01-01T05:00:00Z')
            warnings=store.alerts()
            self.assertIn('BETTING_DRAWDOWN',{a['code'] for a in warnings})
            self.assertTrue(all(a['entity']=='PAPER' for a in warnings))
            self.assertFalse(store.monitor_betting()[0]['verified_profitability'])
