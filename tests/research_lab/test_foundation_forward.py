"""Forward selection and worker isolation with authored inputs, never model fits."""
import contextlib
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from core import Store, encoded
from nfl_model import continuous_gamd_gate as gate
from gamd.continuous_forward import select_models, target_features, FIXED, ADAPTIVE
from gamd.continuous_runtime import observe
from gamd.runtime import Coordinator


def attempt(name, process='DEVELOPMENT', finished='2026-10-20T00:00:00Z', schema='continuous_individual_context_v1', score=1):
    return {'id': name, 'finished_at': finished,
            'plan': {'channel': 'receiving', 'template_id': 'individual_context_receiving' if process == 'DEVELOPMENT' else 'scheduled_receiving',
                     'process': process},
            'result': {'feature_schema': schema, 'status': 'REJECTED', 'model': {'path': name, 'sha256': 'a' * 64},
                       'metrics': {'mae': score}}}


class ForwardTests(unittest.TestCase):
    def test_fixed_is_first_artifact_not_best_score(self):
        rows = [attempt('first', score=100), attempt('later', finished='2026-10-22T00:00:00Z', score=1)]
        selected = dict(select_models(rows, 'receiving', '2026-10-27T00:00:00Z', 'continuous_individual_context_v1'))
        self.assertEqual(selected[FIXED]['id'], 'first')

    def test_adaptive_is_latest_eligible_saved_refit(self):
        rows = [attempt('first', 'SCHEDULED_REFIT'), attempt('latest', 'SCHEDULED_REFIT', '2026-10-26T00:00:00Z'),
                attempt('future', 'SCHEDULED_REFIT', '2026-10-28T00:00:00Z'),
                attempt('incompatible', 'SCHEDULED_REFIT', '2026-10-26T12:00:00Z', schema='different')]
        selected = dict(select_models(rows, 'receiving', '2026-10-27T00:00:00Z', 'continuous_individual_context_v1'))
        self.assertEqual(selected[ADAPTIVE]['id'], 'latest')

    def test_dataset_revision_does_not_require_refitting_compatible_model(self):
        old = attempt('original')
        selected = dict(select_models([old], 'receiving', '2026-10-27T00:00:00Z', 'continuous_individual_context_v1'))
        self.assertEqual(selected[FIXED]['id'], 'original')

    def test_unknown_completion_time_is_not_pregame_evidence(self):
        row = attempt('undated')
        row['finished_at'] = None
        self.assertEqual(select_models([row], 'receiving', '2026-10-27T00:00:00Z', 'continuous_individual_context_v1'), [])

    def test_per_target_history_filter_and_numerical_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def write(name, body):
                path = root / name
                path.write_bytes(encoded(body))
                return gate.ref(root, path)
            history = root / 'history.jsonl'
            history.write_bytes(b'\n'.join(encoded({'season': 2026, 'week': week, 'yards': week}) for week in (6, 7, 8, 9)))
            hr = gate.ref(root, history)
            target = {'season': 2026, 'week': 8, 'game_id': 'game', 'gsis': 'p', 'team': 'A', 'channel': 'receiving', 'membership_state': 'EXACT_WEEK',
                      'membership_lane': 'VERIFIED', 'availability': 'UNKNOWN'}
            cr = write('cohort.json', {'history': hr, 'targets': [target], 'canonical_revision': 'new-data', 'feature_schema': 'continuous_individual_context_v1'})
            m = {'cohort': cr, 'history': hr, 'game_id': 'game', 'channel': 'receiving', 'as_of': '2026-10-27T00:00:00Z',
                 'feature_schema': 'continuous_individual_context_v1', 'model': {'sha256': 'a' * 64}}
            seen = []
            def build(prior, targets, cutoff, **kwargs):
                seen.append([r['week'] for r in prior])
                return [{'identity': [2026, 8, 'game', 'p', 'A', 'receiving'], 'x': [r['yards'] for r in prior]}]
            with patch('gamd.continuous_features.build_target_features', side_effect=build):
                first = target_features(root, m)[3]
                second = target_features(root, dict(m, as_of='2026-10-28T00:00:00Z'))[3]
            self.assertEqual(seen, [[6, 7], [6, 7]])
            self.assertEqual(first, second)
            with patch('gamd.continuous_features.build_target_features', side_effect=build):
                changed = target_features(root, dict(m, model={'sha256': 'b' * 64}))[3]
            self.assertNotEqual(first, changed)

    def test_observation_runs_before_fit_resource_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            lock = threading.Lock()
            worker = SimpleNamespace(run_lock=lock, resource_status=lambda **k: {'allowed': False, 'reasons': ['busy']})
            c = SimpleNamespace(store=store, root=Path(directory), worker=worker, stop_event=threading.Event(),
                                force_idle=False, gate=lambda: SimpleNamespace(require_campaign=lambda root: {}))
            with patch('gamd.continuous_runtime.observe', return_value={'status': 'ACCOUNTED', 'receipt': None, 'scored_n': 4}) as observation:
                result = Coordinator.run_once(c)
            observation.assert_called_once_with(c)
            self.assertEqual(result['status'], 'RESOURCE_WAIT')
            self.assertEqual(result['observation']['scored_n'], 4)
            self.assertTrue(lock.acquire(blocking=False))
            lock.release()

    def test_observation_runs_before_unrelated_legacy_authority_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            def blocked(root):
                raise ValueError('legacy fit not registered')
            c = SimpleNamespace(store=store, root=Path(directory), worker=SimpleNamespace(run_lock=threading.Lock()),
                                stop_event=threading.Event(), gate=lambda: SimpleNamespace(require_campaign=blocked))
            with patch('gamd.continuous_runtime.observe', return_value={'status': 'ACCOUNTED', 'receipt': None}):
                result = Coordinator.run_once(c)
            self.assertEqual(result['observation']['status'], 'ACCOUNTED')
            self.assertEqual(result['status'], 'BLOCKED')

    def test_live_charter_prevents_source_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'TASK_CHARTER.json').write_text('{}')
            c = SimpleNamespace(root=root, store=Store(root / 'store'), stop_event=threading.Event())
            with patch('gamd.continuous_source.execute') as source:
                result = observe(c)
            self.assertEqual(result['status'], 'LIVE_CHARTER_ACTIVE')
            source.assert_not_called()

    def test_fresh_receipt_observed_without_any_future_targets_or_fit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            c = SimpleNamespace(root=root, store=Store(root / 'store'), stop_event=threading.Event())
            receipt = {'receipt': {'path': 'authored', 'sha256': 'a' * 64}, 'source_cutoff': '2026-10-27T00:00:00Z'}
            with patch.object(gate, 'consumer_for', return_value=({}, {})), \
                 patch('gamd.continuous_preparation.source', return_value={}), \
                 patch('gamd.continuous_source.execute', return_value=receipt), \
                 patch('gamd.continuous_runtime.publish'), \
                 patch('gamd.continuous_settlement.run', return_value=[]) as settlement:
                result = observe(c)
            settlement.assert_called_once_with(c, receipt)
            self.assertEqual(result['status'], 'ACCOUNTED')
            self.assertFalse(result['new_learning_update'])

    def test_development_failure_does_not_hide_completed_observation(self):
        from gamd.continuous_runtime import run_next
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            c = SimpleNamespace(root=root, store=Store(root / 'store'), stop_event=threading.Event())
            observation = {'status': 'ACCOUNTED', 'scored_n': 7, 'receipt': {'authored': True}}
            with patch('gamd.continuous_forward.run_next', return_value=[]), \
                 patch.object(gate, 'consumer_for', side_effect=ValueError('fit consumer unavailable')):
                result = run_next(c, observation=observation)
            self.assertEqual(result['status'], 'DEVELOPMENT_BLOCKED')
            self.assertEqual(result['observation']['scored_n'], 7)
            self.assertIn('development', result['problems'])


    def test_all_horizon_games_and_lanes_seal_independently_and_deduplicate(self):
        from gamd.continuous_forward import run_next
        from core import digest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            c = SimpleNamespace(root=root, store=Store(root / 'store'))
            def write(path, value):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(encoded(value))
                return gate.ref(root, path)
            fixtures = [{'game_id': game, 'kickoff_at': kickoff} for game, kickoff in
                        [('g1', '2026-10-28T00:00:00Z'), ('g2', '2026-10-29T00:00:00Z'), ('outside', '2026-11-10T00:00:00Z')]]
            targets = [dict(game_id=f['game_id'], channel='receiving') for f in fixtures]
            history = write(root / 'history', [])
            cohort = {'fixtures': fixtures, 'targets': targets, 'exclusions': [], 'history': history}
            receipt = {'cohort': write(root / 'cohort', cohort), 'history': history, 'receipt': write(root / 'receipt', {})}
            attempts = [attempt('fixed'), attempt('adaptive', 'SCHEDULED_REFIT')]
            for row in attempts:
                write(root / gate.PRIVATE / 'attempts' / row['id'] / 'artifacts/RESULT.json', row['result'])
            def prepare(unused_root, out, manifest, witness):
                return write(out / 'MANIFEST.json', manifest)
            def features(unused_root, m):
                return cohort, [], [], digest({'game': m['game_id'], 'model': m['model']})
            calls = []
            def launch(unused_c, mr):
                m = gate.read(root, mr)
                calls.append((m['game_id'], m['lane']))
                write(root / m['output'] / 'SEAL.json',
                      {'manifest': mr, 'lane': m['lane'], 'model': m['model'],
                       'input_revision': features(root, m)[-1],
                       'forecasts': [{'identity': [2026, 8, m['game_id'], 'p', 'A', 'receiving']}]})
                return {'status': 'COMPLETED'}
            with patch.object(gate, 'now', return_value='2026-10-27T00:00:00Z'), \
                 patch.object(gate, 'manifest'), patch.object(gate, 'pretask'), \
                 patch.object(gate, 'authority', side_effect=lambda *args: contextlib.nullcontext()), \
                 patch('gamd.continuous_preparation.seal', side_effect=prepare), \
                 patch('gamd.continuous_forward.target_features', side_effect=features), \
                 patch('gamd.continuous_runtime.launch', side_effect=launch):
                run_next(c, receipt, attempts)
                self.assertEqual(set(calls), {('g1', FIXED), ('g2', FIXED), ('g1', ADAPTIVE), ('g2', ADAPTIVE)})
                original = [(r['manifest'], r['forecasts']) for r in c.store.records('continuous_benchmark')]
                run_next(c, receipt, attempts)
                receipt['cohort'] = write(root / 'cohort-new-clock', dict(cohort, source_cutoff='2026-10-27T01:00:00Z'))
                run_next(c, receipt, attempts)
                self.assertEqual(len(calls), 4)
                self.assertEqual([(r['manifest'], r['forecasts']) for r in c.store.records('continuous_benchmark')], original)

    def test_one_failed_fixture_does_not_prevent_another_game(self):
        from gamd.continuous_forward import run_next
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            c = SimpleNamespace(root=root, store=Store(root / 'store'))
            fixtures = [{'game_id': x, 'kickoff_at': '2026-10-28T00:00:00Z'} for x in ('failed', 'working')]
            cohort = {'fixtures': fixtures, 'targets': [dict(game_id=x['game_id'], channel='receiving') for x in fixtures], 'exclusions': []}
            row = attempt('fixed')
            modelresult = root / gate.PRIVATE / 'attempts/fixed/artifacts/RESULT.json'
            modelresult.parent.mkdir(parents=True)
            modelresult.write_bytes(encoded(row['result']))
            cohortpath = root / 'cohort'
            cohortpath.write_bytes(encoded(cohort))
            receipt = {'cohort': gate.ref(root, cohortpath), 'history': {'path': 'authored', 'sha256': 'a' * 64}, 'receipt': {}}
            seen = []
            def prepare(unused_root, out, m, witness):
                seen.append(m['game_id'])
                raise ValueError('authored isolated failure')
            with patch.object(gate, 'now', return_value='2026-10-27T00:00:00Z'), \
                 patch('gamd.continuous_preparation.seal', side_effect=prepare):
                run_next(c, receipt, [row])
            self.assertEqual(seen, ['failed', 'working'])
            self.assertEqual(len([p for p in c.store.get_setting('continuous_forward')['problems'] if p.get('game_id')]), 2)

