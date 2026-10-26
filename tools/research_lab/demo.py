"""Deterministic engineering cycle in a separate synthetic database, never NFL evidence."""
from pathlib import Path
import hashlib
import uuid
from core import Store, digest, accounting, line_probabilities

def fixture(week=1, ident="synthetic-player"):
    return {"id":f"synthetic-w{week}-{ident}","game_id":f"SYNTHETIC_2099_{week:02d}_AAA_BBB",
            "gsis":"00-0000001","season":2099,"week":week,"team":"AAA","opponent":"BBB",
            "position":"RB","channel":"rushing","classification":"SYNTHETIC",
            "support":[-5,0,25,60],"pmf":[.05,.25,.5,.2],
            "model_hash":digest({"model":"authored_test_model","through_week":week-1}),
            "input_hash":digest({"history_week":week-1,"fictional":True}),
            "history_week":week-1,"cutoff":f"2099-09-{week:02d}T00:00:00+00:00",
            "issued_at":f"2099-09-{week:02d}T01:00:00+00:00",
            "kickoff":f"2099-09-{week:02d}T03:00:00+00:00",
            "source_observed_at":f"2099-09-{week:02d}T00:00:00+00:00","source_state":"OBSERVED"}

def outcome(forecast, yards):
    return {k:forecast[k] for k in ("game_id","gsis","season","week","team","opponent","position","channel")} | {
        "status":"APPEARED","yards":yards,"opportunities":2,
        "observed_at":f"2099-09-{forecast['week']:02d}T09:00:00+00:00",
        "source_sha256":digest({"fictional_yards":yards,"week":forecast["week"]}),"verified":True}

def run_demo(root, parent_store=None):
    root=Path(root)
    if root.exists():
        raise ValueError("Demonstration requires a fresh separate directory")
    store=Store(root)
    checkpoints=[]
    counts=[1,1,1,1]
    for week,yards in ((1,0),(2,-5)):
        row=fixture(week)
        row["pmf"]=[x/sum(counts) for x in counts]
        row["model_hash"]=digest(counts)
        store.append("input",row["id"],{"classification":"SYNTHETIC","week":week,
                     "history_week":week-1,"original_values":counts.copy()},immutable=True)
        sealed=store.seal_forecast(row)
        result=store.settle_forecast(row["id"],outcome(row,yards))
        before=counts.copy()
        counts[row["support"].index(yards)]+=1
        checkpoint={"classification":"SYNTHETIC","week":week,"before":before,"after":counts.copy(),
                    "forecast_hash":sealed["record_hash"],"outcome_hash":result["record_hash"],
                    "learning_only_after_seal":True}
        store.append("learning",f"week-{week}",checkpoint,immutable=True)
        checkpoints.append(checkpoint)
    # Two intentionally different authored hypotheses; no real scientific results.
    for ident,mechanism,status in (
        ("history-mixture","Earlier observed exposure changes predictive mass","SYNTHETIC_ACCEPTED"),
        ("tail-collapse","Concentrating all probability at the latest outcome erases tails","SYNTHETIC_REJECTED")):
        store.append("experiment",ident,{"classification":"SYNTHETIC","hypothesis":mechanism,
                     "status":status,"max_trials":1,"seed":17,"data":"fictional-fixtures",
                     "before_hash":digest([1,1,1,1]),"after_hash":digest(counts),
                     "promotion":False,"reason":"Authored acceptance/rejection canary, not an NFL finding"},
                     immutable=True)
    for i,status in enumerate(("WON","LOST","PUSH","VOID")):
        bet={"id":f"synthetic-bet-{i}","mode":"HYPOTHETICAL","classification":"SYNTHETIC",
             "placement_status":"PLACED","stake_units":1,"decimal_odds":1.91,"side":"OVER",
             "line":25 if status=="PUSH" else 25.5,"game_id":"SYNTHETIC","channel":"receiving",
             "captured_at":"2099-09-01T00:00:00Z","selected_at":"2099-09-01T01:00:00Z",
             "kickoff":"2099-09-01T03:00:00Z","offer_id":f"authored-offer-{i}",
             "bookmaker":"AUTHORED_FIXTURE","strategy_version":"fixture-v1"}
        store.record_bet(bet)
        store.settle_bet(bet["id"],status=status,evidence_sha256=digest({"status":status}),
                         settled_at=f"2099-09-01T1{i}:00:00Z")
    gap=store.alert("SYNTHETIC_SOURCE_GAP","P1","Authored source partition missing",
                    {"expected":"Fixture source record","actual":"Missing",
                     "next_action":"Backfill authored source fixture","resolution_check":"raw_custody",
                     "backfill_impact_verified":True},"synthetic-source")
    fix=store.fixpack(gap)
    store.retain({"classification":"SYNTHETIC","backfilled":True,"affected_forecasts":0})
    check=store.diagnostics()
    store.resolve(gap)
    unsafe_blocked=False
    try:
        store.blocked_science("automatic public promotion")
    except PermissionError:
        unsafe_blocked=True
    restored=Store(root)  # fresh connection demonstrates durable session readback
    if len(restored.records("learning"))!=2:
        raise AssertionError("Learning checkpoints did not persist")
    summary=accounting(restored.bets())
    receipt={"id":"synthetic-cycle-"+uuid.uuid4().hex,"classification":"SYNTHETIC_ENGINEERING_ONLY",
             "engine":"AUTHORED_FIXTURE_NOT_ADAPTIVE_V3","database":str(store.db),
             "cycles":2,"checkpoints":checkpoints,"experiments":2,"rejected_experiments":1,
             "forecasts":len(restored.records("forecast")),"settlements":len(restored.records("outcome")),
             "monetary_series":summary,"fixpack":str(fix),"gap_resolved":True,
             "unsafe_automatic_action_blocked":unsafe_blocked,"diagnostic_passed":check["passed"],
             "actual_v3_execution":False,"real_nfl_evidence":False,"source_provider_calls":0}
    store.append("receipt",receipt["id"],receipt,immutable=True)
    if parent_store is not None:
        parent_store.append("receipt",receipt["id"],receipt,immutable=True)
        parent_store.audit("synthetic_cycle_completed",{"receipt":receipt["id"],"cycles":2})
    return receipt
