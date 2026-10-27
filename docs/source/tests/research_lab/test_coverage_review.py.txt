"""Authored canaries only. These tests are not evidence of real source recovery."""
from copy import deepcopy
import json
import unittest

from coverage_review import (SNAP_DENOMINATOR, reconcile_coverage, reconciliation_csv,
                             reconciliation_json, repair_warnings)


REF = {'path': 'authored/decisions.json', 'sha256': 'a' * 64}
PROJECTION_REF = {'path': 'authored/projection.json', 'sha256': 'b' * 64}
EVIDENCE_REF = {'path': 'authored/evidence.json', 'sha256': 'c' * 64}
BEFORE = '2026-10-26T00:00:00Z'
AFTER = '2026-10-27T00:00:00Z'
FIELDS = ('targets', 'receptions', 'receiving_yards', 'carries', 'rushing_yards')


def candidate(**changes):
    row = dict(season=2026, week=7, game_id='authored-week-7', gsis='00-0000001', team='PHI',
               position='WR', channel='receiving', name='DeVonta Smith',
               decision='EXCLUDED', reason='UNRESOLVED_PRIOR_PARTICIPATION')
    row.update(changes)
    return row


def history(week, appeared=True, **changes):
    row = dict(season=2026, week=week, game_id=f'authored-week-{week}', gsis='00-0000001',
               team='PHI', position='WR', appeared=appeared,
               kickoff_utc=f'2026-09-{week:02}T00:00:00Z',
               observed_at='2026-10-25T00:00:00Z',
               source_basis=['AUTHORED_TEST_ONLY'], source_lineage=[],
               state='WITNESSED_APPEARANCE' if appeared is True else 'DNP' if appeared is False else 'UNKNOWN_PARTICIPATION',
               **dict.fromkeys(FIELDS, None))
    if appeared is True:
        row.update(targets=8, receptions=5, receiving_yards=55, carries=0, rushing_yards=0)
    row.update(changes)
    return row


def fixture(rows=None, decision=None):
    d = decision or candidate()
    f = {k: v for k, v in d.items() if k not in ('reason', 'decision')}
    eligible = d['decision'] == 'ELIGIBLE'
    projection = dict(data={'records': rows if rows is not None else [history(w, w <= 4) for w in range(1, 7)]},
                      fixtures={'receiving': [f] if eligible else [], 'rushing': []},
                      exclusions=[] if eligible else [dict(fixture=f, reason=d['reason'])], schedule=[])
    return {'rows': [d]}, projection


def admission(**changes):
    result = dict(source_id='authored-independent-participation', dataset='offensive_participation',
        artifact=deepcopy(EVIDENCE_REF), model_use=True, seasons=[2026],
        evidence_types=['positive_production', 'pbp_touch', 'explicit_dnp', 'offensive_snaps', 'complete_category'],
        allowed_fields=list(FIELDS) + ['role', 'play_id', 'did_not_play', 'offense_snaps', 'special_teams_snaps',
            'snap_denominator', 'snap_denominator_definition', 'team_game_complete', 'complete_categories'],
        authority=dict(operation='AUTHORED_TEST_OPERATION', consumer='authored_test_consumer',
            **{key: {'path': f'authored/{key}.json', 'sha256': 'd' * 64}
               for key in ('manifest', 'readiness', 'source_policy', 'semantics')}))
    result.update(changes)
    return result


def evidence(week=5, kind='positive_production', values=None, **changes):
    row = {k: history(week)[k] for k in ('season', 'week', 'game_id', 'gsis', 'team', 'kickoff_utc')}
    row.update(source_id='authored-independent-participation', dataset='offensive_participation',
               source_ref=deepcopy(EVIDENCE_REF), kind=kind,
               values={'targets': 3, 'receptions': 2, 'receiving_yards': 20} if values is None else values,
               identity_state='EXACT_DATED_ROSTER_GSIS', completed=True,
               completion_observed_at='2026-09-10T00:00:00Z', observed_at='2026-10-26T12:00:00Z')
    row.update(changes)
    return row


def reconcile(rows=None, decision=None, **kwargs):
    decisions, projection = fixture(rows, decision)
    return reconcile_coverage(decisions, projection, original_ref=REF, projection_ref=PROJECTION_REF,
        original_cutoff=BEFORE, research_cutoff=AFTER, expected_total=1, **kwargs)


