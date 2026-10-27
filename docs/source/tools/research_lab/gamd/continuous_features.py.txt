"""Pure chronological features from the admitted, nullable player-game projection.

Missing calendar rows are retained. A deterministic identity-only quarter mask
simulates the registered history-coverage failure; it never uses target labels.
No source loading, fitting, provider access, or changes to V3 occur here.
"""
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import math
import statistics

SEED = 20261027
MECHANISM = 'GAMD_INDIVIDUAL_VALIDITY_CONTEXT_MASK_V1'
SCHEMA = 'continuous_individual_context_v1'
POSITIONS = {'receiving': ('WR', 'TE', 'RB', 'FB'), 'rushing': ('RB', 'FB')}
FEATURE_NAMES = (
    'verified_same_team_games', 'unknown_same_team_games', 'dnp_same_team_games',
    'same_team_calendar_games', 'verified_fraction', 'gap_since_verified_periods',
    'observed_opportunity_mean3', 'observed_opportunity_mean5', 'observed_opportunity_mean12',
    'observed_yards_mean3', 'observed_yards_mean5', 'observed_yards_mean12',
    'observed_opportunity_ema', 'observed_opportunity_std5', 'observed_opportunity_slope5',
    'observed_signed_yards_per_opportunity12', 'observed_zero_opportunity_fraction12',
    'observed_negative_yards_fraction12', 'verified_other_team_games',
    'observed_other_team_opportunity_mean5', 'observed_other_team_yards_mean5', 'changed_team',
    'observed_team_opportunity_mean5', 'observed_opponent_allowed_yards_mean5',
    'observed_player_share5', 'team_history_unknown_fraction', 'opponent_history_unknown_fraction',
    'masked_same_team_games', 'position_WR', 'position_TE', 'position_RB', 'position_FB',
)


