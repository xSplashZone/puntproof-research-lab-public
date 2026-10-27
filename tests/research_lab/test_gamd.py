"""Offline synthetic unit cases. None constitutes real GAMD research evidence."""
import copy
import csv
from datetime import datetime
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

LAB = Path(__file__).resolve().parents[2] / 'tools' / 'research_lab'
sys.path.insert(0, str(LAB))
from gamd.features import FEATURE_NAMES, MECHANISM, build_examples, build_target_features, masked
from gamd.source import FIELDS, project, read_partition
from gamd.trainer import (FAMILIES, FOLDS, PARAMETERS, empirical_law, paired_week_interval, predict_artifact,
                          research_decision, run_trial, score_law, split_examples, write_json_once,
                          _calibrate, _law_for, _residual_draws)


def row(season=2021, week=1, gsis='player-a', team='DAL', channel='receiving', appeared=True,
        opportunity=4, yards=40, opponent='PHI', position='WR'):
    return {'season': season, 'week': week, 'game_id': f'{season}_{week:02d}_{team}_{opponent}',
            'gsis': gsis, 'team': team, 'opponent': opponent, 'position': position,
            'channel': channel, 'appeared': appeared, 'opportunity': opportunity, 'yards': yards,
            'participation_state': 'VERIFIED_TEST_CASE' if appeared else 'UNKNOWN_TEST_CASE',
            'source_observed_at': '2026-10-27T00:00:00+00:00',
            'provenance': [{'path': 'SYNTHETIC_TEST_FIXTURE', 'sha256': '0' * 64}]}


def feature(example, name):
    return example['x'][FEATURE_NAMES.index(name)]