class CoverageReviewTests(unittest.TestCase):
    def test_four_known_two_unknown_is_partial_not_four_consecutive_or_six_verified(self):
        rows = [history(w, True if w <= 4 else None) for w in range(1, 7)]
        report = reconcile(rows)
        row = report['rows'][0]
        self.assertEqual((row['before']['verified_appearances'], row['before']['unknown_appearances']), (4, 2))
        self.assertEqual([r['week'] for r in row['before']['unknown_games']], [5, 6])
        self.assertEqual(row['source_repaired_reason'], 'UNRESOLVED_PRIOR_PARTICIPATION')
        self.assertEqual(row['challenger_cohort'], 'ESTABLISHED_MISSINGNESS_RESEARCH')
        self.assertTrue(row['challenger_source_supported'])
        self.assertFalse(row['challenger_current_model_admission'])
        self.assertFalse(row['frozen_eligibility_changed'])
        self.assertEqual(row['priority'], 1)
        self.assertEqual(report['summary']['source_recovered_candidates'], 0)
        self.assertIsNone(report['summary']['offered_prop_population'])
        self.assertEqual(len(report['named_examples']), 1)

    def test_zero_known_candidates_are_retained(self):
        result = reconcile([history(w, None) for w in range(1, 7)])
        self.assertEqual(result['summary']['total'], 1)
        self.assertEqual(result['rows'][0]['category'], 'zero_verified_history')
        self.assertFalse(result['rows'][0]['challenger_source_supported'])

    def test_dnp_is_distinct_from_unknown_and_never_becomes_zero_yards(self):
        report = reconcile([history(1, True), history(2, False), history(3, None)])
        row = report['rows'][0]
        self.assertEqual((row['before']['verified_appearances'], row['before']['explicit_dnp'], row['before']['unknown_appearances']), (1, 1, 1))
        self.assertEqual(row['before']['dnp_games'][0]['week'], 2)
        self.assertEqual(row['before']['unknown_games'][0]['week'], 3)

    def test_transferred_player_other_team_history_does_not_replace_same_team_gap(self):
        rows = [history(1, True, team='DAL'), history(2, True, team='DAL'), history(3, True), history(4, None)]
        row = reconcile(rows)['rows'][0]
        self.assertEqual(row['before']['verified_appearances'], 1)
        self.assertEqual([g['team'] for g in row['other_team_history']], ['DAL', 'DAL'])
        self.assertFalse(row['challenger_source_supported'])

    def test_identity_gap_is_not_repaired_by_participation(self):
        rows = [history(1, True), history(3, True), history(5, None)]
        decision = candidate(reason='UNRESOLVED_PRIOR_ROSTER_MEMBERSHIP')
        report = reconcile(rows, decision, evidence_rows=[evidence()], admissions=[admission()])
        self.assertEqual(report['rows'][0]['source_repaired_reason'], 'UNRESOLVED_PRIOR_ROSTER_MEMBERSHIP')

    def test_complete_source_repair_is_recorded_separately_from_frozen_decision(self):
        rows = [history(1, True), history(2, True), history(5, None)]
        report = reconcile(rows, evidence_rows=[evidence()], admissions=[admission()])
        row = report['rows'][0]
        self.assertEqual(row['after']['verified_appearances'], 3)
        self.assertEqual(row['source_repaired_reason'], 'ELIGIBLE')
        self.assertEqual(row['frozen_decision'], 'EXCLUDED')
        self.assertEqual(report['summary']['source_recovered_candidates'], 1)
        self.assertTrue(report['evidence'][0]['observed_after_original_cutoff'])
        self.assertEqual(report['original_forecasts_modified'], 0)

    def test_new_evidence_cannot_override_known_production(self):
        result = reconcile([history(1), history(5)], evidence_rows=[evidence()], admissions=[admission()])
        self.assertEqual(result['summary']['accepted_evidence'], 0)
        self.assertEqual(result['quarantined'][0]['code'], 'PRODUCTION_EVIDENCE_CONFLICT')

    def test_explicit_dnp_proof_keeps_verified_count_unchanged(self):
        result = reconcile([history(1), history(2), history(5, None)],
                           evidence_rows=[evidence(kind='explicit_dnp', values={'did_not_play': True})],
                           admissions=[admission()])
        row = result['rows'][0]
        self.assertEqual((row['after']['verified_appearances'], row['after']['explicit_dnp']), (2, 1))
        self.assertEqual(row['source_repaired_reason'], 'ELIGIBLE')

    def test_positive_snaps_need_exact_complete_denominator_and_category_to_fill_zero(self):
        values = dict(offense_snaps=10, snap_denominator=60, snap_denominator_definition=SNAP_DENOMINATOR,
                      team_game_complete=True, complete_categories={'receiving': dict(complete=True, reconciled=True,
                          team_totals=dict(targets=3, receptions=2, receiving_yards=20),
                          players={'00-0000002': dict(targets=3, receptions=2, receiving_yards=20)})})
        result = reconcile([history(1), history(2), history(5, None)],
                           evidence_rows=[evidence(kind='offensive_snaps', values=values)], admissions=[admission()])
        row = result['rows'][0]
        self.assertEqual(row['after']['channel_supported_appearances'], 3)
        self.assertEqual(row['after']['prior_usage_mean'], 16 / 3)
        self.assertEqual(row['source_repaired_reason'], 'ELIGIBLE')
        for update in ({'team_game_complete': False}, {'snap_denominator': 5},
                       {'snap_denominator': 0}, {'snap_denominator_definition': 'SUM_OF_PLAYER_SNAPS'}):
            invalid = dict(values, **update)
            report = reconcile([history(5, None)], evidence_rows=[evidence(kind='offensive_snaps', values=invalid)], admissions=[admission()])
            self.assertEqual(report['summary']['accepted_evidence'], 0)
            self.assertEqual(report['rows'][0]['after']['unknown_appearances'], 1)

    def test_positive_snaps_without_category_do_not_invent_production_zero(self):
        values = dict(offense_snaps=10, snap_denominator=60, snap_denominator_definition=SNAP_DENOMINATOR, team_game_complete=True)
        result = reconcile([history(1), history(2), history(5, None)],
                           evidence_rows=[evidence(kind='offensive_snaps', values=values)], admissions=[admission()])
        self.assertEqual(result['rows'][0]['after']['verified_appearances'], 3)
        self.assertEqual(result['rows'][0]['after']['channel_supported_appearances'], 2)
        self.assertEqual(result['rows'][0]['source_repaired_reason'], 'UNKNOWN_RECENT_CHANNEL_VALUES')

    def test_zero_offensive_and_special_teams_only_snaps_keep_unknown(self):
        values = dict(offense_snaps=0, special_teams_snaps=12, snap_denominator=60,
                      snap_denominator_definition=SNAP_DENOMINATOR, team_game_complete=True)
        result = reconcile([history(5, None)], evidence_rows=[evidence(kind='offensive_snaps', values=values)], admissions=[admission()])
        self.assertEqual(result['rows'][0]['after']['unknown_appearances'], 1)
        self.assertEqual(result['rows'][0]['after']['explicit_dnp'], 0)

    def test_zero_box_score_cannot_establish_participation(self):
        result = reconcile([history(5, None)], evidence_rows=[evidence(values={'targets': 0, 'receiving_yards': 0})], admissions=[admission()])
        self.assertEqual(result['quarantined'][0]['code'], 'ZERO_BOX_SCORE_IS_NOT_PARTICIPATION')

    def test_restricted_pfr_cannot_be_admitted_by_generic_flag_or_alias(self):
        for changes in ({'source_id': 'pfr'}, {'dataset': 'snap_counts'}, {'upstream_sources': ['PFR']},
                        {'rights_owner': 'Pro-Football-Reference'}, {'source_id': 'balldontlie-native'}):
            bound = admission(**changes)
            proof = evidence(source_id=bound['source_id'], dataset=bound['dataset'])
            report = reconcile([history(5, None)], evidence_rows=[proof], admissions=[bound])
            self.assertEqual(report['summary']['accepted_evidence'], 0)
            self.assertEqual(report['quarantined'][0]['code'], 'SOURCE_REMAINS_RESTRICTED')

    def test_exact_admission_required_beyond_model_use_flag(self):
        result = reconcile([history(5, None)], evidence_rows=[evidence()], admissions=[admission(authority={})])
        self.assertEqual(result['quarantined'][0]['code'], 'EXACT_SOURCE_AUTHORITY_REQUIRED')
        result = reconcile([history(5, None)], evidence_rows=[evidence()], admissions=[])
        self.assertEqual(result['quarantined'][0]['code'], 'SOURCE_NOT_ADMITTED')

    def test_future_target_identity_and_missing_clocks_rejected(self):
        for proof in (evidence(observed_at='2026-10-28T00:00:00Z'), evidence(observed_at=None),
                      evidence(identity_state='NAME_ONLY'), evidence(week=7), evidence(team='DAL'),
                      evidence(completed=False), evidence(kickoff_utc='2026-09-06T00:00:00Z')):
            result = reconcile([history(5, None)], evidence_rows=[proof], admissions=[admission()])
            self.assertEqual(result['summary']['accepted_evidence'], 0)
            self.assertEqual(result['rows'][0]['after']['unknown_appearances'], 1)

    def test_artifact_and_closed_fields_binding(self):
        proof = evidence(source_ref={'path': 'authored/elsewhere.json', 'sha256': 'c' * 64})
        result = reconcile([history(5, None)], evidence_rows=[proof], admissions=[admission()])
        self.assertEqual(result['quarantined'][0]['code'], 'SOURCE_ARTIFACT_MISMATCH')
        result = reconcile([history(5, None)], evidence_rows=[evidence(values={'unsupported': 3})], admissions=[admission()])
        self.assertEqual(result['quarantined'][0]['code'], 'SOURCE_FIELD_NOT_ADMITTED')

    def test_conflicting_or_duplicate_proof_rolls_back_entire_player_game(self):
        for extra in (evidence(), evidence(kind='explicit_dnp', values={'did_not_play': True})):
            result = reconcile([history(5, None)], evidence_rows=[evidence(), extra], admissions=[admission()])
            self.assertEqual(result['summary']['accepted_evidence'], 0)
            self.assertEqual(result['rows'][0]['after']['unknown_appearances'], 1)

    def test_input_custody_and_full_exports(self):
        original, projection = fixture([history(1), history(5, None)])
        original_copy, projection_copy = deepcopy(original), deepcopy(projection)
        result = reconcile_coverage(original, projection, original_ref=REF, projection_ref=PROJECTION_REF,
            original_cutoff=BEFORE, research_cutoff=AFTER, expected_total=1)
        self.assertEqual(original, original_copy)
        self.assertEqual(projection, projection_copy)
        self.assertEqual(json.loads(reconciliation_json(result))['summary']['total'], 1)
        csv_text = reconciliation_csv(result).decode('utf-8-sig')
        self.assertIn('unknown_games_before', csv_text)
        self.assertIn('authored-week-5', csv_text)
        self.assertEqual(repair_warnings(result)[0]['games'][0]['week'], 5)

    def test_census_changes_and_default_denominator_fail_closed(self):
        original, projection = fixture([history(1)])
        kwargs = dict(original_ref=REF, projection_ref=PROJECTION_REF,
                      original_cutoff=BEFORE, research_cutoff=AFTER)
        with self.assertRaisesRegex(ValueError, 'DENOMINATOR'):
            reconcile_coverage(original, projection, **kwargs)
        projection['exclusions'][0]['fixture']['gsis'] = '00-0000002'
        with self.assertRaisesRegex(ValueError, 'CENSUS_IDENTITY'):
            reconcile_coverage(original, projection, expected_total=1, **kwargs)

    def test_authored_full_783_population_has_exact_161_598_10_14_accounting(self):
        decisions, records, eligible, exclusions = [], [], [], []
        for index in range(783):
            gsis = f'00-{index + 1:07}'
            reason = ('ELIGIBLE' if index < 161 else 'UNRESOLVED_PRIOR_PARTICIPATION' if index < 759
                      else 'UNRESOLVED_PRIOR_ROSTER_MEMBERSHIP' if index < 769
                      else 'TOO_FEW_SAME_TEAM_APPEARANCES')
            row = candidate(gsis=gsis, decision='ELIGIBLE' if reason == 'ELIGIBLE' else 'EXCLUDED', reason=reason)
            decisions.append(row)
            f = {k: v for k, v in row.items() if k not in ('reason', 'decision')}
            if reason == 'ELIGIBLE':
                eligible.append(f)
            else:
                exclusions.append(dict(fixture=f, reason=reason))
            records.append(history(1, gsis=gsis))
            if index < 769:
                records.append(history(2, gsis=gsis))
            if 161 <= index < 759:
                records.append(history(5, None, gsis=gsis))
        projection = dict(data={'records': records}, fixtures={'receiving': eligible, 'rushing': []}, exclusions=exclusions)
        report = reconcile_coverage({'rows': decisions}, projection, original_ref=REF, projection_ref=PROJECTION_REF,
                                    original_cutoff=BEFORE, research_cutoff=AFTER)
        self.assertEqual((report['summary']['total'], report['summary']['frozen_eligible']), (783, 161))
        self.assertEqual(report['summary']['frozen_reasons'], dict(UNRESOLVED_PRIOR_PARTICIPATION=598,
            UNRESOLVED_PRIOR_ROSTER_MEMBERSHIP=10, TOO_FEW_SAME_TEAM_APPEARANCES=14))
        self.assertEqual(report['summary']['source_recovered_candidates'], 0)
        self.assertEqual({r['gsis'] for r in report['rows']}, {r['gsis'] for r in decisions})

    def test_duplicate_history_and_late_original_history_fail_closed(self):
        for rows, error in (([history(1), history(1)], 'DUPLICATE_ORIGINAL_HISTORY'),
                            ([history(1, observed_at=AFTER)], 'ORIGINAL_HISTORY_CLOCK_AFTER_CUTOFF')):
            with self.assertRaisesRegex(ValueError, error):
                reconcile(rows)

    def test_target_outcome_values_are_never_consumed(self):
        report = reconcile([history(1), history(2), history(7, targets=999999)],
                           candidate(decision='ELIGIBLE', reason='ELIGIBLE'))
        self.assertEqual(report['rows'][0]['before']['verified_appearances'], 2)

    def test_signed_negative_yards_are_valid_positive_participation_evidence(self):
        report = reconcile([history(5, None)], evidence_rows=[evidence(values={'receiving_yards': -3})], admissions=[admission()])
        self.assertEqual(report['rows'][0]['after']['verified_appearances'], 1)
        self.assertEqual(report['rows'][0]['after']['channel_supported_appearances'], 0)

    def test_nan_and_boolean_stats_never_pass_as_numbers(self):
        for value in (float('nan'), True):
            report = reconcile([history(5, None)], evidence_rows=[evidence(values={'targets': value})], admissions=[admission()])
            self.assertEqual(report['summary']['accepted_evidence'], 0)

    def test_dnp_conflicting_with_witnessed_history_never_accepted(self):
        result = reconcile([history(5)], evidence_rows=[evidence(kind='explicit_dnp', values={'did_not_play': True})], admissions=[admission()])
        self.assertEqual(result['quarantined'][0]['code'], 'PARTICIPATION_EVIDENCE_CONFLICT')


