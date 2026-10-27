"""Independent, immutable fixed and adaptive pregame observations."""
from pathlib import Path
from datetime import timedelta
import io
import json
from core import digest

FIXED = 'FROZEN_BENCHMARK'
ADAPTIVE = 'ADAPTIVE_CHALLENGER'


def target_features(root, manifest):
    from nfl_model import continuous_gamd_gate as g
    from gamd.continuous_features import build_target_features, FEATURE_NAMES, SCHEMA, identity
    cohort = g.read(root, manifest['cohort'])
    g.need(cohort['history'] == manifest['history'], 'COHORT_HISTORY_BINDING')
    g.need(manifest['feature_schema'] == SCHEMA and cohort.get('feature_schema', SCHEMA) == SCHEMA, 'FEATURE_SCHEMA_INCOMPATIBLE')
    targets = [x for x in cohort['targets'] if x['game_id'] == manifest['game_id'] and x['channel'] == manifest['channel']]
    g.need(bool(targets), 'EMPTY_GAME_CHANNEL')
    history = [json.loads(x) for x in g.checked(root, manifest['history']).splitlines()]
    periods = {(t['season'], t['week']) for t in targets}
    g.need(len(periods) == 1, 'CONFLICTING_GAME_PERIOD')
    target_period = next(iter(periods))
    # One game/channel shares the same history boundary. The list API preserves
    # target order and validates every history row once for the whole batch.
    prior = [r for r in history if (r['season'], r['week']) < target_period]
    built = build_target_features(prior, targets, manifest['as_of'], allowed_history_seasons=(2026,))
    expected = [identity(target) for target in targets]
    by_identity = {tuple(row['identity']): row for row in built}
    g.need(len(by_identity) == len(built) == len(expected) and set(by_identity) == set(expected), 'FORWARD_FEATURE_IDENTITY_MISMATCH')
    rows = [by_identity[key] for key in expected]
    membership = ('membership_state', 'membership_lane', 'membership_basis', 'membership_effective_week', 'availability')
    revision = digest({
        'game_id': manifest['game_id'], 'channel': manifest['channel'], 'model': manifest['model']['sha256'],
        'feature_names': FEATURE_NAMES, 'targets': [
            {'identity': r['identity'], 'x': r['x'], 'membership': {k: t.get(k) for k in membership}}
            for r, t in zip(rows, targets)]})
    return cohort, targets, rows, revision


def select_models(attempts, channel, cutoff, schema):
    """Predetermined fixed model; newest eligible scheduled model, never best score."""
    from nfl_model import continuous_gamd_gate as g
    eligible = []
    for attempt in attempts:
        result, plan = attempt.get('result'), attempt.get('plan', {})
        if not result or plan.get('channel') != channel or result.get('feature_schema') != schema:
            continue
        if result.get('status') not in ('COMPLETED', 'REJECTED', 'INCONCLUSIVE') or not result.get('model'):
            continue
        finished = attempt.get('finished_at')
        if not finished or g.stamp(finished) > g.stamp(cutoff):
            continue
        eligible.append(attempt)
    fixed = sorted((a for a in eligible if a['plan'].get('template_id') == 'individual_context_' + channel),
                   key=lambda a: (a['finished_at'], a['id']))
    adaptive = sorted((a for a in eligible if a['plan'].get('process') == 'SCHEDULED_REFIT'),
                      key=lambda a: (a['finished_at'], a['id']))
    return [(lane, values[index]) for lane, values, index in ((FIXED, fixed, 0), (ADAPTIVE, adaptive, -1)) if values]


