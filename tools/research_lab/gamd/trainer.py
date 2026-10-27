"""Fixed, bounded GAMD development recipes operating only on caller-admitted rows.

The worker owns admission, scientific claims, time limits and terminal custody.
Imports perform no fitting or I/O. Each trial writes new artifacts exclusively;
an interrupted directory cannot be reused to reopen its scientific attempt.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import random
import statistics
import time

from .features import FEATURE_NAMES, MECHANISM, SEED, build_examples

FAMILIES = ('hurdle_glm', 'hist_gradient_boosting', 'extra_trees')
RECIPE_VERSION = 'GAMD_MASKED_HISTORY_FIXED_RECIPES_V1'
FOLDS = (
    {'id': 'development_2023', 'train_end': [2022, 12], 'calibration_start': [2022, 13], 'calibration_end': [2022, 18], 'evaluation_season': 2023},
    {'id': 'development_2024', 'train_end': [2023, 12], 'calibration_start': [2023, 13], 'calibration_end': [2023, 18], 'evaluation_season': 2024},
)
PARAMETERS = {
    'hurdle_glm': {'positive_opportunity': 'LogisticRegression(C=0.5,max_iter=600)',
                   'positive_count': 'PoissonRegressor(alpha=1.0,max_iter=600); conditional positive expectation >=1',
                   'signed_efficiency': 'Ridge(alpha=20.0), opportunity-weighted',
                   'law': 'hurdle zero mass plus prior-calibration positive residual empirical law'},
    'hist_gradient_boosting': {'max_iter': 65, 'max_leaf_nodes': 12, 'min_samples_leaf': 35,
                               'learning_rate': .06, 'l2_regularization': 5., 'early_stopping': False,
                               'loss_yards': 'squared_error', 'loss_workload': 'poisson',
                               'law': 'prior-calibration signed residual empirical law'},
    'extra_trees': {'n_estimators': 64, 'max_depth': 10, 'min_samples_leaf': 20,
                    'max_features': .8, 'n_jobs': 1,
                    'law': '64 deterministic draws from fitted leaf response distributions; prior-calibration center/scale'},
}
MIN_SPLIT_ROWS = 40
LAW_DRAWS = 64
CLASSIFICATION = 'REVISED_HISTORY_DEVELOPMENT_NOT_BLIND'


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def write_bytes_once(path, raw):
    """Never overwrite, including identical bytes: an attempt is not restartable."""
    import os
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def write_json_once(path, body):
    return write_bytes_once(path, encoded(body))


def split_examples(examples, fold):
    train, calibration, evaluation = [], [], []
    for example in examples:
        key = (example['season'], example['week'])
        if key <= tuple(fold['train_end']):
            train.append(example)
        elif tuple(fold['calibration_start']) <= key <= tuple(fold['calibration_end']):
            calibration.append(example)
        elif example['season'] == fold['evaluation_season']:
            evaluation.append(example)
    return train, calibration, evaluation


def quantile(values, probability):
    return _sorted_quantile(sorted(values), probability)


def _sorted_quantile(ordered, probability):
    """Same arithmetic as quantile, for an already ordered immutable sequence."""
    if not ordered:
        raise ValueError('EMPTY_DISTRIBUTION')
    index = (len(ordered) - 1) * probability
    lo = int(math.floor(index))
    hi = int(math.ceil(index))
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)


def _residual_draws(ordered, count):
    return [_sorted_quantile(ordered, (j + .5) / count) for j in range(count)]


def empirical_law(samples):
    """Signed integer-yard support, with no zero clipping or tail truncation."""
    if not samples or any(not math.isfinite(float(v)) for v in samples):
        raise ValueError('INVALID_LAW_SAMPLES')
    # Split fractional draws between adjacent integer yards. This preserves the
    # stated law mean instead of introducing rounding bias into point metrics.
    counts = Counter()
    for sample in samples:
        value = float(sample)
        low = math.floor(value)
        fraction = value - low
        counts[low] += 1 - fraction
        if fraction:
            counts[low + 1] += fraction
    support = sorted(counts)
    pmf = [counts[value] / len(samples) for value in support]
    return {'support': support, 'pmf': pmf, 'draw_count': len(samples),
            'mean': sum(v * p for v, p in zip(support, pmf)),
            'median': law_quantile(support, pmf, .5),
            'lower80': law_quantile(support, pmf, .1), 'upper80': law_quantile(support, pmf, .9)}


def law_quantile(support, pmf, probability):
    total = 0.
    for value, mass in zip(support, pmf):
        total += mass
        if total >= probability:
            return value
    return support[-1]


def score_law(law, actual):
    support, pmf = law['support'], law['pmf']
    first = sum(abs(value - actual) * mass for value, mass in zip(support, pmf))
    cumulative_mass = cumulative_weight = pairwise_half = 0.
    for value, mass in zip(support, pmf):
        pairwise_half += mass * (value * cumulative_mass - cumulative_weight)
        cumulative_mass += mass
        cumulative_weight += value * mass
    p_zero = sum(p for v, p in zip(support, pmf) if v == 0)
    p_negative = sum(p for v, p in zip(support, pmf) if v < 0)
    p_tail = sum(p for v, p in zip(support, pmf) if v >= 150)
    threshold_probabilities = {str(t): sum(p for v, p in zip(support, pmf) if v >= t) for t in (10, 20, 30, 40, 50, 75, 100, 150)}
    errors = [(threshold_probabilities[str(t)] - float(actual >= t)) ** 2 for t in (10, 20, 30, 40, 50, 75, 100, 150)]
    return {'absolute_error': abs(law['mean'] - actual), 'median_absolute_error': abs(law['median'] - actual),
            'signed_error': law['mean'] - actual, 'crps': first - pairwise_half,
            'zero_brier': (p_zero - float(actual == 0)) ** 2,
            'negative_brier': (p_negative - float(actual < 0)) ** 2,
            'tail150_brier': (p_tail - float(actual >= 150)) ** 2,
            'threshold_brier': statistics.fmean(errors), 'threshold_probabilities': threshold_probabilities,
            'inside80': law['lower80'] <= actual <= law['upper80'], 'interval80_width': law['upper80'] - law['lower80'],
            'p_zero': p_zero, 'p_negative': p_negative, 'p_150plus': p_tail}


def _matrix(examples, np):
    return np.asarray([[np.nan if value is None else value for value in e['x']] for e in examples], dtype=float)


def _fit(family, train, np):
    """Fit once with fixed parameters; preprocessing is train-only."""
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression, PoissonRegressor, Ridge
    from sklearn.ensemble import HistGradientBoostingRegressor, ExtraTreesRegressor
    import warnings
    from sklearn.exceptions import ConvergenceWarning
    x_raw = _matrix(train, np)
    imputer = SimpleImputer(strategy='median', add_indicator=True, keep_empty_features=True)
    x = imputer.fit_transform(x_raw)
    y = np.asarray([r['yards'] for r in train], dtype=float)
    workload = np.asarray([r['workload'] for r in train], dtype=float)
    model = {'family': family, 'imputer': imputer, 'fitted_models': 0, 'fallbacks': []}
    with warnings.catch_warnings():
        # A nonconverged fit is a recorded failed attempt, not a silent "success".
        warnings.simplefilter('error', ConvergenceWarning)
        if family == 'hurdle_glm':
            scaler = StandardScaler().fit(x)
            x = scaler.transform(x)
            model['scaler'] = scaler
            positive = workload > 0
            if positive.sum() < 20:
                raise ValueError('INSUFFICIENT_POSITIVE_WORKLOAD_FOR_HURDLE')
            if len(np.unique(positive)) == 2:
                hurdle = LogisticRegression(C=.5, max_iter=600, random_state=SEED).fit(x, positive)
                model.update(hurdle=hurdle, fitted_models=1)
            else:
                model['hurdle_constant'] = float(positive[0])
                model['fallbacks'].append('ONE_CLASS_HURDLE_CONSTANT')
            count = PoissonRegressor(alpha=1., max_iter=600).fit(x[positive], workload[positive])
            efficiency = Ridge(alpha=20.).fit(x[positive], y[positive] / workload[positive], sample_weight=workload[positive])
            model.update(count=count, efficiency=efficiency, fitted_models=model['fitted_models'] + 2)
        elif family == 'hist_gradient_boosting':
            args = dict(max_iter=65, max_leaf_nodes=12, min_samples_leaf=35, learning_rate=.06,
                        l2_regularization=5., early_stopping=False, random_state=SEED)
            model['yards'] = HistGradientBoostingRegressor(loss='squared_error', **args).fit(x, y)
            if workload.sum() <= 0:
                raise ValueError('NO_POSITIVE_WORKLOAD_FOR_POISSON')
            model['workload'] = HistGradientBoostingRegressor(loss='poisson', **args).fit(x, workload)
            model['fitted_models'] = 2
        elif family == 'extra_trees':
            args = dict(n_estimators=64, max_depth=10, min_samples_leaf=20, max_features=.8,
                        n_jobs=1, random_state=SEED)
            model['yards'] = ExtraTreesRegressor(**args).fit(x, y)
            model['workload'] = ExtraTreesRegressor(**args).fit(x, workload)
            # Retain a deterministic quantile from each fitted leaf's true training
            # response distribution. These are predictive draws, not tree means.
            leaves = model['yards'].apply(x)
            leaf_draws = []
            for tree in range(leaves.shape[1]):
                grouped = defaultdict(list)
                for leaf, actual in zip(leaves[:, tree], y):
                    grouped[int(leaf)].append(float(actual))
                q = ((tree * 37) % LAW_DRAWS + .5) / LAW_DRAWS
                leaf_draws.append({leaf: quantile(values, q) for leaf, values in grouped.items()})
            model.update(leaf_draws=leaf_draws, fitted_models=2)
        else:
            raise ValueError('UNREGISTERED_FAMILY')
    return model


def _predict(model, examples, np):
    x = model['imputer'].transform(_matrix(examples, np))
    family = model['family']
    if family == 'hurdle_glm':
        x = model['scaler'].transform(x)
        p = model['hurdle'].predict_proba(x)[:, 1] if 'hurdle' in model else np.full(len(x), model['hurdle_constant'])
        count = np.maximum(model['count'].predict(x), 1.)
        positive_mean = count * model['efficiency'].predict(x)
        result = {'point': p * positive_mean, 'workload': p * count, 'positive_probability': p, 'positive_mean': positive_mean}
        if any(not np.isfinite(value).all() for value in result.values()):
            raise ValueError('NONFINITE_MODEL_OUTPUT')
        return result
    result = {'point': model['yards'].predict(x), 'workload': np.maximum(model['workload'].predict(x), 0.)}
    if family == 'extra_trees':
        leaves = model['yards'].apply(x)
        draws = np.asarray([[model['leaf_draws'][t][int(leaf)] for t, leaf in enumerate(row)] for row in leaves], dtype=float)
        result['draws'] = draws
        result['point'] = np.mean(draws, axis=1)
    for value in result.values():
        if not np.isfinite(value).all():
            raise ValueError('NONFINITE_MODEL_OUTPUT')
    return result


def _calibrate(model, predictions, calibration):
    actual = [row['yards'] for row in calibration]
    if model['family'] == 'hurdle_glm':
        residuals = [y - float(p) for y, p, row in zip(actual, predictions['positive_mean'], calibration) if row['workload'] > 0]
        if len(residuals) < 20:
            raise ValueError('INSUFFICIENT_POSITIVE_CALIBRATION')
        ordered = sorted(residuals)
        return {'method': 'PRIOR_PERIOD_POSITIVE_WORKLOAD_RESIDUALS', 'residuals': ordered, 'n': len(residuals),
                'residual_draws_by_count': [_residual_draws(ordered, count) for count in range(LAW_DRAWS + 1)]}
    if model['family'] == 'extra_trees':
        shift = statistics.fmean(y - float(p) for y, p in zip(actual, predictions['point']))
        ratios = []
        for y, center, draws in zip(actual, predictions['point'], predictions['draws']):
            ordered = sorted(draws)
            width = max(1., (_sorted_quantile(ordered, .9) - _sorted_quantile(ordered, .1)) / 2)
            ratios.append(abs(y - float(center) - shift) / width)
        return {'method': 'PRIOR_PERIOD_LEAF_CENTER_AND_CENTRAL80_SCALE', 'shift': shift,
                'scale': max(.25, quantile(ratios, .8)), 'n': len(actual)}
    residuals = [y - float(p) for y, p in zip(actual, predictions['point'])]
    ordered = sorted(residuals)
    return {'method': 'PRIOR_PERIOD_SIGNED_RESIDUALS', 'residuals': ordered, 'n': len(actual),
            'residual_draws': _residual_draws(ordered, LAW_DRAWS)}


def _law_for(model, calibration, predictions, index):
    if model['family'] == 'extra_trees':
        center = float(predictions['point'][index])
        samples = [center + calibration['shift'] + calibration['scale'] * (float(v) - center) for v in predictions['draws'][index]]
    elif model['family'] == 'hurdle_glm':
        p = float(predictions['positive_probability'][index])
        positive_count = max(0, min(LAW_DRAWS, int(round(p * LAW_DRAWS))))
        samples = [0.] * (LAW_DRAWS - positive_count)
        cache = calibration.get('residual_draws_by_count')
        draws = cache[positive_count] if cache is not None else _residual_draws(calibration['residuals'], positive_count)
        for residual in draws:
            samples.append(float(predictions['positive_mean'][index]) + residual)
    else:
        draws = calibration.get('residual_draws')
        if draws is None:
            draws = _residual_draws(calibration['residuals'], LAW_DRAWS)
        samples = [float(predictions['point'][index]) + residual for residual in draws]
    return empirical_law(samples)


def _baseline_calibration(calibration):
    residuals = [r['yards'] - r['baseline'] for r in calibration if r['baseline'] is not None]
    return sorted(residuals) if len(residuals) >= MIN_SPLIT_ROWS else None


def predict_artifact(artifact, feature_rows, *, model_sha256=None):
    """Infer from a caller-authenticated, already loaded GAMD artifact; never fit.

    The caller must admit forward inference and authenticate its own fitted model
    before deserializing it. This API accepts no path and loads no model or source.
    Every law uses the exact frozen calibration semantics used at development.
    """
    if artifact.get('recipe_version') != RECIPE_VERSION or artifact.get('mechanism') != MECHANISM:
        raise ValueError('UNSUPPORTED_ARTIFACT_RECIPE')
    if artifact.get('family') not in FAMILIES or artifact.get('channel') not in ('receiving', 'rushing'):
        raise ValueError('UNSUPPORTED_ARTIFACT_CHANNEL_FAMILY')
    if artifact.get('feature_names') != list(FEATURE_NAMES):
        raise ValueError('ARTIFACT_FEATURE_SCHEMA_CHANGED')
    if not artifact.get('law_calibration') or not artifact.get('fold'):
        raise ValueError('MISSING_FROZEN_CALIBRATION_OR_FOLD')
    single = isinstance(feature_rows, dict)
    rows = [feature_rows] if single else list(feature_rows)
    if not rows:
        return []
    from .features import _timestamp
    for row in rows:
        if row.get('feature_names') != list(FEATURE_NAMES) or row.get('mechanism') != MECHANISM:
            raise ValueError('FORWARD_FEATURE_SCHEMA_CHANGED')
        if row.get('channel') != artifact['channel'] or row.get('season') != 2026:
            raise ValueError('FORWARD_CHANNEL_OR_SEASON_CHANGED')
        if any(key not in row or row[key] is not None for key in ('target_yards', 'target_opportunity')) or any(
                row.get(key) is not None for key in ('yards', 'opportunity', 'actual')):
            raise ValueError('FORWARD_TARGET_OUTCOME_PRESENT')
        if len(row.get('x', [])) != len(FEATURE_NAMES):
            raise ValueError('FORWARD_FEATURE_WIDTH_CHANGED')
        if _timestamp(row.get('kickoff_at'), 'kickoff_at') <= _timestamp(row.get('cutoff'), 'cutoff'):
            raise ValueError('FORWARD_NOT_PREGAME')
        if row.get('max_source_observed_at') is not None and _timestamp(row['max_source_observed_at'], 'max_source_observed_at') > _timestamp(row['cutoff'], 'cutoff'):
            raise ValueError('FORWARD_HISTORY_OBSERVED_AFTER_CUTOFF')
        if 2025 in row.get('allowed_history_seasons', []):
            raise ValueError('FORWARD_PROTECTED_HISTORY_SEASON')
        if row.get('max_history_period') is not None and tuple(row['max_history_period']) >= (row['season'], row['week']):
            raise ValueError('FORWARD_TARGET_HISTORY_PRESENT')
    import numpy as np
    from threadpoolctl import threadpool_limits
    with threadpool_limits(limits=1):
        predictions = _predict(artifact, rows, np)
        results = []
        for index, row in enumerate(rows):
            law = _law_for(artifact, artifact['law_calibration'], predictions, index)
            uncertainty = []
            if row['unknown_history_count']:
                uncertainty.append('UNKNOWN_PRIOR_PARTICIPATION_RETAINED')
            if not row['verified_history_count']:
                uncertainty.append('NO_VERIFIED_SAME_TEAM_HISTORY')
            if row['changed_role']:
                uncertainty.append('CHANGED_TEAM_PRIOR_WORKLOAD_IS_SEPARATE')
            uncertainty += ['AVAILABILITY_NOT_PREDICTED; CONDITIONAL_ON_APPEARANCE',
                            'MODEL_FITTED_ON_OLDER_REVISED_HISTORY; NO_2026_REFIT',
                            'EMPIRICAL_UNCERTAINTY_NOT_A_GUARANTEE_OF_TAIL_COVERAGE']
            results.append({**{k: row[k] for k in ('season', 'week', 'game_id', 'gsis', 'team', 'opponent', 'position', 'channel', 'identity', 'kickoff_at', 'cutoff')},
                            'family': artifact['family'], 'recipe_version': RECIPE_VERSION, 'mechanism': MECHANISM,
                            'model_sha256': model_sha256, 'mean': law['mean'], 'median': law['median'],
                            'law': law, 'expected_opportunities': float(predictions['workload'][index]),
                            'uncertainty': uncertainty, 'verified_history_count': row['verified_history_count'],
                            'unknown_history_count': row['unknown_history_count'], 'changed_team': row['changed_role'],
                            'training_through': artifact['fold']['train_end'],
                            'calibration_through': artifact['fold']['calibration_end'],
                            'training_classification': CLASSIFICATION, 'prospective_evidence': 'ONLY_IF_CALLER_SEALS_BEFORE_KICKOFF',
                            'label_scope': row['label_scope'], 'source_cutoff': row['cutoff'],
                            'maximum_source_observed_at': row['max_source_observed_at'],
                            'artificial_history_mask_applied': row['artificial_mask_applied'],
                            'promotion': 'DISABLED', 'genuine_roi': None})
    return results[0] if single else results


def summarize_scores(rows):
    if not rows:
        return {'n': 0}
    fields = {'mae': 'absolute_error', 'median_mae': 'median_absolute_error', 'bias': 'signed_error', 'crps': 'crps',
              'zero_brier': 'zero_brier', 'negative_brier': 'negative_brier', 'tail150_brier': 'tail150_brier',
              'threshold_brier': 'threshold_brier', 'coverage80': 'inside80', 'interval80_width': 'interval80_width',
              'workload_mae': 'workload_absolute_error'}
    result = {'n': len(rows)}
    for label, field in fields.items():
        values = [float(r[field]) for r in rows if r.get(field) is not None]
        result[label] = statistics.fmean(values) if values else None
    # Observed-vs-predicted reliability on preregistered odds-free thresholds.
    bins = []
    for left in range(10):
        values = []
        for row in rows:
            for threshold, probability in row.get('threshold_probabilities', {}).items():
                if min(9, int(probability * 10)) == left:
                    values.append((probability, float(row['actual'] >= int(threshold))))
        if values:
            bins.append({'lower': left / 10, 'upper': (left + 1) / 10, 'n': len(values),
                         'predicted': statistics.fmean(v[0] for v in values), 'observed': statistics.fmean(v[1] for v in values)})
    result['calibration_bins'] = bins
    return result


def paired_week_interval(rows, *, metric='absolute_error', bootstrap_samples=2000, seed=SEED):
    groups = defaultdict(list)
    for row in rows:
        if row.get('baseline_score') is not None:
            groups[(row['season'], row['week'])].append(row[metric] - row['baseline_score'][metric])
    if len(groups) < 8:
        return {'status': 'INSUFFICIENT_WEEKS', 'weeks': len(groups), 'lower': None, 'upper': None}
    # Resample complete weeks, retaining every paired player in each drawn block.
    blocks = list(groups.values())
    rng = random.Random(seed)
    draws = []
    for _ in range(bootstrap_samples):
        sampled = [blocks[rng.randrange(len(blocks))] for _ in blocks]
        draws.append(sum(sum(block) for block in sampled) / sum(len(block) for block in sampled))
    alpha = .05 / 6  # six planned family x channel trials, declared before outcomes
    return {'status': 'EXPLORATORY_REVISED_HISTORY', 'weeks': len(blocks), 'paired_n': sum(map(len, blocks)),
            'mean_delta': sum(sum(block) for block in blocks) / sum(map(len, blocks)),
            'lower': quantile(draws, alpha / 2), 'upper': quantile(draws, 1 - alpha / 2),
            'confidence': 1 - alpha, 'multiplicity_trials': 6, 'bootstrap_samples': bootstrap_samples,
            'unit': 'COMPLETE_SEASON_WEEK', 'interpretation': 'NEGATIVE_FAVORS_CANDIDATE; EXPOSED_DEVELOPMENT_NOT_BLIND'}


def research_decision(rows):
    matched = [r for r in rows if r.get('baseline_score') is not None]
    interval = paired_week_interval(matched)
    high = [r for r in matched if r['high_usage']]
    high_delta = statistics.fmean(r['absolute_error'] - r['baseline_score']['absolute_error'] for r in high) if high else None
    crps_delta = statistics.fmean(r['crps'] - r['baseline_score']['crps'] for r in matched) if matched else None
    reasons = []
    if interval.get('mean_delta') is not None and interval['mean_delta'] >= 0:
        reasons.append('MATCHED_MAE_NOT_BETTER_THAN_PRIOR5')
    if crps_delta is not None and crps_delta >= 0:
        reasons.append('MATCHED_CRPS_NOT_BETTER_THAN_PRIOR5')
    if high_delta is not None and high_delta > 0:
        reasons.append('HIGH_USAGE_MAE_REGRESSION')
    if reasons:
        status = 'REJECTED'
    elif interval.get('upper') is None or interval['upper'] >= 0 or len(high) < 30:
        status = 'INCONCLUSIVE'
        reasons.append('UNCERTAINTY_OR_HIGH_USAGE_SAMPLE_GATE_NOT_MET')
    else:
        status = 'COMPLETED'
        reasons.append('DEVELOPMENT_SIGNAL_ONLY; PROSPECTIVE_VALIDATION_REQUIRED')
    return {'status': status, 'reasons': reasons, 'paired_mae_interval': interval,
            'paired_crps_interval': paired_week_interval(matched, metric='crps'),
            'high_usage_n': len(high), 'high_usage_mae_delta': high_delta, 'paired_crps_delta': crps_delta,
            'promotion_authorized': False, 'betting_authorized': False}


def run_trial(rows, channel, family, output_dir, spec_hash=None):
    """Execute one claimed family/channel trial. No retries, replay, or promotion.

    Caller supplies admitted canonical rows, never a source path. The two fixed
    earlier calibration periods are excluded from their respective training fits.
    Full evaluation artifacts include all labelled targets, including cold starts;
    paired baseline comparisons use only independently precomputable prior history.
    """
    if family not in FAMILIES or channel not in ('receiving', 'rushing'):
        raise ValueError('UNREGISTERED_RECIPE')
    started = datetime.now(timezone.utc).isoformat()
    start_wall, start_cpu = time.monotonic(), time.process_time()
    rows = list(rows)
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('ATTEMPT_DIRECTORY_ALREADY_CONSUMED')
    output.mkdir(parents=True, exist_ok=True)
    source_hash = digest(sorted(rows, key=lambda r: (r.get('season', 0), r.get('week', 0), r.get('game_id', ''), r.get('gsis', ''), r.get('team', ''), r.get('channel', ''))))
    plan = {'recipe_version': RECIPE_VERSION, 'mechanism': MECHANISM, 'channel': channel, 'family': family,
            'parameters': PARAMETERS[family], 'folds': list(FOLDS), 'seed': SEED, 'law_draws': LAW_DRAWS,
            'source_projection_sha256': source_hash, 'spec_hash': spec_hash, 'started_at': started,
            'classification': CLASSIFICATION, 'target': 'SIGNED_YARDS_CONDITIONAL_ON_VERIFIED_APPEARANCE',
            'baseline': 'MEAN_OF_LAST_FIVE_OBSERVED_SAME_TEAM_YARDS_WITH_SAME_MASK',
            'decision_rule': 'REJECT_NONIMPROVING_MATCHED_MAE_OR_CRPS_OR_HIGH_USAGE_MAE_REGRESSION; otherwise 6-trial-adjusted paired-week interval upper<0 and high_usage_n>=30 for development signal',
            'protected_seasons': [2025], 'promotion': 'DISABLED', 'genuine_roi': None}
    references = [write_json_once(output / 'PLAN.json', plan)]
    built = build_examples(rows, channel)
    matrix = {'feature_names': built['feature_names'], 'audit': built['audit'], 'examples': built['examples']}
    references.append(write_json_once(output / 'FEATURE_MATRIX.json', matrix))
    import numpy as np
    import joblib
    import sklearn
    from threadpoolctl import threadpool_limits
    all_scores, fold_results, fitted_count = [], [], 0
    with threadpool_limits(limits=1):
        for fold in FOLDS:
            train, calibration_rows, evaluation = split_examples(built['examples'], fold)
            counts = {'train': len(train), 'calibration': len(calibration_rows), 'evaluation': len(evaluation)}
            if any(count < MIN_SPLIT_ROWS for count in counts.values()):
                raise ValueError('INSUFFICIENT_FIXED_SPLIT:' + fold['id'] + ':' + json.dumps(counts, sort_keys=True))
            fold_dir = output / fold['id']
            references.append(write_json_once(fold_dir / 'SPLIT.json', {
                **fold, 'counts': counts, 'train_ids': [r['identity'] for r in train],
                'calibration_ids': [r['identity'] for r in calibration_rows], 'evaluation_ids': [r['identity'] for r in evaluation],
                'feature_matrix_sha256': references[1]['sha256']}))
            model = _fit(family, train, np)
            calibration_predictions = _predict(model, calibration_rows, np)
            law_calibration = _calibrate(model, calibration_predictions, calibration_rows)
            predictions = _predict(model, evaluation, np)
            baseline_residuals = _baseline_calibration(calibration_rows)
            baseline_draws = _residual_draws(baseline_residuals, LAW_DRAWS) if baseline_residuals is not None else None
            baseline_center = statistics.fmean(baseline_draws) if baseline_draws is not None else None
            model.update(law_calibration=law_calibration, feature_names=list(FEATURE_NAMES), channel=channel,
                         mechanism=MECHANISM, recipe_version=RECIPE_VERSION, fold=fold,
                         source_projection_sha256=source_hash, feature_matrix_sha256=references[1]['sha256'],
                         classification=CLASSIFICATION, versions={'numpy': np.__version__, 'sklearn': sklearn.__version__, 'joblib': joblib.__version__})
            buffer = io.BytesIO()
            joblib.dump(model, buffer, compress=3)
            model_ref = write_bytes_once(fold_dir / 'FITTED_MODEL.joblib', buffer.getvalue())
            references.append(model_ref)
            references.append(write_json_once(fold_dir / 'CALIBRATION.json', law_calibration))
            laws, scores = [], []
            for i, row in enumerate(evaluation):
                law = _law_for(model, law_calibration, predictions, i)
                score = score_law(law, row['yards'])
                score.update({k: row[k] for k in ('season', 'week', 'game_id', 'gsis', 'team', 'position', 'channel', 'high_usage', 'changed_role', 'verified_history_count', 'unknown_history_count', 'masked_history_count')})
                score.update(identity=row['identity'], actual=row['yards'], actual_workload=row['workload'],
                             predicted_mean=law['mean'], predicted_median=law['median'],
                             predicted_workload=float(predictions['workload'][i]),
                             workload_absolute_error=abs(float(predictions['workload'][i]) - row['workload']),
                             baseline_score=None, baseline_unavailable_reason=None,
                             label_scope=row['label_scope'], model_sha256=model_ref['sha256'])
                baseline_law = None
                if row['baseline'] is None:
                    score['baseline_unavailable_reason'] = 'NO_VERIFIED_PRIOR_SAME_TEAM_YARDS'
                elif baseline_residuals is None:
                    score['baseline_unavailable_reason'] = 'INSUFFICIENT_PRIOR_BASELINE_CALIBRATION'
                else:
                    baseline_law = empirical_law([row['baseline'] + residual - baseline_center for residual in baseline_draws])
                    baseline_score = score_law(baseline_law, row['yards'])
                    baseline_score.update(actual=row['yards'], workload_absolute_error=abs(row['baseline_workload'] - row['workload']))
                    score['baseline_score'] = baseline_score
                laws.append({'identity': row['identity'], 'candidate': law, 'baseline': baseline_law, 'model_sha256': model_ref['sha256']})
                scores.append(score)
            references.append(write_bytes_once(fold_dir / 'LAWS.jsonl', b''.join(encoded(r) for r in laws)))
            references.append(write_bytes_once(fold_dir / 'SCORES.jsonl', b''.join(encoded(r) for r in scores)))
            fitted_count += model['fitted_models']
            fold_results.append({'id': fold['id'], 'counts': counts, 'metrics': summarize_scores(scores),
                                 'model': model_ref, 'fitted_models': model['fitted_models'], 'fallbacks': model['fallbacks']})
            all_scores.extend(scores)
    matched = [r for r in all_scores if r['baseline_score'] is not None]
    decision = research_decision(all_scores)
    result = {'status': decision['status'], 'family': family, 'channel': channel, 'mechanism': MECHANISM,
              'recipe_version': RECIPE_VERSION, 'classification': CLASSIFICATION, 'source_projection_sha256': source_hash,
              'feature_matrix_sha256': references[1]['sha256'], 'plan_sha256': references[0]['sha256'],
              'fitted_models': fitted_count, 'completed_folds': len(fold_results), 'folds': fold_results,
              'metrics': summarize_scores(all_scores), 'matched_candidate_metrics': summarize_scores(matched),
              'matched_baseline_metrics': summarize_scores([r['baseline_score'] for r in matched]),
              'baseline_unmatched_n': len(all_scores) - len(matched), 'decision': decision,
              'slices': {name: summarize_scores([r for r in all_scores if predicate(r)]) for name, predicate in {
                  'high_usage': lambda r: r['high_usage'], 'changed_team': lambda r: r['changed_role'],
                  'unknown_history': lambda r: r['unknown_history_count'] > 0,
                  'no_verified_same_team_history': lambda r: r['verified_history_count'] == 0}.items()},
              'feature_audit': built['audit'], 'artifacts': references, 'started_at': started,
              'finished_at': datetime.now(timezone.utc).isoformat(),
              'resources': {'wall_seconds': time.monotonic() - start_wall, 'cpu_seconds': time.process_time() - start_cpu,
                            'fit_threads': 1, 'hard_limits': 'ENFORCED_BY_CALLING_WORKER'},
              'genuine_roi': None, 'paper_bets': 0, 'odds_status': 'NO_MATCHED_PREGAME_OFFERS_IN_THIS_DEVELOPMENT_RECIPE',
              'fixed_f_comparison': None, 'fixed_f_status': 'NO_AUTHORIZED_MATCHED_FROZEN_PREDICTIONS; NO_REPLAY',
              'v3_comparison': None, 'v3_status': 'NO_MATCHED_HISTORICAL_V3_PREDICTIONS; NO_REPLAY',
              'promotion': 'DISABLED', 'limitations': [
                  'Revised archives and previously exposed years: chronological development only, not a blind or prospective test.',
                  'Targets require verified appearance and numeric opportunity/yards. Missing participation is never a zero-yard result.',
                  'The quarter mask is an identity-only stress mechanism; it does not certify missing-at-random source coverage.',
                  'Empirical laws approximate uncertainty and can miss tails absent from training or calibration.',
                  'Team context uses observed partial subtotals, not verified full-team opportunities.',
                  'No real market offers or ROI; candidate completion is not promotion or betting approval.']}
    result_ref = write_json_once(output / 'RESULT.json', result)
    return {**result, 'result_artifact': result_ref}
