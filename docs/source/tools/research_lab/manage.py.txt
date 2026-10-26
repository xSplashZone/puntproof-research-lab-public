"""Local administration and exact conditional runtime; never accesses a provider."""
import argparse
import json
from pathlib import Path
import sys
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from core import Store
from demo import run_demo
from integrations import sync_metadata

def main():
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=("status","diagnostics","backup","restore","demo","sync-metadata","scientific-status","refresh-readiness","run-admitted","run-ready","owner-status","recover-forecast"))
    p.add_argument("--data-root",type=Path,default=HERE.parents[1]/"data/private/research-lab")
    p.add_argument("--source-repo",type=Path,default=Path(r"C:\Dev\nfl-model"))
    p.add_argument("--archive",type=Path)
    p.add_argument("--destination",type=Path)
    args=p.parse_args()
    if args.action=="restore":
        if not args.archive or not args.destination:
            p.error("restore requires --archive and a new --destination")
        s=Store.restore(args.archive,args.destination)
        result={"status":"RESTORED_TO_NEW_DIRECTORY","database":str(s.db)}
    elif args.action=="demo":
        if not args.destination:
            p.error("demo requires a new --destination")
        result=run_demo(args.destination)
    else:
        if not (args.data_root/"lab.sqlite3").is_file():
            p.error("Existing Lab database not found; start the application first")
        s=Store(args.data_root)
        if args.action=="status":
            result={"database":str(s.db),"models":len(s.records("model")),
                    "comparisons":len(s.records("comparison")),"knowledge":len(s.records("knowledge")),
                    "sources":len(s.records("source")),"forecasts":len(s.records("forecast")),
                    "worker_heartbeat":s.get_setting("worker_heartbeat"),
                    "next_run":s.get_setting("worker_next_run"),"worker_status":s.get_setting("worker_status"),
                    "scientific_execution":__import__("v3_runtime").ScientificRuntime(s).status(),"actual_bets_placed_by_app":0}
        elif args.action=="recover-forecast":
            from recovery import run_recovery
            result=run_recovery(s,args.source_repo)
        elif args.action=="run-ready":
            from operations import Operations
            result=Operations(s,args.source_repo).run_ready()
        elif args.action=="owner-status":
            from app_view import owner_state
            from v3_runtime import ScientificRuntime
            result=owner_state(s,ScientificRuntime(s).status())
        elif args.action=="scientific-status":
            from v3_runtime import ScientificRuntime
            result=ScientificRuntime(s).status(refresh=True)
        elif args.action=="refresh-readiness":
            from source_adapter import persist_source_readiness
            from core import now
            through=max((h["period"] for h in s.records("model_head")),default=[2026,6])
            result=persist_source_readiness(s,args.source_repo,as_of=now(),through_period=through)
        elif args.action=="run-admitted":
            from worker import Worker
            worker=Worker(s,args.source_repo)
            status=worker.runtime.status(refresh=True)
            if not status['ready']:
                result={'status':'BLOCKED','reason':status.get('reason')}
            else:
                plan=status['plan']
                result=worker.enqueue('admitted_cycle',evidence_hash=plan['manifest']['sha256']+':'+plan['action'])
        elif args.action=="diagnostics":
            result=s.diagnostics()
        elif args.action=="backup":
            result={"archive":str(s.backup())}
        else:
            result=sync_metadata(s,args.source_repo)
    print(json.dumps(result,indent=2,allow_nan=False))

if __name__=="__main__":
    main()
