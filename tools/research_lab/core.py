"""Local Research Lab persistence and accounting. No network, fitting or provider access."""
from __future__ import annotations
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import statistics
import uuid
import zipfile

UTC = timezone.utc
STATES = {"OBSERVED", "ZERO", "UNKNOWN", "NOT_APPLICABLE", "DNP", "SOURCE_UNAVAILABLE"}
LANES = {"FROZEN_CONTROL", "RESEARCH_CANDIDATE", "SHADOW_CHAMPION", "PUBLIC_PRODUCTION"}

def now():
    return datetime.now(UTC).isoformat(timespec="milliseconds")

def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")

def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()

def parse_time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("UTC or explicit timezone required")
    return parsed.astimezone(UTC)

def finite(value, name="value"):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + " must be finite")
    return float(value)

def redact(value):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if re.search(r"token|secret|password|api.?key|authorization|credential", k, re.I)
                    else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)(Bearer\s+)[\w.\-]+", r"\1[REDACTED]", value)
        value = re.sub(r"https?://[^\s?]+\?[^\s]+", "[URL WITH QUERY REDACTED]", value)
        value = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b", "[REDACTED]", value)
    return value

def validate_identity(row):
    required = ("game_id", "gsis", "team", "opponent", "position", "channel", "season", "week")
    if any(row.get(k) in (None, "") for k in required):
        raise ValueError("Missing canonical identity")
    if not re.fullmatch(r"\d{2}-\d{7}", row["gsis"]):
        raise ValueError("Verified GSIS format required")
    if row["channel"] not in ("receiving", "rushing"):
        raise ValueError("Unsupported channel")
    if row["channel"] == "rushing" and row["position"] not in ("RB", "FB"):
        raise ValueError("RB/FB rushing context only; QB scrambles/kneels excluded")
    if row["channel"] == "receiving" and row["position"] not in ("WR", "TE", "RB", "FB"):
        raise ValueError("Ineligible receiving position")
    if type(row["season"]) is not int or type(row["week"]) is not int or not 1 <= row["week"] <= 22:
        raise ValueError("Invalid season/week")
    return tuple(row[k] for k in required)

def validate_observation(value, state):
    if state not in STATES:
        raise ValueError("Unknown observation state")
    if state in {"UNKNOWN", "NOT_APPLICABLE", "DNP", "SOURCE_UNAVAILABLE"}:
        if value is not None:
            raise ValueError("Missing/structural states require null")
    else:
        finite(value)
        if state == "ZERO" and value != 0:
            raise ValueError("ZERO must be witnessed zero")
        if state == "OBSERVED" and value == 0:
            raise ValueError("Zero must be explicitly identified")

def snap_share(player_snaps, team_individual_snaps):
    if player_snaps is None or not team_individual_snaps or any(x is None for x in team_individual_snaps):
        return None
    values = [finite(x) for x in team_individual_snaps]
    player = finite(player_snaps)
    if min(values) < 0 or player < 0 or player > max(values):
        raise ValueError("Invalid individual snap denominator")
    return player / max(values) if max(values) else None

def rushing_context(plays):
    """Include eligible lateral recipients; never infer initial QB yards as RB yards."""
    carries = yards = 0
    for play in plays:
        if play.get("kneel"):
            continue
        participants = play.get("rush_segments", [])
        for segment in participants:
            if segment.get("position") not in ("RB", "FB"):
                continue
            validate_observation(segment.get("yards"), segment.get("state", "UNKNOWN"))
            if segment.get("yards") is None:
                return {"carries": None, "yards": None, "state": "UNKNOWN"}
            yards += segment["yards"]
            carries += int(bool(segment.get("credited_carry")))
    return {"carries": carries, "yards": yards, "state": "ZERO" if yards == 0 else "OBSERVED"}

def distribution(support, pmf):
    if not support or len(support) != len(pmf) or len(set(support)) != len(support):
        raise ValueError("Support/PMF schema mismatch")
    atoms = sorted((finite(x), finite(p)) for x, p in zip(support, pmf))
    if any(p < 0 or p > 1 for _, p in atoms) or abs(sum(p for _, p in atoms) - 1) > 1e-7:
        raise ValueError("Probability mass must sum to one")
    return atoms

