"""Immutable, versioned accounting of independently sealed GAMD forecasts."""
import json
import math
from core import now, digest

IDENTITY = ('season', 'week', 'game_id', 'gsis', 'team', 'channel')
METRICS = {
    'mae': 'absolute_error', 'median_mae': 'median_absolute_error',
    'bias': 'signed_error', 'crps': 'crps', 'zero_brier': 'zero_brier',
    'negative_brier': 'negative_brier', 'tail150_brier': 'tail150_brier',
    'threshold_brier': 'threshold_brier', 'coverage80': 'inside80',
    'interval80_width': 'interval80_width', 'workload_mae': 'workload_absolute_error',
}


def latest_evaluations(records):
    """One current observation per seal; Store.records is sorted by id, not time."""
    latest = {}
    for row in records:
        key = row['forecast_manifest']['sha256']
        rank = (int(row.get('evaluation_version', 1)),
                row.get('recorded_at', row.get('settled_at', '')), row.get('id', ''))
        old = latest.get(key)
        if old is None or rank > old[0]:
            latest[key] = (rank, row)
    return [latest[key][1] for key in sorted(latest)]


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def summary(scores):
    from gamd.trainer import summarize_scores
    result = summarize_scores(scores)
    result['denominators'] = {
        label: sum(row.get(field) is not None for row in scores)
        for label, field in METRICS.items()
    }
    result['threshold_observations_n'] = sum(len(row.get('threshold_probabilities', {})) for row in scores)
    return result