class GamdKnowledgeTests(unittest.TestCase):
    def test_every_existing_concept_and_required_observation_survives(self):
        from knowledge import knowledge_records
        from gamd.knowledge import knowledge_feature_map
        records = list(knowledge_records())
        report = knowledge_feature_map(records)
        self.assertEqual(report['concept_count'], len(records))
        self.assertEqual(report['feature_count'], 32)
        self.assertEqual({r['concept_id'] for r in report['concepts']}, {key for key, _ in records})
        by_id = {r['concept_id']: r for r in report['concepts']}
        for key, record in records:
            self.assertEqual([s['observation'] for s in by_id[key]['required_observations']], record['required_data'])
            self.assertFalse(by_id[key]['concept_ablation_executed'])
            self.assertFalse(by_id[key]['new_hypothesis_execution_authorized'])
        self.assertEqual(report['fits_executed'], 0)

    def test_routes_and_snaps_blocked_partial_subtotal_is_not_complete_share(self):
        from knowledge import knowledge_records
        from gamd.knowledge import knowledge_feature_map
        report = knowledge_feature_map(knowledge_records())
        by_id = {r['concept_id']: r for r in report['concepts']}
        for key in ('snap_share', 'route_share', 'weather_conditions', 'coverage_family'):
            self.assertEqual(by_id[key]['support'], 'BLOCKED_NO_IMPLEMENTED_FEATURE')
            self.assertEqual(by_id[key]['feature_names'], [])
        self.assertEqual(by_id['target_share']['support'], 'PARTIAL_PROXY_ONLY')
        self.assertIn('subtotal', by_id['target_share']['limitation'])
        self.assertIn('not position-stratified', by_id['position_allowances']['limitation'])
        self.assertEqual(by_id['market_settlement']['support'], 'ACCOUNTING_ONLY_NOT_A_FITTED_FEATURE')

    def test_unknown_concept_retained_with_unimplemented_slots(self):
        from gamd.knowledge import knowledge_feature_map
        report = knowledge_feature_map([dict(id='authored_new_concept', required_data=['authored_observation'])])
        self.assertEqual(report['concepts'][0]['missing_observations'], ['authored_observation'])
        self.assertEqual(report['concepts'][0]['feature_names'], [])

    def test_wrong_feature_schema_and_duplicate_concept_fail_closed(self):
        from gamd.knowledge import knowledge_feature_map
        concept = dict(id='authored', required_data=[])
        with self.assertRaisesRegex(ValueError, 'FEATURE_SCHEMA'):
            knowledge_feature_map([concept], feature_names=['snap_share'])
        with self.assertRaisesRegex(ValueError, 'DUPLICATE'):
            knowledge_feature_map([concept, concept])


