"""Explicit one-use continuation of the committed forecast-persistence repair.

This route cannot release a period lease, select new inputs or retry itself.
Ordinary worker operation never calls it.
"""
import json
from contextlib import closing
from core import now
from operations import Operations
from v3_runtime import gate
from worker import ProcessLock

def recovery_plan(operations):
    consumer,_=gate.consumer_for(operations.root,gate.REPLAY)
    contract_ref=consumer.get('engineering_recovery')
    if not contract_ref:raise ValueError('NO_EXACT_FORECAST_RECOVERY_REGISTERED')
    contract=gate.value(operations.root,contract_ref,committed=True)
    checks=gate.value(operations.root,operations._checks(),committed=True)
    verification=gate.value(operations.root,checks['recovery_verification'],committed=True)
    if (verification.get('status')!='PASS' or verification.get('classification')!='OFFLINE_ENGINEERING_FIXTURES'
        or verification.get('provider_calls')!=0 or verification.get('real_model_execution') is not False
        or verification.get('real_outcome_access') is not False):
        raise ValueError('COMMITTED_RECOVERY_PARITY_CHECK_REQUIRED')
    original=gate.value(operations.root,contract['refs']['original_manifest'])
    # Preserve every scientific field and its original clocks exactly.
    generated={'schema_version','purpose','operation','consumer','source_policy','season','week',
               'execution_directory','pretask_satisfiability','input_files'}
    extras={key:value for key,value in original.items() if key not in generated}
    extras['engineering_recovery']=contract_ref
    return operations._plan('FORECAST',original['operation'],original['week'],
        {'population':'Exact preserved failed forecast population; no new trial',
         'records':contract['original_archive_rows'],'recovery_contract':contract_ref},
        inputs=original['input_files'],extra=extras,identity={'recovery_contract':contract_ref})

def run_recovery(store,source_repo,root=None):
    operations=Operations(store,source_repo,root)
    with closing(ProcessLock(store.root/'.operations.lock')):
        mr,rr=recovery_plan(operations)
        result=operations.runtime.recover_forecast(mr,rr)
        store.set_setting('active_forecast_plan',{'operation':gate.REPLAY,'manifest':mr,'readiness':rr,
                            'action':'settle','period':result['period']})
        store.set_setting('last_scientific_run',dict(result,at=now()))
        # Resolve only exact incidents whose failed persistence condition was
        # rechecked by this successful, immutable same-attempt recovery.
        manifest=gate.value(operations.root,mr)
        contract=gate.value(operations.root,manifest['engineering_recovery'],committed=True)
        receipt=gate.value(operations.root,result['recovery_receipt'])
        if receipt['status']!='RECOVERED_AND_SEALED':raise ValueError('RECOVERY_RECEIPT_REQUIRED')
        for item in receipt['bridge_receipts'].values():gate.checked(operations.root,item)
        resolution=json.dumps({'at':now(),'recovery':result['recovery_receipt'],
            'original_failure_preserved':True,'backfill_impact':'Same original population and forecast bytes; no fit, scoring or outcome access.'})
        incident_manifests=[contract['refs']['original_manifest']['sha256']]
        prior=contract.get('predecessor_recovery',{}).get('manifest')
        if prior:
            gate.checked(operations.root,prior)
            incident_manifests.append(prior['sha256'])
        with store.connect() as db:
            placeholders=','.join('?' for _ in incident_manifests)
            candidates=db.execute("SELECT id,code,detail FROM alerts WHERE status!='RESOLVED' AND ((code='V3_EXECUTION_QUARANTINED' AND entity IN ("+placeholders+")) OR (code='MODEL_AUTHORITY' AND entity='scientific-runtime'))",
                            incident_manifests).fetchall()
            legacy_missing_plan='No exact execution manifest and READY certificate. Week7 completed stats/PBP/snaps and an authenticated full fixture census are not established.'
            rows=[r for r in candidates if r['code']=='V3_EXECUTION_QUARANTINED'
                  or json.loads(r['detail']).get('actual')==legacy_missing_plan]
            for row in rows:db.execute("UPDATE alerts SET status='RESOLVED',resolution=? WHERE id=?",(resolution,row['id']))
        if rows:store.audit('forecast_recovery_incidents_resolved',{'alerts':[r['id'] for r in rows],'receipt':result['recovery_receipt']})
        return result
