"""Authored inputs through real hashes and SQLite; no football data or fitting."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from core import Store, encoded
from nfl_model import continuous_gamd_gate as gate
from gamd.continuous_settlement import run, latest_evaluations


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'store')
        self.c = SimpleNamespace(root=self.root, store=self.store)
        self.count = 0
        self.identities = [[2026, 8, '2026_08_A_B', 'p' + str(n), 'A', 'receiving'] for n in range(2)]
        law = {'support': [0, 10, 20], 'pmf': [.2, .5, .3], 'mean': 11, 'median': 10, 'lower80': 0, 'upper80': 20}
        self.forecasts = [dict(identity=i, law=law, point_mean=11, point_median=10, expected_opportunities=3) for i in self.identities]
        self.manifest = self.write('forward/MANIFEST.json', {'game_id': '2026_08_A_B', 'output': 'forward'})
        self.seal = self.write('forward/SEAL.json', {'manifest': self.manifest, 'forecasts': self.forecasts})
        self.original = (self.root / self.seal['path']).read_bytes()
        self.store.append('continuous_benchmark', self.manifest['sha256'],
                          {'manifest': self.manifest, 'forecasts': self.forecasts, 'seal_ref': self.seal,
                           'lane': 'ADAPTIVE_CHALLENGER'}, immutable=True)

    def write(self, path, body):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded(body))
        return gate.ref(self.root, target)

    def outcome(self, n, yards=10, opportunity=3, appeared=True):
        return dict(zip(('season', 'week', 'game_id', 'gsis', 'team', 'channel'), self.identities[n]),
                    yards=yards, opportunity=opportunity, appeared=appeared)

    def receipt(self, rows, clock='2026-11-01T00:00:00Z', complete=True):
        self.count += 1
        path = self.root / ('outcomes/' + str(self.count) + '.jsonl')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'\n'.join(encoded(row) for row in rows))
        body = {'history': gate.ref(self.root, path), 'verified_complete_games': ['2026_08_A_B'] if complete else [],
                'source_cutoff': clock, 'cohort': {'fixtures': [], 'targets': []}}
        return {**body, 'receipt': self.write('outcomes/' + str(self.count) + '-receipt.json', body)}

    def test_partial_results_survive_unknown_player(self):
        value = run(self.c, self.receipt([self.outcome(0)]))[0]
        self.assertEqual((value['status'], value['issued_n'], value['scored_n'], value['unknown_n']), ('PARTIAL', 2, 1, 1))
        self.assertEqual(value['metrics']['denominators']['mae'], 1)
        self.assertEqual(value['lane'], 'ADAPTIVE_CHALLENGER')

    def test_missing_workload_does_not_discard_yards(self):
        value = run(self.c, self.receipt([self.outcome(0, opportunity=None), self.outcome(1)]))[0]
        self.assertEqual(value['scored_n'], 2)
        self.assertEqual(value['workload_scored_n'], 1)
        self.assertEqual(value['metrics']['denominators']['workload_mae'], 1)
        self.assertEqual(value['metrics']['threshold_observations_n'], 16)

    def test_repeat_and_receipt_clock_only_change_are_idempotent(self):
        rows = [self.outcome(0)]
        first = run(self.c, self.receipt(rows))[0]
        again = run(self.c, self.receipt(rows, '2026-11-02T00:00:00Z'))[0]
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(len(self.store.records('continuous_evaluation')), 1)

    def test_partial_then_complete_preserves_old_version_and_seal(self):
        first = run(self.c, self.receipt([self.outcome(0)]))[0]
        second = run(self.c, self.receipt([self.outcome(0), self.outcome(1)], '2026-11-02T00:00:00Z'))[0]
        self.assertEqual(second['evaluation_version'], 2)
        self.assertEqual(second['previous_evaluation'], first['id'])
        self.assertEqual(second['status'], 'SETTLED')
        self.assertEqual(self.store.latest('continuous_evaluation', first['id'])['status'], 'PARTIAL')
        self.assertEqual((self.root / self.seal['path']).read_bytes(), self.original)

    def test_a_b_a_correction_is_a_new_current_version(self):
        first = run(self.c, self.receipt([self.outcome(0, 10), self.outcome(1)]))[0]
        second = run(self.c, self.receipt([self.outcome(0, 20), self.outcome(1)], '2026-11-02T00:00:00Z'))[0]
        third = run(self.c, self.receipt([self.outcome(0, 10), self.outcome(1)], '2026-11-03T00:00:00Z'))[0]
        self.assertEqual(third['content_hash'], first['content_hash'])
        self.assertNotEqual(third['id'], first['id'])
        self.assertEqual(third['previous_evaluation'], second['id'])
        self.assertEqual(latest_evaluations(self.store.records('continuous_evaluation'))[0]['evaluation_version'], 3)

    def test_latest_selection_ignores_lexicographic_record_order(self):
        rows = [dict(forecast_manifest=self.manifest, evaluation_version=n, id=key)
                for n, key in ((3, 'a'), (2, 'z'), (1, 'y'))]
        self.assertEqual(latest_evaluations(rows)[0]['evaluation_version'], 3)

    def test_stale_source_cannot_revert_correction(self):
        newest = run(self.c, self.receipt([self.outcome(0, 20)], '2026-11-03T00:00:00Z'))[0]
        older = run(self.c, self.receipt([self.outcome(0, 10)], '2026-11-02T00:00:00Z'))[0]
        self.assertEqual(newest['id'], older['id'])

    def test_unchanged_newer_receipt_still_prevents_stale_correction(self):
        first = run(self.c, self.receipt([self.outcome(0, 10)], '2026-11-01T00:00:00Z'))[0]
        run(self.c, self.receipt([self.outcome(0, 10)], '2026-11-03T00:00:00Z'))
        stale = run(self.c, self.receipt([self.outcome(0, 20)], '2026-11-02T00:00:00Z'))[0]
        self.assertEqual(stale['id'], first['id'])
        self.assertEqual(len(self.store.get_setting('continuous_benchmark_outcomes')['older_sources_ignored']), 1)

    def test_no_future_fixture_does_not_prevent_settlement(self):
        self.assertEqual(run(self.c, self.receipt([self.outcome(0), self.outcome(1)]))[0]['status'], 'SETTLED')

    def test_no_verified_finality_means_no_evaluation(self):
        self.assertEqual(run(self.c, self.receipt([self.outcome(0)], complete=False)), [])
        self.assertEqual(self.store.records('continuous_evaluation'), [])

    def test_dnp_is_not_a_bet_or_zero_prediction(self):
        value = run(self.c, self.receipt([self.outcome(n, None, None, False) for n in (0, 1)]))[0]
        self.assertEqual((value['status'], value['scored_n'], value['verified_dnp_n']), ('SETTLED', 0, 2))
        self.assertEqual(value['paper_bets'], 0)
        self.assertIsNone(value['genuine_roi'])
        self.assertEqual(value['metrics']['denominators']['mae'], 0)

    def test_all_unknown_still_persists_visible_partial(self):
        value = run(self.c, self.receipt([]))[0]
        self.assertEqual((value['status'], value['scored_n'], value['unknown_n']), ('PARTIAL', 0, 2))

    def test_duplicate_conflicting_outcome_rejected(self):
        with self.assertRaisesRegex(ValueError, 'CONFLICTING_OUTCOME_IDENTITY'):
            run(self.c, self.receipt([self.outcome(0, 10), self.outcome(0, 20)]))

    def test_duplicate_same_outcome_with_separate_lineage_coalesces(self):
        value = run(self.c, self.receipt([self.outcome(0), dict(self.outcome(0), provenance=['another retained source'])]))[0]
        self.assertEqual(value['scored_n'], 1)

    def test_receipt_tampering_rejected(self):
        receipt = self.receipt([self.outcome(0)])
        receipt['verified_complete_games'] = ['made-up-finality']
        with self.assertRaisesRegex(ValueError, 'OUTCOME_RECEIPT_BINDING'):
            run(self.c, receipt)

    def test_seal_tampering_rejected(self):
        (self.root / self.seal['path']).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'HASH_CHANGED'):
            run(self.c, self.receipt([self.outcome(0)]))

    def test_signed_yards_and_zero_workload_are_real_observations(self):
        value = run(self.c, self.receipt([self.outcome(0, -2, 0), self.outcome(1, 0, 0)]))[0]
        self.assertEqual(value['scored_n'], 2)
        self.assertEqual(value['workload_scored_n'], 2)

    def test_fractional_workload_rejected(self):
        with self.assertRaisesRegex(ValueError, 'INVALID_VERIFIED_WORKLOAD'):
            run(self.c, self.receipt([self.outcome(0, 10, .2)]))

    def test_legacy_unversioned_record_can_be_followed(self):
        first = run(self.c, self.receipt([self.outcome(0)]))[0]
        legacy = {k: v for k, v in first.items() if k not in ('id', 'revision', 'record_hash', 'recorded_at', 'evaluation_version', 'content_hash')}
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.append('continuous_benchmark', self.manifest['sha256'],
                         {'manifest': self.manifest, 'forecasts': self.forecasts, 'seal_ref': self.seal}, immutable=True)
            store.append('continuous_evaluation', self.manifest['sha256'], legacy, immutable=True)
            value = run(SimpleNamespace(root=self.root, store=store),
                        self.receipt([self.outcome(0), self.outcome(1)], '2026-11-02T00:00:00Z'))[0]
            self.assertEqual(value['evaluation_version'], 2)


if __name__ == '__main__':
    unittest.main()