def authored_gamd_forecast(**changes):
    row = dict(id='authored-gamd-forecast', season=2026, week=8, game_id='authored-week-8',
        gsis='00-0000001', team='PHI', position='WR', channel='receiving', family='hurdle_glm',
        model_sha256='e' * 64,
        forecast_ref={'path': 'authored/forecast.json', 'sha256': 'a' * 64},
        seal_ref={'path': 'authored/seal.json', 'sha256': 'b' * 64},
        source_cutoff='2026-10-27T23:00:00Z', sealed_at='2026-10-28T00:00:00Z',
        kickoff_at='2026-11-01T00:00:00Z', maximum_source_observed_at='2026-10-27T22:00:00Z',
        training_through=[2023, 12], calibration_through=[2023, 18],
        law={'support': [-5, 0, 20, 50], 'pmf': [.1, .2, .5, .2]},
        expected_opportunities=5., verified_history_count=4, unknown_history_count=2,
        changed_team=False, training_classification='AUTHORED_TEST_ONLY')
    row.update(changes)
    return row


def authored_gamd_outcome(**changes):
    row = dict(season=2026, week=8, game_id='authored-week-8', gsis='00-0000001', team='PHI', channel='receiving',
        status='APPEARED', yards=20, opportunities=4, verified=True, final=True,
        identity_state='EXACT_DATED_ROSTER_GSIS', completed_at='2026-11-01T04:00:00Z',
        observed_at='2026-11-01T05:00:00Z',
        outcome_ref={'path': 'authored/outcome.json', 'sha256': 'c' * 64},
        source_ref={'path': 'authored/source.json', 'sha256': 'd' * 64},
        completion_ref={'path': 'authored/completion.json', 'sha256': 'f' * 64},
        source_verification=dict(operation='AUTHORED_TEST_ONLY', consumer='authored',
            manifest={'path': 'authored/manifest.json', 'sha256': '1' * 64},
            readiness={'path': 'authored/readiness.json', 'sha256': '2' * 64}))
    row.update(changes)
    return row


def observe_authored(forecasts=None, outcomes=None, **kwargs):
    from gamd.observations import observe_sealed_forecasts
    return observe_sealed_forecasts([authored_gamd_forecast()] if forecasts is None else forecasts,
        [authored_gamd_outcome()] if outcomes is None else outcomes, as_of='2026-11-01T06:00:00Z', **kwargs)


