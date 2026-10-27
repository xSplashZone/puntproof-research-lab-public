"""Authored boundary tests; actual-source validation is reported separately."""
import copy
import unittest
from datetime import timezone
from unittest.mock import patch
from gamd import licensed_source as ls
from gamd.continuous_context import reconcile
from gamd.continuous_source import training_context, future_cohort, canonical_calendar, repair, forward_history
from gamd.continuous_features import _context, FEATURE_NAMES, SCHEMA, feature_contract_sha256, build_target_features

AT='2026-10-27T00:00:00Z'
SRC={'path':'authored/input','sha256':'a'*64,'observed_at':'2026-10-26T00:00:00Z'}
def history(week,**kw):
    r=dict(season=2026,week=week,game_id=f'2026_{week:02d}_DAL_PHI',gsis='00-0000001',team='PHI',opponent='DAL',position='WR',channel='receiving',appeared=True,opportunity=5,yards=20,participation_state='WITNESSED_APPEARANCE',source_observed_at=SRC['observed_at'],provenance=[SRC])
    r.update(kw);return r
def fixture(week,day,away='DAL'):
    return dict(season=2026,week=week,game_id=f'2026_{week:02d}_{away}_PHI',home_team='PHI',away_team=away,gameday=f'2026-10-{day:02d}',gametime='12:00',game_type='REG',_source=SRC)
def roster(week,**kw):
    r=dict(season=2026,week=week,gsis_id='00-0000001',team='PHI',position='WR',game_type='REG',_source=SRC,_observed_at=SRC['observed_at'])
    r.update(kw);return r