def run(c, receipt):
    from nfl_model import continuous_gamd_gate as g
    from gamd.trainer import score_law
    # Receipt and exact history bytes are authenticated even with no future games.
    saved_receipt = g.read(c.root, receipt['receipt'])
    for field in ('history', 'verified_complete_games', 'source_cutoff'):
        if saved_receipt.get(field) != receipt.get(field):
            raise ValueError('OUTCOME_RECEIPT_BINDING:' + field)
    history = [json.loads(line) for line in g.checked(c.root, receipt['history']).splitlines()]
    index = {}
    for row in history:
        key = tuple(row[field] for field in IDENTITY)
        if key in index:
            # Source lineage may differ; conflicting outcome values may not.
            fields = ('appeared', 'yards', 'opportunity')
            if any(index[key].get(field) != row.get(field) for field in fields):
                raise ValueError('CONFLICTING_OUTCOME_IDENTITY:' + repr(key))
        else:
            index[key] = row
    complete = set(receipt['verified_complete_games'])
    current = {row['forecast_manifest']['sha256']: row
               for row in latest_evaluations(c.store.records('continuous_evaluation'))}
    evaluations = []
    watermarks = c.store.get_setting('continuous_outcome_source_watermarks', {})
    ignored = []
    for record in c.store.records('continuous_benchmark'):
        key = record['manifest']['sha256']
        previous = current.get(key)
        manifest = g.read(c.root, record['manifest'])
        if manifest['game_id'] not in complete:
            if previous:
                evaluations.append(previous)
            continue
        watermark = watermarks.get(key, {}).get('source_cutoff') or (previous or {}).get('source_cutoff')
        if watermark and g.stamp(receipt['source_cutoff']) < g.stamp(watermark):
            ignored.append({'forecast_manifest': record['manifest'], 'source_cutoff': receipt['source_cutoff'],
                            'current_source_cutoff': watermark})
            if previous:
                evaluations.append(previous)
            continue
        seal_ref = record.get('seal_ref') or g.ref(c.root, c.root / manifest['output'] / 'SEAL.json')
        seal = g.read(c.root, seal_ref)
        if seal['forecasts'] != record['forecasts'] or seal['manifest'] != record['manifest']:
            raise ValueError('SEALED_FORECAST_RECORD_MISMATCH')
        scores, unknown, dnp = [], [], []
        seen = set()
        for forecast in seal['forecasts']:
            identity = tuple(forecast['identity'])
            if len(identity) != 6 or identity[2] != manifest['game_id'] or (manifest.get('channel') and identity[5] != manifest['channel']):
                raise ValueError('SEALED_FORECAST_GAME_CHANNEL_MISMATCH')
            if identity in seen:
                raise ValueError('DUPLICATE_SEALED_FORECAST_IDENTITY')
            seen.add(identity)
            row = index.get(identity)
            if row and row.get('appeared') is False:
                dnp.append(list(identity))
                continue
            if not row or row.get('appeared') is not True or row.get('yards') is None:
                unknown.append(list(identity))
                continue
            if not number(row['yards']) or int(row['yards']) != row['yards']:
                raise ValueError('INVALID_VERIFIED_YARDS')
            workload = row.get('opportunity')
            if workload is not None and (not number(workload) or workload < 0 or int(workload) != workload):
                raise ValueError('INVALID_VERIFIED_WORKLOAD')
            expected = forecast.get('expected_opportunities')
            workload_error = abs(expected - workload) if number(expected) and workload is not None else None
            score = score_law(forecast['law'], row['yards'])
            score.update(identity=list(identity), actual=row['yards'], actual_workload=workload,
                         workload_absolute_error=workload_error,
                         predicted_mean=forecast['point_mean'], predicted_median=forecast['point_median'])
            scores.append(score)
        semantic = {'scores': scores, 'unknown': unknown, 'verified_dnp': dnp}
        content_hash = digest(semantic)
        watermarks[key] = {'source_cutoff': receipt['source_cutoff'], 'source_receipt': receipt['receipt']}
        # Compare to CURRENT state, so A -> B -> A creates a third linked version.
        if previous and (previous.get('content_hash') == content_hash or
                         digest({k: previous.get(k, []) for k in semantic}) == content_hash):
            evaluations.append(previous)
            continue
        version = int(previous.get('evaluation_version', 1)) + 1 if previous else 1
        predecessor = previous.get('id', key) if previous else None
        record_id = key + ':' + digest({'content': content_hash, 'previous': predecessor, 'version': version})
        value = {
            'status': 'PARTIAL' if unknown else 'SETTLED', 'forecast_manifest': record['manifest'],
            'seal_ref': seal_ref, 'source_receipt': receipt['receipt'], 'source_cutoff': receipt['source_cutoff'],
            'settled_at': now(), 'evaluation_version': version, 'previous_evaluation': predecessor,
            'content_hash': content_hash, **semantic, 'metrics': summary(scores),
            'issued_n': len(seal['forecasts']), 'scored_n': len(scores), 'unknown_n': len(unknown),
            'verified_dnp_n': len(dnp), 'workload_scored_n': sum(s['workload_absolute_error'] is not None for s in scores),
            'lane': record.get('lane', 'LEGACY_FROZEN_BENCHMARK'),
            'game_id': manifest['game_id'], 'channel': manifest.get('channel', seal['forecasts'][0]['identity'][-1] if seal['forecasts'] else None),
            'genuine_roi': None, 'paper_bets': 0,
            'classification': 'PROSPECTIVE_EXPERIMENTAL_OBSERVATION',
            'new_learning_update': False,
            'learning_note': 'Observation accounting only. Verified DNP is excluded from conditional-on-appearance prediction metrics; it is not a bookmaker void. New fits require the separate admitted development policy.',
        }
        c.store.append('continuous_evaluation', record_id, value, immutable=True)
        saved = c.store.latest('continuous_evaluation', record_id)
        evaluations.append(saved)
        current[key] = saved
    c.store.set_setting('continuous_outcome_source_watermarks', watermarks)
    c.store.set_setting('continuous_benchmark_outcomes', {
        'status': 'PARTIAL' if any(v['status'] == 'PARTIAL' for v in evaluations) else
                  'OBSERVATIONS_ACCOUNTED' if evaluations else 'WAITING_VERIFIED_FINAL_GAMES',
        'seals': len(evaluations), 'scored_n': sum(v.get('scored_n', len(v.get('scores', []))) for v in evaluations),
        'unknown_n': sum(v.get('unknown_n', len(v.get('unknown', []))) for v in evaluations),
        'source_receipt': receipt['receipt'], 'source_cutoff': receipt['source_cutoff'],
        'older_sources_ignored': ignored, 'new_learning_update': False})
    return evaluations