class BatchedTargetFeatureTests(unittest.TestCase):
    @staticmethod
    def fixture(channel='receiving'):
        from copy import deepcopy
        source={'path':'authored-retained.json','sha256':'a'*64,'observed_at':'2026-10-20T00:00:00Z'}
        players=[('p1','A','WR'),('p2','A','RB'),('p3','B','TE'),('p4','B','FB')]
        history=[]
        for week in range(1,10):
            for player,team,position in players:
                for market in ('receiving','rushing') if position in ('RB','FB') else ('receiving',):
                    appeared=None if week==3 else False if week==4 else True
                    opportunity=None if appeared is not True else 0 if week==2 else week+1
                    yards=None if appeared is not True else 0 if week==2 else -3 if week==5 else week*7
                    side='C' if player=='p1' and week==1 else team
                    history.append({'season':2026,'week':week,'game_id':f'authored-{week}-{side}',
                        'gsis':player,'team':side,'opponent':'B' if side=='A' else 'A','position':position,'channel':market,
                        'appeared':appeared,'opportunity':opportunity,'yards':yards,
                        'participation_state':'UNKNOWN_MEMBERSHIP' if appeared is None else 'VERIFIED_DNP' if appeared is False else 'WITNESSED_APPEARANCE',
                        'source_observed_at':source['observed_at'],'provenance':[deepcopy(source)],
                        'team_context_valid':True,'team_opportunities':20 if market=='rushing' else 35,'team_yards':100 if market=='rushing' else 240})
        targets=[]
        for player,team,position in reversed(players):
            if channel=='rushing' and position not in ('RB','FB'):continue
            targets.append({'season':2026,'week':8,'game_id':'2026_08_A_B','gsis':player,'team':team,
                'opponent':'B' if team=='A' else 'A','position':position,'channel':channel,
                'appeared':None,'opportunity':None,'yards':None,'participation_state':'PREGAME_AVAILABILITY_UNKNOWN',
                'source_observed_at':source['observed_at'],'provenance':[deepcopy(source)],
                'kickoff_at':'2026-10-30T00:15:00Z','membership_state':'PROVISIONAL_PRIOR_WEEK_MEMBERSHIP',
                'membership_lane':'PROVISIONAL','membership_basis':{'effective_week':7,'target_week':8},'availability':'UNKNOWN'})
        return history,targets

    @staticmethod
    def write(root,history,targets,channel='receiving'):
        from gamd.continuous_features import SCHEMA
        history_path=root/'history.jsonl';history_path.write_bytes(b'\n'.join(encoded(row) for row in history))
        hr=gate.ref(root,history_path)
        cohort={'history':hr,'targets':targets,'feature_schema':SCHEMA}
        cp=root/'cohort.json';cp.write_bytes(encoded(cohort))
        return {'cohort':gate.ref(root,cp),'history':hr,'game_id':'2026_08_A_B','channel':channel,
                'as_of':'2026-10-27T00:00:00Z','feature_schema':SCHEMA,'model':{'sha256':'b'*64}}

    def test_batch_matches_serial_vectors_order_metadata_and_revision_for_both_channels(self):
        from gamd import continuous_features as features
        from core import digest
        for channel in ('receiving','rushing'):
            with self.subTest(channel=channel),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);history,targets=self.fixture(channel);manifest=self.write(root,history,targets,channel)
                prior=[r for r in history if (r['season'],r['week'])<(2026,8)]
                with patch.object(features,'validate_row',wraps=features.validate_row) as validator:
                    expected=[row for target in targets for row in features.build_target_features(prior,[target],manifest['as_of'],allowed_history_seasons=(2026,))]
                serial_validations=validator.call_count
                before={name:(root/name).read_bytes() for name in ('history.jsonl','cohort.json')}
                with patch.object(features,'build_target_features',wraps=features.build_target_features) as builder,patch.object(features,'validate_row',wraps=features.validate_row) as validator:
                    cohort,actual_targets,actual,revision=target_features(root,manifest)
                self.assertEqual(builder.call_count,1)
                self.assertEqual(validator.call_count,len(prior)+len(targets))
                self.assertEqual(serial_validations,len(targets)*(len(prior)+1))
                self.assertGreater(serial_validations,validator.call_count)
                self.assertEqual(encoded(actual),encoded(expected),'Batch output must be byte-for-byte equivalent to serial features')
                self.assertEqual(actual_targets,targets)
                self.assertEqual([r['identity'][3] for r in actual],[r['gsis'] for r in targets])
                membership=('membership_state','membership_lane','membership_basis','membership_effective_week','availability')
                expected_revision=digest({'game_id':manifest['game_id'],'channel':channel,'model':manifest['model']['sha256'],
                    'feature_names':features.FEATURE_NAMES,'targets':[{'identity':r['identity'],'x':r['x'],'membership':{k:t.get(k) for k in membership}} for r,t in zip(expected,targets)]})
                self.assertEqual(revision,expected_revision)
                self.assertEqual({name:(root/name).read_bytes() for name in before},before)
                self.assertTrue(all(r['unknown_history_count']>0 and r['dnp_history_count']>0 for r in actual))

    def test_batch_keeps_source_time_identity_schema_and_label_guards(self):
        from copy import deepcopy
        cases=('late','duplicate_history','duplicate_target','target_label','protected_history','schema')
        for case in cases:
            with self.subTest(case=case),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);history,targets=self.fixture()
                expected={'late':'HISTORY_OBSERVED_AFTER_CUTOFF','duplicate_history':'DUPLICATE_PLAYER_GAME_CHANNEL',
                    'duplicate_target':'DUPLICATE_TARGET','target_label':'FUTURE_TARGET_LABELS_MUST_BE_NULL',
                    'protected_history':'UNADMITTED_SEASON','schema':'FEATURE_SCHEMA_INCOMPATIBLE'}[case]
                if case=='late':history[0]['source_observed_at']='2026-10-28T00:00:00Z'
                elif case=='duplicate_history':history.append(deepcopy(history[0]))
                elif case=='duplicate_target':targets.append(deepcopy(targets[0]))
                elif case=='target_label':targets[0].update(appeared=True,opportunity=1,yards=7)
                elif case=='protected_history':history[0]['season']=2025
                manifest=self.write(root,history,targets)
                if case=='schema':manifest['feature_schema']='incompatible'
                with self.assertRaisesRegex(ValueError,expected):target_features(root,manifest)

    def test_batch_output_is_reordered_by_identity_and_missing_duplicates_or_extras_fail(self):
        from copy import deepcopy
        from gamd import continuous_features as features
        build=features.build_target_features
        for mutation in ('reverse','missing','duplicate','extra'):
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);history,targets=self.fixture();manifest=self.write(root,history,targets)
                expected=build([r for r in history if r['week']<8],targets,manifest['as_of'],allowed_history_seasons=(2026,))
                def changed(*args,**kwargs):
                    rows=build(*args,**kwargs)
                    if mutation=='reverse':return list(reversed(rows))
                    if mutation=='missing':return rows[:-1]
                    if mutation=='duplicate':return rows+[deepcopy(rows[0])]
                    extra=deepcopy(rows[0]);extra['identity'][3]='unexpected-player'
                    return rows+[extra]
                with patch.object(features,'build_target_features',side_effect=changed):
                    if mutation=='reverse':self.assertEqual(encoded(target_features(root,manifest)[2]),encoded(expected))
                    else:
                        with self.assertRaisesRegex(ValueError,'FORWARD_FEATURE_IDENTITY_MISMATCH'):target_features(root,manifest)

    def test_one_game_cannot_mix_target_periods(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);history,targets=self.fixture();targets[1]['week']=9
            with self.assertRaisesRegex(ValueError,'CONFLICTING_GAME_PERIOD'):
                target_features(root,self.write(root,history,targets))

    def test_tampered_history_is_rejected_before_feature_construction(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);history,targets=self.fixture();manifest=self.write(root,history,targets)
            with (root/'history.jsonl').open('ab') as stream:stream.write(b'\n')
            with patch('gamd.continuous_features.build_target_features') as builder:
                with self.assertRaises(ValueError):target_features(root,manifest)
            builder.assert_not_called()


if __name__ == '__main__':
    unittest.main()
