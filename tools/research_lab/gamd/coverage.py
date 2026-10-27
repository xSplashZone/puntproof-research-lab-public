"""Exact retained Week 7 coverage reconciliation; no forecast or model changes.

New source reads occur inside the registered GAMD source context. Explicitly
accepted immutable receipts can be reused without reopening those sources. This
module does not call the Data Manager, invoke V3, add career rows to V3 history,
or infer an untouched roster player's absence/zero from a missing box-score row.
"""
from collections import Counter
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

import source_adapter as adapter
from coverage_review import reconcile_coverage, reconciliation_csv, reconciliation_json


ORIGINAL_DECISIONS = {'path': 'data/private/research-lab/sources/coverage-d6496fd2832e68619f90436b/DECISIONS.json',
                      'sha256': '39337d1ad9673b053b068b35197bbd9af86114934ec56afdb7dfddd396d3bd91'}
ORIGINAL_PROJECTION = {'path': 'data/private/research-lab/sources/prepared-88080a52e4951f84ca1097da/PROJECTION.json',
                       'sha256': 'd6496fd2832e68619f90436b45de87b16d55c32fa68036a41c5b845fb38943b2'}
ORIGINAL_PREPARATION = {'path': 'data/private/research-lab/executions/prepare_forecast-c84eee72fbbcf0f494695e9c/MANIFEST.json',
                        'sha256': '1bd861b6cf03c8c0733415e25983e5597ca382d919fb6345267b65706543f89f'}
ORIGINAL_CUTOFF = '2026-10-26T21:32:10.865+00:00'
TARGET = (2026, 7)
FIELDS = ('targets', 'receptions', 'receiving_yards', 'carries', 'rushing_yards')
SCHEDULE_FIELDS = frozenset(('season', 'game_type', 'week', 'game_id', 'home_team', 'away_team', 'gameday', 'gametime'))


def _need(condition, reason):
    if not condition:
        raise ValueError('GAMD_COVERAGE:' + reason)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _key(row):
    return tuple(row[k] for k in ('season', 'week', 'game_id', 'gsis', 'team'))


def _external_ref(entry):
    return {'path': (Path(entry['source_root']) / entry['path']).resolve().as_posix(),
            'sha256': entry['sha256']}


def _provenance(entry):
    return dict(source_id=entry['source_id'], source_version=entry.get('producer_snapshot_id', entry['sha256']),
                raw_sha256=entry['raw_ref']['sha256'], normalized_sha256=entry['sha256'],
                observed_at=entry['observed_at'], ingested_at=entry.get('ingested_at'),
                published_at=entry.get('published_at'), updated_at=entry.get('updated_at'),
                available_at=None, license=entry.get('licence'),
                model_permission='REGISTERED_GAMD_SOURCE_COVERAGE_ONLY')


