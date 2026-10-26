"""Original football concept definitions with supplied reference metadata.

No web access, model execution, evidence claims, or empirical result fabrication.
"""
from __future__ import annotations
import hashlib
import json

SEED_VERSION = 'football-concepts-v1'
REFERENCE_DATE = '2026-10-26'
SOURCES = [
 ('nfl_terms','NFL Football Operations glossary','https://operations.nfl.com/rules-officiating/nfl-football-basics/football-terms','Free-to-read reference; bulk training and redistribution rights unreviewed.'),
 ('nfl_formations','NFL Football Operations formations','https://operations.nfl.com/rules-officiating/nfl-football-basics/formations','Free-to-read reference; no bulk acquisition permission inferred.'),
 ('nfl_rules','NFL 2026 playing rules','https://operations.nfl.com/rules-officiating/2026-nfl-rulebook','Season-specific reference; rule applicability and reuse require review.'),
 ('nfl_injuries','NFL injury reports','https://www.nfl.com/injuries/','Reference only; current availability is not an authenticated historical snapshot.'),
 ('nflverse','nflverse source and licence registry','https://github.com/nflverse','Supplied register describes general CC BY 4.0 and FTN CC BY-SA 4.0 exceptions; dataset-specific rights review required.'),
 ('nflreadpy','nflreadpy loader documentation','https://nflreadpy.nflverse.com/api/load_functions/','Reader licence does not establish source-data rights.'),
 ('source_vintage','nflverse update and source-vintage notes','https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html','Documented pipeline cadence is not proof of a specific observation or historical availability.'),
 ('participation','nflverse participation dictionary','https://nflverse.r-universe.dev/nflreadr/doc/dictionary_participation.html','Coverage is seasonal; FTN share-alike and postseason publication restrictions require review.'),
 ('osf','Open Source Football','https://opensourcefootball.com/index.html','Free-to-read methodology, not implicit training-data permission.'),
 ('epa_strength','EPA team-strength robustness','https://opensourcefootball.com/posts/2021-06-27-estimating-team-ability-from-epa/','Methodology reference; original summaries only.'),
 ('epa_opponent','Opponent-adjusted EPA','https://opensourcefootball.com/posts/2020-08-20-adjusting-epa-for-strenght-of-opponent/','Methodology reference; preserve prior-game cutoffs.'),
 ('matchquarters','Fangio coverage concepts','https://www.matchquarters.com/p/unlocking-the-fangio-coverage-system','Free portions only; do not bypass subscription or infer charting rights.'),
 ('usa_football','USA Football resources','https://usafootball.com/resources','Some resources require registration/payment; only permitted free reading.'),
 ('weather','MET Norway forecast API','https://api.met.no/','Supplied register describes CC BY 4.0, User-Agent and caching requirements; no acquisition enabled.'),
 ('weather_terms','MET Norway terms','https://developer.yr.no/doc/TermsOfService/','Review applicable terms before enabling weather capture.'),
 ('big_data_bowl','NFL Big Data Bowl 2026 Analytics','https://www.kaggle.com/competitions/nfl-big-data-bowl-2026-analytics/data','CC BY-NC 4.0 stated in supplied register; commercial model use blocked pending separate permission review.'),
]
# id, title, category, definition/mechanism, required observations, gap, falsifiable hypothesis, source ids
CONCEPTS = [
 ('receiver_alignment','X, Z and slot receiver roles','Personnel','Alignment changes route access and the defenders a receiver faces.',['dated_alignment_counts','routes_run','coverage_assignment'],'No verified route or alignment feed is admitted.','Prior slot-route share adds useful receiving-distribution information beyond targets and snaps.',['nfl_formations','participation']),
 ('te_role','Tight-end routes versus blocking','Personnel','An inline or movable tight end can play many snaps while receiving few routes.',['routes_run','pass_block_snaps','run_block_snaps','alignment'],'Snaps are not routes; blocking counts require licensed charting.','Earlier eligible-route share improves TE opportunity forecasts beyond offensive snap share.',['nfl_terms','participation']),
 ('rb_role','Early-down, third-down and goal-line backs','Personnel','Situational deployment allocates carries and receiving opportunity differently.',['snap_down_distance','goal_line_carries','third_down_routes'],'Target counts cannot establish routes on untargeted plays.','A prior situational-role mixture predicts workload changes beyond trailing total carries.',['nfl_terms','participation']),
 ('committee','Backfield committee competition','Personnel','Teammates compete for a limited team workload.',['dated_roster','player_carries','team_rbfb_carries','player_snaps'],'Roster membership alone does not prove availability.','Recent teammate carry-share changes improve RB workload predictions.',['nflverse']),
 ('snap_share','Offensive snap share','Personnel','Share of available offensive exposure informs role and uncertainty.',['player_offense_snaps','all_team_player_offense_snaps'],'Use largest individual offensive snaps in the team-game as denominator; unknown is not zero.','A past-only snap-share trend improves forecasts relative to prior opportunity counts alone.',['nflreadpy','source_vintage']),
 ('route_share','Route participation share','Personnel','Routes quantify pass-catching exposure more directly than total snaps.',['player_routes','team_dropbacks'],'No invented route counts from snaps or targets.','Verified prior route share improves receiving calibration over snap share.',['participation']),
 ('target_share','Target and carry participation','Personnel','Player opportunity shares reflect allocation within team volume.',['player_targets','skill_position_targets','rbfb_carries'],'Receiving and rushing denominators must retain their frozen semantic definitions.','Past opportunity-share shifts improve forecasts after controlling for team volume.',['nflverse']),
 ('substitutions','Substitutions and role transitions','Personnel','Changes in personnel can create new usage regimes.',['dated_depth_chart','snap_share_history','transaction_time'],'Postgame role descriptions cannot become pregame inputs.','A predeclared change detector identifies next-week workload shifts without excess false alarms.',['nfl_injuries','nflreadpy']),
 ('coach_change','Coaching changes','Personnel','A new coordinator may change tempo, personnel and allocation.',['announced_coach_change','prior_team_tendencies'],'Names alone do not measure a scheme or establish a causal effect.','A dated coaching-change indicator improves uncertainty calibration in transition weeks.',['nfl_terms','osf']),
 ('qb_transition','Starting and backup quarterback changes','Quarterback and line','Quarterback changes can alter target depth, distribution and sack risk.',['pregame_qb_status','past_qb_dropbacks','target_distribution'],'Expected starter must be known before cutoff; no postgame substitution leakage.','A pregame QB transition scenario improves receiving-tail calibration.',['nfl_injuries','osf']),
 ('proe','Pass/run over expectation','Offensive structure','Situation-adjusted pass tendency distinguishes scheme from score-driven volume.',['prior_play_calls','down','distance','score','clock'],'Expectation model must be trained only on earlier data.','Prior neutral-context pass tendency improves team opportunity predictions.',['osf']),
 ('pace','Neutral-script pace and seconds per play','Offensive structure','Faster play execution can increase total opportunity.',['play_clock_times','score_margin','timeouts','possession'],'Timeouts, kneels and incomplete timing need explicit handling.','Earlier neutral-script pace adds value beyond trailing play count.',['nflverse','osf']),
 ('no_huddle','No-huddle usage','Offensive structure','Tempo without a huddle can alter substitution and play volume.',['verified_no_huddle_flag','play_timing'],'Missing flags are unknown, not evidence of a huddle.','Prior no-huddle tendency improves expected-play-count forecasts.',['participation']),
 ('personnel_groups','11, 12 and 21 personnel','Offensive structure','Personnel packages change eligible receivers and defensive responses.',['verified_personnel_package','dated_player_roles'],'Formation is not personnel; charting rights and coverage must pass.','Prior personnel mixture improves position-level target allocation.',['nfl_formations','participation']),
 ('motion','Pre-snap motion','Offensive structure','Movement can reveal coverage and change release leverage.',['motion_flag','motion_player','routes_run'],'No verified motion counts currently bound to the model.','Past motion involvement predicts target distribution beyond player usage.',['nfl_formations','participation']),
 ('play_action','Play action','Offensive structure','Run-action deception can change pass depth and protection.',['play_action_flag','dropbacks','target_air_yards'],'Passing labels do not imply play action.','Prior play-action usage improves deep receiving-tail forecasts.',['participation','osf']),
 ('rpo','Run-pass options','Offensive structure','An option read changes how opportunity responds to defensive alignment.',['verified_rpo_charting','box_count','play_choice'],'RPO cannot reliably be inferred from final play type alone.','Verified prior RPO frequency improves RB/receiver workload scenarios.',['participation','usa_football']),
 ('screens','Screens and quick game','Offensive structure','Short throws can shift receiving production from air yards to run-after-catch.',['screen_flag','target_depth','time_to_throw'],'No manufactured route labels for uncharted plays.','Prior screen involvement improves zero and explosive-yardage probabilities.',['participation','osf']),
 ('depth_yac','Air yards, deep routes and YAC','Offensive structure','Target depth and after-catch production create different yardage distributions.',['receiving_air_yards','yards_after_catch','targets'],'Air yards require source semantics; YAC is postgame history only.','Past depth and YAC mix improves signed-yardage distribution tails.',['nflreadpy','osf']),
 ('red_zone','Red-zone spacing and opportunity','Offensive structure','Compressed field space changes workload and achievable yardage.',['yardline','red_zone_targets','red_zone_carries'],'Scoring opportunities are not interchangeable with total yards.','Prior red-zone role improves low-yardage and opportunity probabilities.',['nfl_terms','nflverse']),
 ('safety_shell','Single-high and two-high shells','Defensive structure','Safety depth can redistribute underneath and downfield space.',['pregame_shell_history','post_snap_rotation_charting'],'A pre-snap shell does not identify final coverage.','Verified prior shell tendencies improve opponent-adjusted depth forecasts.',['matchquarters']),
 ('coverage_family','Man, zone and match coverages','Defensive structure','Cover 0/1/2/3/4/6 describe responsibilities that affect route access.',['licensed_coverage_charting','route_matchups'],'No fabricated coverage classification from pass outcome.','Prior opponent coverage mix adds information beyond aggregate defensive allowances.',['matchquarters','nfl_formations']),
 ('brackets','Bracket coverage and receiver attention','Defensive structure','Extra defensive attention can shift opportunity to teammates.',['verified_bracket_charting','receiver_alignment'],'Receiver fame or a low box score is not bracket evidence.','Measured earlier bracket frequency improves teammate target-share forecasts.',['matchquarters']),
 ('press','Press versus off coverage','Defensive structure','Release contact and cushion can alter target timing and depth.',['press_off_charting','receiver_releases'],'Public aggregate stats do not establish press exposure.','Prior press-matchup exposure improves receiver distribution calibration.',['usa_football','matchquarters']),
 ('nickel','Slot, nickel and defensive personnel','Defensive structure','Defensive personnel changes affect coverage and run support.',['defensive_personnel','slot_alignment','dated_defender_status'],'Player position labels do not establish matchup assignments.','Verified prior nickel personnel explains position-specific opportunity changes.',['participation','nfl_injuries']),
 ('front_box','Fronts, box counts and run fits','Defensive structure','Front structure and box occupancy affect rushing lanes and support.',['box_count','defensive_front','run_fit_charting'],'Box count is not a complete run-fit description.','Earlier observed box/front tendencies improve RB efficiency forecasts.',['participation','usa_football']),
 ('blitz_pressure','Pressure and blitz','Defensive structure','Pressure can shorten throws, increase sacks and change target allocation.',['pressure_flag','blitz_count','dropbacks'],'Pressure and blitz are distinct; do not treat sacks as every pressure.','Prior pressure rates improve workload and tail risk beyond sack rate.',['nflreadpy','osf']),
 ('missed_tackles','Missed tackles','Defensive structure','Tackling failures can widen explosive-play tails.',['verified_missed_tackle_charting','yards_after_contact'],'No inferred missed-tackle counts from long gains.','Verified prior tackling measures improve explosive-tail probabilities.',['usa_football']),
 ('position_allowances','Opponent WR/slot/TE/RB allowances','Defensive structure','Position-specific defense can differ from team averages.',['prior_targets_by_position','prior_yards_by_position','opponent_schedule'],'WR1/slot designations require observable dated definitions.','Schedule-adjusted position allowances improve matched-cohort accuracy.',['epa_opponent','nflverse']),
 ('defender_health','Defensive personnel health','Defensive structure','Unavailable defenders may change coverage and run defense.',['timestamped_injury_reports','expected_defensive_snaps'],'An injury label is not a known performance penalty.','Pregame absence scenarios improve predictive intervals without overstating direction.',['nfl_injuries']),
 ('line_health','Pass/run-blocking personnel health','Quarterback and line','Line availability can affect pressure, rushing lanes and pace.',['dated_line_roster','injury_status','prior_line_snaps'],'No fabricated blocking grade or health adjustment.','Predeclared line-absence scenarios improve yardage uncertainty.',['nfl_injuries','nflreadpy']),
 ('qb_efficiency','Time to throw, sacks and CPOE','Quarterback and line','Quarterback style affects pressure exposure and completion opportunity.',['time_to_throw','sacks','completion_probability','pass_attempts'],'Native definitions and earlier-source eligibility must be verified.','Earlier QB efficiency measures add value beyond target counts.',['nflreadpy','osf']),
 ('qb_runs','Scrambles, kneels and eligible laterals','Quarterback and line','QB rushing and RB/FB lateral credit must be separated to preserve context.',['rusher_position','qb_scramble','qb_kneel','lateral_credit'],'Exclude QB runs/scrambles/kneels from RB context but retain eligible credited laterals.','Correct semantic classification eliminates deterministic RB context inconsistencies.',['nfl_rules','nflverse']),
 ('protection','Protection and down-distance distributions','Quarterback and line','Protection demands and long-yardage situations affect opportunity and tails.',['down','distance','pass_protection_charting','sacks'],'Down-distance is observable; protection assignments may be unavailable.','Earlier situation mix improves passing opportunity forecasts.',['nfl_terms','participation']),
 ('schedule_strength','Opponent strength adjusted for schedule','Game context','Raw allowances depend on the offenses already faced.',['prior_opponent_efficiency','prior_fixture_graph'],'No target or future results in strength estimates.','Earlier schedule adjustment improves matched-week prediction over raw allowances.',['epa_strength','epa_opponent']),
 ('venue_surface','Venue, roof and turf','Game context','Venue conditions can alter weather exposure and playing surface.',['verified_venue','roof_state_at_cutoff','surface_version'],'A retractable roof is not proof that the roof will be closed.','Known venue conditions improve uncertainty calibration at equal coverage.',['nfl_terms','weather']),
 ('weather_conditions','Wind, precipitation and temperature','Game context','Weather can affect passing depth, handling and play selection.',['forecast_issue_time','kickoff_forecast','venue_coordinates'],'Observed final weather cannot replace the actual pregame forecast vintage.','Authentic pregame wind forecasts improve passing-distribution calibration.',['weather','weather_terms']),
 ('travel_rest','Travel, time zones, altitude and rest','Game context','Travel and recovery intervals can affect participation and pace.',['fixture_kickoff_utc','venue_timezone','travel_distance','altitude','rest_days'],'Derived geography must be verified; avoid unsupported fatigue claims.','Prior-declared rest/travel features improve forecasts beyond home advantage.',['nflverse']),
 ('divisional','Divisional familiarity and byes','Game context','Repeated opponents and additional preparation may change tendencies.',['division_membership_by_season','prior_matchups','bye_dates'],'Familiarity is a hypothesis, not an automatic adjustment.','Dated divisional and bye indicators improve walk-forward calibration.',['nflverse']),
 ('game_script','Score, expected script and play counts','Game context','A team behind on the scoreboard may shift from rushing to passing.',['pregame_script_distribution','prior_neutral_tendencies','play_counts'],'Actual target-game score and in-game state are forbidden pregame features.','A pregame scenario mixture improves opportunity-tail accuracy.',['osf']),
 ('in_game_uncertainty','Halftime and in-game injury uncertainty','Game context','Unpredictable loss of exposure creates low-production mass.',['prior_exposure_distribution','verified_pregame_status'],'Do not use target-game injury information to filter losses.','An exposure-risk distribution improves low-tail calibration across all eligible players.',['nfl_injuries']),
 ('rules_referees','Rule changes, officials and penalties','Game context','Rule or enforcement changes can affect opportunity and pace.',['season_rule_version','dated_official_assignment','prior_penalties'],'Rule text and observed enforcement are distinct; small samples require shrinkage.','Predeclared season-rule indicators improve stability across seasons.',['nfl_rules']),
 ('late_news','Late news and verified inactives','Game context','Final roster status changes whether a conditional forecast is actionable.',['source_publish_time','observed_time','inactive_list','kickoff_utc'],'Unknown availability remains unknown; late snapshots cannot backdate an earlier decision.','Verified pregame inactive handling improves coverage honesty without altering old forecasts.',['nfl_injuries','source_vintage']),
 ('zero_signed','Negative and zero production','Statistical mechanics','Signed yards and true zero appearances belong in the predictive law.',['appearance_evidence','signed_yards','opportunity_counts'],'Unknown or nonappearance must never be imputed to zero.','A signed zero-aware law improves calibration over a strictly positive law.',['nfl_rules','nflverse']),
 ('explosive_tails','Explosive tails and outliers','Statistical mechanics','Rare long gains materially affect means and forecast risk.',['all_valid_appearances','signed_yards','prior_usage'],'Do not remove genuine extremes or select a tail definition after viewing results.','A locked tail-aware law improves CRPS without high-usage deterioration.',['epa_strength']),
 ('catch_uncertainty','Targets, catches and conversion uncertainty','Statistical mechanics','Receiving yards depend on uncertain opportunity and conversion.',['targets','receptions','yards','prior_target_quality'],'Do not use realized target-game catches or targets as predictors.','Joint opportunity/conversion modelling improves workload and yardage accuracy.',['osf']),
 ('teammate_correlation','Correlated teammate outcomes','Statistical mechanics','Shared team volume and injuries induce dependent player outcomes.',['joint_team_game_history','dated_rosters','opportunity_shares'],'Independent marginal forecasts do not establish parlay probabilities.','A declared team-level dependence model improves joint interval coverage.',['osf']),
 ('measurement_revision','Measurement error and official corrections','Statistical mechanics','A correction changes observed labels without changing an old forecast.',['original_source_hash','revision_hash','official_correction_time'],'Preserve every revision and separate statistical from sportsbook settlement.','Versioned correction handling maintains reproducible scores and highlights material changes.',['source_vintage','nfl_rules']),
 ('market_settlement','Market offers, voids, pushes and settlement','Statistical mechanics','Real returns depend on an actual offer, stake and bookmaker rules.',['offer_id','line','decimal_price','captured_at','settlement_rule','placed_status'],'Proxy thresholds and hypothetical prices never establish betting ROI.','An exact-offer prospective paper ledger measures performance without fabricated market history.',['nfl_rules']),
]


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def knowledge_records():
    sources={s[0]:s for s in SOURCES}
    for key,title,category,definition,features,gap,hypothesis,refs in CONCEPTS:
        yield key,dict(id=key,title=title,category=category,definition=definition,mechanism=definition,
            observable_features=features,required_data=features,data_gaps=[gap],hypothesis=hypothesis,
            falsifiable_test=hypothesis,event_time_rule='Use only authenticated observations available before forecast cutoff; historical release time does not establish pregame availability.',
            source_ids=refs,citations=[dict(title=sources[r][1],url=sources[r][2]) for r in refs],
            empirical_status='UNTESTED',evidence_strength='THEORETICAL_MECHANISM_ONLY',measurability='REQUIRES_SOURCE_AND_SEMANTIC_REVIEW',
            empirical_result=None,tests=[],as_of=REFERENCE_DATE,review_date=None,seed_version=SEED_VERSION,
            provenance='Original definitions for the Research Lab; source links supplied by the owner, not live-verified by this application.')