class FoundationSourceTests(unittest.TestCase):
    def test_partial_snap_preserves_count_without_denominator(self):
        raw={'game':{'id':7},'team':'PHI','player':{'id':1},'offense_snaps':20}
        value=ls.snap_values([raw])[7,'PHI']
        self.assertFalse(value['team_game_complete']);self.assertIsNone(value['snap_denominator'])
        self.assertEqual(value['observed_max_offense_snaps'],20)
    def test_independent_census_boundary_not_self_census(self):
        rows=[{'game':{'id':7},'team':'PHI','player':{'id':pid},'offense_snaps':n} for pid,n in ((1,20),(2,60),(3,0))]
        sha=ls.digest(ls.dump(rows).encode())
        witness={'basis':'INDEPENDENT_COMPLETE_OFFENSIVE_PARTICIPANT_CENSUS','capture_sha256':sha,'source_sha256':'b'*64,'native_player_ids':[1,2,3]}
        # This is an AUTHORED authenticated-boundary input, not a real source certificate.
        value=ls.snap_values(rows,complete_censuses={(7,'PHI'):witness})[7,'PHI']
        self.assertTrue(value['team_game_complete']);self.assertEqual(20/value['snap_denominator'],1/3)
        witness['source_sha256']=sha
        self.assertFalse(ls.snap_values(rows,complete_censuses={(7,'PHI'):witness})[7,'PHI']['team_game_complete'])
        witness['source_sha256']='b'*64;witness['native_player_ids']=[1]
        self.assertFalse(ls.snap_values(rows,complete_censuses={(7,'PHI'):witness})[7,'PHI']['team_game_complete'])
    def test_context_join_preserves_individual_and_unknowns(self):
        row=history(1,yards=-2);proof=dict(game_id=row['game_id'],team='PHI',channel='receiving',complete_reconciled=True,independently_reconciled_totals=True,team_totals={'targets':30,'receptions':20,'receiving_yards':200},source_lineage=[SRC])
        good=training_context([row],[proof])[0]
        self.assertEqual((good['yards'],good['team_opportunities'],good['team_yards']),(-2,30,200))
        bad=training_context([row])[0];self.assertEqual(bad['yards'],-2);self.assertIsNone(bad['team_yards'])
        self.assertEqual(_context([good])[:2],(30,200))
    def test_calendar_missing_week_and_transfer(self):
        rows=[history(5),history(7)]
        schedule=[fixture(5,5),fixture(6,12),fixture(7,20)]
        result=canonical_calendar(rows,schedule,[roster(5),roster(7)])
        self.assertEqual(len(result),3);missing=next(r for r in result if r['week']==6)
        self.assertEqual(missing['participation_state'],'UNKNOWN_MEMBERSHIP');self.assertIsNone(missing['yards'])
        transferred=canonical_calendar(rows,schedule,[roster(5),roster(6,team='DAL'),roster(7)])
        self.assertEqual(len(transferred),2)
        anchored=canonical_calendar([history(6)],schedule,[roster(5),roster(7)])
        self.assertEqual([r['week'] for r in anchored],[5,6,7])
    def test_latest_roster_does_not_erase_missing_prior_week(self):
        schedule=[fixture(5,5),fixture(6,12),fixture(7,20),fixture(8,30)]
        with patch('source_adapter.schedule_timezone',return_value=timezone.utc):
            result=future_cohort(schedule,[roster(8)],[history(5),history(6)],AT)
        self.assertEqual(result['targets'][0]['membership_lane'],'STRICT')
        missing=next(r for r in result['history'] if r['week']==7)
        self.assertEqual(missing['participation_state'],'UNKNOWN_MEMBERSHIP')
        self.assertEqual(result['targets'][0]['unknown_history_count'],1)
    def test_calendar_placeholder_joins_team_proof_without_claiming_individual_membership(self):
        # Authored reproduction of the retained 2026 defect: an excluded roster
        # player adds unknown calendar rows after the independent team join.
        # IDs/values here are authored, not copied private source payloads.
        schedule=[fixture(5,5),fixture(6,12),fixture(7,20),fixture(8,30)]
        proofs=[dict(game_id=history(w)['game_id'],team='PHI',channel='receiving',
                     complete_reconciled=True,independently_reconciled_totals=True,
                     category_scope='ALL_DATED_PLAYERS',team_totals={'targets':30,'receptions':20,'receiving_yards':200},
                     source_lineage=[SRC],observed_at=SRC['observed_at']) for w in (5,6,7)]
        original=training_context([history(5),history(6),history(7)],proofs);before=copy.deepcopy(original)
        # This player has an early anchor but no individual statistics. Its
        # placeholders must not veto another player's certified team feature.
        rosters=[roster(5,gsis_id='00-0000000'),roster(8)]
        with patch('source_adapter.schedule_timezone',return_value=timezone.utc):
            result=future_cohort(schedule,rosters,original,AT,contexts=proofs)
        unknown=[r for r in result['history'] if r['gsis']=='00-0000000']
        self.assertEqual(len(unknown),3)
        for row in unknown:
            self.assertIsNone(row['appeared']);self.assertIsNone(row['opportunity']);self.assertIsNone(row['yards'])
            self.assertIn(row['participation_state'],('UNKNOWN_PARTICIPATION','UNKNOWN_MEMBERSHIP'))
            self.assertTrue(row['team_context_valid']);self.assertEqual(row['team_opportunities'],30)
            self.assertEqual(row['context_proof']['category_scope'],'ALL_DATED_PLAYERS')
            self.assertTrue(row['context_proof']['context_record_sha256'])
            self.assertIsNone(row['snap_share']);self.assertIsNone(row['snap_denominator'])
        self.assertEqual(unknown[-1]['membership_state'],'UNKNOWN_MEMBERSHIP')
        self.assertEqual(unknown[-1]['predecessor']['participation_state'],'UNKNOWN_MEMBERSHIP')
        self.assertEqual(original,before)
        feature=build_target_features(result['history'],result['targets'][0],AT,allowed_history_seasons=(2026,))
        self.assertEqual(feature['x'][FEATURE_NAMES.index('observed_team_opportunity_mean5')],30)
        self.assertAlmostEqual(feature['x'][FEATURE_NAMES.index('observed_player_share5')],5/30)
        # The source/history passed by the caller is still immutable.
        self.assertEqual(len(original),3)
        # The nonincremental lane reuses exact existing independent proofs;
        # it does not reconstruct a team total from the selected player rows.
        with patch('source_adapter.schedule_timezone',return_value=timezone.utc):
            fallback=future_cohort(schedule,rosters,original,AT)
        self.assertTrue(all(r['team_context_valid'] for r in fallback['history']))
        self.assertEqual([r['team_opportunities'] for r in fallback['history']],[30]*6)

    def test_calendar_context_requires_exact_independent_category_and_cutoff(self):
        schedule=[fixture(5,5),fixture(6,12),fixture(7,20),fixture(8,30)]
        proof=dict(game_id=history(7)['game_id'],team='PHI',channel='receiving',
                   complete_reconciled=True,independently_reconciled_totals=True,
                   category_scope='ALL_DATED_PLAYERS',team_totals={'targets':30,'receptions':20,'receiving_yards':200},
                   source_lineage=[SRC],observed_at=SRC['observed_at'])
        for changes in ({'team':'DAL'},{'channel':'rushing'},{'independently_reconciled_totals':False},
                        {'complete_reconciled':False},{'category_scope':'WR_TE_RB_FB_ONLY'}):
            with self.subTest(changes=changes),patch('source_adapter.schedule_timezone',return_value=timezone.utc):
                result=future_cohort(schedule,[roster(8)],[history(5),history(6)],AT,contexts=[dict(proof,**changes)])
            missing=next(r for r in result['history'] if r['week']==7)
            self.assertFalse(missing['team_context_valid']);self.assertIsNone(missing['team_opportunities'])
            self.assertIsNone(missing['appeared']);self.assertIsNone(missing['yards'])
        late=dict(proof,source_lineage=[dict(SRC,observed_at='2026-10-28T00:00:00Z')],observed_at='2026-10-28T00:00:00Z')
        with patch('source_adapter.schedule_timezone',return_value=timezone.utc),self.assertRaisesRegex(ValueError,'HISTORY_OBSERVED_AFTER_CUTOFF'):
            future_cohort(schedule,[roster(8)],[history(5),history(6)],AT,contexts=[late])
    def test_fresh_week_one_is_explicitly_provisional_and_two_games(self):
        schedule=[fixture(5,5),fixture(6,12),fixture(7,20),fixture(8,29),fixture(9,31,'NYG')]
        original=[history(5),history(6)];before=copy.deepcopy(original)
        with patch('source_adapter.schedule_timezone',return_value=timezone.utc):
            result=future_cohort(schedule,[roster(1)],original,AT)
        self.assertEqual(len(result['fixtures']),2);self.assertEqual(len(result['targets']),2)
        self.assertTrue(all(r['membership_lane']=='PROVISIONAL' for r in result['targets']))
        self.assertTrue(all(r['membership_basis']['effective_week']==1 for r in result['targets']))
        self.assertEqual(original,before)
        self.assertEqual(result['valid_until'],'2026-10-29T12:00:00+00:00')
    def test_duplicate_roster_alias_quarantined(self):
        with patch('source_adapter.schedule_timezone',return_value=timezone.utc):
            result=future_cohort([fixture(8,30)],[roster(8),roster(8,team='DAL')],[history(5),history(6)],AT)
        self.assertFalse(result['targets']);self.assertEqual(result['exclusions'][0]['reason'],'AMBIGUOUS_CURRENT_TEAM')
    def test_latest_noneligible_role_cannot_revert_to_old_receiver_role(self):
        with patch('source_adapter.schedule_timezone',return_value=timezone.utc):
            result=future_cohort([fixture(8,30)],[roster(7),roster(8,position='QB')],[history(5),history(6)],AT)
        self.assertFalse(result['targets']);self.assertEqual(result['exclusions'][0]['reason'],'LATEST_DATED_POSITION_OUTSIDE_PLAYER_YARDS_SCOPE')
    def test_no_future_fixture_keeps_history(self):
        with patch('source_adapter.schedule_timezone',return_value=timezone.utc):
            result=future_cohort([fixture(7,20)],[],[history(5)],AT)
        self.assertFalse(result['fixtures']);self.assertEqual(len(result['history']),1)
    def test_state_lineage_and_positive_snap_do_not_require_share(self):
        original=dict(season=2026,week=1,game_id='2026_01_DAL_PHI',gsis='00-0000001',team='PHI',opponent='DAL',position='WR',appeared=None,state='UNKNOWN_PARTICIPATION',observed_at='2026-10-20T00:00:00Z',source_lineage=[dict(SRC,observed_at='2026-10-20T00:00:00Z')],original_values={'targets':0,'receptions':0,'receiving_yards':0},targets=None,receptions=None,receiving_yards=None)
        projection={'data':{'records':[original],'team_context':[]}}
        previous={'rows':[],'summary':{'source_repaired_eligible':0}}
        snap=dict(original,offense_snaps=3,snap_share=.5,snap_denominator=6,source=SRC)
        result=repair(projection,previous,{ls.key(original):snap},{},set())['history'][0]
        self.assertTrue(result['appeared']);self.assertEqual(result['state'],'WITNESSED_APPEARANCE')
        self.assertEqual(result['observed_at'],SRC['observed_at']);self.assertIsNone(result['snap_share'])
        self.assertEqual(result['targets'],0);self.assertEqual(result['predecessor']['state'],'UNKNOWN_PARTICIPATION')
        self.assertEqual(forward_history([result])[0]['participation_state'],'WITNESSED_APPEARANCE')
        snap['offense_snaps']=0
        zero=repair(projection,previous,{ls.key(original):snap},{},set())['history'][0]
        self.assertIsNone(zero['appeared']);self.assertIsNone(zero['targets']);self.assertEqual(zero['offense_snaps'],0)
        original.update(appeared=False,state='DNP');snap['offense_snaps']=3
        conflict=repair(projection,previous,{ls.key(original):snap},{},set())['history'][0]
        self.assertIsNone(conflict['appeared']);self.assertEqual(conflict['state'],'PARTICIPATION_CONFLICT')
    def test_feature_contract_stable(self):
        self.assertEqual(SCHEMA,'continuous_individual_context_v1');self.assertEqual(len(FEATURE_NAMES),32)
        self.assertEqual(len(feature_contract_sha256()),64)
    def test_full_pbp_context_reconciliation_and_corrupt_row(self):
        s=dict(fixture(1,5),season=2024,game_id='2024_01_DAL_PHI')
        base=dict(season=2024,week=1,season_type='REG',_source=SRC)
        rr=[dict(roster(1),season=2024)]
        stat=dict(base,player_id='00-0000001',recent_team='PHI',targets=1,receptions=1,receiving_yards=-2,carries=0,rushing_yards=0)
        p=dict(base,game_id=s['game_id'],home_team='PHI',away_team='DAL',posteam='PHI',play_id=1,play_type='pass',pass_attempt=1,complete_pass=1,receiver_player_id='00-0000001',receiving_yards=-2)
        end=dict(p,play_id=2,play_type='game_end')
        data={'schedule':[s],'rosters':rr,'player_stats':[stat],'pbp':[p,end]}
        with patch('source_adapter.schedule_timezone',return_value=timezone.utc):result=reconcile(data,AT)
        good=next(r for r in result['contexts'] if r['team']=='PHI' and r['channel']=='receiving')
        self.assertTrue(good['complete_reconciled']);self.assertEqual(good['team_totals']['receiving_yards'],-2)
        stat['receiving_yards']=9
        with patch('source_adapter.schedule_timezone',return_value=timezone.utc):result=reconcile(data,AT)
        bad=next(r for r in result['contexts'] if r['team']=='PHI' and r['channel']=='receiving')
        self.assertFalse(bad['complete_reconciled']);self.assertIsNone(bad['team_totals']['receiving_yards'])
    def test_qb_receiving_and_rb_lateral_are_full_context(self):
        s=fixture(1,5)
        base=dict(season='2026',week='1',season_type='REG',_source=SRC)
        rb='00-0000001';qb='00-0000002'
        rr=[dict(roster(1,position='RB'),season='2026',week='1'),dict(roster(1,gsis_id=qb,position='QB'),season='2026',week='1')]
        stats=[dict(base,player_id=rb,recent_team='PHI',targets=0,receptions=0,receiving_yards=0,carries=0,rushing_yards=5),dict(base,player_id=qb,recent_team='PHI',targets=1,receptions=1,receiving_yards=10,carries=1,rushing_yards=3)]
        p=dict(base,game_id=s['game_id'],home_team='PHI',away_team='DAL',posteam='PHI',play_id=1,play_type='pass',pass_attempt=1,complete_pass=1,receiver_player_id=qb,receiving_yards=10)
        rush=dict(p,play_id=2,play_type='run',pass_attempt=0,rush_attempt=1,qb_scramble=1,rusher_player_id=qb,rushing_yards=3,lateral_rusher_player_id=rb,lateral_rushing_yards=5)
        end=dict(p,play_id=3,play_type='game_end')
        with patch('source_adapter.schedule_timezone',return_value=timezone.utc):result=reconcile({'schedule':[s],'rosters':rr,'player_stats':stats,'pbp':[p,rush,end]},AT)
        rec=next(r for r in result['contexts'] if r['team']=='PHI' and r['channel']=='receiving')
        run=next(r for r in result['contexts'] if r['team']=='PHI' and r['channel']=='rushing')
        self.assertTrue(rec['complete_reconciled']);self.assertEqual(rec['team_totals']['receiving_yards'],10)
        self.assertTrue(run['complete_reconciled']);self.assertEqual(run['team_totals'],{'carries':0,'rushing_yards':5})

if __name__=='__main__': unittest.main()
