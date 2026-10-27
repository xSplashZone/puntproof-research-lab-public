"""Pure links from the existing football library to GAMD's actual features.

These links describe implementation support, never source admission or evidence
that a football hypothesis works. No concept is acquired, fitted or proposed here.
"""
from collections import Counter
from copy import deepcopy

from .features import FEATURE_NAMES


FAMILIES = ('hurdle_glm', 'hist_gradient_boosting', 'extra_trees')

# concept: actual feature names, qualification, relevant finite recipe families.
# Empty mappings intentionally preserve honest blocked concept slots.
LINKS = {
 'receiver_alignment': (('position_WR', 'position_TE', 'position_RB', 'position_FB'), 'Position is observed; it does not identify X, Z or slot alignment, routes, or a defender.', FAMILIES),
 'te_role': (('position_TE', 'observed_opportunity_mean5'), 'TE position and past targets are available; routes versus blocking are not.', FAMILIES),
 'rb_role': (('position_RB', 'position_FB', 'observed_opportunity_mean5'), 'Aggregate prior opportunity does not identify early-down, third-down or goal-line deployment.', FAMILIES),
 'committee': (('observed_player_share5', 'observed_team_opportunity_mean5', 'team_history_unknown_fraction'), 'Team opportunity is an observed partial subtotal, not a certified RB/FB denominator or availability census.', FAMILIES),
 'snap_share': ((), 'No offensive snap feature is used. Existing PFR restrictions and denominator requirements remain unchanged.', ()),
 'route_share': ((), 'Targets and offensive snaps are not routes; no route denominator is implemented.', ()),
 'target_share': (('observed_player_share5', 'observed_team_opportunity_mean5', 'team_history_unknown_fraction'), 'Observed subtotal share is a missingness-aware proxy; it is not the frozen complete opportunity-share definition.', FAMILIES),
 'substitutions': (('changed_team', 'verified_other_team_games', 'observed_other_team_opportunity_mean5', 'observed_opportunity_slope5'), 'Dated team changes and workload trends are represented; depth charts, substitution times and snap shifts are absent.', FAMILIES),
 'coach_change': ((), 'No dated coaching-change input or coordinator-specific causal adjustment is implemented.', ()),
 'qb_transition': ((), 'No authenticated pregame starting-QB or backup scenario input is implemented.', ()),
 'proe': ((), 'Observed team subtotals are not situation-adjusted pass/run expectation.', ()),
 'pace': ((), 'No neutral-script timing, timeout, score-state or seconds-per-play feature is implemented.', ()),
 'no_huddle': ((), 'No verified no-huddle or play-timing feature is implemented.', ()),
 'personnel_groups': ((), 'Player position indicators do not establish an 11, 12 or 21 personnel package.', ()),
 'motion': ((), 'No motion-player, motion-flag or route feature is implemented.', ()),
 'play_action': ((), 'Pass opportunity does not establish play action; no charting feature is implemented.', ()),
 'rpo': ((), 'Final play type does not establish an RPO; no RPO charting feature is implemented.', ()),
 'screens': ((), 'Targets and yardage do not establish screens, route depth or time to throw.', ()),
 'depth_yac': (('observed_signed_yards_per_opportunity12',), 'Aggregate yards per opportunity does not separate air yards, route depth or YAC.', FAMILIES),
 'red_zone': ((), 'No yardline or red-zone opportunity feature is implemented.', ()),
 'safety_shell': ((), 'No verified pre-snap shell or post-snap rotation feature is implemented.', ()),
 'coverage_family': ((), 'No licensed man, zone or match coverage feature is implemented.', ()),
 'brackets': ((), 'Low usage or a player name does not prove bracket coverage; no charting feature is implemented.', ()),
 'press': ((), 'No press/off or receiver-release feature is implemented.', ()),
 'nickel': ((), 'No defensive personnel or dated slot-matchup feature is implemented.', ()),
 'front_box': ((), 'No box count, defensive front or run-fit feature is implemented.', ()),
 'blitz_pressure': ((), 'No pressure/blitz feature is implemented; sacks are not a substitute.', ()),
 'missed_tackles': ((), 'Long gains do not prove missed tackles; no charting feature is implemented.', ()),
 'position_allowances': (('observed_opponent_allowed_yards_mean5', 'opponent_history_unknown_fraction', 'position_WR', 'position_TE', 'position_RB', 'position_FB'), 'Opponent context is a channel-level observed subtotal, not position-stratified, slot-specific or schedule-adjusted allowance.', FAMILIES),
 'defender_health': ((), 'No authenticated pregame defender health or expected-snap feature is implemented.', ()),
 'line_health': ((), 'No authenticated pregame offensive-line availability, snap or blocking-grade feature is implemented.', ()),
 'qb_efficiency': ((), 'No time-to-throw, CPOE or QB efficiency feature is implemented.', ()),
 'qb_runs': (('position_RB', 'position_FB'), 'The rushing population is RB/FB only. These indicators do not independently audit the upstream scramble/kneel/lateral semantics.', FAMILIES),
 'protection': ((), 'No protection assignment or down-distance feature is implemented.', ()),
 'schedule_strength': (('observed_opponent_allowed_yards_mean5',), 'Raw observed opponent subtotal is not a fixture-graph strength adjustment.', FAMILIES),
 'venue_surface': ((), 'No venue, roof-state or surface-version feature is implemented.', ()),
 'weather_conditions': ((), 'No authenticated forecast-vintage weather feature is implemented.', ()),
 'travel_rest': (('gap_since_verified_periods',), 'The gap is a season/week index from the last verified appearance, not rest days, travel distance, time zone or altitude.', FAMILIES),
 'divisional': ((), 'No dated division or bye feature is implemented.', ()),
 'game_script': (('observed_team_opportunity_mean5', 'team_history_unknown_fraction'), 'Observed previous volume is available; no pregame script or score-state scenario model is implemented.', FAMILIES),
 'in_game_uncertainty': (('verified_fraction', 'unknown_same_team_games', 'dnp_same_team_games', 'observed_opportunity_std5'), 'Prior missingness and variability inform conditional uncertainty. The model does not predict current availability or an in-game injury.', FAMILIES),
 'rules_referees': ((), 'No official assignment, rule-version or penalty enforcement feature is implemented.', ()),
 'late_news': ((), 'No verified inactives/news model input is implemented; source clocks are validation controls only.', ()),
 'zero_signed': (('observed_zero_opportunity_fraction12', 'observed_negative_yards_fraction12', 'verified_same_team_games', 'unknown_same_team_games', 'dnp_same_team_games'), 'Verified zeros, signed yards and unknown/DNP distinctions are implemented, conditional on verified appearance. This does not prove better calibration.', FAMILIES),
 'explosive_tails': (('observed_yards_mean3', 'observed_yards_mean5', 'observed_yards_mean12', 'observed_signed_yards_per_opportunity12'), 'Signed predictive laws retain extremes. There is no dedicated explosive-play feature or demonstrated tail improvement.', FAMILIES),
 'catch_uncertainty': (('observed_opportunity_mean3', 'observed_opportunity_mean5', 'observed_opportunity_mean12', 'observed_opportunity_ema', 'observed_opportunity_std5', 'observed_opportunity_slope5', 'observed_signed_yards_per_opportunity12'), 'Opportunity and yardage are fitted; receptions/conversion and target quality are not separate features or a joint catches model.', ('hurdle_glm',)),
 'teammate_correlation': (('observed_team_opportunity_mean5', 'observed_player_share5'), 'Shared context is not a joint distribution. Independent marginal forecasts cannot price parlays.', FAMILIES),
 'measurement_revision': ((), 'Hash-bound immutable observations can preserve corrections; this is accounting infrastructure, not a fitted correction feature.', ()),
 'market_settlement': ((), 'This campaign has no market-price feature or betting policy. Odds-free scores cannot establish ROI.', ()),
}

