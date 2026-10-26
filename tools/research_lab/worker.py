"""Durable bounded local worker with exact conditional scientific admission.

Fixed handlers only. Current READY manifests are checked again at execution;
scientific attempts are never automatically retried. Provider calls are disabled.
"""
from __future__ import annotations

import ctypes
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time
import uuid

try:
    from .integrations import sync_metadata, canonical
except ImportError:
    from integrations import sync_metadata, canonical

UTC = dt.timezone.utc
HOUSEKEEPING = {'metadata','diagnostics','backup','proposal'}
SCIENTIFIC = {'forecast','settlement','learning','experiment','refit','backtest','source_capture','promotion'}
ADMITTED = {'admitted_cycle'}
PIPELINE = {'connect'}
TERMINAL = {'SUCCEEDED','FAILED','BLOCKED'}

def stamp():
    return dt.datetime.now(UTC).isoformat(timespec='seconds')

def iso_at(seconds):
    return dt.datetime.fromtimestamp(seconds,UTC).isoformat(timespec='seconds')

class ProcessLock:
    """Lock only the Lab worker, released by the operating system after crashes."""
    def __init__(self, path):
        path = Path(path)
        if path.is_symlink():
            raise RuntimeError('Linked worker lock refused')
        self.stream = path.open('a+b')
        try:
            if path.stat().st_size == 0:
                self.stream.write(b'0'); self.stream.flush()
            self.stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise RuntimeError('A Research Lab worker is already running') from None
    def close(self):
        if not self.stream.closed:
            self.stream.close()

class ResourceSampler:
    def __init__(self):
        self.previous_cpu = None
    def sample(self, root):
        result = {'disk_free_mb':shutil.disk_usage(root).free/1024**2,
                  'available_ram_mb':None, 'process_memory_mb':None,
                  'cpu_percent':None, 'idle_seconds':None}
        if os.name == 'nt':
            from ctypes import wintypes
            class MemoryStatus(ctypes.Structure):
                _fields_ = [('length',wintypes.DWORD),('load',wintypes.DWORD),
                    ('total',ctypes.c_ulonglong),('available',ctypes.c_ulonglong),
                    ('page_total',ctypes.c_ulonglong),('page_available',ctypes.c_ulonglong),
                    ('virtual_total',ctypes.c_ulonglong),('virtual_available',ctypes.c_ulonglong),
                    ('extended_available',ctypes.c_ulonglong)]
            mem = MemoryStatus(); mem.length=ctypes.sizeof(mem)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem)):
                result['available_ram_mb']=mem.available/1024**2
            class LastInput(ctypes.Structure):
                _fields_=[('size',wintypes.UINT),('tick',wintypes.DWORD)]
            idle=LastInput(); idle.size=ctypes.sizeof(idle)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(idle)):
                # Both clocks are unsigned 32-bit milliseconds; account for wrap.
                result['idle_seconds']=((ctypes.windll.kernel32.GetTickCount() & 0xffffffff)-idle.tick & 0xffffffff)/1000
            idle_t=wintypes.FILETIME(); kernel_t=wintypes.FILETIME(); user_t=wintypes.FILETIME()
            if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle_t),ctypes.byref(kernel_t),ctypes.byref(user_t)):
                cv=lambda x:(x.dwHighDateTime<<32)|x.dwLowDateTime
                current=(cv(idle_t),cv(kernel_t)+cv(user_t))
                if self.previous_cpu is not None:
                    d_idle=current[0]-self.previous_cpu[0]; d_total=current[1]-self.previous_cpu[1]
                    if d_total>0: result['cpu_percent']=max(0,min(100,100*(1-d_idle/d_total)))
                self.previous_cpu=current
            class ProcessMemory(ctypes.Structure):
                _fields_=[('cb',wintypes.DWORD),('faults',wintypes.DWORD),
                    ('peak_working',ctypes.c_size_t),('working',ctypes.c_size_t),
                    ('peak_page_pool',ctypes.c_size_t),('page_pool',ctypes.c_size_t),
                    ('peak_nonpage_pool',ctypes.c_size_t),('nonpage_pool',ctypes.c_size_t),
                    ('pagefile',ctypes.c_size_t),('peak_pagefile',ctypes.c_size_t)]
            pm=ProcessMemory(); pm.cb=ctypes.sizeof(pm)
            get_process=ctypes.windll.kernel32.GetCurrentProcess
            get_process.restype=wintypes.HANDLE
            get_memory=ctypes.windll.psapi.GetProcessMemoryInfo
            get_memory.argtypes=[wintypes.HANDLE,ctypes.c_void_p,wintypes.DWORD]
            if get_memory(get_process(),ctypes.byref(pm),pm.cb):
                result['process_memory_mb']=pm.working/1024**2
        else:
            # Linux support for tests/portable local use; unknown resources fail
            # closed when their corresponding limit is enabled.
            try:
                values={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in Path('/proc/meminfo').read_text().splitlines()}
                result['available_ram_mb']=int(values['MemAvailable'].split()[0])/1024
                rss=Path('/proc/self/statm').read_text().split()[1]
                result['process_memory_mb']=int(rss)*os.sysconf('SC_PAGE_SIZE')/1024**2
                result['cpu_percent']=min(100,100*os.getloadavg()[0]/max(1,os.cpu_count() or 1))
            except (OSError,ValueError,KeyError,IndexError):
                pass
        return result