def feature_contract_sha256():
    """Dataset corrections do not rename or reorder this established vector."""
    contract = {'schema': SCHEMA, 'features': FEATURE_NAMES,
                'chronology': 'STRICTLY_EARLIER_SEASON_WEEK',
                'team_context': 'FULL_RECONCILED_RECEIVING_OR_RBFB_CATEGORY',
                'yards': 'SIGNED_INTEGER', 'missing': 'NULL_NOT_ZERO'}
    return hashlib.sha256(json.dumps(contract, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def identity(row):
    return tuple(row[k] for k in ('season', 'week', 'game_id', 'gsis', 'team', 'channel'))


def period(row):
    return row['season'], row['week']


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def mean(values):
    values = list(values)
    return statistics.fmean(values) if values else None


def validate_row(row, allowed_seasons):
    if type(row.get('season')) is not int or row['season'] not in allowed_seasons:
        raise ValueError('UNADMITTED_SEASON')
    if type(row.get('week')) is not int or not 1 <= row['week'] <= 18:
        raise ValueError('INVALID_REGULAR_WEEK')
    if row.get('channel') not in POSITIONS:
        raise ValueError('INVALID_CHANNEL')
    for field in ('game_id', 'gsis', 'team', 'opponent', 'position', 'participation_state'):
        if not isinstance(row.get(field), str) or not row[field].strip():
            raise ValueError('MISSING_IDENTITY_OR_STATE:' + field)
    if row.get('appeared') is not None and type(row['appeared']) is not bool:
        raise ValueError('INVALID_APPEARANCE')
    for field in ('opportunity', 'yards'):
        value = row.get(field)
        if value is not None and (not number(value) or int(value) != value):
            raise ValueError('NONINTEGER_OR_NONFINITE_LABEL:' + field)
    if row.get('opportunity') is not None and row['opportunity'] < 0:
        raise ValueError('NEGATIVE_OPPORTUNITY')
    if row.get('appeared') is not True and any(row.get(k) not in (None, 0) for k in ('opportunity', 'yards')):
        raise ValueError('PRODUCTION_WITHOUT_VERIFIED_APPEARANCE')
    observed = row.get('source_observed_at')
    if not isinstance(observed, str):
        raise ValueError('MISSING_SOURCE_OBSERVATION')
    try:
        parsed = datetime.fromisoformat(observed.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('INVALID_SOURCE_OBSERVATION') from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('NAIVE_SOURCE_OBSERVATION')
    if not isinstance(row.get('provenance'), list) or not row['provenance']:
        raise ValueError('MISSING_PROVENANCE')


def masked(row, seed=SEED):
    raw = json.dumps([MECHANISM, seed, identity(row)], separators=(',', ':')).encode()
    return row.get('appeared') is True and int.from_bytes(hashlib.sha256(raw).digest()[:8], 'big') % 4 == 0


def usable(row):
    return row.get('appeared') is True and number(row.get('opportunity')) and number(row.get('yards'))


def prior_view(row, seed, apply_mask):
    result = dict(row)
    if apply_mask and masked(row, seed):
        result.update(appeared=None, opportunity=None, yards=None, participation_state='RESEARCH_MASKED_UNKNOWN')
    return result


def _context(rows):
    """Use certified context only; an individually valid focal row cannot certify peers."""
    groups = defaultdict(list)
    for row in rows: groups[period(row)].append(row)
    latest = [groups[key] for key in sorted(groups)[-5:]]
    opportunities, yards = [], []
    flat = [r for group in latest for r in group]
    for group in latest:
        if not group or not all(r.get('team_context_valid') is True for r in group): continue
        # If a certified source total was supplied, require agreement across rows.
        totals = {(r.get('team_opportunities'), r.get('team_yards')) for r in group}
        if len(totals) != 1: continue
        op, yd = next(iter(totals))
        if number(op) and number(yd): opportunities.append(op); yards.append(yd)
    unknown = sum(r.get('appeared') is None or (r.get('appeared') is True and not usable(r)) for r in flat)
    return mean(opportunities), mean(yards), unknown / len(flat) if flat else None


def _features(target, history, team_history, opponent_history):
    same = [r for r in history if r['team'] == target['team']][-24:]
    seen = [r for r in same if usable(r)]
    unknown = [r for r in same if r.get('appeared') is None or (r.get('appeared') is True and not usable(r))]
    dnp = [r for r in same if r.get('appeared') is False]
    other = [r for r in history if r['team'] != target['team'] and usable(r)][-12:]
    opportunities = [r['opportunity'] for r in seen]
    yards = [r['yards'] for r in seen]
    last = seen[-1] if seen else None
    gap = (target['season'] - last['season']) * 20 + target['week'] - last['week'] if last else None
    ema = None
    for value in opportunities:
        ema = value if ema is None else .4 * value + .6 * ema
    denominator = sum(opportunities[-12:])
    team_opp, _, team_missing = _context(team_history)
    _, opponent_yards, opponent_missing = _context(opponent_history)
    opp5, yards5 = mean(opportunities[-5:]), mean(yards[-5:])
    changed = bool(history and history[-1]['team'] != target['team'])
    mask_count = sum(r.get('participation_state') == 'RESEARCH_MASKED_UNKNOWN' for r in same)
    features = [len(seen), len(unknown), len(dnp), len(same), len(seen) / len(same) if same else None, gap]
    features += [mean(opportunities[-n:]) for n in (3, 5, 12)]
    features += [mean(yards[-n:]) for n in (3, 5, 12)]
    features += [ema, statistics.pstdev(opportunities[-5:]) if opportunities else None,
                 (opportunities[-1] - opportunities[-min(5, len(opportunities))]) / (min(5, len(opportunities)) - 1) if len(opportunities) > 1 else None,
                 sum(yards[-12:]) / denominator if denominator else None,
                 mean(float(v == 0) for v in opportunities[-12:]), mean(float(v < 0) for v in yards[-12:]), len(other),
                 mean(r['opportunity'] for r in other[-5:]), mean(r['yards'] for r in other[-5:]), float(changed),
                 team_opp, opponent_yards, opp5 / team_opp if opp5 is not None and team_opp else None,
                 team_missing, opponent_missing, mask_count]
    features += [float(target['position'] == p) for p in ('WR', 'TE', 'RB', 'FB')]
    assert len(features) == len(FEATURE_NAMES)
    return {'x': features, 'baseline': yards5, 'baseline_workload': opp5,
            'verified_history_count': len(seen), 'unknown_history_count': len(unknown),
            'dnp_history_count': len(dnp), 'masked_history_count': mask_count,
            'history_keys': [list(identity(r)) for r in same], 'max_history_period': list(period(history[-1])) if history else None,
            'high_usage': opp5 is not None and opp5 >= (6 if target['channel'] == 'receiving' else 10),
            'changed_role': changed, 'history_window': 'LAST_24_SAME_TEAM_CALENDAR_RECORDS'}


def build_examples(rows, channel, *, seed=SEED, apply_mask=False, allowed_seasons=(2021, 2022, 2023, 2024)):
    if channel not in POSITIONS:
        raise ValueError('INVALID_CHANNEL')
    rows = list(rows)
    keys = set()
    for row in rows:
        validate_row(row, allowed_seasons)
        key = identity(row)
        if key in keys:
            raise ValueError('DUPLICATE_PLAYER_GAME_CHANNEL')
        keys.add(key)
    selected = sorted((r for r in rows if r['channel'] == channel and r['position'] in POSITIONS[channel]), key=lambda r: (period(r), identity(r)))
    by_period = defaultdict(list)
    for row in selected:
        by_period[period(row)].append(row)
    players, teams, opponents = defaultdict(list), defaultdict(list), defaultdict(list)
    examples, exclusions = [], defaultdict(int)
    for key in sorted(by_period):
        group = by_period[key]
        # Add a whole week only after every target in that week has been built.
        for row in group:
            feature = _features(row, players[row['gsis']], teams[row['team']], opponents[row['opponent']])
            if feature['verified_history_count'] < 2:
                exclusions['INSUFFICIENT_VERIFIED_PRIOR_WORKLOAD'] += 1
                continue
            if not usable(row):
                state = 'VERIFIED_NONAPPEARANCE' if row.get('appeared') is False else 'UNKNOWN_OR_INCOMPLETE_TARGET'
                exclusions[state] += 1
                continue
            item = {k: row[k] for k in ('season', 'week', 'game_id', 'gsis', 'team', 'opponent', 'position', 'channel')}
            item.update(feature)
            item.update(identity=list(identity(row)), yards=float(row['yards']), workload=float(row['opportunity']),
                        label_scope='CONDITIONAL_ON_VERIFIED_APPEARANCE')
            examples.append(item)
        for row in group:
            view = prior_view(row, seed, apply_mask)
            players[row['gsis']].append(view)
            teams[row['team']].append(view)
            opponents[row['opponent']].append(view)
        # Team context only needs five chronological weeks; preserve player calendar independently.
        for index in (teams, opponents):
            for team in index:
                periods = sorted({period(r) for r in index[team]})[-5:]
                if periods:
                    index[team] = [r for r in index[team] if period(r) >= periods[0]]
    return {'examples': examples, 'feature_names': list(FEATURE_NAMES), 'mechanism': MECHANISM,
            'audit': {'input_rows': len(rows), 'channel_calendar_rows': len(selected), 'label_rows': len(examples),
                      'excluded_labels': dict(exclusions), 'unknowns_imputed_as_zero': 0, 'snap_fields_used': [],
                      'target_outcomes_used_as_features': False, 'mask': 'IDENTITY_HASH_QUARTER' if apply_mask else 'NONE',
                      'label_scope': 'CONDITIONAL_ON_VERIFIED_APPEARANCE',
                      'team_context': 'EXPLICIT_VALIDITY_REQUIRED_NO_RECONSTRUCTED_SUBTOTAL',
                      'chronology': 'EARLIER_SEASON_WEEK_ONLY; REVISED_ARCHIVES_NOT_AS_OF_PROSPECTIVE'}}


def _timestamp(value, field):
    if not isinstance(value, str):
        raise ValueError('MISSING_TIMESTAMP:' + field)
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('INVALID_TIMESTAMP:' + field) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('NAIVE_TIMESTAMP:' + field)
    return parsed


def build_target_features(rows, target_rows, cutoff, *, seed=SEED, apply_mask=False,
                          allowed_history_seasons=(2021, 2022, 2023, 2024, 2026)):
    """Pure future inputs with explicit admission boundaries and no target labels.

    A single target dict returns a dict; a list returns a list. The caller still
    authenticates source files, fixtures, roster identities and model authority.
    This function checks every supplied history row rather than silently dropping
    protected seasons, after-cutoff rows or target/future-week outcomes.
    """
    cutoff_time = _timestamp(cutoff, 'cutoff')
    if 2025 in allowed_history_seasons:
        raise ValueError('PROTECTED_HISTORY_SEASON')
    histories = list(rows)
    keys = set()
    for row in histories:
        validate_row(row, allowed_history_seasons)
        if _timestamp(row['source_observed_at'], 'history_source_observed_at') > cutoff_time:
            raise ValueError('HISTORY_OBSERVED_AFTER_CUTOFF')
        key = identity(row)
        if key in keys:
            raise ValueError('DUPLICATE_PLAYER_GAME_CHANNEL')
        keys.add(key)
    single = isinstance(target_rows, dict)
    targets = [target_rows] if single else list(target_rows)
    results, target_keys = [], set()
    for target in targets:
        validate_row(target, (2026,))
        if target['position'] not in POSITIONS[target['channel']]:
            raise ValueError('INVALID_TARGET_CHANNEL_POSITION')
        if any(key not in target or target[key] is not None for key in ('yards', 'opportunity', 'appeared')):
            raise ValueError('FUTURE_TARGET_LABELS_MUST_BE_NULL')
        kickoff = _timestamp(target.get('kickoff_at'), 'kickoff_at')
        if kickoff <= cutoff_time:
            raise ValueError('TARGET_NOT_PREGAME_AT_CUTOFF')
        if _timestamp(target['source_observed_at'], 'target_source_observed_at') > cutoff_time:
            raise ValueError('TARGET_RECORD_OBSERVED_AFTER_CUTOFF')
        if identity(target) in target_keys:
            raise ValueError('DUPLICATE_TARGET')
        target_keys.add(identity(target))
        if any(period(row) >= period(target) for row in histories):
            raise ValueError('TARGET_OR_FUTURE_HISTORY')
        selected = [prior_view(row, seed, apply_mask) for row in histories
                    if row['channel'] == target['channel'] and row['position'] in POSITIONS[target['channel']]]
        selected.sort(key=lambda r: (period(r), identity(r)))
        item = {k: target[k] for k in ('season', 'week', 'game_id', 'gsis', 'team', 'opponent', 'position', 'channel', 'kickoff_at')}
        item.update(_features(target, [r for r in selected if r['gsis'] == target['gsis']],
                              [r for r in selected if r['team'] == target['team']],
                              [r for r in selected if r['opponent'] == target['opponent']]))
        item.update(identity=list(identity(target)), feature_names=list(FEATURE_NAMES), mechanism=MECHANISM,
                    cutoff=cutoff, target_yards=None, target_opportunity=None,
                    max_source_observed_at=max((r['source_observed_at'] for r in histories),
                                               key=lambda v: _timestamp(v, 'source_observed_at'), default=None),
                    allowed_history_seasons=list(allowed_history_seasons),
                    artificial_mask_applied=apply_mask,
                    target_provenance=target['provenance'],
                    label_scope='CONDITIONAL_ON_VERIFIED_APPEARANCE; FUTURE_AVAILABILITY_UNKNOWN')
        results.append(item)
    return results[0] if single else results