class GamdObservationTests(unittest.TestCase):
    def test_exact_sealed_law_point_probability_crps_and_push_scores(self):
        result = observe_authored()
        self.assertEqual(result['new_scored_observations'], 1)
        row = result['new_records'][0]
        score = row['score']
        self.assertEqual(row['classification'], 'PROSPECTIVE_GAME_OBSERVATION')
        self.assertAlmostEqual(score['mean'], 19.5)
        self.assertAlmostEqual(score['absolute_error'], .5)
        self.assertAlmostEqual(score['crps'], 3.05)
        self.assertEqual(score['workload_absolute_error'], 1)
        milestone = next(p for p in score['threshold_probabilities'] if p['threshold'] == 20)
        self.assertAlmostEqual(milestone['probability'], .7)
        self.assertAlmostEqual(milestone['brier'], .09)
        line = next(p for p in score['odds_free_line_choices'] if p['line'] == 20)
        self.assertEqual(line['result'], 'PUSH')
        self.assertFalse(line['market_offer'])
        self.assertIsNone(score['genuine_roi'])
        self.assertFalse(result['learning_completed'])
        self.assertEqual(result['parameter_updates'], 0)

    def test_unknown_dnp_void_are_excluded_without_invented_zero(self):
        for status in ('UNKNOWN', 'NONAPPEARANCE', 'VOID'):
            result = observe_authored(outcomes=[authored_gamd_outcome(status=status, yards=None, opportunities=None)])
            self.assertEqual(result['new_scored_observations'], 0)
            self.assertEqual(result['new_exclusions'], 1)
            self.assertIsNone(result['new_records'][0]['score'])
            self.assertEqual(result['new_records'][0]['learning_status'], 'EXCLUDED_FROM_LEARNING')
            result = observe_authored(outcomes=[authored_gamd_outcome(status=status, yards=0, opportunities=0)])
            self.assertEqual(result['new_records'], [])
            self.assertIn('NOT_ZERO', result['quarantined'][0]['code'])

    def test_missing_outcome_is_pending_not_settled(self):
        result = observe_authored(outcomes=[])
        self.assertEqual(result['new_records'], [])
        self.assertEqual(len(result['pending']), 1)

    def test_exact_identity_prevents_cross_player_game_team_or_channel_join(self):
        for changes in ({'gsis': '00-0000002'}, {'team': 'DAL'}, {'game_id': 'different'}, {'channel': 'rushing'}):
            result = observe_authored(outcomes=[authored_gamd_outcome(**changes)])
            self.assertEqual(result['new_records'], [])
            self.assertEqual(len(result['pending']), 1)

    def test_verified_boolean_alone_is_not_authentication(self):
        result = observe_authored(outcomes=[authored_gamd_outcome(source_verification={})])
        self.assertEqual(result['new_records'], [])
        self.assertEqual(result['quarantined'][0]['code'], 'AUTHENTICATED_OUTCOME_REQUIRED')

    def test_nonfinal_or_invalid_final_clock_never_scores(self):
        for changes in ({'final': False}, {'observed_at': '2026-11-02T00:00:00Z'},
                        {'completed_at': '2026-10-31T23:00:00Z'}, {'observed_at': '2026-11-01T03:00:00Z'},
                        {'observed_at': '2026-11-01T05:00:00'}, {'identity_state': 'NAME_MATCH_ONLY'}):
            result = observe_authored(outcomes=[authored_gamd_outcome(**changes)])
            self.assertEqual(result['new_records'], [])
            self.assertEqual(len(result['quarantined']), 1)

    def test_seal_after_kickoff_requires_and_preserves_retrospective_label(self):
        forecast = authored_gamd_forecast(sealed_at='2026-11-01T01:00:00Z')
        result = observe_authored(forecasts=[forecast])
        self.assertEqual(result['quarantined'][0]['code'], 'LATE_SEAL_REQUIRES_RETROSPECTIVE_LABEL')
        forecast['classification'] = 'RETROSPECTIVE_RESEARCH_FORECAST'
        result = observe_authored(forecasts=[forecast])
        self.assertEqual(result['new_records'][0]['classification'], 'RETROSPECTIVE_GAME_OBSERVATION')

    def test_training_source_clock_and_target_value_lookahead_rejected(self):
        for changes in ({'training_through': [2026, 8]}, {'calibration_through': [2026, 8]},
                        {'maximum_source_observed_at': '2026-10-28T01:00:00Z'}, {'actual': 20},
                        {'source_cutoff': '2026-10-28T01:00:00Z'}):
            result = observe_authored(forecasts=[authored_gamd_forecast(**changes)])
            self.assertEqual(result['new_records'], [])

    def test_malformed_or_changed_law_is_not_scored(self):
        for law in ({'support': [0, 1], 'pmf': [.5, .6]}, {'support': [1, 0], 'pmf': [.5, .5]},
                    {'support': [0, 1], 'pmf': [-.1, 1.1]}, {'support': [0, 1], 'pmf': [.5, .5], 'mean': 30}):
            result = observe_authored(forecasts=[authored_gamd_forecast(law=law)])
            self.assertEqual(result['new_records'], [])

    def test_duplicate_forecasts_or_outcomes_fail_atomically(self):
        forecast = authored_gamd_forecast()
        result = observe_authored(forecasts=[forecast, deepcopy(forecast)])
        self.assertEqual(result['new_records'], [])
        other_id = dict(forecast, id='different-id-same-model-population')
        result = observe_authored(forecasts=[forecast, other_id])
        self.assertEqual(result['new_records'], [])
        result = observe_authored(outcomes=[authored_gamd_outcome(), authored_gamd_outcome(yards=30)])
        self.assertEqual(result['new_records'], [])

    def test_repeated_exact_observation_is_idempotent(self):
        first = observe_authored()['new_records']
        result = observe_authored(existing_observations=first)
        self.assertEqual(result['new_records'], [])
        self.assertEqual(result['unchanged_observation_ids'], [first[0]['id']])
        self.assertEqual(result['per_model']['e' * 64]['n'], 1)

    def test_same_references_cannot_hide_changed_payload_or_scoring_policy(self):
        first = observe_authored()['new_records']
        for kwargs in ({'outcomes': [authored_gamd_outcome(yards=30)]},
                       {'forecasts': [authored_gamd_forecast(expected_opportunities=6.)]},
                       {'thresholds': (20, 30)}):
            result = observe_authored(existing_observations=first, **kwargs)
            self.assertEqual(result['new_records'], [])
            self.assertEqual(result['quarantined'][0]['code'], 'CONSUMED_OBSERVATION_POLICY_OR_FORECAST_CHANGED')

    def test_correction_is_new_record_and_old_payload_remains_unchanged(self):
        old = observe_authored()['new_records'][0]
        copy = deepcopy(old)
        outcome = authored_gamd_outcome(yards=30, observed_at='2026-11-01T05:30:00Z',
            outcome_ref={'path': 'authored/corrected.json', 'sha256': '3' * 64},
            supersedes_outcome_sha256='c' * 64)
        result = observe_authored(outcomes=[outcome], existing_observations=[old])
        new = result['new_records'][0]
        self.assertNotEqual(new['id'], old['id'])
        self.assertEqual(new['supersedes_observation_id'], old['id'])
        self.assertEqual(new['score']['actual'], 30)
        self.assertEqual(result['per_model']['e' * 64]['n'], 1)
        self.assertEqual(old, copy)
        repeated = observe_authored(outcomes=[outcome], existing_observations=[old, new])
        self.assertEqual(repeated['new_records'], [])

    def test_new_outcome_revision_without_chain_is_quarantined(self):
        old = observe_authored()['new_records']
        outcome = authored_gamd_outcome(yards=30, observed_at='2026-11-01T05:30:00Z',
            outcome_ref={'path': 'authored/corrected.json', 'sha256': '3' * 64})
        result = observe_authored(outcomes=[outcome], existing_observations=old)
        self.assertEqual(result['new_records'], [])
        self.assertEqual(result['quarantined'][0]['code'], 'OUTCOME_CORRECTION_CHAIN_REQUIRED')

    def test_outcome_correction_cannot_rewrite_sealed_forecast_or_scoring_policy(self):
        old = observe_authored()['new_records']
        outcome = authored_gamd_outcome(yards=30, observed_at='2026-11-01T05:30:00Z',
            outcome_ref={'path': 'authored/corrected.json', 'sha256': '3' * 64},
            supersedes_outcome_sha256='c' * 64)
        for kwargs in ({'forecasts': [authored_gamd_forecast(expected_opportunities=7)]},
                       {'thresholds': (20, 30)}):
            result = observe_authored(outcomes=[outcome], existing_observations=old, **kwargs)
            self.assertEqual(result['new_records'], [])
            self.assertEqual(result['quarantined'][0]['code'], 'CONSUMED_OBSERVATION_POLICY_OR_FORECAST_CHANGED')

    def test_corrupted_existing_observation_fails_custody(self):
        old = observe_authored()['new_records']
        old[0]['score']['actual'] = 100
        with self.assertRaisesRegex(ValueError, 'CUSTODY_FAILED'):
            observe_authored(existing_observations=old)

    def test_atlas_reports_surprise_without_inventing_cause_or_log_score(self):
        result = observe_authored(outcomes=[authored_gamd_outcome(yards=200, opportunities=8)])
        score = result['new_records'][0]['score']
        self.assertIsNone(score['logarithmic_score'])
        self.assertTrue(score['actual_outside_discrete_support'])
        surprise = result['error_atlas']['surprises'][0]
        self.assertEqual(surprise['cause'], 'UNKNOWN_NOT_INFERRED_FROM_RESIDUAL')
        self.assertIn('150PLUS_WITH_LOW_FORECAST_TAIL_MASS', surprise['flags'])
        self.assertFalse(result['error_atlas']['causes_established'])

    def test_calibration_and_workload_cohorts_are_descriptive_and_price_free(self):
        result = observe_authored()
        summary = result['per_model']['e' * 64]
        self.assertEqual(summary['distinct_games'], 1)
        self.assertEqual(summary['distinct_weeks'], 1)
        self.assertEqual(sum(row['n'] for row in summary['calibration_bins']), 8)
        self.assertIn('correlated', summary['calibration_interpretation'])
        self.assertIn('history:unknown_present', result['error_atlas']['cohorts'])
        self.assertIsNone(result['genuine_roi'])