def predict(root, m, mr):
    from nfl_model import continuous_gamd_gate as g
    from gamd.continuous_features import FEATURE_NAMES
    from gamd.continuous_train import law_for
    from gamd import trainer as t
    import numpy as np
    import joblib
    g.checked(root, m['source_receipt'])
    cohort, targets, rows, revision = target_features(root, m)
    g.need(all(g.stamp(x['kickoff_at']) > g.stamp(g.now()) for x in targets), 'FUTURE_GAME_REQUIRED')
    model = joblib.load(io.BytesIO(g.checked(root, m['model'])))
    g.need(tuple(model.get('feature_names', ())) == tuple(FEATURE_NAMES), 'SAVED_MODEL_FEATURE_ORDER_INCOMPATIBLE')
    g.need(all(len(r['x']) == len(FEATURE_NAMES) for r in rows), 'FEATURE_WIDTH_INCOMPATIBLE')
    pred = t._predict(model, rows, np)
    forecasts = []
    for i, (row, target) in enumerate(zip(rows, targets)):
        law = law_for(model['law_calibration'], pred, i, row)
        forecasts.append({
            'identity': row['identity'], 'name': target['name'], 'channel': target['channel'],
            'point_mean': law['mean'], 'point_median': law['median'],
            'expected_opportunities': float(pred['workload'][i]), 'law': law,
            'threshold_probabilities': {str(n): sum(p for y, p in zip(law['support'], law['pmf']) if y >= n) for n in range(-20, 301)},
            'unknown_history_count': row['unknown_history_count'], 'verified_history_count': row['verified_history_count'],
            'availability': target.get('availability', 'UNKNOWN'), 'kickoff_at': target['kickoff_at'],
            'roster_age_days': target.get('roster_observation_age_days'), 'source_max_observed_at': row['max_source_observed_at'],
            'membership_state': target.get('membership_state', 'UNKNOWN'),
            'membership_lane': target.get('membership_lane', 'UNKNOWN'),
            'membership_basis': target.get('membership_basis'),
            'membership_effective_week': target.get('membership_effective_week'),
            'model': m['model'], 'probability_status': 'EXPERIMENTAL_UNVALIDATED',
            'conditional_on': 'VERIFIED_OFFENSIVE_APPEARANCE', 'offer': None,
        })
    sealed_at = g.now()
    g.need(all(g.stamp(x['kickoff_at']) > g.stamp(sealed_at) for x in forecasts), 'FINISHED_AFTER_KICKOFF_NO_PROSPECTIVE_SEAL')
    g.write_once(Path(root) / m['output'] / 'SEAL.json', {
        'status': 'SEALED_PREGAME', 'sealed_at': sealed_at, 'manifest': mr, 'model': m['model'],
        'lane': m.get('lane', FIXED), 'game_id': m['game_id'], 'channel': m['channel'],
        'input_revision': revision, 'forecasts': forecasts,
        'exclusions': [r for r in cohort['exclusions'] if r.get('game_id') == m['game_id'] and r.get('channel') == m['channel']],
        'canonical_revision': cohort.get('canonical_revision'), 'feature_schema': m['feature_schema'],
        'feature_contract_sha256': cohort.get('feature_contract_sha256'),
        'training_support': m.get('training_support'),
        'classification': 'PROSPECTIVE_EXPERIMENTAL_OBSERVATION', 'fit': False,
        'original_forecasts_modified': 0, 'offers': None,
    })


