"""Bounded development and independent observation in the existing worker."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import json, os, secrets, hashlib, csv, io
from core import now, digest
from .continuous_registry import Queue, verify_legacy_recovery


class StopControl:
    def __init__(self, c):
        self.c = c

    def is_set(self):
        return self.c.stop_event.is_set() or self.c.store.get_setting('worker_paused', False)


def status(c):
    q = Queue(c.store)
    return {
        'runtime': c.store.get_setting('continuous_runtime', {'status': 'AWAITING_ACTIVATION'}),
        'budget': q.budget(), 'attempts': q.rows(),
        'source': c.store.get_setting('continuous_source', {}),
        'forward': c.store.get_setting('continuous_forward', {}),
        'observation': c.store.get_setting('continuous_observation', {}),
        'processes': {'A': 'EXPOSED_HISTORICAL_DEVELOPMENT',
                      'B': c.store.get_setting('continuous_refit_status', 'WAITING_VERIFIED_COMPLETED_PERIOD_AND_NEW_VERSIONED_SNAPSHOT'),
                      'C': 'FIXED_AND_ADAPTIVE_PROSPECTIVE_OBSERVATIONS'},
        'paused': c.store.get_setting('worker_paused', False),
        'next_wake': (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
        'duplicate_policy': 'Same recipe/schema/data/split/seed is never rerun; interruptions are preserved.',
    }


def launch(c, mr):
    from nfl_model import continuous_gamd_gate as g
    from gamd.limits import run, load_runtime
    m = g.read(c.root, mr)
    out = c.root / m['output']
    value = load_runtime(c.root)
    nonce = secrets.token_hex(32)
    g.write_once(out / 'CLAIM.json', {'manifest': mr, 'parent_pid': os.getpid(),
                 'nonce_sha256': hashlib.sha256(nonce.encode()).hexdigest(), 'at': now()})
    argv = [value['base_executable']['path'], '-I', '-B', '-S',
            str(c.root / 'tools/research_lab/gamd/continuous_child.py'), str(c.root / mr['path'])]
    argv.insert(-1, str(c.root))
    return run(argv, c.root, out / 'stdout.log', out / 'stderr.log', seconds=300,
               memory_mb=4096, stop=StopControl(c), handshake=('EXECUTE ' + nonce + '\n').encode())


def publish(c, receipt):
    from nfl_model import continuous_gamd_gate as g
    report = g.read(c.root, receipt['coverage'])
    exports = c.store.root / 'exports'
    exports.mkdir(exist_ok=True)
    links = {}
    for name, body in [('CONTINUOUS_COVERAGE', report), ('CONTINUOUS_FIELD_65', report['priority_65']),
                       ('CONTINUOUS_NAMED_GAPS', g.read(c.root, receipt['named_gaps']))]:
        path = exports / (name + '-' + digest(body)[:16] + '.json')
        g.write_once(path, body)
        links[name] = '/api/download/' + path.name
    if report['rows']:
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=list(report['rows'][0]))
        writer.writeheader()
        for row in report['rows']:
            writer.writerow({k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in row.items()})
        raw = stream.getvalue().encode()
        path = exports / ('CONTINUOUS_TRANSITIONS-' + hashlib.sha256(raw).hexdigest()[:16] + '.csv')
        if not path.exists():
            path.write_bytes(raw)
        links['csv'] = '/api/download/' + path.name
    c.store.set_setting('continuous_source', {
        'summary': receipt['summary'], 'downloads': links, 'transition_matrix': report['transition_matrix'],
        'provider_calls': 0, 'original_forecasts_modified': 0,
        'receipt': receipt['receipt'], 'source_cutoff': receipt['source_cutoff'],
        **{key: receipt.get(key) for key in ('canonical_revision', 'feature_schema', 'feature_contract_sha256',
                                            'dataset_health', 'dataset_health_ref', 'before_after_coverage', 'context_table')},
    })


def observe(c):
    """Refresh admitted retained inputs and account outcomes; never fit or infer."""
    from nfl_model import continuous_gamd_gate as g
    from gamd.continuous_preparation import source
    from gamd.continuous_source import execute
    from gamd.continuous_settlement import run as settle
    if c.stop_event.is_set() or c.store.get_setting('worker_paused', False):
        return {'status': 'PAUSED', 'receipt': None}
    if (c.root / 'TASK_CHARTER.json').exists():
        return {'status': 'LIVE_CHARTER_ACTIVE', 'receipt': None}
    receipt, source_error = None, None
    try:
        # Verify this source consumer independently of the fitting consumer.
        g.consumer_for(c.root, g.SOURCE)
    except Exception as exc:
        state = {'status': 'SOURCE_AUTHORITY_BLOCKED', 'reason': str(exc), 'at': now()}
        c.store.set_setting('continuous_observation', state)
        return {**state, 'receipt': None}
    try:
        receipt = execute(c.root, c.store, source(c.root, c.store))
        publish(c, receipt)
    except Exception as exc:
        source_error = str(exc)
        previous = c.store.get_setting('continuous_source', {}).get('receipt')
        if previous:
            try:
                receipt = {**g.read(c.root, previous), 'receipt': previous}
                directory = (c.root / previous['path']).parent
                for key, name in (('cohort', 'COHORT.json'), ('legacy_receipt', 'LEGACY_RECEIPT.json'),
                                  ('scheduled_receipt', 'SCHEDULED_RECEIPT.json')):
                    if (directory / name).is_file():
                        receipt[key] = g.ref(c.root, directory / name)
            except Exception as previous_error:
                source_error += '; retained receipt: ' + str(previous_error)
    if receipt is None:
        state = {'status': 'SOURCE_BLOCKED', 'reason': source_error, 'at': now(),
                 'next_action': 'Restore the named source prerequisite. No observation or model fit was performed.'}
        c.store.set_setting('continuous_observation', state)
        return {**state, 'receipt': None}
    try:
        evaluations = settle(c, receipt)
        state = {'status': 'ACCOUNTED_RETAINED_RECEIPT' if source_error else 'ACCOUNTED',
                 'at': now(), 'source_receipt': receipt['receipt'], 'source_cutoff': receipt['source_cutoff'],
                 'source_refresh_problem': source_error, 'seals': len(evaluations),
                 'scored_n': sum(v.get('scored_n', len(v.get('scores', []))) for v in evaluations),
                 'unknown_n': sum(v.get('unknown_n', len(v.get('unknown', []))) for v in evaluations),
                 'new_learning_update': False}
    except Exception as exc:
        state = {'status': 'OBSERVATION_BLOCKED', 'reason': str(exc), 'at': now(),
                 'source_refresh_problem': source_error, 'new_learning_update': False}
    c.store.set_setting('continuous_observation', state)
    return {**state, 'receipt': receipt}


def _eligible_plans(c, q, policy, receipt):
    """Keep the controlled paired comparison fixed; no snapshot-driven replenishment."""
    from nfl_model import continuous_gamd_gate as g
    controlled = policy.get('controlled_reevaluation')
    protocol = controlled.get('protocol') if controlled else None
    old = q.rows()
    proposed = []
    for template in policy['templates']:
        if protocol and any(a['plan'].get('foundation_protocol') == protocol and
                            a['plan']['template_id'] == template['id'] and
                            (a['status'] in ('PLANNED', 'CLAIMED') or
                             a.get('phase') in ('FIT_STARTED', 'FIT_COMPLETED', 'EVALUATION_OPENED') or
                             a.get('result') or
                             (a.get('started_at') and a['status'] not in ('PREFLIGHT_FAILED',)))
                            for a in old):
            continue
        scheduled = template['process'] == 'SCHEDULED_REFIT'
        if scheduled and not receipt['verified_complete_games']:
            continue
        legacy = bool(template.get('recovery_of'))
        projection = receipt['legacy_training'] if legacy else receipt['scheduled_training'] if scheduled else receipt['training_projection']
        rr = receipt['legacy_receipt'] if legacy else receipt['scheduled_receipt'] if scheduled else receipt['receipt']
        plan = {k: v for k, v in template.items() if k not in ('id', 'recovery_of')}
        plan.update(template_id=template['id'], projection=projection, projection_receipt=rr, data_sha256=projection['sha256'])
        if protocol:
            plan['foundation_protocol'] = protocol
        if legacy:
            plan['recovery'] = verify_legacy_recovery(c.root, template['recovery_of'])
        proposed.append(plan)
    return proposed, protocol


def run_next(c, observation=None):
    from nfl_model import continuous_gamd_gate as g
    from gamd.continuous_preparation import fit
    from gamd.continuous_forward import run_next as forward
    if c.stop_event.is_set() or c.store.get_setting('worker_paused', False):
        return {'status': 'PAUSED'}
    if (c.root / 'TASK_CHARTER.json').exists():
        return {'status': 'LIVE_CHARTER_ACTIVE'}
    observation = observe(c) if observation is None else observation
    receipt = observation.get('receipt')
    if receipt is None:
        state = {k: v for k, v in observation.items() if k != 'receipt'}
        c.store.set_setting('continuous_runtime', state)
        return state
    q = Queue(c.store)
    issues = {}
    # Both lanes can consume compatible existing models before any new fitting.
    try:
        forward(c, receipt, q.rows())
    except Exception as exc:
        issues['future_inference'] = str(exc)
    try:
        consumer, _ = g.consumer_for(c.root, g.DEVELOPMENT)
        if not getattr(c, 'continuous_recovered', False):
            q.recover(c.root)
            c.continuous_recovered = True
        policy = g.read(c.root, consumer['policy'], True)
        proposed, protocol = _eligible_plans(c, q, policy, receipt)
        source_manifest = receipt.get('source_manifest')
        if source_manifest is None:
            # Existing receipts bind their source manifest through the output directory.
            source_manifest = g.ref(c.root, Path(c.root / receipt['receipt']['path']).parent / 'MANIFEST.json')
        with g.authority(c.root, g.SOURCE, source_manifest):
            skipped = q.admit_distinct(c.root, proposed)
        if skipped:
            c.store.set_setting('continuous_duplicate_evidence', skipped)
        admitted = {t['id'] for t in policy['templates']}
        pending = [r for r in q.rows() if r['status'] == 'PLANNED' and r['plan']['template_id'] in admitted
                   and (not protocol or r['plan'].get('foundation_protocol') == protocol)]
        budget = q.budget()
        if pending and budget['allowed']:
            r = pending[0]
            mr = fit(c.root, r, receipt)
            g.manifest(c.root, g.DEVELOPMENT, mr)
            g.pretask(c.root, g.DEVELOPMENT, g.read(c.root, mr))
            c.child_command(c.root / mr['path'])
            q.claim(r['id'])
            c.force_idle = False
            c.store.set_setting('continuous_runtime', {'status': 'TRAINING', 'attempt': r['id'],
                                'template': r['plan']['template_id'], 'started_at': now(),
                                'next_action': 'One bounded fit; all earlier outcomes are retained.'})
            try:
                execution = launch(c, mr)
                tp = c.root / g.PRIVATE / 'attempts' / r['id'] / 'TERMINAL.json'
                if execution['status'] == 'COMPLETED' and tp.exists():
                    value = g.read(c.root, g.ref(c.root, tp))
                    g.need(value['manifest_sha256'] == mr['sha256'], 'TERMINAL_BINDING')
                    for artifact in value['artifacts']:
                        path = Path(artifact['path'])
                        g.need(path.resolve().is_relative_to(tp.parent.resolve()), 'TERMINAL_ARTIFACT_SCOPE')
                        g.need(g.sha(path) == artifact['sha256'], 'TERMINAL_ARTIFACT_HASH')
                    q.finish(r['id'], value['status'], result=value, elapsed=execution['elapsed_seconds'])
                    if r['plan']['process'] == 'SCHEDULED_REFIT':
                        c.store.set_setting('continuous_refit_status', 'VERSIONED_MODEL_SAVED_AWAITING_FUTURE_EVALUATION')
                    if not c.store.latest('continuous_model', r['id']):
                        c.store.append('continuous_model', r['id'], value, immutable=True)
                else:
                    q.finish(r['id'], execution['status'] if execution['status'] != 'COMPLETED' else 'FAILED',
                             error='Inspect retained stdout/stderr; no automatic scientific retry.', elapsed=execution['elapsed_seconds'])
            except Exception as exc:
                if next(a for a in q.rows() if a['id'] == r['id'])['status'] == 'CLAIMED':
                    q.finish(r['id'], 'FAILED', error=str(exc), elapsed=300)
                raise
        pending = [r for r in q.rows() if r['status'] == 'PLANNED' and r['plan']['template_id'] in admitted
                   and (not protocol or r['plan'].get('foundation_protocol') == protocol)]
        state = {'status': 'QUEUED' if pending and q.budget()['allowed'] else 'BUDGET_COOLDOWN' if pending else 'IDLE_EVIDENCE_EXHAUSTED',
                 'at': now(), 'next_action': 'Next fixed admitted candidate awaits resources.' if pending else
                 'Fixed development work is exhausted. Saved-model future observations and outcome accounting continue.'}
    except Exception as exc:
        issues['development'] = str(exc)
        state = {'status': 'DEVELOPMENT_BLOCKED', 'at': now(),
                 'next_action': 'Resolve the precise development prerequisite. Independent observation has already run.'}
    # A fitting failure must not suppress independent accounting or hide its status.
    state['observation'] = {k: v for k, v in observation.items() if k != 'receipt'}
    state['problems'] = issues
    c.store.set_setting('continuous_runtime', state)
    c.store.set_setting('gamd_next_action', state['next_action'])
    if issues:
        c.store.alert('CONTINUOUS_GAMD', 'P1', 'A research function needs investigation', state, 'continuous')
    return state