class Worker:
    def __init__(self,store,source_repo):
        self.store=store
        self.source_repo=Path(source_repo)
        self.sampler=ResourceSampler()
        self.thread=None
        self.lock=None
        self.stop_event=threading.Event()
        self.wake_event=threading.Event()
        self.run_lock=threading.Lock()
        try:
            from .v3_runtime import ScientificRuntime
        except ImportError:
            from v3_runtime import ScientificRuntime
        self.runtime=ScientificRuntime(store)
        from operations import Operations
        self.operations=Operations(store,self.source_repo)
        with store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS lab_jobs(
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL, payload TEXT NOT NULL,
                status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                next_at REAL NOT NULL, started_at TEXT, finished_at TEXT,
                elapsed_seconds REAL, result TEXT, error TEXT);
            CREATE INDEX IF NOT EXISTS lab_jobs_due ON lab_jobs(status,next_at);
            ''')
        defaults={'worker_paused':False,'worker_interval_seconds':900,
            'worker_idle_only':True,'worker_idle_seconds':120,'worker_max_cpu_percent':70,
            'worker_min_available_ram_mb':1024,'worker_min_free_disk_mb':512,
            'worker_memory_limit_mb':1024,'worker_max_job_seconds':30,
            'worker_concurrency':1,'worker_max_attempts':3,'worker_retry_seconds':60}
        for key,value in defaults.items():
            if store.get_setting(key,None) is None:
                store.set_setting(key,value)

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return {'status':'ALREADY_RUNNING'}
        self.lock=ProcessLock(Path(self.store.root)/'.worker.lock')
        self.recover()
        self.stop_event.clear(); self.wake_event.clear()
        self.thread=threading.Thread(target=self._loop,name='PuntProofResearchLabWorker',daemon=True)
        self.thread.start()
        return {'status':'STARTED','pid':os.getpid(),'scientific_execution':self.runtime.status()['status']}

    def stop(self,timeout=3):
        self.stop_event.set(); self.wake_event.set()
        if self.thread is not None:
            self.thread.join(timeout)
        if self.thread is not None and self.thread.is_alive():
            return {'status':'STOPPING','detail':'Waiting for bounded active handler to release its lock'}
        if self.lock is not None:
            self.lock.close(); self.lock=None
        self.store.set_setting('worker_status','STOPPED')
        return {'status':'STOPPED'}

    def wake(self):
        self.wake_event.set()
        return {'status':'WAKE_REQUESTED','note':'Resource limits and pause still apply'}

    def recover(self):
        """Only call after own worker process lock is acquired."""
        now=stamp(); limit=int(self.store.get_setting('worker_max_attempts',3))
        with self.store.connect() as db:
            interrupted=db.execute("SELECT id,kind,attempts FROM lab_jobs WHERE status='RUNNING'").fetchall()
            for job in interrupted:
                state='FAILED' if job['kind'] in ADMITTED|PIPELINE or job['attempts']>=limit else 'QUEUED'
                db.execute('UPDATE lab_jobs SET status=?,updated_at=?,next_at=?,error=? WHERE id=?',
                    (state,now,time.time(),('Interrupted scientific attempt; inspect immutable receipts; no automatic retry' if job['kind'] in ADMITTED|PIPELINE else 'Previous process ended before completion; idempotent handler will retry within budget'),job['id']))
        if interrupted:
            self.store.audit('WORKER_RECOVERY',{'interrupted_jobs':len(interrupted),'max_attempts':limit})
        return len(interrupted)

    def enqueue(self,kind,payload=None,evidence_hash=None):
        if kind not in HOUSEKEEPING|SCIENTIFIC|ADMITTED|PIPELINE:
            raise ValueError('Unknown worker job; arbitrary code and commands are refused')
        payload={} if payload is None else payload
        if not isinstance(payload,dict) or payload:
            raise ValueError('Worker handlers accept no arbitrary payload or paths')
        evidence_hash=evidence_hash or uuid.uuid4().hex
        if not isinstance(evidence_hash,str) or len(evidence_hash)>128:
            raise ValueError('Invalid evidence identity')
        fingerprint=hashlib.sha256(canonical({'kind':kind,'evidence':evidence_hash})).hexdigest()
        jid=uuid.uuid4().hex; now=stamp()
        admitted=self.runtime.status(refresh=True) if kind in ADMITTED else {}
        status='BLOCKED' if kind in SCIENTIFIC or kind in ADMITTED and not admitted.get('ready') else 'QUEUED'
        error=admitted.get('reason','Exact source/semantics consumer, READY manifest and finite execution admission are required. No model or response was opened.') if status=='BLOCKED' else None
        with self.store.connect() as db:
            db.execute('INSERT OR IGNORE INTO lab_jobs(id,fingerprint,kind,payload,status,created_at,updated_at,next_at,error) VALUES(?,?,?,?,?,?,?,?,?)',
                       (jid,fingerprint,kind,'{}',status,now,now,time.time(),error))
            row=db.execute('SELECT id,status FROM lab_jobs WHERE fingerprint=?',(fingerprint,)).fetchone()
        if status=='BLOCKED':
            self.store.alert('SCIENTIFIC_EXECUTION_BLOCKED','P1','Research execution needs exact admission',error,entity=kind)
        return dict(row)

    def resource_status(self,force=False):
        sample=self.sampler.sample(self.store.root); reasons=[]
        s=self.store.get_setting
        if s('worker_paused',False): reasons.append('PAUSED_BY_USER')
        if sample['disk_free_mb'] < float(s('worker_min_free_disk_mb',512)):
            reasons.append('DISK_BELOW_RESERVE')
        for field,setting,default,comparison,reason in (
            ('available_ram_mb','worker_min_available_ram_mb',1024,'minimum','AVAILABLE_RAM_BELOW_RESERVE'),
            ('process_memory_mb','worker_memory_limit_mb',1024,'maximum','PROCESS_MEMORY_LIMIT'),
            ('cpu_percent','worker_max_cpu_percent',70,'maximum','CPU_BUSY')):
            value=sample[field]; bound=float(s(setting,default))
            if value is None:
                reasons.append('RESOURCE_UNKNOWN:'+field)
            elif comparison=='minimum' and value<bound or comparison=='maximum' and value>bound:
                reasons.append(reason)
        if s('worker_idle_only',True) and not force:
            if sample['idle_seconds'] is None: reasons.append('IDLE_STATE_UNKNOWN')
            elif sample['idle_seconds']<float(s('worker_idle_seconds',120)): reasons.append('WAITING_FOR_IDLE')
        return {**sample,'allowed':not reasons,'reasons':reasons,'concurrency':1,'network_budget':0,
                'limits_mode':'Bounded housekeeping and exact admitted cycles; no arbitrary compute tasks'}

    def _schedule(self):
        now=time.time(); next_at=float(self.store.get_setting('worker_next_epoch',0))
        if now<next_at: return
        interval=max(60,min(86400,float(self.store.get_setting('worker_interval_seconds',900))))
        slot=str(int(now//interval))
        try:
            self.enqueue('connect',evidence_hash=self.operations.fingerprint())
        except (OSError,ValueError,KeyError):
            pass # The next bounded metadata check retries availability.
        readiness=self.runtime.status()
        if readiness.get('ready') and readiness.get('plan',{}).get('operation')=='PUNTPROOF_RESEARCH_LAB_V3_FORWARD_V1':
            plan=readiness['plan'];self.enqueue('admitted_cycle',evidence_hash=plan['manifest']['sha256']+':'+plan['action'])
        for kind in ('metadata','diagnostics','proposal'):
            self.enqueue(kind,evidence_hash='schedule:'+slot)
        next_at=now+interval
        self.store.set_setting('worker_next_epoch',next_at)
        self.store.set_setting('worker_next_run',iso_at(next_at))

    def _proposal(self):
        sources=self.store.records('source')
        # Rank readiness hypotheses by deterministic data dependencies, never by
        # observed holdout errors or winning outcomes. Existing finite proposal
        # specifications are reused rather than renamed or repeatedly tested.
        concepts={x['id']:x for x in self.store.records('knowledge')}
        priority=('snap_share','zero_signed','measurement_revision','late_news','market_settlement')
        linked=[key for key in priority if key in concepts]
        clean=[{k:v for k,v in x.items() if k not in {'recorded_at','revision','record_hash','checked_at'}} for x in sources]
        signature={'runtime_registered':self.runtime.status().get('registered',False),'readiness_policy':'v3-admission-v1','sources':sorted(clean,key=lambda x:x['id']),
                   'concepts':[{ 'id':key,'record_hash':concepts[key]['record_hash']} for key in linked]}
        digest=hashlib.sha256(canonical(signature)).hexdigest()
        pid='coverage-'+digest[:20]
        existing=self.store.latest('experiment',pid)
        if existing:
            return {'status':'UNCHANGED','experiment_id':pid,'verified_improvement':False}
        proposals=[]
        if linked:
            try:
                from .knowledge import propose
            except ImportError:
                from knowledge import propose
            for key in linked:
                proposals.append(propose(self.store,key)['id'])
        else:
            self.store.alert('KNOWLEDGE_LIBRARY_UNAVAILABLE','P1','Research proposals need the knowledge registry',
                'No seed concepts are available. Scientific execution remains blocked; restore the original versioned knowledge seed.',entity='worker')
        body={'name':'Source completeness before adaptive learning','title':'Knowledge-linked readiness before adaptive learning',
            'hypothesis':'Chronology and participation coverage can be checked before admitting any new model run.',
            'mechanism':'Data readiness only; no scientific attempt or outcome search',
            'status':'BLOCKED','evidence_class':'METADATA_PROPOSAL_NOT_EXPERIMENT',
            'evidence_hash':digest,'source_groups':len(sources),'trial_budget':0,
            'provider_calls':0,'fits':0,'score_count':0,'cutoff':stamp(),
            'reason':'Execution awaits exact measurable source inputs, whole-week evidence, committed runner readiness and a frozen unconsumed finite scientific specification.',
            'decision':'No verified improvement','features':[], 'data_permission':'METADATA_ONLY',
            'knowledge_concept_ids':linked,'knowledge_proposal_ids':proposals,
            'priority_rule':'Participation denominator, zero appearances, revisions, pregame availability, then exact market settlement; no outcome-conditioned ranking'}
        self.store.append('experiment',pid,body,immutable=True)
        return {'status':'PROPOSAL_SAVED','experiment_id':pid,'verified_improvement':False,
                'knowledge_concept_ids':linked,'knowledge_proposal_ids':proposals}

    def _handle(self,kind):
        if kind=='metadata':
            result=sync_metadata(self.store,self.source_repo)
            try:
                from .source_adapter import persist_source_readiness
            except ImportError:
                from source_adapter import persist_source_readiness
            heads=self.store.records('model_head');through=max((h['period'] for h in heads),default=[2026,6])
            persist_source_readiness(self.store,self.source_repo,as_of=stamp(),through_period=through)
            return {k:result[k] for k in ('status','checked_at','revisions_added')}
        if kind=='connect':return self.operations.run_ready()
        if kind=='admitted_cycle':return self.runtime.run_current()
        if kind=='diagnostics': return self.store.diagnostics()
        if kind=='backup': return {'path':str(self.store.backup())}
        if kind=='proposal': return self._proposal()
        raise ValueError('Unadmitted scientific or unknown worker action')

    def run_once(self,force=False):
        if not self.run_lock.acquire(blocking=False): return {'status':'BUSY'}
        try:
            self.store.set_setting('worker_heartbeat',stamp())
            self._schedule()
            manual=bool(self.store.get_setting('worker_manual_run_requested',False))
            resources=self.resource_status(force=force or manual)
            self.store.set_setting('worker_resources',resources)
            if not resources['allowed']:
                since=self.store.get_setting('worker_resource_wait_since') or time.time()
                self.store.set_setting('worker_resource_wait_since',since)
                if time.time()-since>=3600 and any(x!='WAITING_FOR_IDLE' for x in resources['reasons']):
                    self.store.alert('WORKER_RESOURCE_STARVATION','P1','Research work has waited over an hour for resources',{'actual':resources['reasons'],'expected':'Worker resource budget available','next_action':'Free memory or CPU, then use Refresh and continue; limits are unchanged.'},entity='resources')
                self.store.set_setting('worker_status','WAITING_RESOURCES')
                self.store.set_setting('worker_retry_at',iso_at(time.time()+5))
                return {'status':'WAITING_RESOURCES',**resources}
            self.store.set_setting('worker_resource_wait_since',None)
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                job=db.execute("SELECT * FROM lab_jobs WHERE status='QUEUED' AND next_at<=? ORDER BY CASE kind WHEN 'connect' THEN 0 ELSE 1 END,created_at,id LIMIT 1",(time.time(),)).fetchone()
                if job is not None:
                    db.execute("UPDATE lab_jobs SET status='RUNNING',attempts=attempts+1,updated_at=?,started_at=? WHERE id=? AND status='QUEUED'",(stamp(),stamp(),job['id']))
            if job is None:
                self.store.set_setting('worker_status','IDLE')
                return {'status':'IDLE'}
            if manual:self.store.set_setting('worker_manual_run_requested',False)
            self.store.set_setting('worker_retry_at',None)
            self.store.audit('WORKER_JOB_STARTED',{'job_id':job['id'],'kind':job['kind'],'attempt':job['attempts']+1})
            self.store.set_setting('worker_status','RUNNING:'+job['kind'])
            started=time.monotonic()
            try:
                result=self._handle(job['kind'])
                elapsed=time.monotonic()-started
                budget=float(self.store.get_setting('scientific_max_seconds',300))*2+30 if job['kind'] in ADMITTED|PIPELINE else float(self.store.get_setting('worker_max_job_seconds',30))
                if elapsed>budget:
                    # Handler has completed; report budget overrun and pause all
                    # further jobs, rather than pretending a thread was preempted.
                    self.store.set_setting('worker_paused',True)
                    self.store.alert('WORKER_TIME_BUDGET','P1','Worker paused after bounded handler overrun',
                        'Completed handler exceeded configured wall-clock budget; subsequent work is paused.',entity=job['id'])
                    result={**result,'budget_overrun':True}
                with self.store.connect() as db:
                    db.execute("UPDATE lab_jobs SET status='SUCCEEDED',updated_at=?,finished_at=?,elapsed_seconds=?,result=?,error=NULL WHERE id=?",
                        (stamp(),stamp(),elapsed,json.dumps(result,sort_keys=True,allow_nan=False),job['id']))
                self.store.audit('WORKER_JOB_SUCCEEDED',{'job_id':job['id'],'kind':job['kind'],'attempt':job['attempts']+1,'elapsed_seconds':elapsed})
                self.store.set_setting('worker_last_completed',stamp())
                self.store.set_setting('worker_status','IDLE')
                return {'status':'SUCCEEDED','job_id':job['id'],'kind':job['kind'],'result':result}
            except Exception as error:
                elapsed=time.monotonic()-started
                attempts=job['attempts']+1; limit=max(1,min(3,int(self.store.get_setting('worker_max_attempts',3))))
                state='FAILED' if job['kind'] in ADMITTED|PIPELINE or attempts>=limit else 'QUEUED'
                # Exception class is safe; arbitrary provider/credential text is
                # deliberately excluded from the persisted worker failure.
                message=type(error).__name__+': bounded '+job['kind']+' handler failed; inspect its diagnostic receipt'
                delay=max(1,min(3600,int(self.store.get_setting('worker_retry_seconds',60))))
                with self.store.connect() as db:
                    db.execute('UPDATE lab_jobs SET status=?,updated_at=?,next_at=?,elapsed_seconds=?,error=? WHERE id=?',
                        (state,stamp(),time.time()+delay,elapsed,message,job['id']))
                self.store.audit('WORKER_JOB_FAILED',{'job_id':job['id'],'kind':job['kind'],'attempt':attempts,'status':state,'error':message,'elapsed_seconds':elapsed})
                self.store.alert('WORKER_JOB_FAILURE','P1','Worker job needs investigation',message,entity=job['id'])
                self.store.set_setting('worker_status',state)
                return {'status':state,'job_id':job['id'],'attempts':attempts,'error':message}
        finally:
            self.run_lock.release()

    def _loop(self):
        try:
            while not self.stop_event.is_set():
                try:
                    self.run_once()
                except Exception as error:
                    self.store.set_setting('worker_status','ERROR:'+type(error).__name__)
                    self.store.alert('WORKER_LOOP_FAILURE','P1','Worker loop failed safely',type(error).__name__+'; next bounded heartbeat will retry',entity='worker')
                self.wake_event.wait(5)
                self.wake_event.clear()
        finally:
            self.store.set_setting('worker_status','STOPPED')
            if self.lock is not None:
                self.lock.close(); self.lock=None
