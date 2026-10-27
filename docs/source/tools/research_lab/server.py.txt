"""Authenticated loopback-only HTTP application for PuntProof Research Lab."""
from __future__ import annotations
import argparse
import hashlib
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from urllib.parse import urlsplit, unquote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from core import Store, accounting, now, encoded, redact, digest
from knowledge import seed_knowledge, propose
from package_adapter import seed_packages
from integrations import sync_metadata, export_signed, import_signed, local_exchange_key
from worker import Worker

REPO = HERE.parents[1]
DEFAULT_DATA = REPO/"data/private/research-lab"

def coverage_display_record(record, *, include_rows=True):
    """Present every decision without duplicating its complete source provenance.

    The stored record and CSV/JSON exports are unchanged. Exact missing-game
    identities remain searchable here; source chains stay in the full export.
    """
    from gamd.coverage import runtime_summary
    result = runtime_summary(record)
    for key in ('id', 'revision', 'record_hash', 'recorded_at'):
        if key in record:
            result[key] = record[key]
    result['display_only'] = True
    result['full_evidence_in_export'] = True
    if not include_rows:
        return result
    fields = ('season', 'week', 'game_id', 'gsis', 'name', 'team', 'position', 'opponent',
              'channel', 'frozen_decision', 'frozen_reason', 'source_repaired_reason',
              'source_repaired_eligible', 'frozen_eligibility_changed', 'category', 'priority',
              'recovered_unknown_games', 'challenger_cohort', 'challenger_source_supported',
              'challenger_current_model_admission', 'pregame_active_verified', 'offered_prop',
              'current_availability', 'classification')
    counts = ('prior_rows', 'verified_appearances', 'unknown_appearances', 'explicit_dnp',
              'channel_supported_appearances', 'prior_usage_mean', 'prior_usage_cases')
    identity = ('season', 'week', 'game_id', 'team', 'appeared', 'state')
    rows = []
    for original in record.get('rows', []):
        row = {key: original[key] for key in fields if key in original}
        for stage in ('before', 'after'):
            history = original.get(stage, {})
            row[stage] = {key: history[key] for key in counts if key in history}
            for games in ('verified_games', 'unknown_games', 'dnp_games'):
                row[stage][games] = [{key: game.get(key) for key in identity}
                                     for game in history.get(games, [])]
        row['other_team_history'] = [{key: game.get(key) for key in identity}
                                     for game in original.get('other_team_history', [])]
        rows.append(row)
    result['rows'] = rows
    return result