def run_next(c, receipt, attempts):
    from nfl_model import continuous_gamd_gate as g
    from gamd.continuous_preparation import seal
    from gamd.continuous_runtime import launch
    from gamd.continuous_features import SCHEMA
    cohort = g.read(c.root, receipt['cohort'])
    results, problems = [], []
    existing = c.store.records('continuous_benchmark')
    moment = g.stamp(g.now())
    horizon = moment + timedelta(days=7)
    for channel in ('receiving', 'rushing'):
        models = select_models(attempts, channel, moment.isoformat(), SCHEMA)
        if not models:
            problems.append({'channel': channel, 'reason': 'NO_COMPATIBLE_SAVED_MODEL'})
        for lane, selected in models:
            value = selected['result']
            outfit = c.root / g.PRIVATE / 'attempts' / selected['id']
            model = value['model']
            modelresult = g.ref(c.root, outfit / 'artifacts/RESULT.json')
            for fixture in cohort['fixtures']:
                if fixture['game_id'] == '2026_07_DAL_PHI' or not moment < g.stamp(fixture['kickoff_at']) <= horizon:
                    continue
                targets = [x for x in cohort['targets'] if x['game_id'] == fixture['game_id'] and x['channel'] == channel]
                if not targets:
                    continue
                identity = {'lane': lane, 'game': fixture['game_id'], 'channel': channel,
                            'model': model, 'model_result': modelresult, 'cohort': receipt['cohort'], 'history': receipt['history']}
                out = c.root / g.PRIVATE / 'forward' / ('revision-' + digest(identity)[:32])
                sp = out / 'SEAL.json'
                try:
                    if sp.exists():
                        saved = g.read(c.root, g.ref(c.root, sp))
                    else:
                        if (out / 'CLAIM.json').exists():
                            raise ValueError('INFERENCE_CLAIM_WITHOUT_SEAL_REQUIRES_PHASE_REVIEW')
                        m = {
                            'operation': g.FORWARD, 'purpose': 'PREGAME_BENCHMARK', 'lane': lane, 'fit': False,
                            'as_of': g.now(), 'output': out.relative_to(c.root).as_posix(),
                            'game_id': fixture['game_id'], 'channel': channel, 'model': model, 'model_result': modelresult,
                            'feature_schema': value['feature_schema'], 'cohort': receipt['cohort'], 'history': receipt['history'],
                            'source_receipt': receipt['receipt'], 'inputs': [model, modelresult, receipt['cohort'], receipt['history']],
                            'training_support': {
                                'attempt': selected['id'], 'finished_at': selected['finished_at'],
                                'process': selected['plan'].get('process'), 'training_projection': selected['plan'].get('projection'),
                                'note': 'Saved model retained without refitting. Repaired context can be outside its observed training support; probabilities remain experimental.',
                            },
                        }
                        mp = out / 'MANIFEST.json'
                        if mp.exists():
                            mr = g.ref(c.root, mp)
                            m = g.read(c.root, mr)
                        else:
                            mr = seal(c.root, out, m, {'status': 'PASS', 'records': len(targets), 'seasons': [2026], 'provider_calls': 0})
                        g.manifest(c.root, g.FORWARD, mr)
                        g.pretask(c.root, g.FORWARD, g.read(c.root, mr))
                        with g.authority(c.root, g.FORWARD, mr):
                            _, _, _, revision = target_features(c.root, m)
                        duplicate = next((r for r in existing if r.get('lane') == lane and r.get('input_revision') == revision), None)
                        if duplicate:
                            results.append(duplicate)
                            continue
                        execution = launch(c, mr)
                        if not sp.exists():
                            raise ValueError('FORWARD_' + execution['status'] + '_NO_SEAL')
                        saved = g.read(c.root, g.ref(c.root, sp))
                        g.need(saved.get('input_revision') == revision, 'FORWARD_INPUT_REVISION_CHANGED')
                    saved = {**saved, 'seal_ref': g.ref(c.root, sp)}
                    key = saved['manifest']['sha256']
                    prior = c.store.latest('continuous_benchmark', key)
                    if prior:
                        if prior['forecasts'] != saved['forecasts']:
                            raise ValueError('SEALED_FORECAST_RECORD_MISMATCH')
                    else:
                        c.store.append('continuous_benchmark', key, saved, immutable=True)
                        existing.append(saved)
                    results.append(saved)
                except Exception as exc:
                    problems.append({'game_id': fixture['game_id'], 'channel': channel, 'lane': lane, 'reason': str(exc)})
    all_records = c.store.records('continuous_benchmark')
    c.store.set_setting('continuous_forward', {
        'status': 'SEALED_PREGAME_WITH_GAPS' if results and problems else 'SEALED_PREGAME' if results else
                  'WAITING_FUTURE_COHORT' if not cohort['fixtures'] else 'WAITING_SAVED_MODEL_OR_SUPPORTED_PLAYERS',
        'horizon_days': 7, 'games': len({x['forecasts'][0]['identity'][2] for x in all_records if x['forecasts']}),
        'forecasts': sum(len(x['forecasts']) for x in all_records), 'seals': len(all_records),
        'current_horizon_seals': len({x['manifest']['sha256'] for x in results}),
        'targets': len(cohort['targets']), 'exclusions': len(cohort['exclusions']), 'problems': problems,
        'availability': 'UNKNOWN_UNLESS_EXPLICITLY_VERIFIED', 'lanes': [FIXED, ADAPTIVE],
        'next_action': 'Observe each immutable version on verified final outcomes. Conditional experimental predictions do not establish betting ROI.',
    })
    return results