# Exact observation names from the existing register. Other requested slots remain
# explicitly unimplemented even when a nearby proxy occurs in LINKS above.
SLOTS = {
 'dated_roster': (('position_WR', 'position_TE', 'position_RB', 'position_FB', 'changed_team'), 'PARTIAL', 'Player-team-position identities are used; current availability is unknown.'),
 'player_carries': (('observed_opportunity_mean3', 'observed_opportunity_mean5', 'observed_opportunity_mean12'), 'SUPPORTED_HISTORY', 'Rushing channel only; verified historical carries.'),
 'player_targets': (('observed_opportunity_mean3', 'observed_opportunity_mean5', 'observed_opportunity_mean12'), 'SUPPORTED_HISTORY', 'Receiving channel only; verified historical targets.'),
 'targets': (('observed_opportunity_mean3', 'observed_opportunity_mean5', 'observed_opportunity_mean12'), 'SUPPORTED_HISTORY', 'Receiving channel only; no target-game targets in features.'),
 'opportunity_counts': (('observed_opportunity_mean3', 'observed_opportunity_mean5', 'observed_opportunity_mean12'), 'SUPPORTED_HISTORY', 'Prior verified channel-specific opportunities.'),
 'signed_yards': (('observed_yards_mean3', 'observed_yards_mean5', 'observed_yards_mean12', 'observed_negative_yards_fraction12'), 'SUPPORTED_HISTORY', 'Signed prior yards and laws; unknown/DNP is not zero.'),
 'yards': (('observed_yards_mean3', 'observed_yards_mean5', 'observed_yards_mean12'), 'SUPPORTED_HISTORY', 'Prior observed yards only.'),
 'prior_usage': (('observed_opportunity_mean5', 'observed_opportunity_ema'), 'SUPPORTED_HISTORY', 'Chronological prior verified workload.'),
 'appearance_evidence': (('verified_same_team_games', 'unknown_same_team_games', 'dnp_same_team_games', 'verified_fraction'), 'SUPPORTED_HISTORY', 'Preserves the caller-admitted canonical participation states; this does not create new participation evidence.'),
 'all_valid_appearances': (('verified_same_team_games', 'unknown_same_team_games', 'dnp_same_team_games'), 'PARTIAL', 'No valid extreme is removed, but verified label coverage is incomplete and deterministic training masking is explicit.'),
 'team_rbfb_carries': (('observed_team_opportunity_mean5', 'team_history_unknown_fraction'), 'PARTIAL', 'Observed RB/FB subtotal; not certified full denominator.'),
 'skill_position_targets': (('observed_team_opportunity_mean5', 'team_history_unknown_fraction'), 'PARTIAL', 'Observed receiving subtotal; not certified full denominator.'),
 'rbfb_carries': (('observed_team_opportunity_mean5', 'team_history_unknown_fraction'), 'PARTIAL', 'Observed rushing subtotal; not certified full denominator.'),
 'opportunity_shares': (('observed_player_share5', 'team_history_unknown_fraction'), 'PARTIAL', 'Share of observed subtotal, not verified complete team opportunity.'),
 'prior_targets_by_position': (('position_WR', 'position_TE', 'position_RB', 'position_FB', 'observed_opponent_allowed_yards_mean5'), 'PARTIAL', 'Position and channel context exist separately; opponent context is not stratified by position.'),
 'prior_yards_by_position': (('position_WR', 'position_TE', 'position_RB', 'position_FB', 'observed_opponent_allowed_yards_mean5'), 'PARTIAL', 'Position and channel context exist separately; opponent context is not stratified by position.'),
 'prior_exposure_distribution': (('observed_opportunity_std5', 'observed_zero_opportunity_fraction12', 'verified_fraction'), 'PARTIAL', 'Past opportunity variability is a proxy; appearance itself is not forecast.'),
 'rusher_position': (('position_RB', 'position_FB'), 'PARTIAL', 'Dated RB/FB player-game role; no play-level rusher classification feature.'),
}


