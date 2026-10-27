"""Durable continuous queue; old six/four-attempt journals are never changed."""
from datetime import datetime,timezone,timedelta
from pathlib import Path
import hashlib,json
from core import now,digest,encoded
TERMINAL={'COMPLETED','REJECTED','INCONCLUSIVE','PREFLIGHT_FAILED','FAILED','INTERRUPTED','TIMEOUT','STOPPED'}
def verify_legacy_recovery(root,old_id):
 if old_id!='02e513962ad99b23bb8ef98386312897':raise ValueError('UNREGISTERED_LEGACY_RECOVERY')
 p=Path(root)/'data/private/research-lab/gamd/attempts'/old_id
 if not (p/'CLAIM.json').is_file() or (p/'stdout.log').stat().st_size!=0 or 'GAMD:FIT_PARENT_CLAIM' not in (p/'stderr.log').read_text():raise ValueError('NO_EXACT_PREFIT_FAILURE_PROOF')
 if any((p/n).exists() for n in ('EXECUTION.json','TERMINAL.json','artifacts')):raise ValueError('FITTING_OR_AMBIGUOUS_RECOVERY_STATE')
 return {'old_attempt':old_id,'state':'FAILED_BEFORE_NUMERICAL_FIT_AND_EVALUATION','error_signature':hashlib.sha256((p/'stderr.log').read_bytes()).hexdigest(),'claim_sha256':hashlib.sha256((p/'CLAIM.json').read_bytes()).hexdigest()}
def scientific_key(plan):
 return digest({k:plan[k] for k in ('family','channel','mechanism','feature_schema','data_sha256','folds','seed','calibration','process')})
def numerical_key(root,plan,cache=None):
 """Second dedup key only: exact source byte identity remains on the original plan.

 Retain every field and row order except observation clocks and provenance. Those
 two fields validate custody but are not numerical training inputs. Any identity,
 participation, label, context, control or recipe change remains a distinct key.
 """
 cache={} if cache is None else cache;ref=plan['projection'];key=ref['sha256']
 if key not in cache:
  raw=(Path(root)/ref['path']).read_bytes()
  if hashlib.sha256(raw).hexdigest()!=key:raise ValueError('DEDUP_PROJECTION_HASH')
  h=hashlib.sha256()
  for line in raw.splitlines():
   row=json.loads(line)
   if row.get('season')==2025:raise ValueError('DEDUP_PROTECTED_YEAR')
   h.update(encoded({k:v for k,v in row.items() if k not in ('source_observed_at','provenance')}))
  cache[key]=h.hexdigest()
 return scientific_key(dict(plan,data_sha256=cache[key]))

