"""Finite candidate registry and paired evaluator; no parameter search or promotion."""
import hashlib
import json
import time
from monitoring import score_rows,paired_week_interval

REQUIRED=('hypothesis','mechanism','features','algorithm','hyperparameters','seed','time_partitions','endpoint','matched_population','selection_rule','uncertainty_rule','duplicate_review','coverage_policy','multiple_testing_budget','acceptance_rejection_criteria','comparison_controls')


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def fingerprint(value):return hashlib.sha256(canonical(value)).hexdigest()


def validate_spec(spec):
    if any(k not in spec or spec[k] is None or spec[k]=='' for k in REQUIRED):raise ValueError('FINITE_SPEC_INCOMPLETE')
    if spec.get('schema_version')!='puntproof-lab-candidate-spec-v1' or spec.get('frozen') is not True:raise ValueError('FROZEN_SPEC_REQUIRED')
    if spec.get('max_trials')!=1 or not 0<spec.get('max_seconds',0)<=300 or spec.get('candidate_ordinal') not in (1,2):raise ValueError('FINITE_BUDGET_REQUIRED')
    if spec.get('provider_calls')!=0 or spec.get('protected_access') is not False:raise ValueError('CAPABILITIES_DENIED')
    if spec['algorithm'] not in ('SEALED_PAIRED_DISTRIBUTION_COMPARISON',):raise ValueError('UNREGISTERED_CANDIDATE_ALGORITHM')
    if spec['endpoint']!='paired_crps' or spec['uncertainty_rule']!='WHOLE_WEEK_97_5_MIN_8_WEEKS':raise ValueError('UNREGISTERED_ENDPOINT')
    if spec.get('season')!=2026 or type(spec.get('week')) is not int or not 1<=spec['week']<=18:raise ValueError('CANDIDATE_TARGET_WEEK_REQUIRED')
    return fingerprint(spec)


class ExperimentRegistry:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS lab_candidate_attempts(
             id TEXT PRIMARY KEY,week_fingerprint TEXT NOT NULL,ordinal INTEGER NOT NULL,season INTEGER NOT NULL,week INTEGER NOT NULL,
             spec_hash TEXT NOT NULL,mechanism_key TEXT NOT NULL,status TEXT NOT NULL,
             created_at TEXT DEFAULT CURRENT_TIMESTAMP,result TEXT,
             UNIQUE(season,week,ordinal),UNIQUE(season,week,mechanism_key));''')
    def reserve(self,spec,*,whole_week_receipt,duplicate_review):
        spec_hash=validate_spec(spec)
        if whole_week_receipt.get('status')!='VERIFIED_COMPLETE' or whole_week_receipt.get('fingerprint')!=spec.get('week_fingerprint'):
            raise ValueError('NEW_VERIFIED_WHOLE_WEEK_REQUIRED')
        if duplicate_review.get('status')!='NEW_UNCONSUMED_MECHANISM' or duplicate_review.get('consumed_equivalent') is not False or duplicate_review.get('fingerprint')!=spec['week_fingerprint']:
            raise ValueError('DUPLICATION_REVIEW_REQUIRED')
        # This stable scientific identity must be reviewed against the repository
        # index; a cosmetic candidate name is not part of it.
        key=fingerprint({k:spec[k] for k in ('mechanism','matched_population','endpoint')})
        attempt=fingerprint([spec['week_fingerprint'],key])
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM lab_candidate_attempts WHERE id=? OR (season=? AND week=? AND ordinal=?)',(attempt,spec['season'],spec['week'],spec['candidate_ordinal'])).fetchone():
                raise ValueError('CANDIDATE_ATTEMPT_ALREADY_CONSUMED')
            if db.execute('SELECT count(*) FROM lab_candidate_attempts WHERE season=? AND week=?',(spec['season'],spec['week'])).fetchone()[0]>=2:
                raise ValueError('WEEKLY_CANDIDATE_BUDGET_EXHAUSTED')
            db.execute('INSERT INTO lab_candidate_attempts(id,week_fingerprint,ordinal,season,week,spec_hash,mechanism_key,status) VALUES(?,?,?,?,?,?,?,?)',(attempt,spec['week_fingerprint'],spec['candidate_ordinal'],spec['season'],spec['week'],spec_hash,key,'CONSUMED'))
        self.store.append('candidate_spec',attempt,spec,immutable=True)
        self.store.audit('CANDIDATE_ATTEMPT_CONSUMED',{'attempt':attempt,'spec_hash':spec_hash,'week_fingerprint':spec['week_fingerprint'],'max_seconds':spec['max_seconds']})
        return attempt
    def finish(self,attempt,result):
        with self.store.connect() as db:
            row=db.execute('SELECT status FROM lab_candidate_attempts WHERE id=?',(attempt,)).fetchone()
            if not row or row['status']!='CONSUMED':raise ValueError('ATTEMPT_NOT_OPEN')
            db.execute('UPDATE lab_candidate_attempts SET status=?,result=? WHERE id=?',('FINISHED',canonical(result).decode(),attempt))
        self.store.append('experiment_result',attempt,result,immutable=True)
        self.store.audit('CANDIDATE_ATTEMPT_CLOSED',{'attempt':attempt,'decision':result['decision']})


def evaluate_paired(spec,baseline,candidate,outcomes):
    """Pure arithmetic for an already sealed, exact matched forecast population."""
    validate_spec(spec);started=time.monotonic()
    b=score_rows(baseline,outcomes);c=score_rows(candidate,outcomes)
    interval=paired_week_interval(b['rows'],c['rows'],seed=spec['seed'])
    regression=c['cohorts']['all']['mae'] is not None and c['cohorts']['all']['mae']>b['cohorts']['all']['mae']
    high_b=b['cohorts']['high_usage'];high_c=c['cohorts']['high_usage']
    high_regression=high_b['n']>=30 and high_c['mae']>high_b['mae']
    accepted=interval['upper'] is not None and interval['upper']<0 and not regression and not high_regression and high_b['n']>=30
    decision='ACCEPT_RESEARCH_ONLY' if accepted else ('REJECT_REGRESSION' if regression or high_regression else 'INCONCLUSIVE_NO_VERIFIED_IMPROVEMENT')
    elapsed=time.monotonic()-started
    if elapsed>spec['max_seconds']:decision='FAILED_TIME_BUDGET';accepted=False
    return {'decision':decision,'accepted':accepted,'baseline':b['cohorts'],'candidate':c['cohorts'],'paired_interval':interval,'elapsed_seconds':elapsed,'trials':1,'promotion':'DISABLED','genuine_roi':'ROI_NOT_ESTABLISHED','scope':'EXPOSED_RESEARCH_COMPARISON_NOT_BLIND_VALIDATION'}
