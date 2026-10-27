"""Append-only scientific attempt journal, separate from frozen V3 comparisons."""
import json
from pathlib import Path
from core import now,digest,encoded

FAMILIES=('hurdle_glm','hist_gradient_boosting','extra_trees')
CHANNELS=('receiving','rushing')
TERMINAL={'COMPLETED','FAILED','INTERRUPTED','TIMEOUT','REJECTED','INCONCLUSIVE'}

def write_once(path,body):
 import os
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);raw=encoded(body)
 if p.exists():
  if p.read_bytes()!=raw:raise ValueError('IMMUTABLE_GAMD_CONFLICT')
  return p
 with p.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
 return p

class Registry:
 def __init__(self,store):
  self.store=store
  with store.connect() as db:
   db.executescript('''CREATE TABLE IF NOT EXISTS gamd_attempts(
    id TEXT PRIMARY KEY, scientific_key TEXT NOT NULL UNIQUE, plan TEXT NOT NULL,
    status TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
    result TEXT, error TEXT, elapsed_seconds REAL);
    CREATE TABLE IF NOT EXISTS gamd_events(attempt TEXT NOT NULL, sequence INTEGER NOT NULL,
    at TEXT NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(attempt,sequence));
    CREATE TRIGGER IF NOT EXISTS gamd_events_no_update BEFORE UPDATE ON gamd_events BEGIN SELECT RAISE(ABORT,'immutable GAMD event'); END;
    CREATE TRIGGER IF NOT EXISTS gamd_events_no_delete BEFORE DELETE ON gamd_events BEGIN SELECT RAISE(ABORT,'immutable GAMD event'); END;''')
 def plan(self,spec):
  if spec['family'] not in FAMILIES or spec['channel'] not in CHANNELS:raise ValueError('UNREGISTERED_RECIPE')
  identity={k:spec[k] for k in ('mechanism','population','endpoint','data_sha256','family','channel','recipe_version','folds','seed')}
  key=digest(identity);attempt=key[:32]
  with self.store.connect() as db:
   db.execute('BEGIN IMMEDIATE')
   prior=db.execute('SELECT id FROM gamd_attempts WHERE scientific_key=?',(key,)).fetchone()
   if prior:return prior['id']
   if db.execute('SELECT count(*) FROM gamd_attempts WHERE json_extract(plan,\'$.campaign\')=?',(spec['campaign'],)).fetchone()[0]>=6:raise ValueError('CAMPAIGN_BUDGET_CONSUMED')
   db.execute('INSERT INTO gamd_attempts(id,scientific_key,plan,status,created_at) VALUES(?,?,?,?,?)',(attempt,key,encoded(spec).decode(),'PLANNED',now()))
  self.event(attempt,'PLAN',{'spec_hash':digest(spec)});self.store.append('gamd_plan',attempt,spec,immutable=True)
  return attempt
 def event(self,attempt,state,body):
  with self.store.connect() as db:
   db.execute('BEGIN IMMEDIATE');n=db.execute('SELECT coalesce(max(sequence),0)+1 FROM gamd_events WHERE attempt=?',(attempt,)).fetchone()[0]
   db.execute('INSERT INTO gamd_events VALUES(?,?,?,?,?)',(attempt,n,now(),state,encoded(body).decode()))
 def claim(self,attempt):
  with self.store.connect() as db:
   db.execute('BEGIN IMMEDIATE')
   if db.execute("SELECT 1 FROM gamd_attempts WHERE status='CLAIMED'").fetchone():raise ValueError('FIT_CONCURRENCY_ONE')
   cur=db.execute("UPDATE gamd_attempts SET status='CLAIMED',started_at=? WHERE id=? AND status='PLANNED'",(now(),attempt))
   if cur.rowcount!=1:raise ValueError('ATTEMPT_ALREADY_CONSUMED')
  self.event(attempt,'CLAIMED',{});self.store.audit('GAMD_ATTEMPT_CONSUMED',{'attempt':attempt})
 def finish(self,attempt,status,result=None,error=None,elapsed=None):
  if status not in TERMINAL:raise ValueError('INVALID_TERMINAL')
  with self.store.connect() as db:
   cur=db.execute("UPDATE gamd_attempts SET status=?,finished_at=?,result=?,error=?,elapsed_seconds=? WHERE id=? AND status='CLAIMED'",(status,now(),encoded(result).decode() if result is not None else None,error,elapsed,attempt))
   if cur.rowcount!=1:raise ValueError('ATTEMPT_NOT_OPEN')
  self.event(attempt,status,{'result':result,'error':error});self.store.append('gamd_result',attempt,{'status':status,'result':result,'error':error,'elapsed_seconds':elapsed},immutable=True)
 def recover(self):
  for r in self.store.rows("SELECT id FROM gamd_attempts WHERE status='CLAIMED'"):
   self.finish(r['id'],'INTERRUPTED',error='Process ended after scientific claim. Artifacts preserved; automatic retry forbidden.')
 def attempts(self):
  return [{**r,'plan':json.loads(r['plan']),'result':json.loads(r['result']) if r['result'] else None} for r in self.store.rows('SELECT * FROM gamd_attempts ORDER BY created_at,id')]
 def summary(self):
  rows=self.attempts();completed=[r for r in rows if r['result'] and r['status'] in ('COMPLETED','REJECTED','INCONCLUSIVE')]
  return {'attempts':rows,'planned':sum(r['status']=='PLANNED' for r in rows),'consumed':sum(r['status']!='PLANNED' for r in rows),
   'completed':len(completed),'genuine_fits':sum(r['result'].get('fitted_models',0) for r in completed),
   'fitted_artifacts':sum(r['result'].get('completed_folds',0) for r in completed),
   'failed':sum(r['status'] in ('FAILED','INTERRUPTED','TIMEOUT') for r in rows),'rejected':sum(r['status']=='REJECTED' for r in rows),
   'classification':'REVISED_HISTORY_DEVELOPMENT_NOT_BLIND','promotion':'DISABLED','genuine_roi':None}