class Queue:
 def __init__(self,store):
  self.store=store
  with store.connect() as db:db.executescript("CREATE TABLE IF NOT EXISTS gamd_continuous_attempts(id TEXT PRIMARY KEY, scientific_key TEXT NOT NULL UNIQUE,plan TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT,elapsed_seconds REAL,phase TEXT NOT NULL DEFAULT 'PLANNED',result TEXT,error TEXT,recovery_of TEXT,error_signature TEXT);\nCREATE TABLE IF NOT EXISTS gamd_continuous_events(attempt TEXT NOT NULL, sequence INTEGER NOT NULL,at TEXT NOT NULL,state TEXT NOT NULL,body TEXT NOT NULL,PRIMARY KEY(attempt,sequence));\nCREATE TRIGGER IF NOT EXISTS continuous_events_immutable_update BEFORE UPDATE ON gamd_continuous_events BEGIN SELECT RAISE(ABORT,'immutable continuous event'); END;\nCREATE TRIGGER IF NOT EXISTS continuous_events_immutable_delete BEFORE DELETE ON gamd_continuous_events BEGIN SELECT RAISE(ABORT,'immutable continuous event'); END;")
 def event(self,attempt,state,body):
  with self.store.connect() as db:
   db.execute('BEGIN IMMEDIATE');n=db.execute('SELECT coalesce(max(sequence),0)+1 FROM gamd_continuous_events WHERE attempt=?',(attempt,)).fetchone()[0];db.execute('INSERT INTO gamd_continuous_events VALUES(?,?,?,?,?)',(attempt,n,now(),state,encoded(body).decode()))
 def plan(self,plan):
  key=scientific_key(plan);aid=key[:32]
  with self.store.connect() as db:
   db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT id FROM gamd_continuous_attempts WHERE scientific_key=?',(key,)).fetchone()
   if old:return old['id'],False
   cut=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()
   if db.execute('SELECT count(*) FROM gamd_continuous_attempts WHERE created_at>=?',(cut,)).fetchone()[0]>=12:raise ValueError('ROLLING_SPECIFICATION_BUDGET')
   recovery=plan.get('recovery',{})
   if recovery and db.execute('SELECT 1 FROM gamd_continuous_attempts WHERE recovery_of=? AND error_signature=?',(recovery['old_attempt'],recovery['error_signature'])).fetchone():raise ValueError('ENGINEERING_RECOVERY_ALREADY_USED')
   db.execute('INSERT INTO gamd_continuous_attempts(id,scientific_key,plan,status,created_at,recovery_of,error_signature) VALUES(?,?,?,?,?,?,?)',(aid,key,encoded(plan).decode(),'PLANNED',now(),recovery.get('old_attempt'),recovery.get('error_signature')))
  self.event(aid,'PLAN',{'spec_hash':digest(plan),'classification':'EXPOSED_DEVELOPMENT_NOT_BLIND'});return aid,True
 def admit_distinct(self,root,plans):
  # Called only inside the registered retained-source context by the coordinator.
  cache={};seen={};skipped=[]
  for old in self.rows():
   key=numerical_key(root,old['plan'],cache)
   if key in seen and old['status']=='PLANNED':
    reason='NUMERICAL_DUPLICATE_SKIPPED: equivalent to '+seen[key]+'; only custody metadata changed; no fit or evaluation'
    self.finish(old['id'],'PREFLIGHT_FAILED',error=reason,elapsed=0)
    skipped.append({'attempt':old['id'],'equivalent_to':seen[key],'reason':reason})
   else:seen.setdefault(key,old['id'])
  for plan in plans:
   key=numerical_key(root,plan,cache)
   if key in seen:continue
   try:aid,_=self.plan(plan)
   except ValueError as exc:
    if str(exc)=='ROLLING_SPECIFICATION_BUDGET':break
    raise
   seen[key]=aid
  return skipped
 def budget(self,at=None):
  current=at or datetime.now(timezone.utc);cut=(current-timedelta(hours=24)).isoformat();rows=self.store.rows('SELECT started_at,finished_at,elapsed_seconds,status FROM gamd_continuous_attempts WHERE started_at>=?',(cut,));spent=sum(float(r['elapsed_seconds'] or (300 if r['status']=='CLAIMED' else 0)) for r in rows)
  reset=min((datetime.fromisoformat(r['started_at'])+timedelta(hours=24) for r in rows),default=None)
  return {'started_specs_24h':len(rows),'elapsed_seconds_24h':spent,'remaining_specs':max(0,12-len(rows)),'remaining_seconds':max(0,3600-spent),'next_reset':reset.isoformat() if reset else None,'allowed':len(rows)<12 and spent+300<=3600}
 def claim(self,aid):
  with self.store.connect() as db:
   db.execute('BEGIN IMMEDIATE');cut=(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat();rows=db.execute('SELECT status,elapsed_seconds FROM gamd_continuous_attempts WHERE started_at>=?',(cut,)).fetchall()
   if db.execute("SELECT 1 FROM gamd_continuous_attempts WHERE status='CLAIMED'").fetchone():raise ValueError('ONE_TRAINING_WORKER')
   if len(rows)>=12 or sum(float(x['elapsed_seconds'] or 0) for x in rows)+300>3600:raise ValueError('ROLLING_COMPUTE_BUDGET')
   if db.execute("UPDATE gamd_continuous_attempts SET status='CLAIMED',phase='LAUNCH_CLAIMED',started_at=? WHERE id=? AND status='PLANNED'",(now(),aid)).rowcount!=1:raise ValueError('DUPLICATE_CONSUMED_ATTEMPT')
  self.event(aid,'LAUNCH_CLAIMED',{})
 def phase(self,aid,state,body=None):
  if state not in ('PREFLIGHT_PASSED','FIT_STARTED','FIT_COMPLETED','EVALUATION_OPENED'):raise ValueError('INVALID_PHASE')
  self.event(aid,state,body or {})
  with self.store.connect() as db:db.execute("UPDATE gamd_continuous_attempts SET phase=? WHERE id=? AND status='CLAIMED'",(state,aid))
 def finish(self,aid,status,*,result=None,error=None,elapsed=0):
  if status not in TERMINAL:raise ValueError('INVALID_TERMINAL')
  with self.store.connect() as db:
   if db.execute("UPDATE gamd_continuous_attempts SET status=?,finished_at=?,elapsed_seconds=?,result=?,error=? WHERE id=? AND status IN ('PLANNED','CLAIMED')",(status,now(),elapsed,encoded(result).decode() if result else None,error,aid)).rowcount!=1:raise ValueError('ATTEMPT_ALREADY_CLOSED')
  self.event(aid,status,{'result':result,'error':error,'elapsed_seconds':elapsed})
 def rows(self):return [{**r,'plan':json.loads(r['plan']),'result':json.loads(r['result']) if r['result'] else None} for r in self.store.rows('SELECT * FROM gamd_continuous_attempts ORDER BY created_at,id')]
 def recover(self,root):
  for a in self.rows():
   if a['status']!='CLAIMED':continue
   p=Path(root)/'data/private/research-lab/gamd/continuous_v1/attempts'/a['id'];terminal=p/'TERMINAL.json'
   if terminal.is_file():
    value=json.loads(terminal.read_bytes());claim=json.loads((p/'CLAIM.json').read_bytes());mp=Path(root)/claim['manifest']['path']
    if not mp.resolve().is_relative_to(p.resolve()):raise ValueError('RECOVERY_MANIFEST_SCOPE')
    raw=mp.read_bytes();manifest=json.loads(raw)
    if value.get('manifest_sha256')==claim['manifest']['sha256']==hashlib.sha256(raw).hexdigest() and manifest['candidate']==a['plan']:
     for item in value['artifacts']:
      f=Path(item['path']);f=f if f.is_absolute() else Path(root)/f
      if not f.resolve().is_relative_to(p.resolve()) or hashlib.sha256(f.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('RECOVERY_ARTIFACT_HASH')
     self.finish(a['id'],value['status'],result=value,elapsed=value['resources']['wall_seconds']);continue
   # No rerun after an ambiguous interruption. A pre-fit repair requires its own proved linked specification.
   self.finish(a['id'],'INTERRUPTED',error='Preserved interruption. No automatic restart; inspect exact fit/evaluation phase.',elapsed=300)