class Application:
    def __init__(self, data_root, source_repo, starter_root, *, start_worker=True):
        self.store = Store(data_root)
        self.source_repo = Path(source_repo).resolve()
        self.starter_root = Path(starter_root).resolve()
        self.cookie_token, self.csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.worker = Worker(self.store, self.source_repo)
        from gamd.runtime import Coordinator
        self.gamd = Coordinator(self.store,self.worker)
        self.stop_callback = None
        seed_knowledge(self.store)
        try:
            seed_packages(self.store, self.starter_root, self.source_repo)
        except (OSError, ValueError, KeyError) as e:
            self.store.alert("PACKAGE_INTEGRITY", "P0", "Supplied package could not be verified",
                             {"actual":str(e),"expected":"Exact starter manifest hashes","next_action":"Restore supplied package without modifying it"},
                             "starter-pack")
        runtime=self.worker.runtime.status()
        self.store.alert("NO_MARKET_OFFERS", "P1", "Genuine betting ROI is not established",
                         {"expected":"Exact historical or future offered line, price, capture time and settlement",
                          "actual":"The supplied comparison has synthetic reference thresholds, not genuine offers",
                          "next_action":"Connect an admitted timestamped offer export. Published picks are not a full bookmaker menu."},
                         "betting")
        self.store.alert("PRODUCTION_IDENTITY", "P2", "Current deployed model identity remains unverified",
                         {"expected":"Authenticated production model/release readback",
                          "actual":"Retained recipe IDs are available; saved Sites version 631 does not prove deployed runtime",
                          "next_action":"Read an explicitly authorized production metadata export"},
                         "production")
        self.store.set_setting("app_started_at", now())
        self.store.set_setting("source_repo", str(self.source_repo))
        self.store.audit("application_started", {"source_repo":str(self.source_repo),"providers_enabled":False})
        if start_worker:
            self.worker.start()
            self.gamd.start()

    def state(self):
        s = self.store
        models, comparisons = s.records("model"), s.records("comparison")
        alerts, sources = s.alerts(), s.records("source")
        settings = {r["key"]:json.loads(r["value"]) for r in s.rows("SELECT key,value FROM settings")
                    if not any(x in r["key"].lower() for x in ("key","token","secret","password"))}
        tables = {r["name"] for r in s.rows("SELECT name FROM sqlite_master WHERE type='table'")}
        jobs = s.rows("SELECT * FROM lab_jobs ORDER BY rowid DESC LIMIT 100") if "lab_jobs" in tables else []
        runtime=self.worker.runtime.status()
        readiness=settings.get("last_source_readiness",{})
        overview = {
            "status":"LOCAL_APP_OPERATIONAL",
            "research_champion":"F — retained research reference; no promotion",
            "production_model":"UNVERIFIED_CURRENT_DEPLOYMENT",
            "challenger":"Adaptive V3 — supplied read-only seed",
            "data_root":str(s.root), "database":str(s.db),
            "source_repository":str(self.source_repo), "timezone":"Australia/Brisbane",
            "scientific_execution":runtime["status"], "integration_message":runtime.get("reason","Exact conditional runtime is ready"),
            "runtime_registered":runtime["registered"],"runtime_checked_at":runtime.get("checked_at"),
            "latest_week":"Seed through 2026 Week "+str(max((h["period"][1] for h in s.records("model_head")),default=6)),
            "provider_access":"DISABLED", "public_publishing":"DISABLED", "automatic_wagering":"DISABLED",
            "genuine_roi":"ROI_NOT_ESTABLISHED",
            "new_improvement":"No verified improvement",
            "warning_count":sum(a["status"]!="RESOLVED" for a in alerts),
            "p0_count":sum(a["severity"]=="P0" and a["status"]!="RESOLVED" for a in alerts),
            "worker_heartbeat":settings.get("worker_heartbeat"), "next_run":settings.get("worker_next_run"),
            "last_diagnostic":settings.get("last_diagnostic",{}).get("at"),
            "retained_comparison":"Exposed revised-history, 2026 Weeks 1–6",
            "disk_bytes":sum(p.stat().st_size for p in s.root.rglob("*") if p.is_file()),
            "last_scientific_run":settings.get("last_scientific_run","No real Lab scientific run; authored engine checks are separate"),
            "app_started_at":settings.get("app_started_at"),
            "source_records":len(sources),
            "last_metadata_sync":settings.get("last_metadata_sync"),
            "engine_installation":"Isolated Python 3.13 runtime installed; see verification receipts" if runtime["installed"] else "V3 runtime environment missing",
            "source_readiness":readiness.get("status","NOT_CHECKED"),"cycle_status":runtime["status"]
        }
        from app_view import owner_state
        from game_shadow import observation_views
        from gamd.forward import status as gamd_forward_status
        from gamd.continuous_settlement import latest_evaluations
        gamd_status = dict(self.gamd.status())
        gamd_status['coverage'] = [coverage_display_record(record) for record in gamd_status.get('coverage', [])]
        if isinstance(settings.get('gamd_coverage_runtime'), dict):
            settings['gamd_coverage_runtime'] = coverage_display_record(settings['gamd_coverage_runtime'], include_rows=False)
        gamd_status["forward"] = gamd_forward_status(REPO,s)
        owner=owner_state(s,runtime,execution_busy=self.worker.run_lock.locked())
        benchmarks=s.records('continuous_benchmark')
        continuous=dict(gamd_status.get('continuous') or {})
        batch=dict(continuous.get('forward') or {})
        # Counts describe all saved immutable versions, including verified recovery.
        # The original batch snapshot and its timeout are retained without mutation.
        continuous['forward']={**batch,'batch_snapshot':batch,
            'forecasts':sum(len(r.get('forecasts',[])) for r in benchmarks),'seals':len(benchmarks),
            'games':len({f['identity'][2] for r in benchmarks for f in r.get('forecasts',[]) if len(f.get('identity',[]))>2}),
            'count_basis':'ALL_SAVED_IMMUTABLE_FORECAST_VERSIONS',**owner['home']['forward_recovery']}
        gamd_status['continuous']=continuous
        return redact({**owner,"overview":overview,"models":models,"leaderboard":comparisons,
                       "gamd":gamd_status, "continuous_benchmarks":benchmarks, "continuous_evaluations":latest_evaluations(s.records("continuous_evaluation")), "continuous_evaluation_history":s.records("continuous_evaluation"), "game_observations":observation_views(s,REPO), "forecasts":s.records("forecast"), "coverage_audits":s.records("coverage_audit"), "candidate_decisions":s.records("candidate_decision"), "automation":s.get_setting("automation_status",{}), "ledger":accounting(s.bets()),
                       "bets":s.bets(),"experiments":s.records("experiment"),"alerts":alerts,
                       "knowledge":s.records("knowledge"),"knowledge_sources":s.records("knowledge_source"),
                       "sources":sources,"integrations":s.records("integration"),"packages":s.records("package"),
                       "jobs":jobs,"settings":settings,"performance":s.records("performance"),"experiment_results":s.records("experiment_result"),"runtime":runtime,"receipts":s.records("receipt")[-50:]})

    def action(self, body):
        action = body.get("action")
        s = self.store
        if action == "gamd_run_ready":
            self.gamd.wake(force_idle=True)
            return {"status":"REQUESTED","message":"Only the next admitted unconsumed GAMD recipe may run; current source and resource gates still apply."}
        if action in ("pause","resume"):
            s.set_setting("worker_paused",action=="pause")
            s.audit(action,{"target":"research_lab_worker"})
            self.worker.wake()
            self.gamd.wake()
            return {"status":"PAUSED" if action=="pause" else "RESUMED"}
        if action == "run_ready":
            # Keep the queued request and its one-shot idle override atomic with
            # run_once. A duplicate or busy request must not affect a later job.
            if not self.worker.run_lock.acquire(blocking=False):
                return {"status":"BUSY","message":"Research work is already running. Wait for the current check to finish."}
            try:
                result=self.worker.enqueue('connect',evidence_hash=self.worker.operations.fingerprint())
                if result['status']=='QUEUED':
                    s.set_setting('worker_manual_run_requested',True)
                    self.worker.wake()
                return result
            finally:
                self.worker.run_lock.release()
        if action == "diagnostics":
            return s.diagnostics()
        if action == "refresh_metadata":
            result = sync_metadata(s,self.source_repo)
            s.set_setting("last_metadata_sync",now())
            return result
        if action == "refresh_readiness":
            from source_adapter import persist_source_readiness
            through=max((h["period"] for h in s.records("model_head")),default=[2026,6])
            report=persist_source_readiness(s,self.source_repo,as_of=now(),through_period=through)
            return {"source":report,"runtime":self.worker.runtime.status(refresh=True)}
        if action == "run_admitted":
            current=self.worker.runtime.status(refresh=True)
            if not current["ready"]:raise PermissionError(current.get("reason","Exact readiness is required"))
            plan=current["plan"]
            result=self.worker.enqueue("admitted_cycle",evidence_hash=plan["manifest"]["sha256"]+":"+plan["action"])
            self.worker.wake();return result
        if action == "backup":
            p = s.backup()
            return {"status":"BACKUP_VERIFIED","download":"/api/download/"+p.name}
        if action == "propose":
            return propose(s, str(body.get("concept_id","")))
        if action == "acknowledge":
            s.acknowledge(str(body.get("alert_id","")),body.get("justification",""))
            return {"status":"ACKNOWLEDGED","unresolved":True}
        if action == "resolve":
            s.resolve(str(body.get("alert_id","")))
            return {"status":"RESOLVED"}
        if action == "fixpack":
            p = s.fixpack(str(body.get("alert_id","")))
            return {"status":"FIX_PACK_READY","download":"/api/download/"+p.name}
        if action == "run_demo":
            from demo import run_demo
            return run_demo(s.root/"validation"/secrets.token_hex(6), parent_store=s)
        if action == "settings":
            aliases = {"interval_seconds":"worker_interval_seconds","idle_seconds":"worker_idle_seconds",
                       "max_cpu_percent":"worker_max_cpu_percent","max_memory_mb":"worker_memory_limit_mb",
                       "min_free_mb":"worker_min_free_disk_mb","paused":"worker_paused",
                       "idle_only":"worker_idle_only","min_available_ram_mb":"worker_min_available_ram_mb"}
            ranges = {"worker_interval_seconds":(60,86400),"worker_idle_seconds":(0,7200),
                      "worker_max_cpu_percent":(5,95),"worker_memory_limit_mb":(128,16384),
                      "worker_min_free_disk_mb":(256,1048576),"worker_min_available_ram_mb":(256,32768),
                      "source_stale_hours":(1,8760)}
            values=body.get("values")
            if not isinstance(values,dict):
                raise ValueError("Settings values object required")
            validated={}
            for raw,value in values.items():
                key=aliases.get(raw,raw)
                if key in ("worker_paused","worker_idle_only"):
                    if type(value) is not bool:
                        raise ValueError("Boolean setting required")
                elif key in ranges:
                    lo,hi=ranges[key]
                    if type(value) not in (int,float) or not lo<=value<=hi:
                        raise ValueError(f"{key} must be {lo}–{hi}")
                else:
                    raise ValueError("Unknown or immutable setting: "+key)
                validated[key]=value
            for key,value in validated.items():
                s.set_setting(key,value)
            s.audit("settings_changed",validated)
            self.worker.wake()
            return {"status":"SAVED","values":validated}
        if action == "export":
            if any(a["severity"]=="P0" and a["status"]!="RESOLVED" and a["entity"]=="storage" for a in s.alerts()):
                raise PermissionError("Export quarantined by unresolved storage integrity failure")
            envelope=export_signed(s,local_exchange_key(s))
            p=s.root/"exports"/("metadata-"+secrets.token_hex(8)+".json")
            p.write_bytes(encoded(envelope))
            return {"download":"/api/download/"+p.name,"status":"SIGNED_METADATA_EXPORT"}
        if action == "import":
            return import_signed(s,body.get("envelope",{}),local_exchange_key(s))
        if action == "record_bet":
            # Explicit user input is accounting only; never place a wager.
            result=s.record_bet(body.get("bet",{}))
            s.audit("user_recorded_wager",{"id":result["id"],"mode":result["mode"]})
            return result
        if action == "settle_bet":
            result=s.settle_bet(str(body.get("bet_id","")),status=body.get("status"),
                                evidence_sha256=body.get("evidence_sha256",""),settled_at=body.get("settled_at",""))
            s.audit("user_recorded_settlement",result)
            return result
        if action in ("run_experiment","promote","forecast","refit"):
            return s.blocked_science(action)
        if action == "stop":
            s.audit("application_stopped",{"by":"local_user"})
            if self.stop_callback:
                threading.Thread(target=self.stop_callback,daemon=True).start()
            return {"status":"STOPPING"}
        raise ValueError("Unsupported action")