def seed_knowledge(store):
    for key,title,url,rights in SOURCES:
        store.append('knowledge_source',key,dict(id=key,title=title,url=url,rights_notes=rights,
            access_status='SUPPLIED_REFERENCE_NOT_LIVE_VERIFIED',permission_status='REFERENCE_ONLY_NO_MODEL_ADMISSION',
            source_register_date=REFERENCE_DATE,publication_date=None,reviewed_at=None,acquisition_enabled=False),immutable=True)
    records=list(knowledge_records())
    for key,body in records: store.append('knowledge',key,body,immutable=True)
    return dict(concepts=len(records),sources=len(SOURCES),empirical_status='UNTESTED')


def propose(store,concept_id):
    """Record one frozen, blocked hypothesis; never start an experiment."""
    concept=store.latest('knowledge',concept_id)
    if concept is None: raise ValueError('UNKNOWN_KNOWLEDGE_CONCEPT')
    concept={k:v for k,v in concept.items() if k not in ('revision','record_hash','recorded_at')}
    concept_hash=hashlib.sha256(_canonical(concept)).hexdigest()
    spec=dict(protocol_version=1,concept_id=concept_id,concept_sha256=concept_hash,
        title=concept['title'],hypothesis=concept['hypothesis'],mechanism=concept['mechanism'],
        features=concept['observable_features'],source_ids=concept['source_ids'],required_data=concept['required_data'],
        cutoffs='Authenticated pregame source snapshots only; embargo target-week outcomes.',
        splits=dict(training='Earlier admitted completed weeks only',development='Declared exposed development only',
                    validation='Future whole weeks sealed before kickoff; no protected-outcome access',
                    quarantine='Forward period remains unavailable to iterative optimization'),
        algorithm='NOT_SELECTED_PENDING_FINITE_PROTOCOL_REVIEW',hyperparameters=None,
        baseline=['F','B','Adaptive V3'],seeds=[20261026],max_trials=1,
        budget=dict(max_seconds=300,max_workers=1,max_provider_calls=0,max_cost=0),
        evaluation=dict(primary='Same-population CRPS',guardrails=['High-usage noninferiority','Tail calibration','Coverage','Weekly-block uncertainty'],
                        promotion='No public promotion; shadow gate requires separately declared finite protocol'),
        data_gaps=concept['data_gaps'],status='BLOCKED',blocker='OPERATION_NOT_ADMITTED',
        empirical_status='UNTESTED',trials_attempted=0,result=None,
        duplicate_policy='Reuse this immutable concept/specification proposal; no renamed rerun of a consumed attempt.')
    digest=hashlib.sha256(_canonical(spec)).hexdigest()
    proposal_id='knowledge-'+concept_id+'-'+digest[:16]
    body=dict(spec,id=proposal_id,spec_sha256=digest,frozen=True)
    store.append('experiment',proposal_id,body,immutable=True)
    return body