def read_coverage_partition(root, entry):
    """Closed current-season projection, retaining no target-week outcome values."""
    from nfl_model import gamd_gate as gate
    gate.require_active_source(root)
    dataset = entry['dataset']
    allowed = set(entry.get('coverage_allowed_fields', []))
    expected = SCHEDULE_FIELDS if dataset == 'schedule' else adapter.FIELD_ALLOWLISTS.get(dataset)
    _need(expected is not None and allowed == set(expected), 'EXACT_EXISTING_COVERAGE_FIELDS_REQUIRED')
    _need(entry['season'] == 2026, 'SOURCE_SEASON')
    adapter.instant(entry.get('observed_at'))
    cap = 18 if dataset == 'schedule' else 7 if dataset == 'rosters' else 6
    _need(entry.get('through_week') == cap, 'SOURCE_WEEK_CEILING')
    source_root = Path(entry['source_root']).resolve()
    path = (source_root / entry['path']).resolve()
    _need(path.is_relative_to(source_root) and not path.is_symlink(), 'SOURCE_PATH')
    _need(gate.sha(path) == entry['sha256'], 'SOURCE_HASH')
    _need(path.stat().st_size <= 64 * 1024 * 1024, 'SOURCE_SIZE_LIMIT')
    rows, scanned, withheld, phase_excluded, expanded = [], 0, 0, 0, 0

    def clean(native):
        nonlocal scanned, withheld, phase_excluded
        scanned += 1
        _need(scanned <= 500000 and isinstance(native, dict), 'SOURCE_ROW_LIMIT_OR_SHAPE')
        season = adapter.number(native.get('season'), count=True)
        if dataset == 'schedule' and season != 2026:
            withheld += 1
            return
        _need(season == 2026, 'NORMALIZED_PARTITION_SEASON_MISMATCH')
        year, week = adapter.period(native)
        if week > cap:
            withheld += 1
            return  # before projecting any production/PBP field
        phase = native.get('game_type' if dataset in ('schedule', 'rosters') else 'season_type')
        _need(phase is not None and phase != '', 'EXPLICIT_REG_PHASE_REQUIRED')
        if phase != 'REG':
            phase_excluded += 1
            return
        projected = {field: native.get(field) for field in allowed}
        _need(all(value is None or type(value) is str for value in projected.values()),
              'NORMALIZED_STRING_OR_NULL_REQUIRED')
        projected['_source'] = _provenance(entry)
        rows.append(projected)

    if dataset == 'schedule':
        _need(entry['format'] == 'csv', 'SCHEDULE_FORMAT')
        with path.open(encoding='utf-8-sig', newline='') as stream:
            for native in csv.DictReader(stream):
                clean(native)
    else:
        _need(entry['format'] == 'jsonl.gz', 'NORMALIZED_FORMAT')
        with gzip.open(path, 'rb') as stream:
            while True:
                line = stream.readline(2 * 1024 * 1024 + 1)
                if not line:
                    break
                expanded += len(line)
                _need(len(line) <= 2 * 1024 * 1024 and expanded <= 256 * 1024 * 1024, 'EXPANSION_LIMIT')
                native = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE_SOURCE_JSON')))
                clean(native)
    _need(gate.sha(path) == entry['sha256'], 'SOURCE_CHANGED_DURING_READ')
    return rows, dict(dataset=dataset, source_ref=_external_ref(entry), admitted_fields=sorted(allowed),
                      through_week=cap, scanned_rows=scanned, retained_rows=len(rows),
                      withheld_future_or_non2026_rows=withheld, nonregular_rows=phase_excluded,
                      football_values_from_target_week_retained=0 if dataset != 'rosters' else None,
                      roster_target_fields='IDENTITY_AND_MEMBERSHIP_ONLY' if dataset == 'rosters' else None)


def _touches(rows):
    result = {}
    yes = lambda value: value in (1, '1', '1.0', True)
    for row in rows:
        if adapter.period(row) >= TARGET or row.get('play_type') == 'no_play' or yes(row.get('two_point_attempt')):
            continue
        fields = []
        if yes(row.get('pass_attempt')):
            fields += [('receiver_player_id', 'receiver'), ('lateral_receiver_player_id', 'lateral_receiver')]
        if yes(row.get('rush_attempt')):
            fields += [('rusher_player_id', 'rusher'), ('lateral_rusher_player_id', 'lateral_rusher')]
        for field, role in fields:
            gsis = row.get(field)
            if not gsis or not adapter.GSIS.fullmatch(gsis) or row.get('play_id') in (None, ''):
                continue
            key = (*adapter.period(row), row.get('game_id'), gsis, adapter.team(row.get('posteam')))
            result.setdefault(key, dict(role=role, play_id=row['play_id'], observed_at=adapter.observation(row)))
    return result