def line_probabilities(support, pmf, line):
    atoms = distribution(support, pmf)
    line = finite(line, "line")
    under = sum(p for x, p in atoms if x < line)
    push = sum(p for x, p in atoms if x == line)
    over = sum(p for x, p in atoms if x > line)
    return {"under": under, "push": push, "over": over}

def decimal_odds(value, kind="decimal"):
    value = finite(value, "price")
    if kind == "american":
        if abs(value) < 100:
            raise ValueError("American odds must be <= -100 or >= 100")
        value = 1 + (value / 100 if value > 0 else 100 / abs(value))
    elif kind != "decimal":
        raise ValueError("Unknown odds format")
    if value <= 1 or value > 10001:
        raise ValueError("Invalid decimal odds")
    return value

def no_vig(over_price, under_price):
    a, b = 1 / decimal_odds(over_price), 1 / decimal_odds(under_price)
    return {"over": a / (a+b), "under": b / (a+b), "overround": a+b-1}

def net_units(stake, odds, status):
    s, o = Decimal(str(finite(stake, "stake"))), Decimal(str(decimal_odds(odds)))
    if s <= 0:
        raise ValueError("Positive stake required")
    if status == "WON":
        return float(s * (o - 1))
    if status == "LOST":
        return float(-s)
    if status in ("PUSH", "VOID"):
        return 0.0
    raise ValueError("Settlement must be WON/LOST/PUSH/VOID")

def accounting(bets):
    """No pooling quoted recommendations with wagers; pushes/voids excluded from risk."""
    result = []
    for mode in ("REAL", "PAPER", "HYPOTHETICAL"):
        rows = [b for b in bets if b.get("mode") == mode]
        placed = [b for b in rows if b.get("placement_status") == "PLACED"]
        settled = [b for b in placed if b.get("settlement") in ("WON", "LOST", "PUSH", "VOID")]
        wins = sum(b["settlement"] == "WON" for b in settled)
        losses = sum(b["settlement"] == "LOST" for b in settled)
        risked = sum(b["stake_units"] for b in settled if b["settlement"] in ("WON", "LOST"))
        net = sum(net_units(b["stake_units"], b["decimal_odds"], b["settlement"]) for b in settled)
        equity = peak = drawdown = 0
        curve = []
        for bet in sorted(settled, key=lambda b: (b.get("settled_at", ""), b["id"])):
            equity += net_units(bet["stake_units"], bet["decimal_odds"], bet["settlement"])
            peak = max(peak, equity)
            drawdown = max(drawdown, peak-equity)
            curve.append({"at": bet.get("settled_at"), "net_units": equity})
        result.append({"mode": mode, "status": ("USER_ATTESTED_ACCOUNTING_UNVERIFIED" if mode == "REAL" else mode + "_ACCOUNTING") if risked else "ROI_NOT_ESTABLISHED",
                       "verified_profitability": False,
                       "quoted": len(rows), "placed": len(placed), "settled": len(settled),
                       "wins": wins, "losses": losses,
                       "pushes": sum(b["settlement"] == "PUSH" for b in settled),
                       "voids": sum(b["settlement"] == "VOID" for b in settled),
                       "risked_units": risked, "total_wagered": sum(b["stake_units"] for b in placed),
                       "net_units": net if settled else None, "roi": net/risked if risked else None,
                       "strike_rate": wins/(wins+losses) if wins+losses else None,
                       "max_drawdown": drawdown if settled else None,
                       "exposure_units": sum(b["stake_units"] for b in placed if b not in settled),
                       "distinct_events": len({b.get("game_id") for b in settled}), "equity": curve})
    return result