class LabServer(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True
    def __init__(self,address,application):
        if address[0] != "127.0.0.1":
            raise ValueError("Research Lab only binds to 127.0.0.1")
        self.application=application
        super().__init__(address,Handler)
        application.stop_callback=self.shutdown
        self.last_observation_check=0.

    def service_actions(self):
        # Output reporting only; the existing single worker owns all source/model work.
        if time.monotonic()-self.last_observation_check<60:return
        self.last_observation_check=time.monotonic()
        try:
            from game_shadow import refresh_evaluations
            refresh_evaluations(self.application.store,REPO)
        except (OSError,ValueError,KeyError,TypeError) as error:
            self.application.store.alert('GAME_OBSERVATION_REPORT','P1','Game observation report needs investigation',{'reason':type(error).__name__,'next_action':'Inspect the sealed game manifest and completed whole-week score custody; do not rerun forecasts.'},'game-observation')

class Handler(BaseHTTPRequestHandler):
    server_version="PuntProofResearchLab/1"
    def log_message(self,format,*args):
        pass

    def send(self,status,body,content_type="application/json",headers=None):
        if not isinstance(body,bytes):
            body=encoded(body)
        self.send_response(status)
        self.send_header("Content-Type",content_type)
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("X-Frame-Options","DENY")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key,value in (headers or {}).items():
            self.send_header(key,value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError,ConnectionResetError):
            pass

    def valid_host(self):
        return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

    def authenticated(self):
        cookie=cookies.SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie",""))
            token=cookie.get("lab_session")
            return token is not None and secrets.compare_digest(token.value,self.server.application.cookie_token)
        except cookies.CookieError:
            return False

    def do_GET(self):
        if not self.valid_host():
            return self.send(403,{"error":"Invalid loopback Host"})
        path=urlsplit(self.path).path
        app=self.server.application
        if path=="/health":
            return self.send(200,{"application":"PuntProof Research Lab","status":"RUNNING","version":1,
                                  "scientific_execution":app.worker.runtime.status()["status"],"data_root":str(app.store.root)})
        if path=="/":
            return self.send(200,(HERE/"static/index.html").read_bytes(),"text/html; charset=utf-8",
                             {"Set-Cookie":"lab_session="+app.cookie_token+"; HttpOnly; SameSite=Strict; Path=/"})
        if path in ("/app.js","/style.css"):
            return self.send(200,(HERE/"static"/path[1:]).read_bytes(),
                             "text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8")
        if not self.authenticated():
            return self.send(401,{"error":"Open Research Lab homepage to authenticate this local session"})
        if path=="/api/session":
            return self.send(200,{"csrf":app.csrf})
        if path=="/api/state":
            return self.send(200,app.state())
        if path.startswith("/api/download/"):
            filename=unquote(path.removeprefix("/api/download/"))
            if not filename or Path(filename).name != filename or "/" in filename or "\\" in filename:
                return self.send(400,{"error":"Invalid filename"})
            for folder in ("backups","fixpacks","exports"):
                target=app.store.root/folder/filename
                if target.is_file() and not target.is_symlink():
                    return self.send(200,target.read_bytes(),mimetypes.guess_type(filename)[0] or "application/octet-stream",
                                     {"Content-Disposition":'attachment; filename="'+filename+'"'})
            return self.send(404,{"error":"Export not found"})
        return self.send(404,{"error":"Not found"})

    def reject_post(self, status, error):
        """Drain only a bounded framed body before closing a rejected POST.

        Closing a socket with unread request bytes can reset it on Windows and
        discard the intended denial response. Discarding bytes is not JSON
        parsing or action dispatch; all authentication checks remain closed.
        Oversized, ambiguous or stalled bodies are never drained indefinitely.
        """
        self.close_connection = True
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) == 1 and not self.headers.get("Transfer-Encoding"):
            original_timeout = self.connection.gettimeout()
            try:
                length = int(lengths[0])
                if 0 < length <= 1024 * 1024:
                    self.connection.settimeout(2.0)
                    self.rfile.read(length)
            except (ValueError, OSError):
                pass
            finally:
                self.connection.settimeout(original_timeout)
        return self.send(status, {"error": error}, headers={"Connection": "close"})

    def do_POST(self):
        app=self.server.application
        if not self.valid_host() or not self.authenticated():
            return self.reject_post(403,"Local authenticated session required")
        origin=self.headers.get("Origin")
        valid=(f"http://127.0.0.1:{self.server.server_port}",f"http://localhost:{self.server.server_port}")
        if origin not in valid or not secrets.compare_digest(self.headers.get("X-CSRF-Token",""),app.csrf):
            return self.reject_post(403,"Same-origin CSRF check failed")
        if self.path!="/api/action":
            return self.reject_post(404,"Not found")
        if self.headers.get("Content-Type","").split(";")[0]!="application/json":
            return self.reject_post(415,"JSON required")
        try:
            length=int(self.headers.get("Content-Length","0"))
            if not 0<length<=1024*1024:
                return self.send(413,{"error":"Request size limit"})
            body=json.loads(self.rfile.read(length),parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))
            if not isinstance(body,dict):
                raise ValueError("JSON object required")
            return self.send(200,app.action(body))
        except PermissionError as e:
            return self.send(403,{"error":str(e)})
        except (ValueError,KeyError,TypeError,OSError) as e:
            return self.send(400,{"error":redact(str(e))})
        except Exception as e:
            app.store.alert("APPLICATION_ERROR","P0","Application action failed",
                            {"exception_type":type(e).__name__,"action":body.get("action") if isinstance(body,dict) else "unknown",
                             "next_action":"Create a fix pack and inspect the local engineering tests"},"application")
            return self.send(500,{"error":"Action failed; see application warning"})

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--data-root",type=Path,default=DEFAULT_DATA)
    p.add_argument("--source-repo",type=Path,default=Path(r"C:\Dev\nfl-model"))
    p.add_argument("--starter-root",type=Path,default=REPO/"scratch/research-lab-starter")
    p.add_argument("--port",type=int,default=8787)
    args=p.parse_args()
    app=Application(args.data_root,args.source_repo,args.starter_root)
    server=LabServer(("127.0.0.1",args.port),app)
    app.store.set_setting("server_pid",os.getpid())
    app.store.set_setting("server_url",f"http://127.0.0.1:{args.port}")
    try:
        server.serve_forever(poll_interval=.5)
    finally:
        app.gamd.stop()
        app.worker.stop()
        server.server_close()

if __name__=="__main__":
    main()