def participation_proofs(original, revised, pbp_rows, entries, *, authority):
    """Only positive, completed, dated evidence can change an original unknown.

    Raw touch evidence without channel production can resolve participation yet
    leave channel values unknown. This is reported honestly rather than imputing
    zero, using roster status as DNP, or borrowing prohibited snap records.
    """
    original_index = {_key(r): r for r in original['data']['records'] if adapter.period(r) < TARGET}
    revised_index = {_key(r): r for r in revised['data']['records'] if adapter.period(r) < TARGET}
    _need(len(revised_index) == len([r for r in revised['data']['records'] if adapter.period(r) < TARGET]), 'DUPLICATE_REVISED_HISTORY')
    fixtures = {f['game_id']: f for f in revised['schedule']}
    source = {e['dataset']: e for e in entries}
    touches = _touches(pbp_rows)
    proofs, unresolved, changes = [], [], []
    for key, old in original_index.items():
        current = revised_index.get(key)
        if old.get('appeared') is not None:
            if current and any(old.get(field) != current.get(field) for field in ('appeared', *FIELDS)):
                changes.append(dict(identity=list(key), code='NEW_VERSION_DIFFERS_FROM_FROZEN_KNOWN_HISTORY',
                                    original_retained=True, values_applied=False))
            continue
        reason = None
        fixture = fixtures.get(old['game_id'])
        if current is None:
            reason = 'NO_EXACT_DATED_HISTORY_ROW_IN_NEW_VERSION'
        elif current.get('position') != old.get('position'):
            reason = 'DATED_POSITION_CONFLICT_WITH_ORIGINAL'
        elif not fixture or fixture.get('completed') is not True or not fixture.get('completion_observed_at'):
            reason = 'NO_VERIFIED_PBP_COMPLETION_WITNESS'
        elif current.get('appeared') is not True:
            reason = 'NO_NEW_POSITIVE_PARTICIPATION_WITNESS'
        if reason:
            unresolved.append(dict(identity=list(key), code=reason))
            continue
        values = {k: current[k] for k in FIELDS if current.get(k) is not None}
        if any(value != 0 for value in values.values()):
            dataset, kind = 'player_stats', 'positive_production'
        elif key in touches:
            dataset, kind = 'pbp', 'pbp_touch'
            values = {k: touches[key][k] for k in ('role', 'play_id')}
        else:
            unresolved.append(dict(identity=list(key), code='NO_ADMITTED_POSITIVE_PROOF_FOR_CHANNEL_VALUES'))
            continue
        entry = source[dataset]
        clocks = [entry['observed_at'], source['rosters']['observed_at'], fixture['completion_observed_at']]
        if current.get('observed_at'):
            clocks.append(current['observed_at'])
        proof = dict(zip(('season', 'week', 'game_id', 'gsis', 'team'), key))
        proof.update(source_id=entry['source_id'], dataset=dataset, source_ref=_external_ref(entry),
                     kind=kind, values=values, identity_state=current.get('identity_state'),
                     observed_at=max(clocks, key=adapter.instant),
                     native_source_observed_at=entry['observed_at'],
                     kickoff_utc=fixture['kickoff_utc'], completed=True,
                     completion_observed_at=fixture['completion_observed_at'],
                     completion_source_ref=_external_ref(source['pbp']),
                     roster_source_ref=_external_ref(source['rosters']),
                     classification='RETROSPECTIVE_RESEARCH_RECONCILIATION')
        proofs.append(proof)
    admissions = []
    for dataset, types, allowed in (('player_stats', ['positive_production'], FIELDS),
                                    ('pbp', ['pbp_touch'], ('role', 'play_id'))):
        entry = source[dataset]
        admissions.append(dict(source_id=entry['source_id'], dataset=dataset, artifact=_external_ref(entry),
            seasons=[2026], allowed_fields=list(allowed), evidence_types=types, model_use=True,
            authority=authority, rights_scope='Diagnostic source reconciliation only; not a V3 forecast or fitting grant.'))
    return dict(evidence_rows=proofs, admissions=admissions,
                unresolved_player_games=unresolved, unchanged_known_history_revisions=changes,
                proof_counts=dict(Counter(p['kind'] for p in proofs)),
                new_history_keys_ignored=len(set(revised_index) - set(original_index)),
                missing_original_history_keys=len(set(original_index) - set(revised_index)))