class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "lab.sqlite3"
        for name in ("objects", "exports", "backups", "fixpacks", "sessions"):
            (self.root/name).mkdir(exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported database version; preserve database")
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS records(
                  kind TEXT NOT NULL, id TEXT NOT NULL, revision INTEGER NOT NULL,
                  body TEXT NOT NULL, hash TEXT NOT NULL, previous_hash TEXT,
                  recorded_at TEXT NOT NULL, PRIMARY KEY(kind,id,revision), UNIQUE(kind,id,hash));
                CREATE INDEX IF NOT EXISTS record_lookup ON records(kind,id,revision DESC);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS alerts(
                  id TEXT PRIMARY KEY, code TEXT NOT NULL, severity TEXT NOT NULL, title TEXT NOT NULL,
                  entity TEXT NOT NULL, detail TEXT NOT NULL, first_seen TEXT NOT NULL,
                  last_seen TEXT NOT NULL, status TEXT NOT NULL, justification TEXT,
                  resolution TEXT, occurrences INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS audit(
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                  action TEXT NOT NULL, detail TEXT NOT NULL, previous_hash TEXT, hash TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS records_no_update BEFORE UPDATE ON records
                  BEGIN SELECT RAISE(ABORT,'immutable record'); END;
                CREATE TRIGGER IF NOT EXISTS records_no_delete BEFORE DELETE ON records
                  BEGIN SELECT RAISE(ABORT,'immutable record'); END;
                CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
                  BEGIN SELECT RAISE(ABORT,'immutable audit'); END;
                CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
                  BEGIN SELECT RAISE(ABORT,'immutable audit'); END;
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=2000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self, sql, args=()):
        with self.connect() as db:
            return [dict(r) for r in db.execute(sql, args)]

    def append(self, kind, record_id, body, immutable=False):
        if not isinstance(kind, str) or not isinstance(record_id, str) or not record_id or not isinstance(body, dict):
            raise ValueError("Record identity and object required")
        body_raw = encoded(body).decode()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM records WHERE kind=? AND id=? ORDER BY revision DESC LIMIT 1",
                             (kind, record_id)).fetchone()
            if old and old["body"] == body_raw:
                return old["hash"]
            if old and immutable:
                raise ValueError("Immutable record conflict: " + kind + "/" + record_id)
            revision = old["revision"]+1 if old else 1
            previous = old["hash"] if old else None
            stamp = now()
            h = digest([kind, record_id, revision, body, previous, stamp])
            db.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?)",
                       (kind, record_id, revision, body_raw, h, previous, stamp))
        return h

    def records(self, kind):
        rows = self.rows("""SELECT r.* FROM records r JOIN
            (SELECT id,MAX(revision) AS revision FROM records WHERE kind=? GROUP BY id) latest
            ON r.id=latest.id AND r.revision=latest.revision WHERE r.kind=? ORDER BY r.id""", (kind, kind))
        return [dict(json.loads(r["body"]), id=r["id"], revision=r["revision"],
                     record_hash=r["hash"], recorded_at=r["recorded_at"]) for r in rows]

    def latest(self, kind, record_id):
        rows = self.rows("SELECT * FROM records WHERE kind=? AND id=? ORDER BY revision DESC LIMIT 1",
                         (kind, record_id))
        if not rows:
            return None
        r = rows[0]
        return dict(json.loads(r["body"]), id=r["id"], revision=r["revision"],
                    record_hash=r["hash"], recorded_at=r["recorded_at"])

    def get_setting(self, key, default=None):
        rows = self.rows("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, encoded(value).decode()))

    def audit(self, action, details):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT hash FROM audit ORDER BY sequence DESC LIMIT 1").fetchone()
            previous = prior[0] if prior else None
            stamp, clean = now(), redact(details)
            h = digest([stamp, action, clean, previous])
            db.execute("INSERT INTO audit(at,action,detail,previous_hash,hash) VALUES(?,?,?,?,?)",
                       (stamp, action, encoded(clean).decode(), previous, h))
        return h

    def retain(self, raw):
        if not isinstance(raw, bytes):
            raw = encoded(raw)
        sha = hashlib.sha256(raw).hexdigest()
        path = self.root/"objects"/sha
        try:
            with path.open("xb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
        except FileExistsError:
            if path.read_bytes() != raw:
                raise ValueError("Content address collision")
        return {"sha256": sha, "path": str(path), "bytes": len(raw)}

    def alert(self, code, severity, title, detail, entity="system"):
        if severity not in ("P0", "P1", "P2"):
            raise ValueError("Alert severity")
        aid = digest([code, entity])[:24]
        clean = encoded(redact(detail)).decode()
        stamp = now()
        with self.connect() as db:
            db.execute("""INSERT INTO alerts(id,code,severity,title,entity,detail,first_seen,last_seen,status)
              VALUES(?,?,?,?,?,?,?,?,'OPEN') ON CONFLICT(id) DO UPDATE SET
              severity=excluded.severity,title=excluded.title,detail=excluded.detail,
              last_seen=excluded.last_seen,occurrences=alerts.occurrences+1,
              status=CASE WHEN alerts.status='RESOLVED' THEN 'OPEN' ELSE alerts.status END""",
                       (aid, code, severity, title, entity, clean, stamp, stamp))
        return aid

    def alerts(self):
        rows = self.rows("SELECT * FROM alerts ORDER BY CASE status WHEN 'RESOLVED' THEN 1 ELSE 0 END,severity,first_seen")
        return [dict(r, detail=json.loads(r["detail"])) for r in rows]

    def acknowledge(self, alert_id, justification):
        if not isinstance(justification, str) or len(justification.strip()) < 8:
            raise ValueError("A meaningful acknowledgement justification is required")
        with self.connect() as db:
            changed = db.execute("UPDATE alerts SET status='ACKNOWLEDGED',justification=? WHERE id=? AND status!='RESOLVED'",
                                 (justification.strip(), alert_id)).rowcount
            if not changed:
                raise ValueError("Unresolved alert not found")
        self.audit("alert_acknowledged", {"alert_id": alert_id, "justification": justification})

    def blocked_science(self, action):
        self.alert("AUTHORITY_REQUIRED", "P0", "Research execution is not admitted",
                   {"action": action, "expected": "Exact registered operation, source semantics, runtime and finite protocol",
                    "actual": "This generic action has no exact admitted execution manifest", "next_action": "Inspect current source and runner readiness; use only the conditional admitted runtime"},
                   "scientific-runtime")
        raise PermissionError("MODEL_EXECUTION_BLOCKED: generic actions cannot borrow the conditional V3 admission")

    def seal_forecast(self, forecast):
        row = dict(forecast)
        validate_identity(row)
        if row.get("classification") != "SYNTHETIC":
            return self.blocked_science("seal real forecast")
        atoms = distribution(row["support"], row["pmf"])
        if not row.get("model_hash") or not row.get("input_hash"):
            raise ValueError("Forecast requires exact model and input hashes")
        cutoff = parse_time(row["cutoff"])
        issued = parse_time(row["issued_at"])
        kickoff = parse_time(row["kickoff"])
        if not cutoff <= issued < kickoff:
            raise ValueError("Forecast must be sealed before kickoff")
        if parse_time(row["source_observed_at"]) > cutoff or row["history_week"] >= row["week"]:
            raise ValueError("Look-ahead source poisoning")
        if row.get("source_state") not in ("OBSERVED", "ZERO"):
            raise ValueError("Source unavailable")
        if any(a["severity"] == "P0" and a["status"] != "RESOLVED" and a["entity"] == row["id"] for a in self.alerts()):
            raise ValueError("Forecast quarantined by unresolved P0")
        row["mean"] = sum(x*p for x,p in atoms)
        cumulative = 0
        for x,p in atoms:
            cumulative += p
            if cumulative >= .5:
                row["median"] = x
                break
        raw = self.retain(row)
        row["archive_sha256"] = raw["sha256"]
        self.append("forecast", row["id"], row, immutable=True)
        return self.latest("forecast", row["id"])

    def settle_forecast(self, forecast_id, outcome):
        forecast = self.latest("forecast", forecast_id)
        if not forecast:
            raise ValueError("Seal forecast first")
        if forecast["classification"] != "SYNTHETIC":
            return self.blocked_science("settle real forecast")
        row = dict(outcome)
        validate_identity(row)
        for key in ("game_id","gsis","team","channel","season","week"):
            if row[key] != forecast[key]:
                raise ValueError("Settlement identity mismatch")
        if parse_time(row["observed_at"]) <= parse_time(forecast["kickoff"]):
            raise ValueError("Outcome must be observed after kickoff")
        status = row.get("status")
        if status not in ("APPEARED","NONAPPEARANCE","UNKNOWN","VOID"):
            raise ValueError("Explicit appearance status required")
        if status == "APPEARED":
            if type(row.get("yards")) is not int or type(row.get("opportunities")) is not int or row["opportunities"] < 0:
                raise ValueError("Verified signed yards and opportunities required")
        elif row.get("yards") is not None or row.get("opportunities") is not None:
            raise ValueError("Nonappearance/unknown/void is not zero")
        if not row.get("source_sha256") or not row.get("verified"):
            raise ValueError("Authoritative outcome provenance required")
        row["forecast_hash"] = forecast["record_hash"]
        row["forecast_id"] = forecast_id
        row["classification"] = "SYNTHETIC"
        self.append("outcome", forecast_id, row, immutable=True)
        return self.latest("outcome", forecast_id)

    def record_bet(self, bet):
        row = dict(bet)
        if row.get("legs") or row.get("market_type") in ("SGM","PARLAY","MULTI"):
            raise ValueError("Multi-leg markets require a joint-price and house-rule adapter; single-leg probabilities cannot be multiplied")
        if not all(isinstance(row.get(k),str) and row[k].strip() for k in ("id","game_id","channel")):
            raise ValueError("Exact wager identity, game and channel required")
        if row.get("channel") not in ("receiving","rushing"):
            raise ValueError("Unsupported wager channel")
        if row.get("mode") not in ("REAL", "PAPER", "HYPOTHETICAL"):
            raise ValueError("Separate REAL/PAPER/HYPOTHETICAL mode required")
        if row.get("placement_status") not in ("QUOTED", "SELECTED", "PLACED"):
            raise ValueError("Explicit placement status required")
        if row.get("mode") == "REAL" and row.get("classification") == "SYNTHETIC":
            raise ValueError("Synthetic data cannot be real bets")
        if row.get("mode") == "REAL":
            if row.get("user_attested_placed") is not True or row.get("placement_status") != "PLACED":
                raise ValueError("A real wager requires explicit user placement attestation")
            if not row.get("offer_id") or not row.get("bookmaker") or not row.get("offer_raw_sha256") or not row.get("house_rules"):
                raise ValueError("Real wager requires real offer provenance and settlement rules")
        if row.get("mode") == "REAL" and not re.fullmatch(r"[a-f0-9]{64}", row.get("offer_raw_sha256", "")):
            raise ValueError("A valid source hash is required; it remains user-attested until independently verified")
        row["independently_verified"] = False
        decimal_odds(row["decimal_odds"])
        if finite(row["stake_units"]) <= 0:
            raise ValueError("Stake must be positive")
        if row["side"] not in ("OVER", "UNDER"):
            raise ValueError("Explicit OVER/UNDER side required")
        finite(row["line"])
        if not parse_time(row["captured_at"]) <= parse_time(row["selected_at"]) < parse_time(row["kickoff"]):
            raise ValueError("Offer/selection chronology failed")
        if row.get("settlement"):
            raise ValueError("Past settlement cannot be supplied with a new selection")
        self.append("bet", row["id"], row, immutable=True)
        return self.latest("bet", row["id"])

    def settle_bet(self, bet_id, *, status, evidence_sha256, settled_at):
        bet = self.latest("bet", bet_id)
        if not bet or bet["placement_status"] != "PLACED":
            raise ValueError("Only explicitly placed series can settle")
        if not re.fullmatch(r"[a-f0-9]{64}", evidence_sha256):
            raise ValueError("Settlement evidence SHA256 required; independent verification remains unavailable")
        if parse_time(settled_at) <= parse_time(bet["kickoff"]):
            raise ValueError("Settlement before kickoff")
        result = {"bet_id":bet_id,"bet_hash":bet["record_hash"],"settlement":status,
                  "net_units":net_units(bet["stake_units"],bet["decimal_odds"],status),
                  "evidence_sha256":evidence_sha256,"settled_at":settled_at}
        self.append("bet_settlement",bet_id,result,immutable=True)
        self.monitor_betting()
        return result

    def monitor_betting(self):
        summaries=accounting(self.bets())
        for summary in summaries:
            evidence={"mode":summary["mode"],"verification":summary["status"],"independent_events":summary["distinct_events"],"verified_profitability":False}
            if summary["settled"] and summary["distinct_events"]<30:
                self.alert("BETTING_LOW_VOLUME","P2","Betting sample is too small for profitability claims",{**evidence,"next_action":"Accumulate genuinely new events with exact offered prices and verified settlements. Multiple selections in one game remain correlated."},summary["mode"])
            if summary["max_drawdown"] is not None and summary["max_drawdown"]>=5:
                self.alert("BETTING_DRAWDOWN","P1","Recorded series has a drawdown of at least 5 units",{**evidence,"max_drawdown":summary["max_drawdown"],"next_action":"Review reconciled settlements and exposure limits. Preserve the strategy and report losses; no automatic wagering or threshold tuning."},summary["mode"])
            if summary["distinct_events"]>=30 and summary["roi"] is not None and summary["roi"]<-.10:
                self.alert("BETTING_ROI_DETERIORATION","P1","Recorded series ROI is below -10%",{**evidence,"roi":summary["roi"],"next_action":"Review matched offer coverage, calibration and settlement rules before any separately admitted research change. User-entered economics remain unverified."},summary["mode"])
        return summaries

    def bets(self):
        settled = {b["id"]:b for b in self.records("bet_settlement")}
        return [dict(b, **{k:v for k,v in settled.get(b["id"],{}).items()
                           if k in ("settlement","net_units","evidence_sha256","settled_at")}) for b in self.records("bet")]

    def diagnostics(self):
        checks = []
        with self.connect() as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        checks.append({"id":"sqlite_integrity","passed":integrity=="ok","detail":integrity})
        bad = []
        chains = {}
        for r in self.rows("SELECT * FROM records ORDER BY kind,id,revision"):
            expected = digest([r["kind"],r["id"],r["revision"],json.loads(r["body"]),r["previous_hash"],r["recorded_at"]])
            previous_revision, previous_hash = chains.get((r["kind"],r["id"]),(0,None))
            if expected != r["hash"] or r["revision"] != previous_revision+1 or r["previous_hash"] != previous_hash:
                bad.append(r["kind"]+"/"+r["id"])
            chains[(r["kind"],r["id"])]=(r["revision"],r["hash"])
        checks.append({"id":"record_hashes","passed":not bad,"detail":bad})
        previous = None
        audit_ok = True
        for r in self.rows("SELECT * FROM audit ORDER BY sequence"):
            if previous != r["previous_hash"] or digest([r["at"],r["action"],json.loads(r["detail"]),previous]) != r["hash"]:
                audit_ok = False
            previous = r["hash"]
        checks.append({"id":"audit_chain","passed":audit_ok,"detail":"append-only hash chain"})
        object_bad = []
        for p in (self.root/"objects").iterdir():
            if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() != p.name:
                object_bad.append(p.name)
        checks.append({"id":"raw_custody","passed":not object_bad,"detail":object_bad})
        receipt = {"id":uuid.uuid4().hex,"at":now(),"passed":all(c["passed"] for c in checks),"checks":checks,
                   "kind":"ENGINEERING_DIAGNOSTICS","real_source_validation":False}
        self.append("receipt",receipt["id"],receipt,immutable=True)
        self.set_setting("last_diagnostic",receipt)
        for check in checks:
            if not check["passed"]:
                self.alert(check["id"].upper(),"P0","Integrity check failed",check,"storage")
        return receipt

    def resolve(self, alert_id):
        alerts = {a["id"]:a for a in self.alerts()}
        alert = alerts.get(alert_id)
        if not alert:
            raise ValueError("Alert not found")
        receipt = self.get_setting("last_diagnostic")
        required = alert["detail"].get("resolution_check") if isinstance(alert["detail"],dict) else None
        passed = receipt and parse_time(receipt["at"]) >= parse_time(alert["last_seen"]) and any(
            c["id"] == required and c["passed"] for c in receipt["checks"])
        if not required or not passed:
            raise ValueError("No fresh passing diagnostic and verified backfill-impact receipt for this alert")
        if not alert["detail"].get("backfill_impact_verified"):
            raise ValueError("Backfill impact has not been verified")
        with self.connect() as db:
            db.execute("UPDATE alerts SET status='RESOLVED',resolution=? WHERE id=?",
                       (encoded({"diagnostic":receipt["id"],"at":now()}).decode(),alert_id))
        self.audit("alert_resolved",{"id":alert_id,"receipt":receipt["id"]})

    def fixpack(self, alert_id):
        alert = next((a for a in self.alerts() if a["id"] == alert_id),None)
        if not alert:
            raise ValueError("Alert not found")
        path = self.root/"fixpacks"/("fix-"+alert_id+"-"+uuid.uuid4().hex[:8]+".zip")
        task = ("Investigate Research Lab alert "+alert["code"]+".\n"
                "Read repository AGENTS.md and task protocol. Reproduce using the redacted alert and receipt.\n"
                "Expected: "+alert["title"]+"\n"
                "Preserve all frozen research, source bytes and production. No provider calls, model execution,\n"
                "authority transitions, publishing or real wagers are authorized by this pack.\n"
                "Add a focused regression test, explain source/backfill impact, and verify before resolving.\n")
        with zipfile.ZipFile(path,"x",zipfile.ZIP_DEFLATED) as z:
            z.writestr("ALERT.json",encoded(redact(alert)))
            z.writestr("DIAGNOSTIC.json",encoded(redact(self.get_setting("last_diagnostic"))))
            z.writestr("CODEX_TASK.txt",task)
            z.writestr("MANIFEST.json",encoded({"alert_sha256":digest(redact(alert)),"created_at":now(),
                                               "raw_source_included":False,"credentials_included":False}))
        self.audit("fixpack_created",{"alert_id":alert_id,"name":path.name})
        return path

    def backup(self):
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")+uuid.uuid4().hex[:6]
        path = self.root/"backups"/("lab-"+stamp+".zip")
        db_copy = self.root/"backups"/("snapshot-"+stamp+".sqlite3")
        try:
            with closing(sqlite3.connect(self.db)) as src, closing(sqlite3.connect(db_copy)) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Backup integrity failed")
            files = [("lab.sqlite3",db_copy)]
            for folder in ("objects","sessions","exchanges","sources","executions","admission"):
                files.extend((str(p.relative_to(self.root)).replace("\\","/"),p)
                             for p in (self.root/folder).rglob("*") if p.is_file())
            manifest = {name:{"sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"bytes":p.stat().st_size}
                        for name,p in files}
            with zipfile.ZipFile(path,"x",zipfile.ZIP_DEFLATED) as z:
                for name,p in files:
                    z.write(p,name)
                z.writestr("BACKUP_MANIFEST.json",encoded({"schema":1,"created_at":now(),"files":manifest}))
            self.audit("backup_created",{"name":path.name,"files":len(files)})
            return path
        finally:
            db_copy.unlink(missing_ok=True)

    @staticmethod
    def restore(archive, destination):
        destination = Path(destination).resolve()
        if destination.exists():
            raise ValueError("Restore requires a new destination; never overwrite a live database")
        with zipfile.ZipFile(archive) as z:
            manifest = json.loads(z.read("BACKUP_MANIFEST.json"))
            names = z.namelist()
            if len(names) != len(set(names)) or set(names) != set(manifest["files"])|{"BACKUP_MANIFEST.json"}:
                raise ValueError("Backup member mismatch")
            contents = {}
            for name, meta in manifest["files"].items():
                path = (destination/name).resolve()
                if not path.is_relative_to(destination) or name.startswith(("/","\\")) or "\\" in name:
                    raise ValueError("Unsafe backup path")
                raw = z.read(name)
                if len(raw) != meta["bytes"] or hashlib.sha256(raw).hexdigest() != meta["sha256"]:
                    raise ValueError("Corrupt backup")
                contents[name] = raw
            destination.mkdir(parents=True)
            for name,raw in contents.items():
                target = destination/name
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(raw)
        store = Store(destination)
        if not store.diagnostics()["passed"]:
            raise ValueError("Restored integrity failed")
        return store
