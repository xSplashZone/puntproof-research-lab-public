"""Immutable coverage reconciliation. Import and calls perform no file/network I/O.

The caller must obtain the existing execution gate before supplying real inputs.
An admission object is a hash-bound description of that gate's evidence, not an
authority grant. This module cannot admit a source, change a V3 forecast, fit a
model, or certify historical point-in-time availability.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
import re


KEY = ('season', 'week', 'game_id', 'gsis', 'team', 'channel')
HISTORY_KEY = KEY[:-1]
CHANNELS = {'receiving': ('targets', 'receptions', 'receiving_yards'),
            'rushing': ('carries', 'rushing_yards')}
HASH = re.compile(r'[a-f0-9]{64}\Z')
GSIS = re.compile(r'\d{2}-\d{7}\Z')
SNAP_DENOMINATOR = 'MAX_INDIVIDUAL_OFFENSIVE_SNAPS_TEAM_GAME'
BLOCKED = ('pfr', 'pro-football-reference', 'pro_football_reference', 'snap_counts',
           'balldontlie', 'ball_dont_lie', 'availability', 'odds')


def _need(condition, code):
    if not condition:
        raise ValueError(code)


def _time(value):
    _need(isinstance(value, str) and bool(value), 'CLOCK_REQUIRED')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    _need(result.tzinfo is not None, 'CLOCK_NAIVE')
    return result.astimezone(timezone.utc)


def _ref(value):
    _need(isinstance(value, dict) and isinstance(value.get('path'), str)
          and bool(value['path']) and HASH.fullmatch(value.get('sha256', '') or ''),
          'HASH_BOUND_REFERENCE_REQUIRED')
    return {'path': value['path'], 'sha256': value['sha256']}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _key(row, *, history=False):
    _need(isinstance(row.get('season'), int) and not isinstance(row['season'], bool)
          and isinstance(row.get('week'), int) and not isinstance(row['week'], bool)
          and 1 <= row['week'] <= 22, 'PERIOD_IDENTITY_REQUIRED')
    _need(GSIS.fullmatch(row.get('gsis', '') or '') is not None
          and isinstance(row.get('game_id'), str) and bool(row['game_id'])
          and isinstance(row.get('team'), str) and bool(row['team']),
          'EXACT_PLAYER_GAME_TEAM_IDENTITY_REQUIRED')
    if not history:
        _need(row.get('channel') in CHANNELS, 'CHANNEL_REQUIRED')
    return tuple(row.get(k) for k in (HISTORY_KEY if history else KEY))


def _number(value, *, count=False):
    _need(not isinstance(value, bool) and isinstance(value, (int, float))
          and math.isfinite(value) and float(value).is_integer()
          and (not count or value >= 0), 'INVALID_EVIDENCE_VALUE')
    return int(value)


def _game(row):
    return {k: deepcopy(row.get(k)) for k in
            ('season', 'week', 'game_id', 'team', 'appeared', 'state', 'observed_at',
             'source_basis', 'source_lineage')}


def _source(admission, row, research_time):
    """Validate closed proof bindings; a generic model_use flag is insufficient."""
    source_ref = _ref(row.get('source_ref'))
    _need(source_ref == _ref(admission.get('artifact')), 'SOURCE_ARTIFACT_MISMATCH')
    _need(row.get('source_id') == admission.get('source_id')
          and row.get('dataset') == admission.get('dataset'), 'SOURCE_ID_MISMATCH')
    origins = [admission.get(k, '') for k in ('source_id', 'dataset', 'rights_owner')]
    origins += list(admission.get('upstream_sources', []))
    origins += [row.get('source_id', ''), row.get('dataset', '')]
    licensed = (admission.get('licence_lane') == 'puntproof_licensed_bdl_v1'
                and admission.get('authority', {}).get('operation') == 'PUNTPROOF_BDL_RESEARCH_SOURCE_V1'
                and admission.get('authority', {}).get('consumer') in ('research/licensed_bdl_v1/SOURCE_CONSUMER.json', 'research/licensed_bdl_v1/lf_v2/SOURCE_CONSUMER.json','research/licensed_bdl_v1/format_v3/SOURCE_CONSUMER.json','research/licensed_bdl_v1/format_v4/SOURCE_CONSUMER.json','research/licensed_bdl_v1/closeout_v5/SOURCE_CONSUMER.json')
                and admission.get('rights_owner') == 'BALLDONTLIE')
    blocked = ('pfr', 'pro-football-reference', 'pro_football_reference', 'availability', 'odds') if licensed else BLOCKED
    _need(not any(term in str(origin).lower() for origin in origins for term in blocked),
          'SOURCE_REMAINS_RESTRICTED')
    authority = admission.get('authority') or {}
    _need(admission.get('model_use') is True and bool(authority.get('operation'))
          and bool(authority.get('consumer')), 'EXACT_SOURCE_AUTHORITY_REQUIRED')
    for field in ('manifest', 'readiness', 'source_policy', 'semantics'):
        _ref(authority.get(field))
    _need(row['season'] in admission.get('seasons', [])
          and row.get('kind') in admission.get('evidence_types', []),
          'SOURCE_PROOF_OUTSIDE_ADMISSION')
    _need(set(row.get('values', {})) <= set(admission.get('allowed_fields', [])),
          'SOURCE_FIELD_NOT_ADMITTED')
    observed = _time(row.get('observed_at'))
    _need(observed <= research_time, 'SOURCE_OBSERVED_AFTER_RESEARCH_CUTOFF')
    for field in ('published_at', 'updated_at', 'available_at', 'ingested_at'):
        if row.get(field) is not None:
            _need(_time(row[field]) <= research_time, 'SOURCE_CLOCK_AFTER_RESEARCH_CUTOFF')
    _need(row.get('identity_state') == 'EXACT_DATED_ROSTER_GSIS',
          'DATED_ROSTER_IDENTITY_REQUIRED')
    _need(row.get('completed') is True
          and _time(row.get('completion_observed_at')) <= research_time,
          'COMPLETED_GAME_WITNESS_REQUIRED')
    _need(_time(row.get('kickoff_utc')) < _time(row['completion_observed_at']),
          'GAME_CLOCK_ORDER_INVALID')
    return observed


def _apply_evidence(old, proof, admission, research_time):
    observed = _source(admission, proof, research_time)
    _need(_key(old, history=True) == _key(proof, history=True), 'EVIDENCE_IDENTITY_MISMATCH')
    if old.get('kickoff_utc'):
        _need(_time(old['kickoff_utc']) == _time(proof['kickoff_utc']), 'KICKOFF_IDENTITY_MISMATCH')
    result = deepcopy(old)
    values = proof.get('values', {})
    kind = proof['kind']
    new_values = {}
    if kind == 'positive_production':
        _need(values and set(values) <= set(sum(CHANNELS.values(), ())), 'PRODUCTION_FIELDS_REQUIRED')
        new_values = {k: _number(v, count=not k.endswith('_yards')) for k, v in values.items()}
        _need(any(v != 0 for v in new_values.values()), 'ZERO_BOX_SCORE_IS_NOT_PARTICIPATION')
        appeared, state = True, 'WITNESSED_PRODUCTION'
    elif kind == 'pbp_touch':
        _need(values.get('role') in ('receiver', 'lateral_receiver', 'rusher', 'lateral_rusher')
              and values.get('play_id') is not None, 'EXACT_PBP_TOUCH_REQUIRED')
        appeared, state = True, 'WITNESSED_PBP_TOUCH'
    elif kind == 'explicit_dnp':
        _need(values.get('did_not_play') is True, 'EXPLICIT_DNP_REQUIRED')
        appeared, state = False, 'DNP'
    elif kind == 'offensive_snaps':
        off = _number(values.get('offense_snaps'), count=True)
        den = _number(values.get('snap_denominator'), count=True)
        _need(values.get('snap_denominator_definition') == SNAP_DENOMINATOR
              and values.get('team_game_complete') is True and den > 0 and 0 <= off <= den,
              'COMPLETE_CORRECT_SNAP_DENOMINATOR_REQUIRED')
        # Special-teams participation or zero offensive snaps proves neither DNP
        # nor an eligible offensive opportunity. Keep that uncertainty explicit.
        appeared = True if off > 0 else None
        state = 'WITNESSED_OFFENSIVE_SNAPS' if off > 0 else 'ZERO_OFFENSIVE_SNAPS_UNKNOWN'
        result.update(offense_snaps=off, snap_denominator=den,
                      snap_denominator_definition=SNAP_DENOMINATOR, snap_share=off / den)
        if off > 0 and values.get('explicit_player_stats') is not None:
            _need(admission.get('licence_lane') == 'puntproof_licensed_bdl_v1'
                  and 'explicit_player_stats' in admission.get('evidence_types', []), 'EXPLICIT_STAT_ROW_NOT_ADMITTED')
            _ref(values.get('explicit_stats_ref'))
            explicit = values['explicit_player_stats']
            _need(set(explicit) <= set(sum(CHANNELS.values(), ())), 'EXPLICIT_STAT_FIELDS')
            new_values.update({k: _number(v, count=not k.endswith('_yards')) for k, v in explicit.items()})
        if off > 0:
            for channel, category in values.get('complete_categories', {}).items():
                _need(channel in CHANNELS and 'complete_category' in admission.get('evidence_types', []),
                      'CATEGORY_PROOF_NOT_ADMITTED')
                _need(category.get('complete') is True and category.get('reconciled') is True,
                      'COMPLETE_RECONCILED_CATEGORY_REQUIRED')
                players, totals = category.get('players'), category.get('team_totals')
                _need(isinstance(players, dict) and isinstance(totals, dict), 'CATEGORY_DENOMINATOR_REQUIRED')
                for gs, fields in players.items():
                    _need(GSIS.fullmatch(gs or '') is not None, 'CATEGORY_PLAYER_IDENTITY_REQUIRED')
                    _need(set(fields) == set(CHANNELS[channel]), 'CATEGORY_FIELDS_INCOMPLETE')
                    if channel == 'receiving':
                        _need(_number(fields['receptions'], count=True) <= _number(fields['targets'], count=True),
                              'CATEGORY_RECEPTIONS_EXCEED_TARGETS')
                for field in CHANNELS[channel]:
                    nums = [_number(p[field], count=not field.endswith('_yards')) for p in players.values()]
                    total = _number(totals.get(field), count=not field.endswith('_yards'))
                    _need(sum(nums) == total, 'CATEGORY_TOTAL_MISMATCH')
                    new_values[field] = players.get(old['gsis'], {}).get(field, 0)
    else:
        raise ValueError('UNSUPPORTED_EVIDENCE_TYPE')
    if old.get('appeared') is not None:
        _need(appeared is not None and old['appeared'] == appeared, 'PARTICIPATION_EVIDENCE_CONFLICT')
    for field, value in new_values.items():
        _need(old.get(field) is None or old[field] == value, 'PRODUCTION_EVIDENCE_CONFLICT')
        result[field] = value
    if result.get('targets') is not None and result.get('receptions') is not None:
        _need(result['receptions'] <= result['targets'], 'RECEPTIONS_EXCEED_TARGETS')
    if appeared is False:
        _need(not any(old.get(k) not in (None, 0) for k in sum(CHANNELS.values(), ())),
              'DNP_WITH_PRODUCTION_CONFLICT')
        for field in sum(CHANNELS.values(), ()):
            result[field] = None
    latest_observation = max(observed, _time(old['observed_at'])) if old.get('observed_at') else observed
    result.update(appeared=appeared, state=state, observed_at=latest_observation.isoformat())
    result['source_basis'] = list(old.get('source_basis') or []) + [kind.upper()]
    result['source_lineage'] = list(old.get('source_lineage') or []) + [dict(
        source_id=proof['source_id'], dataset=proof['dataset'], source_ref=_ref(proof['source_ref']),
        observed_at=proof['observed_at'], authority=deepcopy(admission['authority']))]
    return result


def _summary(history, channel):
    known = [r for r in history if r.get('appeared') is True]
    unknown = [r for r in history if r.get('appeared') is None]
    dnp = [r for r in history if r.get('appeared') is False]
    fields = CHANNELS[channel]
    supported = [r for r in known if all(r.get(k) is not None for k in fields)]
    usage = [r[fields[0]] for r in known[-4:] if r.get(fields[0]) is not None]
    return dict(prior_rows=len(history), verified_appearances=len(known),
                unknown_appearances=len(unknown), explicit_dnp=len(dnp),
                channel_supported_appearances=len(supported),
                prior_usage_mean=sum(usage) / len(usage) if usage else None,
                prior_usage_cases=len(usage), verified_games=[_game(r) for r in known],
                unknown_games=[_game(r) for r in unknown], dnp_games=[_game(r) for r in dnp])


def _remaining_reason(original, history):
    # These require dated fixture/roster repair, which participation evidence alone
    # cannot establish. Never silently remove a team-membership gap after a trade.
    if original in ('UNRESOLVED_PRIOR_FIXTURE_IDENTITY', 'UNRESOLVED_PRIOR_ROSTER_MEMBERSHIP'):
        return original
    if any(r.get('appeared') is None for r in history):
        return 'UNRESOLVED_PRIOR_PARTICIPATION'
    known = [r for r in history if r.get('appeared') is True]
    if len(known) < 2:
        return 'TOO_FEW_SAME_TEAM_APPEARANCES'
    return None


def reconcile_coverage(original_decisions, original_projection, *, original_ref,
                       projection_ref, original_cutoff, research_cutoff,
                       evidence_rows=(), admissions=(), expected_total=783):
    """Reconcile the *same* candidates without changing original V3 eligibility.

    Inputs are already-gated decoded objects. Optional evidence may repair known
    historical keys only; added players, games, aliases and outcomes are rejected.
    New evidence is retrospective research evidence even when it describes an
    earlier game. ``source_repaired_reason`` is diagnostic, never a forecast grant.
    """
    original_ref, projection_ref = _ref(original_ref), _ref(projection_ref)
    original_time, research_time = _time(original_cutoff), _time(research_cutoff)
    _need(original_time <= research_time, 'RESEARCH_CUTOFF_PRECEDES_ORIGINAL')
    decisions = original_decisions.get('rows') if isinstance(original_decisions, dict) else original_decisions
    _need(isinstance(decisions, list) and len(decisions) == expected_total, 'ORIGINAL_DENOMINATOR_MISMATCH')
    original_hash, projection_hash = _digest(original_decisions), _digest(original_projection)
    decision_map = {}
    for row in decisions:
        key = _key(row)
        _need(key not in decision_map, 'DUPLICATE_ORIGINAL_DECISION')
        _need(row.get('decision') in ('ELIGIBLE', 'EXCLUDED') and bool(row.get('reason')),
              'ORIGINAL_DECISION_REQUIRED')
        decision_map[key] = row
    projected = []
    for channel, fixtures in original_projection['fixtures'].items():
        projected.extend((_key(dict(f, channel=channel)), 'ELIGIBLE') for f in fixtures)
    projected.extend((_key(x['fixture']), x['reason']) for x in original_projection['exclusions'])
    _need(len(projected) == len(set(k for k, _ in projected)) == expected_total
          and set(k for k, _ in projected) == set(decision_map), 'ORIGINAL_CENSUS_IDENTITY_MISMATCH')
    for key, reason in projected:
        _need(reason == decision_map[key]['reason'], 'ORIGINAL_REASON_MISMATCH')
        _need(decision_map[key]['decision'] == ('ELIGIBLE' if reason == 'ELIGIBLE' else 'EXCLUDED'),
              'ORIGINAL_DECISION_REASON_CONFLICT')
    periods = {(r['season'], r['week']) for r in decisions}
    _need(len(periods) == 1, 'SINGLE_ORIGINAL_TARGET_PERIOD_REQUIRED')
    target = next(iter(periods))
    old_by_key = {}
    for row in original_projection['data']['records']:
        if (row['season'], row['week']) >= target:
            continue  # discard target/future rows before any football value access
        key = _key(row, history=True)
        _need(key not in old_by_key, 'DUPLICATE_ORIGINAL_HISTORY')
        _need(row.get('appeared') is None or type(row['appeared']) is bool, 'INVALID_APPEARANCE_STATE')
        if row.get('observed_at'):
            _need(_time(row['observed_at']) <= original_time, 'ORIGINAL_HISTORY_CLOCK_AFTER_CUTOFF')
        old_by_key[key] = row
    new_by_key = deepcopy(old_by_key)
    admission_map = {}
    for admission in admissions:
        key = (admission.get('source_id'), _ref(admission.get('artifact'))['sha256'])
        _need(key not in admission_map, 'DUPLICATE_SOURCE_ADMISSION')
        admission_map[key] = admission
    evidence_log, quarantined, seen, rejected_keys = [], [], set(), set()
    for proof in evidence_rows:
        key = None
        try:
            key = _key(proof, history=True)
            _need((proof['season'], proof['week']) < target, 'TARGET_OR_FUTURE_EVIDENCE_REJECTED')
            _need(key in old_by_key, 'EVIDENCE_OUTSIDE_ORIGINAL_DATED_HISTORY')
            proof_key = (*key, proof.get('kind'))
            _need(proof_key not in seen, 'DUPLICATE_PARTICIPATION_EVIDENCE')
            seen.add(proof_key)
            admission = admission_map.get((proof.get('source_id'), _ref(proof.get('source_ref'))['sha256']))
            _need(admission is not None, 'SOURCE_NOT_ADMITTED')
            updated = _apply_evidence(new_by_key[key], proof, admission, research_time)
            new_by_key[key] = updated
            evidence_log.append(dict(identity=list(key), kind=proof['kind'],
                source_ref=_ref(proof['source_ref']), observed_at=proof['observed_at'],
                observed_after_original_cutoff=_time(proof['observed_at']) > original_time,
                classification='RETROSPECTIVE_RESEARCH_RECONCILIATION'))
        except (ValueError, TypeError, KeyError) as exc:
            if key in old_by_key:
                rejected_keys.add(key)
            quarantined.append(dict(identity=[proof.get(k) for k in HISTORY_KEY],
                                    kind=proof.get('kind'), code=str(exc)))
    # Contradictory or duplicate proofs must not leave the first proof accepted.
    # Reconcile each player-game atomically and retain the immutable original.
    for key in rejected_keys:
        new_by_key[key] = deepcopy(old_by_key[key])
    evidence_log = [r for r in evidence_log if tuple(r['identity']) not in rejected_keys]
    old_histories, new_histories = defaultdict(list), defaultdict(list)
    for key, row in old_by_key.items():
        old_histories[row['gsis'], row['team']].append(row)
        new_histories[row['gsis'], row['team']].append(new_by_key[key])
    rows = []
    for original in decisions:
        key = (original['gsis'], original['team'])
        before_history = sorted(old_histories[key], key=lambda r: (r['season'], r['week'], r['game_id']))
        after_history = sorted(new_histories[key], key=lambda r: (r['season'], r['week'], r['game_id']))
        # Match the frozen adapter's last-twelve-known-appearance history window.
        known = [r for r in before_history if r.get('appeared') is True][-12:]
        if len(known) == 12:
            first = (known[0]['season'], known[0]['week'])
            before_history = [r for r in before_history if (r['season'], r['week']) >= first]
            after_history = [r for r in after_history if (r['season'], r['week']) >= first]
        before, after = _summary(before_history, original['channel']), _summary(after_history, original['channel'])
        remaining = _remaining_reason(original['reason'], after_history)
        known_after = [r for r in after_history if r.get('appeared') is True]
        if remaining is None and any(r.get(k) is None for r in known_after for k in CHANNELS[original['channel']]):
            remaining = 'UNKNOWN_RECENT_CHANNEL_VALUES'
        if remaining is None and any(('RB' if r.get('position') == 'FB' else r.get('position')) !=
                                     ('RB' if original.get('position') == 'FB' else original.get('position'))
                                     for r in known_after):
            remaining = 'DATED_POSITION_CHANGE'
        remaining = remaining or 'ELIGIBLE'
        established = after['channel_supported_appearances'] >= 2
        missing = after['unknown_appearances'] > 0
        category = ('identity_or_membership_gap' if remaining.startswith('UNRESOLVED_PRIOR_') and remaining != 'UNRESOLVED_PRIOR_PARTICIPATION'
                    else 'zero_verified_history' if after['verified_appearances'] == 0
                    else 'established_partial_history' if established and missing
                    else 'limited_verified_history' if not established
                    else 'established_complete_history')
        transfers = [r for (gs, tm), history in old_histories.items() if gs == original['gsis'] and tm != original['team'] for r in history]
        high_usage = before['prior_usage_mean'] is not None and before['prior_usage_mean'] >= (5 if original['channel'] == 'receiving' else 10)
        row = {k: deepcopy(original.get(k)) for k in KEY + ('name', 'position', 'opponent')}
        row.update(original=deepcopy(original), frozen_decision=original['decision'], frozen_reason=original['reason'],
                   before=before, after=after, source_repaired_reason=remaining,
                   source_repaired_eligible=remaining == 'ELIGIBLE',
                   frozen_eligibility_changed=False, category=category,
                   recovered_unknown_games=before['unknown_appearances'] - after['unknown_appearances'],
                   priority=1 if missing and before['verified_appearances'] >= 4 and high_usage else
                            2 if missing and before['verified_appearances'] >= 2 else 3,
                   other_team_history=[_game(r) for r in sorted(transfers, key=lambda r: (r['season'], r['week']))],
                   challenger_cohort=('ESTABLISHED_MISSINGNESS_RESEARCH' if established and missing else
                                      'ESTABLISHED_COMPLETE_RESEARCH' if established else 'INSUFFICIENT_VERIFIED_CHANNEL_HISTORY'),
                   challenger_source_supported=established,
                   challenger_current_model_admission=False,
                   pregame_active_verified=None, offered_prop=None,
                   current_availability='UNKNOWN', classification='RETROSPECTIVE_RESEARCH_RECONCILIATION')
        rows.append(row)
    rows.sort(key=lambda r: (r['priority'], *(_key(r))))
    summary = dict(total=len(rows), frozen_eligible=sum(r['frozen_decision'] == 'ELIGIBLE' for r in rows),
        frozen_excluded=sum(r['frozen_decision'] == 'EXCLUDED' for r in rows),
        frozen_reasons=dict(Counter(r['frozen_reason'] for r in rows if r['frozen_decision'] == 'EXCLUDED')),
        source_repaired_eligible=sum(r['source_repaired_eligible'] for r in rows),
        source_recovered_candidates=sum(r['frozen_decision'] == 'EXCLUDED' and r['source_repaired_eligible'] for r in rows),
        candidates_with_unknown_before=sum(r['before']['unknown_appearances'] > 0 for r in rows),
        candidates_with_unknown_after=sum(r['after']['unknown_appearances'] > 0 for r in rows),
        established_partial_history=sum(r['category'] == 'established_partial_history' for r in rows),
        challenger_source_supported=sum(r['challenger_source_supported'] for r in rows),
        categories=dict(Counter(r['category'] for r in rows)),
        accepted_evidence=len(evidence_log), rejected_evidence=len(quarantined),
        pregame_active_population=None, offered_prop_population=None)
    report = dict(schema_version='puntproof-coverage-reconciliation-v1', classification='RETROSPECTIVE_RESEARCH_RECONCILIATION',
        original_ref=original_ref, projection_ref=projection_ref, original_cutoff=original_cutoff,
        research_cutoff=research_cutoff, original_decisions_content_sha256=original_hash,
        original_projection_content_sha256=projection_hash, target_period=list(target),
        census_key=list(KEY), same_population_verified=True, rows=rows, summary=summary,
        evidence=evidence_log, quarantined=quarantined, provider_calls=0,
        original_forecasts_modified=0, new_forecasts_issued=0,
        source_repairs_are_model_changes=False,
        interpretation='Known workload with unknown games remains partial history. A separate missingness-aware challenger cohort is not recovered V3 eligibility, verified current availability, or a bookmaker market population.',
        named_examples=[r for r in rows if str(r.get('name', '')).lower() in ('saquon barkley', 'devonta smith', 'devonta smith jr.')])
    report['warnings'] = repair_warnings(report)
    _need(_digest(original_decisions) == original_hash and _digest(original_projection) == projection_hash,
          'ORIGINAL_INPUT_MUTATED')
    return report


def repair_warnings(report):
    """Owner-visible missing prerequisite/action pairs, with exact candidate keys."""
    warnings = []
    for row in report['rows']:
        unknown = row['after']['unknown_games']
        if unknown:
            warnings.append(dict(code='PARTICIPATION_UNKNOWN', identity={k: row[k] for k in KEY},
                name=row.get('name'), priority=row['priority'], games=unknown,
                missing_prerequisite='Admitted dated offensive-participation or explicit DNP evidence for each listed game.',
                next_action='Use an already retained, rights-permitted, identity-matched source if available. Otherwise retain unknown and use only the separately authorised missingness-aware research cohort.',
                automatic_download=False, source_repair_completed=False))
        elif row['source_repaired_reason'] in ('UNRESOLVED_PRIOR_ROSTER_MEMBERSHIP', 'UNRESOLVED_PRIOR_FIXTURE_IDENTITY'):
            warnings.append(dict(code=row['source_repaired_reason'], identity={k: row[k] for k in KEY},
                name=row.get('name'), priority=2, games=[],
                missing_prerequisite='Exact dated roster/transaction or fixture evidence; other-team history is not same-team membership.',
                next_action='Resolve the dated identity gap through an admitted source before applying the frozen V3 eligibility rule.',
                automatic_download=False, source_repair_completed=False))
    return warnings


def reconciliation_json(report):
    return (json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


def reconciliation_csv(report):
    fields = list(KEY) + ['name', 'position', 'frozen_decision', 'frozen_reason', 'source_repaired_reason',
        'category', 'priority', 'verified_before', 'unknown_before', 'dnp_before', 'verified_after',
        'unknown_after', 'dnp_after', 'unknown_games_before', 'unknown_games_after', 'other_team_games',
        'prior_usage_mean', 'challenger_cohort', 'challenger_source_supported',
        'pregame_active_verified', 'offered_prop', 'frozen_eligibility_changed']
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for row in report['rows']:
        flat = dict(row, prior_usage_mean=row['before']['prior_usage_mean'])
        for label in ('before', 'after'):
            for column, field in (('verified', 'verified_appearances'), ('unknown', 'unknown_appearances'), ('dnp', 'explicit_dnp')):
                flat[f'{column}_{label}'] = row[label][field]
            flat[f'unknown_games_{label}'] = json.dumps(row[label]['unknown_games'], sort_keys=True, separators=(',', ':'))
        flat['other_team_games'] = json.dumps(row['other_team_history'], sort_keys=True, separators=(',', ':'))
        writer.writerow(flat)
    return out.getvalue().encode('utf-8-sig')