def _write_bytes_once(path, content):
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _need(path.read_bytes() == content, 'IMMUTABLE_CHILD_ARTIFACT_CONFLICT')
        return path
    with path.open('xb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return path


def runtime_summary(receipt):
    """Small runtime status; the immutable record and exports retain every row."""
    fields = ('status', 'classification', 'manifest', 'catalogue', 'original_cutoff',
              'research_cutoff', 'completed_at', 'summary', 'artifacts', 'download',
              'json_download', 'source_repairs_completed', 'source_supported_challenger_candidates',
              'unknown_candidate_decisions_remaining', 'original_forecasts_modified',
              'target_outcomes_used', 'provider_calls', 'new_fits', 'missing_prerequisite',
              'historical_development_blocked', 'receipt_ref', 'record_id', 'evidence_reused')
    return {key: receipt[key] for key in fields if key in receipt}


def _reuse_accepted(root, policy):
    """Authenticate an explicit committed acceptance before any source context."""
    from nfl_model import gamd_gate as gate
    accepted = policy.get('accepted_coverage_receipt')
    if not accepted:
        return None
    _need(not (root / 'TASK_CHARTER.json').exists(), 'LIVE_TASK_PRECEDENCE')
    consumer, consumer_ref = gate.consumer_for(root, gate.DEVELOPMENT)
    _need(policy.get('consumer') == consumer_ref and policy.get('campaign_ref') == consumer['campaign'],
          'ACCEPTED_POLICY_CONSUMER_MISMATCH')
    active = gate.active_registration(root, consumer)
    _need(active.get('accepted_coverage_receipt') == accepted and
          active.get('coverage_manifest') == policy.get('coverage_manifest'), 'ACCEPTED_RECEIPT_NOT_ACTIVE')
    receipt = gate.read(root, accepted)
    _need(receipt.get('status') == 'COMPLETE' and
          receipt.get('classification') == 'REAL_RETAINED_SOURCE_COVERAGE_RECONCILIATION' and
          receipt.get('summary', {}).get('total') == len(receipt.get('rows', [])) == 783,
          'ACCEPTED_COVERAGE_NOT_COMPLETE')
    _need(receipt.get('catalogue') == consumer['catalogue'] and
          receipt.get('original_cutoff') == ORIGINAL_CUTOFF and
          all(receipt.get(key) == 0 for key in ('original_forecasts_modified', 'target_outcomes_used',
                                               'provider_calls', 'new_fits')),
          'ACCEPTED_COVERAGE_BOUNDARY')
    # This is control metadata, not a re-read of the old decisions/projection.
    original_manifest = gate.read(root, receipt['manifest'], committed=True)
    _need(original_manifest.get('purpose') == 'COVERAGE_RECONCILIATION' and
          original_manifest.get('original_cutoff') == ORIGINAL_CUTOFF and
          original_manifest.get('original_decisions') == ORIGINAL_DECISIONS and
          original_manifest.get('original_projection') == ORIGINAL_PROJECTION and
          original_manifest.get('original_preparation_manifest') == ORIGINAL_PREPARATION,
          'ACCEPTED_ORIGINAL_BINDING')
    artifacts = receipt.get('artifacts', {})
    _need(set(artifacts) == {'report', 'csv', 'revised_projection', 'proofs'}, 'ACCEPTED_ARTIFACT_CENSUS')
    for name, artifact in artifacts.items():
        prefix = 'data/private/research-lab/' + ('exports/' if name in ('report', 'csv') else 'gamd/coverage/')
        _need(artifact.get('path', '').startswith(prefix), 'ACCEPTED_ARTIFACT_SCOPE')
        _need(gate.sha(gate.path_for(root, artifact)) == artifact['sha256'], 'ACCEPTED_ARTIFACT_HASH')
    key = _digest({'manifest': receipt['manifest'], 'catalogue': receipt['catalogue'],
                   'original_decisions': ORIGINAL_DECISIONS, 'projection': ORIGINAL_PROJECTION})
    return dict(runtime_summary(receipt), status='UNCHANGED', evidence_reused=True,
                receipt_ref=accepted, record_id=key)


def run(root, store, policy):
    """Append one exact real reconciliation, independently of fitting/settlement."""
    from nfl_model import gamd_gate as gate
    root = Path(root).resolve()
    reused = _reuse_accepted(root, policy)
    if reused is not None:
        return reused
    manifest_ref = policy['coverage_manifest']
    with gate.source_authority(root, manifest_ref) as admitted:
        manifest = admitted['manifest']
        _need(manifest['purpose'] == 'COVERAGE_RECONCILIATION', 'MANIFEST_PURPOSE')
        for key, expected in (('original_decisions', ORIGINAL_DECISIONS), ('original_projection', ORIGINAL_PROJECTION),
                              ('original_preparation_manifest', ORIGINAL_PREPARATION)):
            _need(manifest.get(key) == expected, 'ORIGINAL_BOUND_REFERENCE_REQUIRED:' + key)
        _need(manifest.get('original_cutoff') == ORIGINAL_CUTOFF, 'ORIGINAL_CUTOFF')
        original_manifest = gate.read(root, ORIGINAL_PREPARATION)
        _need(original_manifest['cutoff'] == ORIGINAL_CUTOFF and original_manifest['week'] == 7
              and original_manifest['season'] == 2026, 'ORIGINAL_EXECUTION_CLOCK_IDENTITY')
        original_key = _digest({'inputs': original_manifest['input_files'], 'target': [2026, 7]})
        _need(original_key.startswith('88080a52e4951f84ca1097da'), 'ORIGINAL_PREPARATION_INPUT_IDENTITY')
        key = _digest({'manifest': manifest_ref, 'catalogue': admitted['catalogue'],
                       'original_decisions': ORIGINAL_DECISIONS, 'projection': ORIGINAL_PROJECTION})
        saved = store.latest('coverage_reconciliation', key)
        if saved:
            for artifact in saved['artifacts'].values():
                gate.checked(root, artifact)
            return dict(runtime_summary(saved), status='UNCHANGED', evidence_reused=True, record_id=key)
        original_decisions = gate.read(root, ORIGINAL_DECISIONS)
        original = gate.read(root, ORIGINAL_PROJECTION)
        entries = admitted['inputs']
        data, read_audits = {}, []
        for entry in entries:
            _need(entry['dataset'] not in data, 'DUPLICATE_COVERAGE_SOURCE')
            data[entry['dataset']], audit = read_coverage_partition(root, entry)
            read_audits.append(audit)
        _need(set(data) == {'schedule', 'rosters', 'player_stats', 'pbp'}, 'EXACT_COVERAGE_SOURCE_SET')
        research_cutoff = manifest['as_of']
        revised = adapter.prepare_retained_inputs(data['schedule'], data['rosters'], data['player_stats'], data['pbp'],
                                                  cutoff=research_cutoff, target_period=TARGET)
        _need(revised.get('full_fixture_census_verified') is True, 'REVISED_TARGET_FIXTURE_CENSUS_NOT_VERIFIED')
        consumer, consumer_ref = gate.consumer_for(root, gate.SOURCE)
        authority = dict(operation=gate.SOURCE, consumer=consumer_ref, manifest=manifest_ref,
                         readiness=manifest['readiness'], source_policy=manifest['source_policy'],
                         semantics=consumer['foundations']['certificate'])
        proof = participation_proofs(original, revised, data['pbp'], entries, authority=authority)
        report = reconcile_coverage(original_decisions, original, original_ref=ORIGINAL_DECISIONS,
            projection_ref=ORIGINAL_PROJECTION, original_cutoff=ORIGINAL_CUTOFF, research_cutoff=research_cutoff,
            evidence_rows=proof['evidence_rows'], admissions=proof['admissions'], expected_total=783)
        report['source_refresh'] = {k: v for k, v in proof.items() if k not in ('evidence_rows', 'admissions')}
        report['source_refresh']['read_audits'] = read_audits
        report['source_refresh']['revised_projection_coverage'] = revised['coverage']
        report['source_refresh']['known_history_revisions_applied'] = False
        report['source_refresh']['historical_career_rows_added_to_v3'] = 0
        directory = store.root / 'gamd' / 'coverage' / key[:24]
        projection_path = gate.write_once(directory / 'REVISED_PROJECTION.json', revised)
        proof_path = gate.write_once(directory / 'PARTICIPATION_PROOFS.json', proof)
        report['revised_projection'] = gate.ref(root, projection_path)
        report['participation_proofs'] = gate.ref(root, proof_path)
        json_path = _write_bytes_once(store.root / 'exports' / ('GAMD_COVERAGE-' + key[:24] + '.json'), reconciliation_json(report))
        csv_path = _write_bytes_once(store.root / 'exports' / ('GAMD_COVERAGE-' + key[:24] + '.csv'), reconciliation_csv(report))
        receipt = dict(status='COMPLETE', classification='REAL_RETAINED_SOURCE_COVERAGE_RECONCILIATION',
            manifest=manifest_ref, catalogue=admitted['catalogue'], original_cutoff=ORIGINAL_CUTOFF,
            research_cutoff=research_cutoff, summary=report['summary'], rows=report['rows'],
            artifacts=dict(report=gate.ref(root, json_path), csv=gate.ref(root, csv_path),
                           revised_projection=gate.ref(root, projection_path), proofs=gate.ref(root, proof_path)),
            download='/api/download/' + csv_path.name, json_download='/api/download/' + json_path.name,
            source_repairs_completed=report['summary']['source_recovered_candidates'],
            source_supported_challenger_candidates=report['summary']['challenger_source_supported'],
            unknown_candidate_decisions_remaining=report['summary']['candidates_with_unknown_after'],
            original_forecasts_modified=0, target_outcomes_used=0, provider_calls=0, new_fits=0,
            missing_prerequisite='Rights-permitted dated offensive-participation or explicit nonappearance evidence for each remaining unknown game.',
            historical_development_blocked=False,
            completed_at=datetime.now(timezone.utc).isoformat())
        receipt_path = gate.write_once(directory / 'RECEIPT.json', receipt)
        store.append('coverage_reconciliation', key, receipt, immutable=True)
        if receipt['unknown_candidate_decisions_remaining']:
            store.alert('GAMD_COVERAGE_UNKNOWN', 'P1', 'Some player histories still lack participation evidence',
                dict(actual=receipt['unknown_candidate_decisions_remaining'],
                     repaired=receipt['source_repairs_completed'], next_action='Open the coverage export for exact missing weeks. Historical challenger fitting can proceed independently; untouched games remain unknown.',
                     download=receipt['download']), key)
        return dict(runtime_summary(receipt), receipt_ref=gate.ref(root, receipt_path), record_id=key, evidence_reused=False)