def knowledge_feature_map(concepts, feature_names=None):
    """Map supplied knowledge records without opening the library or any source.

    Accepts the dictionaries returned by the Lab store or ``knowledge_records``
    (including its ``(id, body)`` pairs). All required observation slots survive.
    A feature link means implementation support, never an empirically proven
    mechanism or permission to execute a new concept experiment.
    """
    feature_names = list(FEATURE_NAMES if feature_names is None else feature_names)
    if feature_names != list(FEATURE_NAMES) or len(feature_names) != 32:
        raise ValueError('KNOWLEDGE_FEATURE_SCHEMA_CHANGED')
    rows, seen = [], set()
    for record in concepts:
        if isinstance(record, (list, tuple)) and len(record) == 2:
            concept_id, record = record
        else:
            concept_id = record.get('id')
        if not isinstance(concept_id, str) or not concept_id or concept_id in seen:
            raise ValueError('KNOWLEDGE_CONCEPT_IDENTITY_REQUIRED_OR_DUPLICATE')
        seen.add(concept_id)
        linked, limitation, families = LINKS.get(concept_id, ((), 'No implementation mapping exists for this concept.', ()))
        required = record.get('required_data', record.get('observable_features', []))
        if not isinstance(required, list) or any(not isinstance(slot, str) for slot in required):
            raise ValueError('KNOWLEDGE_REQUIRED_OBSERVATIONS_MALFORMED')
        slots = []
        for slot in required:
            names, status, reason = SLOTS.get(slot, ((), 'BLOCKED_NO_IMPLEMENTED_OBSERVATION',
                'Requires an admitted exact source field and chronological feature implementation; the current 32-feature matrix does not represent this observation.'))
            slots.append(dict(observation=slot, support=status, feature_names=list(names), limitation=reason))
        status = ('IMPLEMENTED_SIGNED_MISSINGNESS_MECHANICS' if concept_id == 'zero_signed'
                  else 'ACCOUNTING_ONLY_NOT_A_FITTED_FEATURE' if concept_id in ('measurement_revision', 'market_settlement')
                  else 'PARTIAL_PROXY_ONLY' if linked else 'BLOCKED_NO_IMPLEMENTED_FEATURE')
        rows.append(dict(concept_id=concept_id, title=record.get('title', concept_id),
            category=record.get('category'), hypothesis=record.get('hypothesis'),
            support=status, feature_names=list(linked), required_observations=slots,
            missing_observations=[x['observation'] for x in slots if x['support'] != 'SUPPORTED_HISTORY'],
            limitation=limitation, original_data_gaps=deepcopy(record.get('data_gaps', [])),
            current_recipe_families=list(families), concept_ablation_executed=False,
            empirical_status='NO_CONCEPT_SPECIFIC_CAUSAL_OR_ABLATION_RESULT',
            new_hypothesis_execution_authorized=False,
            next_action='Use the finite registered campaign only. A new concept-specific feature or ablation requires its own deduplication, source semantics and experimental admission.'))
    linked = {name for row in rows for name in row['feature_names']}
    return dict(schema_version='puntproof-gamd-knowledge-feature-map-v1', feature_count=32,
        feature_names=feature_names, concepts=rows, concept_count=len(rows),
        support_counts=dict(Counter(row['support'] for row in rows)),
        feature_coverage=[dict(feature=name, concepts=[r['concept_id'] for r in rows if name in r['feature_names']],
            role='MECHANISM_OR_CONTEXT' if name in linked else 'MISSINGNESS_OR_WINDOW_CONTROL') for name in feature_names],
        fits_executed=0, provider_calls=0, concept_experiments_executed=0,
        source_admission_created=False, empirical_improvement_claimed=False)
