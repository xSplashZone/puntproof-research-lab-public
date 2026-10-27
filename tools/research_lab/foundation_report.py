"""Descriptive evidence from saved records; no fitting, source fetching or promotion."""
from collections import Counter
from hashlib import sha256
import json
import math
import statistics


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def average(values):
    values = [v for v in values if finite(v)]
    return statistics.fmean(values) if values else None


def identity_hash(identities):
    return sha256(json.dumps(sorted([list(k) for k in identities]), separators=(',', ':')).encode()).hexdigest()


def feature_health(rows, names):
    """Profile the exact saved fit inputs, preserving missingness and split names."""
    result = {}
    for label, subset in rows.items():
        features = {}
        for row in subset:
            if 'x' in row and len(row['x']) != len(names):
                raise ValueError('FEATURE_VECTOR_LENGTH_MISMATCH')
        for index, name in enumerate(names):
            raw = [r['x'][index] if 'x' in r else r.get(name) for r in subset]
            values = [value for value in raw if finite(value)]
            unique = set(values)
            features[name] = {'rows': len(subset), 'nonmissing': len(values),
                'nonmissing_rate': len(values) / len(subset) if subset else None,
                'distinct': len(unique), 'minimum': min(values) if values else None,
                'maximum': max(values) if values else None,
                'status': 'NO_ROWS' if not subset else 'ALL_UNKNOWN' if not values else
                          'CONSTANT' if len(unique) == 1 else 'VARIES'}
        result[label] = features
    return result


def law_statistics(law, actual):
    """Independent discrete-CDF integration, with signed yards intact."""
    x, p = law['support'], law['pmf']
    if (not x or len(x) != len(p) or any(not finite(v) for v in [*x, *p, actual])
            or any(v < 0 for v in p) or abs(sum(p) - 1) > 1e-7
            or any(a >= b for a, b in zip(x, x[1:]))):
        raise ValueError('INVALID_SAVED_LAW')
    cumulative, total = [], 0.0
    for probability in p:
        total += probability
        cumulative.append(total)
    def quantile(q):
        return next(value for value, cdf in zip(x, cumulative) if cdf + 1e-12 >= q)
    knots = sorted(set([*x, actual]))
    index, cdf, crps = 0, 0.0, 0.0
    for left, right in zip(knots, knots[1:]):
        while index < len(x) and x[index] <= left:
            cdf += p[index]
            index += 1
        crps += (cdf - (left >= actual)) ** 2 * (right - left)
    mean = sum(a * b for a, b in zip(x, p))
    probabilities = {name: sum(prob for value, prob in zip(x, p) if condition(value))
        for name, condition in [('negative', lambda v: v < 0), ('zero', lambda v: v == 0),
                                ('150plus', lambda v: v >= 150)]}
    thresholds = {str(t): sum(prob for value, prob in zip(x, p) if value >= t) for t in (10,20,30,40,50,75,100,150)}
    return {'threshold_probabilities': thresholds, 'absolute_error': abs(mean - actual), 'crps': crps,
            'inside80': int(quantile(.1) <= actual <= quantile(.9)),
            'predicted_mean': mean, 'actual': actual, 'probabilities': probabilities}


def probability_summary(rows):
    result = {'n': len(rows), 'mae': average([r['absolute_error'] for r in rows]),
              'crps': average([r['crps'] for r in rows]),
              'coverage80': average([r['inside80'] for r in rows]), 'events': {}}
    for name, event in [('negative', lambda y: y < 0), ('zero', lambda y: y == 0),
                        ('150plus', lambda y: y >= 150)]:
        pairs = [(r['probabilities'][name], int(event(r['actual']))) for r in rows]
        bins = []
        for i in range(10):
            subset = [(p, y) for p, y in pairs if min(9, int(p * 10)) == i]
            if subset:
                bins.append({'lower': i / 10, 'upper': (i + 1) / 10, 'n': len(subset),
                             'predicted': average([p for p, y in subset]),
                             'observed': average([y for p, y in subset])})
        likelihoods = [p if y else 1 - p for p, y in pairs]
        impossible = sum(v <= 0 for v in likelihoods)
        result['events'][name] = {'predicted': average([p for p, y in pairs]),
            'observed': average([y for p, y in pairs]),
            'brier': average([(p - y) ** 2 for p, y in pairs]),
            'log_loss': None if impossible else average([-math.log(v) for v in likelihoods]),
            'zero_likelihood_cases': impossible, 'reliability': bins,
            'ece': sum(b['n'] * abs(b['predicted'] - b['observed']) for b in bins) / len(rows) if rows else None}
    result['thresholds'] = {}
    for threshold in (10,20,30,40,50,75,100,150):
        pairs = [(r['threshold_probabilities'][str(threshold)], int(r['actual'] >= threshold)) for r in rows]
        result['thresholds'][str(threshold)] = {'n': len(pairs), 'predicted': average([p for p,y in pairs]),
            'observed': average([y for p,y in pairs]), 'brier': average([(p-y)**2 for p,y in pairs]),
            'choice_accuracy': average([int((p>.5)==bool(y)) for p,y in pairs])}
    return result