class SourceSentinelTests(unittest.TestCase):
    def test_raw_missing_gsis_cannot_merge_players_or_invent_pbp_involvement(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def partition(dataset, rows):
                path = root / (dataset + '.csv')
                with path.open('w', encoding='utf-8', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=FIELDS[dataset])
                    writer.writeheader()
                    writer.writerows(rows)
                entry = {'dataset': dataset, 'season': 2023, 'path': path.name,
                         'allowed_fields': FIELDS[dataset], 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'observed_at': '2026-10-01T00:00:00Z'}
                return list(read_partition(root, entry, years=[2023]))
            roster = partition('rosters', [
                {'season': 2023, 'week': 1, 'season_type': 'NA', 'game_type': 'REG',
                 'gsis_id': 'NA', 'team': 'DAL', 'position': 'WR'},
                {'season': 2023, 'week': 1, 'season_type': 'NaN', 'game_type': 'REG',
                 'gsis_id': 'nan', 'team': 'DAL', 'position': 'RB'},
                {'season': 2023, 'week': 1, 'game_type': 'REG',
                 'gsis_id': 'authored-player', 'team': 'DAL', 'position': 'WR'},
            ])
            pbp = partition('pbp', [
                {'season': 2023, 'week': 1, 'season_type': 'REG', 'game_id': '2023_01_DAL_PHI',
                 'home_team': 'PHI', 'away_team': 'DAL', 'posteam': 'DAL', 'play_type': 'pass',
                 'receiver_player_id': 'NA', 'rusher_player_id': 'nan', 'passer_player_id': 'NaN'},
            ])
            stats = partition('player_stats', [
                {'season': 2023, 'week': 1, 'season_type': 'REG', 'player_id': 'authored-player',
                 'team': 'DAL', 'position': 'WR', 'opponent_team': 'PHI',
                 'targets': 'NA', 'receiving_yards': 'NaN', 'carries': 'nan'},
            ])
            self.assertEqual([r['gsis_id'] for r in roster], [None, None, 'authored-player'])
            self.assertIsNone(pbp[0]['receiver_player_id'])
            self.assertIsNone(stats[0]['targets'])
            self.assertIsNone(stats[0]['receiving_yards'])
            result = project([('rosters', roster), ('pbp', pbp), ('player_stats', stats)])
            self.assertEqual(len(result['rows']), 1)
            self.assertIsNone(result['rows'][0]['appeared'])
            self.assertIsNone(result['rows'][0]['opportunity'])
            self.assertIsNone(result['rows'][0]['yards'])
            self.assertEqual(result['audit']['problems'], [])
            self.assertEqual(result['audit']['label_rows_by_channel_season_week']['receiving'], {})


class FeaturesTests(unittest.TestCase):
    def test_target_and_same_week_outcomes_never_enter_features(self):
        rows = [row(week=1), row(week=2), row(week=2, gsis='player-b')]
        original = build_examples(rows, 'receiving', apply_mask=False)
        changed = copy.deepcopy(rows)
        changed[1]['yards'] = 999
        changed[1]['opportunity'] = 99
        changed[2]['yards'] = -5
        changed[2]['opportunity'] = 0
        altered = build_examples(changed, 'receiving', apply_mask=False)
        before = {tuple(e['identity']): e for e in original['examples']}
        after = {tuple(e['identity']): e for e in altered['examples']}
        for key in before:
            self.assertEqual(before[key]['x'], after[key]['x'])

    def test_unknown_dnp_and_verified_zero_remain_distinct(self):
        rows = [row(week=1, opportunity=0, yards=0),
                row(week=2, appeared=None, opportunity=None, yards=None),
                row(week=3, appeared=False, opportunity=0, yards=0), row(week=4)]
        built = build_examples(rows, 'receiving', apply_mask=False)
        last = built['examples'][-1]
        self.assertEqual(last['verified_history_count'], 1)
        self.assertEqual(last['unknown_history_count'], 1)
        self.assertEqual(last['dnp_history_count'], 1)
        self.assertEqual(feature(last, 'same_team_calendar_games'), 3)
        self.assertEqual(last['baseline'], 0.)
        self.assertEqual(built['audit']['label_rows'], 2)
        self.assertEqual(built['audit']['unknowns_imputed_as_zero'], 0)

    def test_four_observed_plus_two_unknown_is_not_six_appearances(self):
        rows = [row(week=w) for w in range(1, 5)]
        rows += [row(week=w, appeared=None, opportunity=None, yards=None) for w in (5, 6)]
        rows.append(row(week=7))
        last = build_examples(rows, 'receiving', apply_mask=False)['examples'][-1]
        self.assertEqual(last['verified_history_count'], 4)
        self.assertEqual(last['unknown_history_count'], 2)
        self.assertEqual(feature(last, 'gap_since_verified_periods'), 3)
        self.assertEqual(last['baseline'], 40.)

    def test_prior_team_workload_is_separate_from_same_team_baseline(self):
        rows = [row(week=1), row(week=2), row(week=3, team='NYG')]
        last = build_examples(rows, 'receiving', apply_mask=False)['examples'][-1]
        self.assertIsNone(last['baseline'])
        self.assertEqual(feature(last, 'verified_other_team_games'), 2)
        self.assertEqual(feature(last, 'observed_other_team_yards_mean5'), 40.)
        self.assertTrue(last['changed_role'])

    def test_mask_depends_on_identity_not_outcome(self):
        a = row(week=4)
        b = dict(a, yards=500, opportunity=80)
        self.assertEqual(masked(a), masked(b))
        rows = [row(week=w) for w in range(1, 19)]
        masked_weeks = [r['week'] for r in rows if masked(r)]
        self.assertTrue(masked_weeks)
        built = build_examples(rows, 'receiving')
        last = built['examples'][-1]
        self.assertEqual(last['masked_history_count'], sum(w < 18 for w in masked_weeks))
        self.assertEqual(last['verified_history_count'] + last['unknown_history_count'], 17)

    def test_negative_yards_are_retained(self):
        rows = [row(week=1, opportunity=2, yards=-3), row(week=2)]
        last = build_examples(rows, 'receiving', apply_mask=False)['examples'][-1]
        self.assertEqual(last['baseline'], -3.)
        self.assertEqual(feature(last, 'observed_signed_yards_per_opportunity12'), -1.5)

    def test_targets_with_incomplete_labels_are_not_zero_outcomes(self):
        rows = [row(week=1, yards=None), row(week=2)]
        built = build_examples(rows, 'receiving', apply_mask=False)
        self.assertEqual(len(built['examples']), 1)
        self.assertEqual(built['examples'][0]['unknown_history_count'], 1)
        self.assertIsNone(built['examples'][0]['baseline'])

    def test_duplicate_keys_protected_seasons_and_invalid_labels_fail(self):
        for rows, message in [([row(), row()], 'DUPLICATE'),
                              ([row(season=2025)], 'UNADMITTED'),
                              ([row(appeared=None)], 'PRODUCTION'),
                              ([row(opportunity=-1)], 'NEGATIVE'),
                              ([row(yards=float('nan'))], 'NONINTEGER'),
                              ([row(yards=1.1)], 'NONINTEGER')]:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                build_examples(rows, 'receiving')

    def test_timezone_and_provenance_required(self):
        invalid = row()
        invalid['source_observed_at'] = '2026-10-27T00:00:00'
        with self.assertRaisesRegex(ValueError, 'NAIVE'):
            build_examples([invalid], 'receiving')
        invalid = row()
        invalid['provenance'] = []
        with self.assertRaisesRegex(ValueError, 'PROVENANCE'):
            build_examples([invalid], 'receiving')

    def test_future_input_matches_batch_and_rejects_target_week(self):
        prior = [row(season=2026, week=w) for w in range(1, 5)]
        labelled_target = row(season=2026, week=5)
        target = dict(labelled_target, appeared=None, opportunity=None, yards=None, kickoff_at='2026-10-30T00:00:00+00:00')
        batch = build_examples(prior + [labelled_target], 'receiving', apply_mask=False, allowed_seasons=(2026,))['examples'][-1]
        future = build_target_features(prior, target, '2026-10-27T01:00:00+00:00')
        self.assertEqual(batch['x'], future['x'])
        with self.assertRaisesRegex(ValueError, 'TARGET_OR_FUTURE'):
            build_target_features(prior + [labelled_target], target, '2026-10-27T01:00:00+00:00')

    def test_future_requires_null_targets_and_pregame_cutoff(self):
        target = row(season=2026, week=8, appeared=None, opportunity=None, yards=None)
        target['kickoff_at'] = '2026-10-30T00:00:00+00:00'
        good_cutoff = '2026-10-27T01:00:00+00:00'
        bad = dict(target, appeared=True, opportunity=0, yards=0)
        with self.assertRaisesRegex(ValueError, 'LABELS_MUST_BE_NULL'):
            build_target_features([], bad, good_cutoff)
        with self.assertRaisesRegex(ValueError, 'NOT_PREGAME'):
            build_target_features([], target, '2026-10-30T00:00:00+00:00')
        with self.assertRaisesRegex(ValueError, 'UNADMITTED_SEASON'):
            build_target_features([row(season=2025)], target, good_cutoff)
        with self.assertRaisesRegex(ValueError, 'OBSERVED_AFTER_CUTOFF'):
            build_target_features([row(season=2026, week=1)], target, '2026-10-26T00:00:00+00:00')


class EvaluationTests(unittest.TestCase):
    def test_cached_residual_laws_exactly_match_prior_quantile_arithmetic(self):
        # Reference preserves the pre-optimization sort and floating operations.
        def previous_quantile(values, probability):
            ordered = sorted(values)
            index = (len(ordered) - 1) * probability
            lo, hi = int(math.floor(index)), int(math.ceil(index))
            return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)

        observed = [{'yards': (i % 7) * 3.1 - 6.25, 'workload': 1.} for i in range(27)]
        predictions = {'point': [i * .13 for i in range(27)],
                       'positive_mean': [i * .17 for i in range(27)]}
        for family in ('hist_gradient_boosting', 'hurdle_glm'):
            model = {'family': family}
            calibration = _calibrate(model, predictions, observed)
            residuals = calibration['residuals']
            # Baseline draws also move out of the per-player scoring loop.
            self.assertEqual(_residual_draws(residuals, 64),
                             [previous_quantile(residuals, (j + .5) / 64) for j in range(64)])
            for count in range(65):
                point = -2.125 + count * .19
                forecast = {'point': [point], 'positive_mean': [point], 'positive_probability': [count / 64]}
                if family == 'hurdle_glm':
                    expected_samples = [0.] * (64 - count) + [
                        point + previous_quantile(residuals, (j + .5) / count) for j in range(count)]
                else:
                    expected_samples = [point + previous_quantile(residuals, (j + .5) / 64) for j in range(64)]
                expected = empirical_law(expected_samples)
                with patch('gamd.trainer.quantile', side_effect=AssertionError('Per-row residual sort reintroduced')):
                    self.assertEqual(_law_for(model, calibration, forecast, 0), expected)

    def test_split_excludes_calibration_from_training_and_target_year(self):
        examples = [{'season': year, 'week': week} for year in range(2021, 2025) for week in range(1, 19)]
        for fold in FOLDS:
            train, calibration, evaluation = split_examples(examples, fold)
            self.assertEqual(max((r['season'], r['week']) for r in train), tuple(fold['train_end']))
            self.assertEqual(len(calibration), 6)
            self.assertTrue(all(r['season'] == fold['evaluation_season'] for r in evaluation))
            keys = lambda rows: {(r['season'], r['week']) for r in rows}
            self.assertFalse(keys(train) & keys(calibration))
            self.assertFalse(keys(train) & keys(evaluation))

    def test_law_signed_support_mean_crps_and_thresholds(self):
        law = empirical_law([-1, 1])
        self.assertEqual(law['support'], [-1, 1])
        self.assertEqual(law['mean'], 0.)
        score = score_law(law, 0)
        self.assertAlmostEqual(score['crps'], .5)
        self.assertAlmostEqual(score['negative_brier'], .25)
        self.assertEqual(score['zero_brier'], 1.)
        fractional = empirical_law([.25, 1.75])
        self.assertAlmostEqual(fractional['mean'], 1.)
        self.assertAlmostEqual(sum(fractional['pmf']), 1.)
        self.assertAlmostEqual(empirical_law([.25])['mean'], .25)

    def test_deterministic_law_has_absolute_error_crps(self):
        law = empirical_law([20.] * 64)
        score = score_law(law, 30)
        self.assertEqual(score['crps'], 10.)
        self.assertEqual(score['threshold_probabilities']['20'], 1.)
        self.assertEqual(score['threshold_probabilities']['30'], 0.)

    def test_paired_intervals_require_weeks_and_keep_baseline_match(self):
        rows = [{'season': 2023, 'week': w, 'absolute_error': 1., 'baseline_score': {'absolute_error': 3.}}
                for w in range(1, 19)]
        rows.append({'season': 2023, 'week': 1, 'absolute_error': 999., 'baseline_score': None})
        result = paired_week_interval(rows, bootstrap_samples=100)
        self.assertEqual(result['paired_n'], 18)
        self.assertEqual(result['lower'], -2.)
        self.assertEqual(result['upper'], -2.)
        self.assertEqual(result['multiplicity_trials'], 6)
        self.assertEqual(paired_week_interval(rows[:3])['status'], 'INSUFFICIENT_WEEKS')

    def test_regression_rejected_without_claiming_real_negative_evidence(self):
        rows = [{'season': 2023, 'week': w, 'absolute_error': 5., 'crps': 4., 'high_usage': True,
                 'baseline_score': {'absolute_error': 3., 'crps': 2.}} for w in range(1, 19)]
        decision = research_decision(rows)
        self.assertEqual(decision['status'], 'REJECTED')
        self.assertIn('HIGH_USAGE_MAE_REGRESSION', decision['reasons'])
        self.assertFalse(decision['promotion_authorized'])

    def test_recipe_is_three_distinct_families_and_no_random_validation(self):
        self.assertEqual(len(FAMILIES), 3)
        self.assertEqual(set(FAMILIES), {'hurdle_glm', 'hist_gradient_boosting', 'extra_trees'})
        self.assertFalse(PARAMETERS['hist_gradient_boosting']['early_stopping'])
        self.assertEqual(PARAMETERS['extra_trees']['n_jobs'], 1)

    def test_write_once_refuses_identical_and_different_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'receipt.json'
            first = write_json_once(path, {'value': 1})
            for value in (1, 2):
                with self.assertRaises(FileExistsError):
                    write_json_once(path, {'value': value})
            self.assertEqual(json.loads(path.read_text())['value'], 1)
            self.assertEqual(len(first['sha256']), 64)


