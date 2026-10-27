"""Pure append-only scoring payloads for caller-authenticated sealed GAMD laws.

The caller owns source admission, hash verification and immutable persistence.
This module reads no files, calls no provider, fits no model, changes no parameter,
and cannot turn an unknown/nonappearance into a zero or create betting returns.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import statistics


KEY = ('season', 'week', 'game_id', 'gsis', 'team', 'channel')
HASH = re.compile(r'[a-f0-9]{64}\Z')
GSIS = re.compile(r'\d{2}-\d{7}\Z')
THRESHOLDS = (10, 20, 30, 40, 50, 75, 100, 150)
SCHEMA = 'puntproof-gamd-observation-v1'
FAMILIES = ('hurdle_glm', 'hist_gradient_boosting', 'extra_trees')


def _need(condition, code):
    if not condition:
        raise ValueError(code)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _input_digest(row):
    return _digest({k: v for k, v in row.items() if k not in ('revision', 'record_hash', 'recorded_at')})


def _time(value):
    _need(isinstance(value, str) and bool(value), 'AUTHENTICATED_CLOCK_REQUIRED')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    _need(stamp.tzinfo is not None, 'NAIVE_CLOCK_REJECTED')
    return stamp.astimezone(timezone.utc)


def _ref(value):
    _need(isinstance(value, dict) and isinstance(value.get('path'), str) and bool(value['path'])
          and HASH.fullmatch(value.get('sha256', '') or '') is not None,
          'HASH_BOUND_EVIDENCE_REFERENCE_REQUIRED')
    return {'path': value['path'], 'sha256': value['sha256']}


def _number(value, *, integer=False, nonnegative=False):
    _need(type(value) in (int, float) and math.isfinite(value)
          and (not integer or float(value).is_integer()) and (not nonnegative or value >= 0),
          'FINITE_NUMERIC_VALUE_REQUIRED')
    return value


def _identity(row):
    _need(type(row.get('season')) is int and row['season'] == 2026
          and type(row.get('week')) is int and 1 <= row['week'] <= 18,
          'EXACT_2026_REGULAR_PERIOD_REQUIRED')
    _need(isinstance(row.get('game_id'), str) and bool(row['game_id'])
          and GSIS.fullmatch(row.get('gsis', '') or '') is not None
          and isinstance(row.get('team'), str) and bool(row['team'])
          and row.get('channel') in ('receiving', 'rushing'), 'EXACT_FORECAST_OUTCOME_IDENTITY_REQUIRED')
    key = tuple(row[k] for k in KEY)
    if row.get('identity') is not None:
        _need(tuple(row['identity']) == key, 'EMBEDDED_IDENTITY_MISMATCH')
    return key


def _law(value):
    support, pmf = value.get('support'), value.get('pmf')
    _need(isinstance(support, list) and isinstance(pmf, list) and 0 < len(support) <= 10000
          and len(support) == len(pmf), 'SEALED_LAW_SHAPE_INVALID')
    for point in support:
        _number(point, integer=True)
    for probability in pmf:
        _number(probability, nonnegative=True)
    _need(all(a < b for a, b in zip(support, support[1:]))
          and math.isclose(sum(pmf), 1., abs_tol=1e-8, rel_tol=0), 'SEALED_LAW_MASS_OR_SUPPORT_INVALID')
    def quantile(q):
        cumulative = 0.
        for point, probability in zip(support, pmf):
            cumulative += probability
            if cumulative >= q:
                return point
        return support[-1]
    result = dict(support=list(support), pmf=list(pmf), mean=sum(p * y for p, y in zip(pmf, support)),
                  median=quantile(.5), lower80=quantile(.1), upper80=quantile(.9))
    for field in ('mean', 'median', 'lower80', 'upper80'):
        if value.get(field) is not None:
            _number(value[field])
            _need(math.isclose(value[field], result[field], abs_tol=1e-8, rel_tol=1e-9),
                  'SEALED_LAW_SUMMARY_MISMATCH')
    return result


def _forecast(row, as_of):
    key = _identity(row)
    _need(isinstance(row.get('id'), str) and bool(row['id']), 'SEALED_FORECAST_ID_REQUIRED')
    _need(row.get('family') in FAMILIES and HASH.fullmatch(row.get('model_sha256', '') or ''),
          'GAMD_MODEL_IDENTITY_REQUIRED')
    _ref(row.get('forecast_ref'))
    _ref(row.get('seal_ref'))
    cutoff, sealed, kickoff = _time(row.get('source_cutoff')), _time(row.get('sealed_at')), _time(row.get('kickoff_at'))
    _need(cutoff <= sealed <= as_of, 'FORECAST_CLOCK_ORDER_INVALID')
    if row.get('maximum_source_observed_at') is not None:
        _need(_time(row['maximum_source_observed_at']) <= cutoff, 'FORECAST_SOURCE_LOOKAHEAD')
    if row.get('cutoff') is not None:
        _need(_time(row['cutoff']) == cutoff, 'FORECAST_CUTOFF_MISMATCH')
    for period in ('training_through', 'calibration_through'):
        _need(isinstance(row.get(period), list) and len(row[period]) == 2
              and all(type(v) is int for v in row[period]) and tuple(row[period]) < key[:2],
              'FORECAST_TRAINING_OR_CALIBRATION_LOOKAHEAD')
    _need(not any(row.get(k) is not None for k in ('actual', 'yards', 'opportunities', 'target_yards', 'target_opportunity')),
          'SEALED_FORECAST_CONTAINS_TARGET_OUTCOME')
    law = _law(row.get('law') or {})
    for field in ('mean', 'median'):
        if row.get(field) is not None:
            _need(math.isclose(_number(row[field]), law[field], abs_tol=1e-8, rel_tol=1e-9),
                  'FORECAST_POINT_LAW_MISMATCH')
    _number(row.get('expected_opportunities'), nonnegative=True)
    prospective = cutoff <= sealed < kickoff
    if not prospective:
        _need(row.get('classification') == 'RETROSPECTIVE_RESEARCH_FORECAST',
              'LATE_SEAL_REQUIRES_RETROSPECTIVE_LABEL')
    return key, law, prospective


def _outcome(row, forecast, as_of):
    _need(_identity(row) == _identity(forecast), 'OUTCOME_IDENTITY_MISMATCH')
    for name in ('outcome_ref', 'source_ref', 'completion_ref'):
        _ref(row.get(name))
    verification = row.get('source_verification') or {}
    _need(row.get('verified') is True and bool(verification.get('operation'))
          and bool(verification.get('consumer')), 'AUTHENTICATED_OUTCOME_REQUIRED')
    for name in ('manifest', 'readiness'):
        _ref(verification.get(name))
    _need(row.get('identity_state') == 'EXACT_DATED_ROSTER_GSIS', 'OUTCOME_DATED_IDENTITY_REQUIRED')
    _need(row.get('final') is True, 'OUTCOME_NOT_FINAL')
    kickoff = _time(forecast['kickoff_at'])
    if row.get('kickoff_at') is not None:
        _need(_time(row['kickoff_at']) == kickoff, 'OUTCOME_KICKOFF_MISMATCH')
    completed, observed = _time(row.get('completed_at')), _time(row.get('observed_at'))
    _need(kickoff < completed <= observed <= as_of, 'FINAL_OUTCOME_CLOCK_ORDER_INVALID')
    status = row.get('status')
    _need(status in ('APPEARED', 'UNKNOWN', 'NONAPPEARANCE', 'VOID'), 'OUTCOME_APPEARANCE_STATE_REQUIRED')
    if status == 'APPEARED':
        _number(row.get('yards'), integer=True)
        _number(row.get('opportunities'), integer=True, nonnegative=True)
    else:
        _need(row.get('yards') is None and row.get('opportunities') is None, 'UNKNOWN_OR_NONAPPEARANCE_IS_NOT_ZERO')
    return status


def _score(law, actual, actual_opportunities, expected_opportunities, thresholds):
    support, pmf = law['support'], law['pmf']
    cumulative = weighted = half_pair = 0.
    for point, probability in zip(support, pmf):
        half_pair += probability * (point * cumulative - weighted)
        cumulative += probability
        weighted += probability * point
    at = sum(p for y, p in zip(support, pmf) if y == actual)
    below = sum(p for y, p in zip(support, pmf) if y < actual)
    probability_rows, line_rows = [], []
    for threshold in thresholds:
        p_ge = sum(p for y, p in zip(support, pmf) if y >= threshold)
        p_over = sum(p for y, p in zip(support, pmf) if y > threshold)
        p_under = sum(p for y, p in zip(support, pmf) if y < threshold)
        p_push = sum(p for y, p in zip(support, pmf) if y == threshold)
        event = int(actual >= threshold)
        probability_rows.append(dict(threshold=threshold, predicate='YARDS_AT_LEAST', probability=p_ge,
                                     observed=event, brier=(p_ge - event) ** 2))
        choice = 'OVER' if p_over > p_under else 'UNDER' if p_under > p_over else 'ABSTAIN'
        result = 'ABSTAIN' if choice == 'ABSTAIN' else 'PUSH' if actual == threshold else (
            'CORRECT' if (actual > threshold) == (choice == 'OVER') else 'INCORRECT')
        line_rows.append(dict(line=threshold, p_over=p_over, p_under=p_under, p_push=p_push,
                              choice=choice, result=result, market_offer=False, decimal_price=None))
    p_zero = sum(p for y, p in zip(support, pmf) if y == 0)
    p_negative = sum(p for y, p in zip(support, pmf) if y < 0)
    p_tail = sum(p for y, p in zip(support, pmf) if y >= 150)
    error = law['mean'] - actual
    return dict(actual=actual, actual_opportunities=actual_opportunities, mean=law['mean'], median=law['median'],
        absolute_error=abs(error), squared_error=error * error, signed_error=error,
        median_absolute_error=abs(law['median'] - actual),
        workload_absolute_error=abs(expected_opportunities - actual_opportunities),
        crps=sum(abs(y - actual) * p for y, p in zip(support, pmf)) - half_pair,
        lower80=law['lower80'], upper80=law['upper80'], inside80=law['lower80'] <= actual <= law['upper80'],
        zero_brier=(p_zero - int(actual == 0)) ** 2, negative_brier=(p_negative - int(actual < 0)) ** 2,
        tail150_brier=(p_tail - int(actual >= 150)) ** 2,
        threshold_brier=statistics.fmean(r['brier'] for r in probability_rows),
        p_zero=p_zero, p_negative=p_negative, p_150plus=p_tail,
        probability_of_actual=at, logarithmic_score=-math.log(at) if at > 0 else None,
        actual_outside_discrete_support=at == 0,
        pit_interval=[below, below + at], mid_pit=below + .5 * at,
        threshold_probabilities=probability_rows, odds_free_line_choices=line_rows,
        genuine_roi=None, betting_profit=None)


def _seal_payload(body):
    result = deepcopy(body)
    value = _digest(result)
    result.update(id='gamd-observation-' + value[:32], observation_sha256=value)
    return result


def _existing_body(row):
    clean = {k: deepcopy(v) for k, v in row.items()
             if k not in ('id', 'observation_sha256', 'revision', 'record_hash', 'recorded_at')}
    _need(clean.get('schema_version') == SCHEMA and _digest(clean) == row.get('observation_sha256')
          and row.get('id') == 'gamd-observation-' + row['observation_sha256'][:32], 'EXISTING_OBSERVATION_CUSTODY_FAILED')
    return clean


def observation_summary(observations):
    """Descriptive metrics for already authenticated observation payloads."""
    scored = [r for r in observations if r.get('status') == 'SCORED_VERIFIED_FINAL' and r.get('score')]
    result = dict(n=len(scored), excluded=dict(Counter(r.get('outcome_status') for r in observations if not r.get('score'))),
                  genuine_roi=None, parameter_updates=0)
    for name in ('absolute_error', 'median_absolute_error', 'signed_error', 'crps', 'workload_absolute_error',
                 'zero_brier', 'negative_brier', 'tail150_brier', 'threshold_brier', 'inside80'):
        values = [float(r['score'][name]) for r in scored]
        result[name] = statistics.fmean(values) if values else None
    result['rmse'] = math.sqrt(statistics.fmean(r['score']['squared_error'] for r in scored)) if scored else None
    result['off_support_outcomes'] = sum(r['score']['actual_outside_discrete_support'] for r in scored)
    result['distinct_games'] = len({r['identity'][2] for r in scored})
    result['distinct_weeks'] = len({tuple(r['identity'][:2]) for r in scored})
    calibration = []
    for index in range(10):
        events = [p for r in scored for p in r['score']['threshold_probabilities']
                  if min(9, int(p['probability'] * 10)) == index]
        if events:
            calibration.append(dict(lower=index / 10, upper=(index + 1) / 10, n=len(events),
                predicted=statistics.fmean(p['probability'] for p in events),
                observed=statistics.fmean(p['observed'] for p in events)))
    result['calibration_bins'] = calibration
    result['calibration_interpretation'] = 'Descriptive only. Thresholds, teammates and multiple models are correlated; this is not a confidence interval or independent-event count.'
    choices = [p for r in scored for p in r['score']['odds_free_line_choices']]
    result['odds_free_choices'] = dict(Counter(p['result'] for p in choices))
    return result


def error_atlas(observations):
    """Keep causes unknown; report observed residual patterns and honest cohorts."""
    rows = list(observations)
    cohorts = {'all': rows}
    for row in rows:
        for label in ('channel', 'family', 'position'):
            cohorts.setdefault(label + ':' + str(row.get(label)), []).append(row)
        cohorts.setdefault('history:unknown_present' if row.get('unknown_history_count', 0) else 'history:no_recorded_unknown', []).append(row)
        cohorts.setdefault('team:changed' if row.get('changed_team') else 'team:unchanged', []).append(row)
        cutoff = 6 if row.get('channel') == 'receiving' else 10
        cohorts.setdefault('usage:higher_forecast_workload' if row.get('expected_opportunities', 0) >= cutoff else 'usage:other', []).append(row)
    surprises = []
    for row in rows:
        score = row.get('score')
        if not score:
            continue
        flags = []
        if not score['inside80']:
            flags.append('OUTSIDE_SEALED_80_PERCENT_INTERVAL')
        if score['actual'] == 0 and score['p_zero'] < .1:
            flags.append('ZERO_WITH_LOW_FORECAST_ZERO_MASS')
        if score['actual'] >= 150 and score['p_150plus'] < .05:
            flags.append('150PLUS_WITH_LOW_FORECAST_TAIL_MASS')
        if score['actual_outside_discrete_support']:
            flags.append('OUTCOME_OUTSIDE_EMPIRICAL_LAW_SUPPORT')
        if flags:
            surprises.append(dict(observation_id=row['id'], forecast_id=row['forecast_id'],
                identity=row['identity'], flags=flags, actual=score['actual'], mean=score['mean'],
                absolute_error=score['absolute_error'], cause='UNKNOWN_NOT_INFERRED_FROM_RESIDUAL',
                next_action='Inspect verified opportunity, role and source-revision evidence. Preserve this observation and the original forecast; no automatic retuning.'))
    return dict(schema_version='puntproof-gamd-error-atlas-v1',
                cohorts={key: observation_summary(value) for key, value in sorted(cohorts.items())},
                surprises=sorted(surprises, key=lambda r: (-r['absolute_error'], r['forecast_id'])),
                causes_established=False, parameter_updates=0, new_fits=0, genuine_roi=None)


def observe_sealed_forecasts(sealed_forecasts, final_outcomes, *, as_of,
                             existing_observations=(), thresholds=THRESHOLDS):
    """Return immutable records to append; do not read, persist, train or acquire.

    ``forecast_ref`` and ``seal_ref`` must be verified by the caller before this
    call. Each final outcome needs ``outcome_ref``, ``source_ref``,
    ``completion_ref`` and an exact ``source_verification`` operation/consumer/
    manifest/readiness binding. The function validates their closed identities
    and clocks; these dictionaries alone do not grant authority or verify bytes.

    Repeated calls are idempotent. A correction requires an explicit
    ``supersedes_outcome_sha256`` chain and yields a new record. Past observations
    remain intact. Unknown/DNP/void outcomes are recorded as exclusions, not zero.
    """
    cutoff = _time(as_of)
    thresholds = tuple(thresholds)
    _need(bool(thresholds) and len(thresholds) <= 100 and len(set(thresholds)) == len(thresholds)
          and all(type(t) is int for t in thresholds), 'FIXED_INTEGER_THRESHOLD_POLICY_REQUIRED')
    policy = {'version': 'GAMD_ODDS_FREE_OBSERVATION_V1', 'thresholds': list(thresholds),
              'threshold_predicate': 'YARDS_AT_LEAST', 'line_push_rule': 'EXACT_EQUALITY_PUSH',
              'label_scope': 'CONDITIONAL_ON_VERIFIED_APPEARANCE'}
    existing = list(existing_observations)
    latest, known_ids = {}, set()
    for row in sorted(existing, key=lambda r: (_time(r.get('outcome_observed_at')), r.get('id', ''))):
        _existing_body(row)
        _need(row['id'] not in known_ids, 'DUPLICATE_EXISTING_OBSERVATION')
        known_ids.add(row['id'])
        previous = latest.get(row['forecast_id'])
        if previous:
            _need(row.get('supersedes_observation_id') == previous['id'], 'EXISTING_CORRECTION_CHAIN_BROKEN')
        latest[row['forecast_id']] = row
    grouped, quarantined = defaultdict(list), []
    for outcome in final_outcomes:
        try:
            grouped[_identity(outcome)].append(outcome)
        except (ValueError, KeyError, TypeError) as exc:
            quarantined.append(dict(kind='outcome', identity=[outcome.get(k) for k in KEY], code=str(exc)))
    forecasts = list(sealed_forecasts)
    forecast_counts, population_counts = Counter(), Counter()
    for forecast in forecasts:
        if isinstance(forecast.get('id'), str):
            forecast_counts[forecast['id']] += 1
        try:
            population_counts[(forecast.get('model_sha256'), _identity(forecast))] += 1
        except (ValueError, KeyError, TypeError):
            pass  # malformed rows are quarantined by the full check below
    new_records, pending, unchanged = [], [], []
    for forecast in forecasts:
        try:
            key, law, prospective = _forecast(forecast, cutoff)
            _need(forecast_counts[forecast['id']] == 1, 'DUPLICATE_SEALED_FORECAST_ID')
            model_key = (forecast['model_sha256'], key)
            _need(population_counts[model_key] == 1, 'DUPLICATE_MODEL_PLAYER_GAME_FORECAST')
            matches = grouped.get(key, [])
            if not matches:
                pending.append(dict(forecast_id=forecast['id'], identity=list(key), status='WAITING_VERIFIED_FINAL_OUTCOME'))
                continue
            _need(len(matches) == 1, 'CONFLICTING_OR_DUPLICATE_FINAL_OUTCOMES')
            outcome = matches[0]
            status = _outcome(outcome, forecast, cutoff)
            previous = latest.get(forecast['id'])
            if previous:
                _need(previous['score_policy'] == policy
                      and previous['forecast_ref'] == _ref(forecast['forecast_ref'])
                      and previous['model_sha256'] == forecast['model_sha256']
                      and previous['forecast_payload_sha256'] == _input_digest(forecast),
                      'CONSUMED_OBSERVATION_POLICY_OR_FORECAST_CHANGED')
            if previous and previous['outcome_ref'] == _ref(outcome['outcome_ref']):
                _need(previous['score_policy'] == policy and previous['forecast_ref'] == _ref(forecast['forecast_ref'])
                      and previous['model_sha256'] == forecast['model_sha256']
                      and previous['forecast_payload_sha256'] == _input_digest(forecast)
                      and previous['outcome_payload_sha256'] == _input_digest(outcome),
                      'CONSUMED_OBSERVATION_POLICY_OR_FORECAST_CHANGED')
                unchanged.append(previous['id'])
                continue
            if previous:
                _need(outcome.get('supersedes_outcome_sha256') == previous['outcome_ref']['sha256']
                      and _time(outcome['observed_at']) > _time(previous['outcome_observed_at']),
                      'OUTCOME_CORRECTION_CHAIN_REQUIRED')
            score = _score(law, outcome['yards'], outcome['opportunities'], forecast['expected_opportunities'], thresholds) if status == 'APPEARED' else None
            body = dict(schema_version=SCHEMA, forecast_id=forecast['id'], identity=list(key),
                family=forecast['family'], channel=forecast['channel'], position=forecast.get('position'),
                model_sha256=forecast['model_sha256'], forecast_ref=_ref(forecast['forecast_ref']),
                forecast_payload_sha256=_input_digest(forecast), outcome_payload_sha256=_input_digest(outcome),
                seal_ref=_ref(forecast['seal_ref']), sealed_at=forecast['sealed_at'],
                source_cutoff=forecast['source_cutoff'], kickoff_at=forecast['kickoff_at'],
                forecast_law_sha256=_digest(law), outcome_ref=_ref(outcome['outcome_ref']),
                outcome_source_ref=_ref(outcome['source_ref']), completion_ref=_ref(outcome['completion_ref']),
                source_verification=deepcopy(outcome['source_verification']),
                outcome_observed_at=outcome['observed_at'], completed_at=outcome['completed_at'],
                outcome_status=status, status='SCORED_VERIFIED_FINAL' if score else 'EXCLUDED_' + status,
                classification='PROSPECTIVE_GAME_OBSERVATION' if prospective else 'RETROSPECTIVE_GAME_OBSERVATION',
                training_classification=forecast.get('training_classification'),
                expected_opportunities=forecast['expected_opportunities'],
                unknown_history_count=forecast.get('unknown_history_count', 0),
                verified_history_count=forecast.get('verified_history_count', 0),
                changed_team=forecast.get('changed_team', False),
                score_policy=deepcopy(policy), score=score,
                supersedes_observation_id=previous['id'] if previous else None,
                learning_status='VERIFIED_OBSERVATION_AVAILABLE_NO_PARAMETER_UPDATE' if score else 'EXCLUDED_FROM_LEARNING',
                parameter_updates=0, new_fits=0, genuine_roi=None,
                economic_reason='No exact admitted offer, price, stake and bookmaker-settlement policy is consumed by this odds-free observation function.')
            record = _seal_payload(body)
            new_records.append(record)
            latest[forecast['id']] = record
        except (ValueError, KeyError, TypeError) as exc:
            quarantined.append(dict(kind='forecast_outcome_join', forecast_id=forecast.get('id'),
                                    identity=[forecast.get(k) for k in KEY], code=str(exc)))
    current = list(latest.values())
    models = defaultdict(list)
    for row in current:
        models[row['model_sha256']].append(row)
    return dict(schema_version='puntproof-gamd-observation-batch-v1', as_of=as_of,
        new_records=new_records, new_scored_observations=sum(r['score'] is not None for r in new_records),
        new_exclusions=sum(r['score'] is None for r in new_records), unchanged_observation_ids=unchanged,
        pending=pending, quarantined=quarantined,
        latest_observation_ids=[r['id'] for r in current],
        per_model={key: observation_summary(rows) for key, rows in sorted(models.items())},
        error_atlas=error_atlas(current), originals_modified=0, parameter_updates=0, new_fits=0,
        learning_completed=False, provider_calls=0, genuine_roi=None)