def audit_saved_laws(laws, scores):
    indexed = {tuple(row['identity']): row for row in scores}
    if len(indexed) != len(scores):
        raise ValueError('DUPLICATE_SCORE_IDENTITY')
    observed, mismatches, seen = [], [], set()
    for law in laws:
        key = tuple(law['identity'])
        if key in seen or key not in indexed:
            raise ValueError('LAW_SCORE_IDENTITY_MISMATCH')
        seen.add(key)
        score = indexed[key]
        row = law_statistics(law['candidate'], score['actual'])
        row.update(identity=list(key), high_usage=score.get('high_usage'),
                   unknown_history_count=score.get('unknown_history_count'), position=score.get('position'))
        for field in ('absolute_error', 'crps', 'inside80'):
            if field in score and abs(row[field] - score[field]) > 1e-7:
                mismatches.append({'identity': list(key), 'metric': field,
                                   'saved': score[field], 'recomputed': row[field]})
        row['F'] = score.get('frozen_control_scores', {}).get('F')
        observed.append(row)
    if seen != set(indexed):
        raise ValueError('LAW_SCORE_POPULATION_MISMATCH')
    matched = [row for row in observed if row['F'] is not None]
    def compare(rows):
        return {'n': len(rows), 'identity_sha256': identity_hash(r['identity'] for r in rows),
                'candidate_mae': average([r['absolute_error'] for r in rows]),
                'F_mae': average([r['F']['absolute_error'] for r in rows]),
                'candidate_crps': average([r['crps'] for r in rows]),
                'F_crps': average([r['F']['crps'] for r in rows])}
    slices = {'all': probability_summary(observed),
              'higher_usage': probability_summary([r for r in observed if r['high_usage'] is True]),
              'partial_history': probability_summary([r for r in observed if (r['unknown_history_count'] or 0) > 0])}
    for position in sorted({r['position'] for r in observed if r['position']}):
        slices[position] = probability_summary([r for r in observed if r['position'] == position])
    return {'classification': 'EXPOSED_HISTORICAL_DIAGNOSTIC', 'slices': slices,
            'matched_F': compare(matched),
            'matched_F_higher_usage': compare([r for r in matched if r['high_usage'] is True]),
            'unmatched_F': len(observed) - len(matched), 'mismatches': mismatches,
            'probability_status': 'EXPERIMENTAL_UNVALIDATED', 'rows': observed}


def population_transition(before, after):
    """Actual saved populations, not an old algorithm rerun on a new dataset."""
    a, b = {tuple(r['identity']) for r in before}, {tuple(r['identity']) for r in after}
    if len(a) != len(before) or len(b) != len(after):
        raise ValueError('DUPLICATE_POPULATION_IDENTITY')
    return {'before': len(a), 'after': len(b), 'intersection': len(a & b),
            'newly_admitted': len(b - a), 'dropped': len(a - b),
            'intersection_sha256': identity_hash(a & b)}


def owner_foundation(store):
    """Metadata-only home readback; saved artifacts are never executed on a view."""
    source = store.get_setting('continuous_source', {})
    report = store.get_setting('foundation_report', {})
    controlled = report.get('controlled_attempt_ids')
    controlled = set(controlled) if isinstance(controlled, list) and all(isinstance(x, str) for x in controlled) else None
    invalid_ids = report.get('invalid_attempt_ids', [])
    invalid_ids = set(x for x in invalid_ids if isinstance(x, str)) if isinstance(invalid_ids, list) else set()
    results = []
    for row in store.records('continuous_model'):
        if row['id'] in invalid_ids:
            continue
        f = row.get('frozen_controls') or {}
        n = f.get('same_population_n')
        candidate, control = f.get('candidate') or {}, f.get('F') or {}
        if not n or not finite(candidate.get('mae')) or not finite(control.get('mae')):
            continue
        delta = candidate['mae'] - control['mae']
        basis = ('Foundation repaired data' if row['id'] in controlled else 'Earlier saved result') if controlled is not None else 'Saved result — dataset shown in evidence'
        results.append({'id': row['id'], 'recipe': row.get('recipe_id'), 'channel': row.get('channel'), 'comparison_basis': basis,
            'n': n, 'candidate_mae': candidate['mae'], 'F_mae': control['mae'], 'delta_mae': delta,
            'candidate_crps': candidate.get('crps'), 'F_crps': control.get('crps'),
            'status': 'WORSE_THAN_F' if delta > 0 else 'LOWER_MAE_THAN_F' if delta < 0 else 'SAME_MAE_AS_F',
            'recorded_at': row.get('recorded_at'), 'projection_sha256': row.get('projection_file_sha256')})
    from gamd.continuous_settlement import latest_evaluations
    evaluations = latest_evaluations(store.records('continuous_evaluation'))
    seals = store.records('continuous_benchmark')
    issued = sum(len(row.get('forecasts', [])) for row in seals)
    scored = sum(row.get('scored_n', len(row.get('scores', []))) for row in evaluations)
    unknown = sum(row.get('unknown_n', len(row.get('unknown', []))) for row in evaluations)
    dnp = sum(row.get('verified_dnp_n', len(row.get('verified_dnp', []))) for row in evaluations)
    evaluated_seals = {row['forecast_manifest']['sha256'] for row in evaluations}
    pending = sum(len(row.get('forecasts', [])) for row in seals if row['manifest']['sha256'] not in evaluated_seals)
    return {'dataset_health': source.get('dataset_health'), 'canonical_revision': source.get('canonical_revision'),
        'coverage': source.get('before_after_coverage') or source.get('summary'),
        'source_cutoff': source.get('source_cutoff'), 'matched_F': results,
        'probability_audit': [row for row in report.get('probability_audit', []) if row.get('probability_metrics_verified') is not False],
        'evidence_warnings': {'invalid_result_count': len(invalid_ids), 'invalid_attempt_ids': sorted(invalid_ids)},
        'probability_status': 'Experimental; calibration and signed/zero/tail mass require review.',
        'observation': {'issued': issued, 'scored': scored, 'pending': pending, 'unknown': unknown, 'verified_dnp': dnp},
        'last_gamd_learning': store.get_setting('continuous_refit_status'),
        'next_job': store.get_setting('continuous_runtime', {}).get('next_action'),
        'report_links': report.get('downloads', {}), 'report_at': report.get('at')}