class GamdCoverageProofTests(unittest.TestCase):
    def test_closed_normalized_reader_filters_week_before_poisoned_values(self):
        import gzip
        import hashlib
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from gamd.coverage import read_coverage_partition
        import source_adapter
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'authored.jsonl.gz'
            rows = [dict(season='2026', week='6', season_type='REG', player_id='00-0000001',
                         targets='2', receptions='1', receiving_yards='-3', carries='0', rushing_yards='0'),
                    dict(season='2026', week='7', season_type='REG', receiving_yards={'TARGET_POISON': True})]
            with gzip.open(path, 'wt', encoding='utf-8') as stream:
                for row in rows:
                    stream.write(json.dumps(row) + '\n')
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            entry = dict(dataset='player_stats', source_id='player_stats', season=2026,
                source_root=directory, path=path.name, sha256=sha, raw_ref={'sha256': 'a' * 64},
                observed_at='2026-10-26T00:00:00Z', format='jsonl.gz', through_week=6,
                coverage_allowed_fields=sorted(source_adapter.FIELD_ALLOWLISTS['player_stats']))
            with patch('nfl_model.gamd_gate.require_active_source') as require:
                records, audit = read_coverage_partition(root, entry)
                require.assert_called_once_with(root)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['receiving_yards'], '-3')
            self.assertEqual(records[0]['_source']['observed_at'], entry['observed_at'])
            self.assertEqual((audit['scanned_rows'], audit['retained_rows'], audit['withheld_future_or_non2026_rows']), (2, 1, 1))
            self.assertEqual(audit['football_values_from_target_week_retained'], 0)
            for update, error in (({'observed_at': None}, 'CLOCK_REQUIRED'),
                                  ({'observed_at': '2026-10-26T00:00:00'}, 'CLOCK_NAIVE'),
                                  ({'through_week': 7}, 'WEEK_CEILING'),
                                  ({'coverage_allowed_fields': ['season', 'week', 'targets']}, 'EXACT_EXISTING_COVERAGE_FIELDS')):
                with patch('nfl_model.gamd_gate.require_active_source'):
                    with self.assertRaisesRegex(ValueError, error):
                        read_coverage_partition(root, dict(entry, **update))

    def test_schedule_reader_keeps_full_2026_identity_census_without_other_seasons_or_scores(self):
        import csv
        import hashlib
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from unittest.mock import patch
        from gamd.coverage import read_coverage_partition, SCHEDULE_FIELDS
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'authored-schedule.csv'
            fields = list(SCHEDULE_FIELDS) + ['home_score']
            with path.open('w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for year, week in ((2025, 1), (2026, 1), (2026, 18)):
                    writer.writerow(dict(season=year, week=week, game_type='REG', game_id=f'{year}_{week}_DAL_PHI',
                        home_team='PHI', away_team='DAL', gameday='2026-09-01', gametime='20:15', home_score='DO_NOT_PROJECT'))
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            entry = dict(dataset='schedule', source_id='retained_nflverse_game_edge_schedule', season=2026,
                source_root=directory, path=path.name, sha256=sha, raw_ref={'sha256': sha},
                observed_at='2026-10-01T00:00:00Z', format='csv', through_week=18,
                coverage_allowed_fields=list(SCHEDULE_FIELDS))
            with patch('nfl_model.gamd_gate.require_active_source'):
                records, audit = read_coverage_partition(root, entry)
            self.assertEqual([r['week'] for r in records], ['1', '18'])
            self.assertTrue(all('home_score' not in row for row in records))
            self.assertEqual(audit['withheld_future_or_non2026_rows'], 1)

    def inputs(self, *, completed=True, current=None):
        original = {'data': {'records': [history(1), history(2), history(5, None)]}}
        latest = current or history(5, True, identity_state='EXACT_DATED_ROSTER_GSIS')
        revised = {'data': {'records': [history(1), history(2), latest]},
                   'schedule': [dict(game_id='authored-week-5', kickoff_utc='2026-09-05T00:00:00Z',
                      completed=completed, completion_observed_at='2026-09-10T00:00:00Z' if completed else None)]}
        entries = [dict(source_id=dataset, dataset=dataset, source_root='C:/Dev/nfl-model',
                        path=f'authored/{dataset}.json', sha256='a' * 64,
                        observed_at='2026-10-26T12:00:00Z') for dataset in ('player_stats', 'pbp', 'rosters')]
        return original, revised, entries

    def test_positive_completed_dated_repair_has_real_completion_and_roster_lineage(self):
        from gamd.coverage import participation_proofs
        original, revised, entries = self.inputs()
        copy = deepcopy(original)
        report = participation_proofs(original, revised, [], entries, authority={})
        self.assertEqual(len(report['evidence_rows']), 1)
        proof = report['evidence_rows'][0]
        self.assertEqual(proof['kind'], 'positive_production')
        self.assertEqual(proof['completion_observed_at'], '2026-09-10T00:00:00Z')
        self.assertIn('pbp.json', proof['completion_source_ref']['path'])
        self.assertIn('rosters.json', proof['roster_source_ref']['path'])
        self.assertEqual(original, copy)

    def test_positive_stats_without_completed_game_witness_cannot_repair(self):
        from gamd.coverage import participation_proofs
        original, revised, entries = self.inputs(completed=False)
        report = participation_proofs(original, revised, [], entries, authority={})
        self.assertEqual(report['evidence_rows'], [])
        self.assertEqual(report['unresolved_player_games'][0]['code'], 'NO_VERIFIED_PBP_COMPLETION_WITNESS')

    def test_unknown_or_dnp_state_is_never_inferred_from_latest_roster(self):
        from gamd.coverage import participation_proofs
        for appeared in (None, False):
            original, revised, entries = self.inputs(current=history(5, appeared))
            report = participation_proofs(original, revised, [], entries, authority={})
            self.assertEqual(report['evidence_rows'], [])
            self.assertEqual(report['unresolved_player_games'][0]['code'], 'NO_NEW_POSITIVE_PARTICIPATION_WITNESS')

    def test_new_career_rows_do_not_expand_original_v3_history(self):
        from gamd.coverage import participation_proofs
        original, revised, entries = self.inputs(current=history(5, None))
        revised['data']['records'].append(history(1, season=2024, game_id='authored-2024-week-1'))
        report = participation_proofs(original, revised, [], entries, authority={})
        self.assertEqual(report['evidence_rows'], [])
        self.assertEqual(report['new_history_keys_ignored'], 1)

    def test_new_stats_revision_cannot_overwrite_known_original_history(self):
        from gamd.coverage import participation_proofs
        original, revised, entries = self.inputs(current=history(5, None))
        revised['data']['records'][0]['receiving_yards'] = 999
        report = participation_proofs(original, revised, [], entries, authority={})
        self.assertEqual(report['evidence_rows'], [])
        self.assertEqual(len(report['unchanged_known_history_revisions']), 1)
        self.assertFalse(report['unchanged_known_history_revisions'][0]['values_applied'])

    def test_position_conflict_cannot_relabel_original_role(self):
        from gamd.coverage import participation_proofs
        original, revised, entries = self.inputs(current=history(5, True, position='TE'))
        report = participation_proofs(original, revised, [], entries, authority={})
        self.assertEqual(report['evidence_rows'], [])
        self.assertEqual(report['unresolved_player_games'][0]['code'], 'DATED_POSITION_CONFLICT_WITH_ORIGINAL')

    def test_pbp_only_proof_does_not_manufacture_channel_zero(self):
        from gamd.coverage import participation_proofs
        current = history(5, True, identity_state='EXACT_DATED_ROSTER_GSIS', **dict.fromkeys(FIELDS))
        original, revised, entries = self.inputs(current=current)
        pbp = dict(season=2026, week=5, game_id='authored-week-5', posteam='PHI',
                   play_type='pass', pass_attempt='1', receiver_player_id='00-0000001',
                   play_id='123', observed_at='2026-10-25T00:00:00Z')
        report = participation_proofs(original, revised, [pbp], entries, authority={})
        self.assertEqual(report['evidence_rows'][0]['kind'], 'pbp_touch')
        self.assertEqual(report['evidence_rows'][0]['values'], {'role': 'receiver', 'play_id': '123'})
        self.assertNotIn('receiving_yards', report['evidence_rows'][0]['values'])

    def test_target_pbp_or_no_play_cannot_become_prior_touch_proof(self):
        from gamd.coverage import participation_proofs
        current = history(5, True, identity_state='EXACT_DATED_ROSTER_GSIS', **dict.fromkeys(FIELDS))
        original, revised, entries = self.inputs(current=current)
        base = dict(season=2026, week=5, game_id='authored-week-5', posteam='PHI',
                    play_type='pass', pass_attempt='1', receiver_player_id='00-0000001',
                    play_id='123', observed_at='2026-10-25T00:00:00Z')
        for update in ({'week': 7}, {'play_type': 'no_play'}, {'two_point_attempt': '1'}):
            report = participation_proofs(original, revised, [dict(base, **update)], entries, authority={})
            self.assertEqual(report['evidence_rows'], [])


class GamdCoverageAcceptedReceiptTests(unittest.TestCase):
    def setUp(self):
        import hashlib
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from gamd.coverage import ORIGINAL_CUTOFF, ORIGINAL_DECISIONS, ORIGINAL_PROJECTION, ORIGINAL_PREPARATION
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        def write(name, value):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = json.dumps(value, sort_keys=True).encode()
            path.write_bytes(raw)
            return {'path': name, 'sha256': hashlib.sha256(raw).hexdigest()}
        self.write = write
        old_manifest = write('research/authored-old-coverage.json', dict(purpose='COVERAGE_RECONCILIATION',
            original_cutoff=ORIGINAL_CUTOFF, original_decisions=ORIGINAL_DECISIONS,
            original_projection=ORIGINAL_PROJECTION, original_preparation_manifest=ORIGINAL_PREPARATION))
        self.artifacts = {name: write('data/private/research-lab/' + folder + '/' + name + '.json', {'authored': name})
                          for name, folder in (('report', 'exports'), ('csv', 'exports'),
                                               ('revised_projection', 'gamd/coverage/authored'), ('proofs', 'gamd/coverage/authored'))}
        self.catalogue = {'path': 'research/authored-catalogue.json', 'sha256': 'a' * 64}
        self.receipt = dict(status='COMPLETE', classification='REAL_RETAINED_SOURCE_COVERAGE_RECONCILIATION',
            original_cutoff=ORIGINAL_CUTOFF, original_forecasts_modified=0, target_outcomes_used=0,
            provider_calls=0, new_fits=0, catalogue=self.catalogue, manifest=old_manifest,
            artifacts=self.artifacts, rows=[{'authored_index': i} for i in range(783)], summary={'total': 783},
            download='/api/download/authored.csv', json_download='/api/download/authored.json')
        self.receipt_ref = write('data/private/research-lab/gamd/coverage/authored/RECEIPT.json', self.receipt)
        self.consumer_ref = {'path': 'research/authored-consumer.json', 'sha256': 'b' * 64}
        self.campaign_ref = {'path': 'research/authored-campaign.json', 'sha256': 'c' * 64}
        self.consumer = {'catalogue': self.catalogue, 'campaign': self.campaign_ref}
        self.current_manifest = {'path': 'research/authored-new-coverage.json', 'sha256': 'd' * 64}
        self.policy = dict(consumer=self.consumer_ref, campaign_ref=self.campaign_ref,
                           coverage_manifest=self.current_manifest, accepted_coverage_receipt=self.receipt_ref)
        self.active = dict(coverage_manifest=self.current_manifest, accepted_coverage_receipt=self.receipt_ref)

    def reused(self, policy=None):
        from contextlib import ExitStack
        from unittest.mock import Mock, patch
        from nfl_model import gamd_gate as gate
        from gamd.coverage import run
        original_read = gate.read
        allowed = (self.receipt_ref, self.receipt['manifest'])
        def metadata_only(root, reference, committed=False):
            self.assertIn(reference, allowed, 'No source or original projection may be parsed on reuse')
            if reference == self.receipt['manifest']:
                self.assertTrue(committed)
            return original_read(root, reference, committed=False)
        with ExitStack() as stack:
            consumer = stack.enter_context(patch.object(gate, 'consumer_for', return_value=(self.consumer, self.consumer_ref)))
            active = stack.enter_context(patch.object(gate, 'active_registration', return_value=self.active))
            stack.enter_context(patch.object(gate, 'read', side_effect=metadata_only))
            source = stack.enter_context(patch.object(gate, 'source_authority', side_effect=AssertionError('No source context')))
            store = Mock()
            result = run(self.root, store, policy or self.policy)
            consumer.assert_called_once_with(self.root, gate.DEVELOPMENT)
            active.assert_called_once_with(self.root, self.consumer)
            source.assert_not_called()
            store.append.assert_not_called()
            store.latest.assert_not_called()
            return result

    def test_explicit_accepted_receipt_reuses_before_source_context_without_rewriting(self):
        before = (self.root / self.receipt_ref['path']).read_bytes()
        result = self.reused()
        self.assertEqual(result['status'], 'UNCHANGED')
        self.assertTrue(result['evidence_reused'])
        self.assertEqual(result['receipt_ref'], self.receipt_ref)
        self.assertEqual(result['summary']['total'], 783)
        self.assertNotIn('rows', result)
        self.assertEqual((self.root / self.receipt_ref['path']).read_bytes(), before)

    def test_caller_cannot_substitute_accepted_receipt_consumer_campaign_or_manifest(self):
        for key in ('consumer', 'campaign_ref', 'coverage_manifest', 'accepted_coverage_receipt'):
            with self.subTest(key=key):
                policy = dict(self.policy, **{key: {'path': 'unbound.json', 'sha256': 'e' * 64}})
                with self.assertRaisesRegex(ValueError, 'MISMATCH|NOT_ACTIVE'):
                    self.reused(policy)

    def test_receipt_child_corruption_fails_without_source_reprojection(self):
        (self.root / self.artifacts['proofs']['path']).write_text('changed authored evidence')
        with self.assertRaisesRegex(ValueError, 'ACCEPTED_ARTIFACT_HASH'):
            self.reused()

    def test_incomplete_population_and_changed_original_boundary_are_rejected(self):
        for change in ({'summary': {'total': 782}}, {'provider_calls': 1}, {'original_forecasts_modified': 1}):
            with self.subTest(change=change):
                new = self.write(self.receipt_ref['path'], dict(self.receipt, **change))
                self.policy['accepted_coverage_receipt'] = self.active['accepted_coverage_receipt'] = new
                self.receipt_ref = new
                with self.assertRaisesRegex(ValueError, 'ACCEPTED_COVERAGE_'):
                    self.reused()

    def test_live_charter_blocks_standing_reuse_and_missing_acceptance_does_not_authenticate(self):
        from unittest.mock import patch
        from gamd.coverage import _reuse_accepted
        with patch('nfl_model.gamd_gate.consumer_for') as consumer:
            self.assertIsNone(_reuse_accepted(self.root, {}))
            consumer.assert_not_called()
            (self.root / 'TASK_CHARTER.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'LIVE_TASK_PRECEDENCE'):
                _reuse_accepted(self.root, self.policy)
            consumer.assert_not_called()


class GamdCoverageDisplayTests(unittest.TestCase):
    def test_full_population_game_identities_and_counts_survive_display_compaction(self):
        from server import coverage_display_record
        lineage = {'source_basis': ['authored source'] * 100, 'source_lineage': [{'authored': 'x' * 2000}]}
        known = dict(season=2026, week=1, game_id='2026_01_DAL_PHI', team='PHI', appeared=True, **lineage)
        unknown = dict(season=2026, week=2, game_id='2026_02_PHI_NYG', team='PHI', appeared=None, **lineage)
        hist = dict(prior_rows=2, verified_appearances=1, unknown_appearances=1, explicit_dnp=0,
                    channel_supported_appearances=1, prior_usage_mean=5, prior_usage_cases=1,
                    verified_games=[known], unknown_games=[unknown], dnp_games=[])
        rows = [dict(season=2026, week=7, game_id='authored-week7', gsis=f'authored-{i}',
                     name=f'Authored player {i}', team='PHI', channel='receiving', priority=2,
                     before=hist, after=hist, original={'large_provenance': lineage},
                     other_team_history=[dict(known, team='DAL')], frozen_reason='UNRESOLVED_PRIOR_PARTICIPATION',
                     source_repaired_reason='UNRESOLVED_PRIOR_PARTICIPATION', challenger_source_supported=True)
                for i in range(783)]
        record = dict(status='COMPLETE', rows=rows, summary={'total': 783},
                      download='/api/download/full.csv', json_download='/api/download/full.json', record_hash='a' * 64)
        before = deepcopy(record)
        result = coverage_display_record(record)
        self.assertEqual(record, before)
        self.assertEqual(len(result['rows']), 783)
        self.assertEqual([r['gsis'] for r in result['rows']], [r['gsis'] for r in rows])
        row = result['rows'][0]
        self.assertEqual(row['before']['prior_usage_mean'], 5)
        self.assertEqual(row['after']['unknown_appearances'], 1)
        self.assertEqual(row['after']['unknown_games'][0]['game_id'], unknown['game_id'])
        self.assertIsNone(row['after']['unknown_games'][0]['appeared'])
        self.assertEqual(row['after']['verified_games'][0]['game_id'], known['game_id'])
        self.assertEqual(row['other_team_history'][0]['team'], 'DAL')
        self.assertNotIn('source_lineage', json.dumps(result))
        self.assertNotIn('original', row)
        self.assertEqual(result['json_download'], record['json_download'])
        self.assertEqual(result['record_hash'], record['record_hash'])
        self.assertLess(len(json.dumps(result)), len(json.dumps(record)) / 3)
        status = coverage_display_record(record, include_rows=False)
        self.assertNotIn('rows', status)
        self.assertEqual(status['summary']['total'], 783)
        self.assertEqual(status['download'], record['download'])


if __name__ == '__main__':
    unittest.main()