@unittest.skipUnless(importlib.util.find_spec('sklearn') and importlib.util.find_spec('numpy'), 'Requires the isolated locked V3 runtime; never install packages for a test')
class TrainerSyntheticContractTests(unittest.TestCase):
    """Small fixture fits verify implementation; not research or model performance."""
    def test_each_family_persists_two_folds_and_full_probability_laws(self):
        rows = []
        for year in range(2021, 2025):
            for week in range(1, 19):
                for player in range(8):
                    workload = (week + player + year) % 9
                    yards = workload * (4 + player % 3) + (week % 3 - 1 if workload else 0)
                    rows.append(row(season=year, week=week, gsis=f'fixture-{player}', opportunity=workload, yards=yards))
        for family in FAMILIES:
            with self.subTest(family=family), tempfile.TemporaryDirectory() as folder:
                output = Path(folder) / family
                result = run_trial(rows, 'receiving', family, output, spec_hash='SYNTHETIC_CONTRACT_TEST')
                self.assertEqual(result['completed_folds'], 2)
                self.assertGreaterEqual(result['fitted_models'], 4)
                self.assertIsNone(result['genuine_roi'])
                self.assertIsNone(result['fixed_f_comparison'])
                self.assertEqual(result['metrics']['n'], 288)
                self.assertTrue((output / 'development_2024' / 'FITTED_MODEL.joblib').exists())
                import joblib
                artifact = joblib.load(output / 'development_2024' / 'FITTED_MODEL.joblib')
                target = row(season=2026, week=8, gsis='fixture-1', appeared=None, opportunity=None, yards=None)
                target['kickoff_at'] = '2026-10-30T00:00:00+00:00'
                inputs = build_target_features(rows, [target], '2026-10-27T01:00:00+00:00')
                forecasts = predict_artifact(artifact, inputs, model_sha256='SYNTHETIC_OWN_MODEL')
                self.assertEqual(len(forecasts), 1)
                self.assertEqual(forecasts[0]['training_through'], [2023, 12])
                self.assertEqual(forecasts[0]['calibration_through'], [2023, 18])
                self.assertIsNone(forecasts[0]['genuine_roi'])
                self.assertEqual(forecasts[0]['model_sha256'], 'SYNTHETIC_OWN_MODEL')
                laws = [json.loads(line) for line in (output / 'development_2024' / 'LAWS.jsonl').read_text().splitlines()]
                for record in laws:
                    law = record['candidate']
                    self.assertAlmostEqual(sum(law['pmf']), 1.)
                    self.assertAlmostEqual(law['mean'], sum(x * p for x, p in zip(law['support'], law['pmf'])))
                before = (output / 'RESULT.json').read_bytes()
                with self.assertRaisesRegex(FileExistsError, 'ALREADY_CONSUMED'):
                    run_trial(rows, 'receiving', family, output)
                self.assertEqual((output / 'RESULT.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
