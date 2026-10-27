"""Immutable action preparation from authenticated control and projection receipts.

This module never reads football rows or fitted models. Its helper can prepare
source registration before live admission; execution still requires committed
consumers, implementation, source witnesses and the normal GAMD authority gate.
"""
from pathlib import Path

PRIOR_SANDBOX = 'scratch/coverage-gamd-kit-20261027/PuntProof_Coverage_GAMD/evidence/COMPLETED_SANDBOX_TRIALS.json'
PRIOR_CONTEXT = 'research/puntproof_rebuild_v1/context_repair_v2/REPORT.md'
PRIOR_CLOSEOUT = 'research/research_lab_v1/runtime_v1/ENGINEERING_CLOSEOUT.json'


def seal_manifest(root, directory, manifest, consumer, consumer_ref, witness):
    """Seal one action and return MANIFEST.json; no live authority is inferred.

    The manifest spec hash excludes its readiness and PRETASK references, avoiding
    a circular hash. Preparation contains no time-dependent generated fields, so
    an unchanged interrupted preclaim preparation is idempotent.
    """
    from nfl_model import gamd_gate as gate
    root = Path(root).resolve()
    directory = Path(directory).resolve()
    if not directory.is_relative_to(root):
        raise ValueError('GAMD_PREPARATION_DIRECTORY_ESCAPE')
    body = dict(manifest)
    operation = body.get('operation')
    if operation not in gate.OPS or consumer.get('operation') != operation:
        raise ValueError('GAMD_PREPARATION_OPERATION')
    # Authenticate supplied control bytes without consumer_for/live stage calls.
    if gate.read(root, consumer_ref) != consumer:
        raise ValueError('GAMD_PREPARATION_CONSUMER_CHANGED')
    canary = gate.read(root, consumer['adapter_canary'])
    if (canary.get('status') != 'PASS' or canary.get('provider_calls') != 0
            or canary.get('real_model_fits') != 0
            or canary.get('code_sha256') != {r['path']: r['sha256'] for r in consumer['implementation']}
            or not set(gate.CANARIES) <= set(canary.get('passed_defects', []))):
        raise ValueError('GAMD_PREPARATION_ADAPTER_NOT_VERIFIED')
    for reference in consumer['implementation']:
        gate.checked(root, reference)
    seasons = [2021, 2022, 2023, 2024] if operation == gate.DEVELOPMENT or body.get('purpose') == 'HISTORICAL_PROJECTION' else [2026]
    if witness.get('status') != 'PASS' or witness.get('provider_calls') != 0 or witness.get('seasons') != seasons or type(witness.get('records')) is not int or witness['records'] <= 0:
        raise ValueError('GAMD_PREPARATION_TARGET_WITNESS')
    if operation == gate.SOURCE and witness.get('catalogue') != consumer['catalogue']:
        raise ValueError('GAMD_PREPARATION_SOURCE_WITNESS_BINDING')
    if operation == gate.DEVELOPMENT and any(witness.get(key) != body.get(key) for key in ('projection', 'projection_receipt')):
        raise ValueError('GAMD_PREPARATION_FIT_WITNESS_BINDING')
    for key, value in {'consumer': consumer_ref, 'campaign_ref': consumer['campaign'],
                       'source_policy': consumer['source_policy'], 'provider_calls': 0,
                       'cost_cents': 0, 'protected_access': False, 'production': False}.items():
        if key in body and body[key] != value:
            raise ValueError('GAMD_PREPARATION_BOUNDARY_CHANGED:' + key)
        body[key] = value
    body.pop('readiness', None)
    body.pop('pretask_satisfiability', None)
    body['spec_hash'] = gate.spec_hash(body)
    path = directory / 'MANIFEST.json'
    if path.exists():
        retained = gate.read(root, gate.ref(root, path))
        if retained.get('spec_hash') != body['spec_hash'] or gate.spec_hash(retained) != body['spec_hash']:
            raise ValueError('GAMD_PREPARATION_MANIFEST_CONFLICT')
        ready = gate.read(root, retained['readiness'])
        if gate.read(root, ready['target_witness']) != witness or ready.get('consumer') != consumer_ref:
            raise ValueError('GAMD_PREPARATION_RETAINED_WITNESS_CHANGED')
        gate.checked(root, retained['pretask_satisfiability'])
        return path
    directory.mkdir(parents=True, exist_ok=True)
    witness_path = directory / 'WITNESS.json'
    gate.write_once(witness_path, witness)
    witness_ref = gate.ref(root, witness_path)
    ready_path = directory / 'READINESS.json'
    gate.write_once(ready_path, {
        'schema_version': 'puntproof-gamd-action-readiness-v1', 'status': 'READY',
        'operation': operation, 'consumer': consumer_ref, 'manifest_spec_sha256': body['spec_hash'],
        'implementation': consumer['implementation'], 'target_witness': witness_ref,
        'adapter_canary': consumer['adapter_canary'], 'provider_calls': 0,
        'does_not_grant_execution': True,
    })
    witness_class = ('GAMD_SOURCE_CATALOGUE' if operation == gate.SOURCE else
                     'GAMD_FIT_PROJECTION' if operation == gate.DEVELOPMENT else 'GAMD_PREGAME_INPUTS')
    season_strings = list(map(str, seasons))
    defects = []
    for defect in gate.CANARIES:
        defect_path = directory / 'canaries' / (defect + '.json')
        gate.write_once(defect_path, {
            'status': 'PASS', 'provider_calls': 0, 'defect_id': defect,
            'target_seasons': season_strings, 'witness_class': witness_class,
            'adapter_canary': consumer['adapter_canary'], 'implementation': consumer['implementation'],
            'evidence_kind': 'AUTHENTICATED_SAME_ADAPTER_CANARY_NOT_REAL_MODEL_EVIDENCE',
        })
        defects.append({'defect_id': defect, 'canary': gate.ref(root, defect_path)})
    index_ref = gate.ref(root, 'research/experiment_index.jsonl')
    pretask = {
        'schema_version': 'nfl-pretask-satisfiability-v1', 'task_id': operation, 'phase': 'REAL_EXECUTION',
        'review': {
            'experiment_index': index_ref, 'consumed_attempts': [index_ref],
            'negative_knowledge': [gate.ref(root, PRIOR_CONTEXT)],
            'closeouts': [gate.ref(root, PRIOR_CLOSEOUT), gate.ref(root, PRIOR_SANDBOX)],
            'equivalent_work_exists': True, 'reuse_only': operation != gate.DEVELOPMENT,
            'source_repair_reopens_science': False,
            'unresolved_question': ('The exact six approved missing-history GAMD recipes; preserved overlapping results remain operative.'
                                   if operation == gate.DEVELOPMENT else
                                   'Reuse exact admitted records or saved artifacts for this separately bounded source or forward action; no new fitting.'),
        },
        'budget': {'max_calls': 0, 'max_cost_cents': 0, 'remaining_calls': 0, 'reserve_calls': 0},
        'predicates': [{
            'predicate_id': 'GAMD_EXACT_ACTION_INPUTS_AND_ADAPTER', 'mandatory': True,
            'terminal_outcome_dependency': ['GAMD_OPERATION_COMPLETE'],
            'required_witness_class': witness_class,
            'source_artifacts': [{**witness_ref, 'seasons': season_strings, 'witness_class': witness_class},
                                 {**consumer['adapter_canary'], 'seasons': season_strings, 'witness_class': 'GAMD_SAME_ADAPTER_CANARY'}],
            'target_seasons': season_strings, 'target_population': body.get('population', body.get('purpose', operation)),
            'retained_availability': True, 'authorised_acquisition': [],
            'required_authority': ['READ_METADATA'] + ([] if operation == gate.SOURCE else ['MODEL_EXECUTION']),
            'maximum_calls': 0, 'maximum_cost_cents': 0, 'known_defects': defects,
            'cross_season_provenance': {'inherited_from_seasons': [], 'verified_target_seasons': season_strings},
            'equivalent_prior_work_checked': True, 'status': 'SATISFIABLE_RETAINED',
        }],
    }
    pretask_path = directory / 'PRETASK.json'
    gate.write_once(pretask_path, pretask)
    body['readiness'] = gate.ref(root, ready_path)
    body['pretask_satisfiability'] = gate.ref(root, pretask_path)
    if body['spec_hash'] != gate.spec_hash(body):
        raise ValueError('GAMD_PREPARATION_CIRCULAR_SPEC')
    gate.write_once(path, body)
    return path


