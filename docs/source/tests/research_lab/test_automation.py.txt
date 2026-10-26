import unittest
from automation import decision_audit,decision_csv,source_demands
from manager_bridge import validate_request

class AutomationTests(unittest.TestCase):
    def projection(self):
        f=dict(season=2026,week=7,game_id='authored7',gsis='00-0000001',team='AAA',position='WR',channel='receiving')
        return dict(data={'records':[dict(f,week=6,game_id='authored6',appeared=True,targets=7),dict(f,week=7,game_id='authored7',appeared=True,targets=999)]},fixtures={'receiving':[f],'rushing':[]},exclusions=[dict(fixture=dict(f,gsis='00-0000002'),reason='UNRESOLVED_PRIOR_PARTICIPATION')])
    def test_exact_population_and_prior_only_cohort(self):
        a=decision_audit(self.projection(),projection_ref={'path':'authored','sha256':'a'*64})
        self.assertEqual((a['total'],a['eligible'],a['excluded']),(2,1,1))
        self.assertEqual(a['rows'][0]['prior_usage_mean'],7)
        self.assertEqual(a['rows'][0]['prior_game_ids'],['authored6'])
        self.assertEqual(a['rows'][1]['category'],'unknown_participation')
        self.assertIsNone(a['offered_prop_population'])
        self.assertIn(b'UNRESOLVED_PRIOR_PARTICIPATION',decision_csv(a))
    def test_duplicate_fails_closed(self):
        p=self.projection();p['fixtures']['receiving']*=2
        with self.assertRaisesRegex(ValueError,'DUPLICATE'):decision_audit(p,projection_ref={})
    def test_missing_import_is_not_claimed_unpublished(self):
        d=source_demands([], [2026,7]);self.assertTrue(all(x['state']=='PUBLICATION_UNKNOWN' for x in d))
        d=source_demands([dict(dataset='pbp',weeks=[7],rows=5)],[2026,7]);self.assertEqual(d[1]['state'],'VALIDATED_USABLE')
    def test_bridge_does_not_accept_extra_or_historical_scope(self):
        self.assertEqual(validate_request(dict(source='pbp',season=2026,weeks=[7]))['weeks'],[7])
        for req in (dict(source='pbp',season=2025,weeks=[7]),dict(source='snap_counts',season=2026,weeks=[7]),dict(source='pbp',season=2026,weeks=[7],override=True)):
            with self.assertRaises(ValueError):validate_request(req)