def fit_witness(receipt, projection_ref, receipt_ref, channel, folds, minimum_rows=40, *, family=None):
    """Prove fixed-fold label sufficiency from the admitted projection audit."""
    audit = receipt['audit']
    if receipt.get('projection') != projection_ref:
        raise ValueError('GAMD_FIT_WITNESS_PROJECTION_CHANGED')
    if audit.get('unknowns_imputed_zero') is not False or audit.get('snap_fields_used') is not False:
        raise ValueError('GAMD_FIT_WITNESS_SOURCE_SEMANTICS')
    by_period = audit.get('label_rows_by_channel_season_week', {}).get(channel)
    if not isinstance(by_period, dict):
        raise ValueError('GAMD_PROJECTION_LABEL_COUNTS_REQUIRED')
    positive_periods = audit.get('positive_label_rows_by_channel_season_week', {}).get(channel)
    if family == 'hurdle_glm' and not isinstance(positive_periods, dict):
        raise ValueError('GAMD_POSITIVE_LABEL_COUNTS_REQUIRED')
    counts, positive_counts = [], []
    for fold in folds:
        split = {'train': 0, 'calibration': 0, 'evaluation': 0}
        for key, count in by_period.items():
            year, week = map(int, key.split('-'))
            if year not in (2021, 2022, 2023, 2024) or not 1 <= week <= 18 or type(count) is not int or count < 0:
                raise ValueError('GAMD_INVALID_LABEL_COUNT_WITNESS')
            period = (year, week)
            if period <= tuple(fold['train_end']):
                split['train'] += count
            elif tuple(fold['calibration_start']) <= period <= tuple(fold['calibration_end']):
                split['calibration'] += count
            elif year == fold['evaluation_season']:
                split['evaluation'] += count
        if any(n < minimum_rows for n in split.values()):
            raise ValueError('GAMD_INSUFFICIENT_FIXED_SPLIT:' + channel + ':' + fold['id'] + ':' + str(split))
        counts.append({'fold': fold['id'], **split})
        if family == 'hurdle_glm':
            positive = {'train': 0, 'calibration': 0}
            for key, count in positive_periods.items():
                year, week = map(int, key.split('-'))
                if year not in (2021, 2022, 2023, 2024) or not 1 <= week <= 18 or type(count) is not int or count < 0 or count > by_period.get(key, 0):
                    raise ValueError('GAMD_INVALID_POSITIVE_LABEL_WITNESS')
                period = (year, week)
                if period <= tuple(fold['train_end']):
                    positive['train'] += count
                elif tuple(fold['calibration_start']) <= period <= tuple(fold['calibration_end']):
                    positive['calibration'] += count
            if any(n < 20 for n in positive.values()):
                raise ValueError('GAMD_INSUFFICIENT_HURDLE_POSITIVE_SPLIT:' + channel + ':' + fold['id'] + ':' + str(positive))
            positive_counts.append({'fold': fold['id'], **positive})
    return {'status': 'PASS', 'provider_calls': 0, 'seasons': [2021, 2022, 2023, 2024],
            'records': sum(by_period.values()), 'projection': projection_ref, 'projection_receipt': receipt_ref,
            'channel': channel, 'family': family, 'fold_counts': counts, 'minimum_split_rows': minimum_rows,
            'positive_fold_counts': positive_counts,
            'label_scope': 'CONDITIONAL_ON_VERIFIED_APPEARANCE; UNKNOWN_LABELS_EXCLUDED_AND_COUNTED',
            'basis': 'ADMITTED_SOURCE_PROJECTION_AUDIT; NO_FIT_OR_EVALUATION_PERFORMED'}
