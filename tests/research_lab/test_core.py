import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import zipfile
from core import (Store, accounting, decimal_odds, digest, distribution, line_probabilities,
                  net_units, no_vig, rushing_context, snap_share, validate_identity, validate_observation)
from demo import fixture, outcome, run_demo

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)/"lab"
        self.store=Store(self.root)
    def tearDown(self):
        self.temp.cleanup()

    def test_missing_zero_and_negative_are_distinct(self):
        for state in ("UNKNOWN","NOT_APPLICABLE","DNP","SOURCE_UNAVAILABLE"):
            validate_observation(None,state)
            with self.assertRaises(ValueError): validate_observation(0,state)
        validate_observation(0,"ZERO")
        validate_observation(-4,"OBSERVED")
        with self.assertRaises(ValueError): validate_observation(None,"ZERO")

    def test_identity_qb_and_temporal_fields(self):
        row=fixture();validate_identity(row)
        row["position"]="QB"
        with self.assertRaises(ValueError): validate_identity(row)
        row["position"]="RB";row["gsis"]=""
        with self.assertRaises(ValueError): validate_identity(row)

    def test_correct_snap_denominator(self):
        self.assertEqual(snap_share(20,[40,30,20]),.5)
        self.assertIsNone(snap_share(None,[40]))
        self.assertIsNone(snap_share(0,[0]))
        with self.assertRaises(ValueError):snap_share(60,[40])

    def test_lateral_credit_and_qb_exclusion(self):
        plays=[{"scramble":True,"rush_segments":[{"position":"QB","yards":10,"state":"OBSERVED","credited_carry":True},
                                 {"position":"RB","yards":5,"state":"OBSERVED","credited_carry":False}]},
               {"scramble":True,"rush_segments":[{"position":"QB","yards":20,"state":"OBSERVED"}]},
               {"kneel":True,"rush_segments":[{"position":"QB","yards":-1,"state":"OBSERVED"}]}]
        self.assertEqual(rushing_context(plays),{"carries":0,"yards":5,"state":"OBSERVED"})

    def test_probability_and_integer_push(self):
        self.assertEqual(line_probabilities([0,25,30],[.2,.3,.5],25),{"under":.2,"push":.3,"over":.5})
        self.assertEqual(line_probabilities([0,25,30],[.2,.3,.5],25.5)["push"],0)
        with self.assertRaises(ValueError):distribution([0,1],[.8,.8])
        with self.assertRaises(ValueError):distribution([0,0],[.5,.5])

    def test_odds_conversion_and_vig(self):
        self.assertAlmostEqual(decimal_odds(-110,"american"),1+100/110)
        self.assertEqual(decimal_odds(150,"american"),2.5)
        self.assertEqual(no_vig(1.91,1.91)["over"],.5)
        with self.assertRaises(ValueError):decimal_odds(1)
        with self.assertRaises(ValueError):decimal_odds(99,"american")

    def test_unit_settlement(self):
        self.assertEqual(net_units(2,1.91,"WON"),1.82)
        self.assertEqual(net_units(2,1.91,"LOST"),-2)
        self.assertEqual(net_units(2,1.91,"VOID"),0)
        self.assertEqual(net_units(2,1.91,"PUSH"),0)

    def test_append_revision_and_idempotence(self):
        h=self.store.append("source","s",{"value":None})
        self.assertEqual(h,self.store.append("source","s",{"value":None}))
        self.store.append("source","s",{"value":0})
        self.assertEqual(len(self.store.rows("SELECT * FROM records")),2)
        self.assertEqual(self.store.latest("source","s")["revision"],2)
        self.assertTrue(self.store.diagnostics()["passed"])

    def test_database_immutability(self):
        self.store.append("source","s",{"a":1},immutable=True)
        with self.assertRaises(ValueError):self.store.append("source","s",{"a":2},immutable=True)
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connect() as db:db.execute("DELETE FROM records")

    def test_seal_then_settle_idempotent_and_conflicts(self):
        f=fixture();self.store.seal_forecast(f)
        out=outcome(f,0);a=self.store.settle_forecast(f["id"],out)
        b=self.store.settle_forecast(f["id"],out)
        self.assertEqual(a["record_hash"],b["record_hash"])
        with self.assertRaises(ValueError):self.store.settle_forecast(f["id"],outcome(f,20))
        self.assertEqual(len(self.store.records("outcome")),1)

    def test_future_poisoning(self):
        row=fixture();row["source_observed_at"]="2099-09-01T02:00:00Z"
        with self.assertRaises(ValueError):self.store.seal_forecast(row)
        row=fixture();row["history_week"]=1
        with self.assertRaises(ValueError):self.store.seal_forecast(row)

    def test_nonappearance_not_zero(self):
        f=fixture();self.store.seal_forecast(f)
        out=outcome(f,0);out["status"]="NONAPPEARANCE"
        with self.assertRaises(ValueError):self.store.settle_forecast(f["id"],out)
        out["yards"]=None;out["opportunities"]=None
        self.store.settle_forecast(f["id"],out)

    def test_settlement_requires_seal(self):
        f=fixture()
        with self.assertRaises(ValueError):self.store.settle_forecast(f["id"],outcome(f,0))

    def test_real_forecast_cannot_bypass_authority(self):
        f=fixture();f["classification"]="PROSPECTIVE"
        with self.assertRaises(PermissionError):self.store.seal_forecast(f)
        self.assertEqual(len(self.store.records("forecast")),0)

    def test_p0_acknowledgement_does_not_resolve(self):
        aid=self.store.alert("TEST","P0","Bad source",{"actual":"missing"},"fixture")
        self.store.acknowledge(aid,"Investigating retained source")
        self.assertEqual(self.store.alerts()[0]["status"],"ACKNOWLEDGED")
        with self.assertRaises(ValueError):self.store.resolve(aid)

    def test_no_false_resolution_from_unrelated_diagnostic(self):
        aid=self.store.alert("MISSING","P1","Source missing",{"resolution_check":"unimplemented","backfill_impact_verified":True})
        self.store.diagnostics()
        with self.assertRaises(ValueError):self.store.resolve(aid)

    def test_backup_restore_and_custody(self):
        self.store.append("source","s",{"unknown":None})
        self.store.retain({"source":"fixture"})
        self.store.audit("test",{"authorization":"secret"})
        backup=self.store.backup()
        restored=Store.restore(backup,Path(self.temp.name)/"restored")
        self.assertEqual(restored.latest("source","s")["unknown"],None)
        self.assertTrue(restored.diagnostics()["passed"])
        with self.assertRaises(ValueError):Store.restore(backup,self.root)

    def test_fixpack_redacts(self):
        aid=self.store.alert("SECRET","P1","Fixture",{"token":"do-not-export"})
        path=self.store.fixpack(aid)
        with zipfile.ZipFile(path) as z:
            text=z.read("ALERT.json").decode()
            self.assertNotIn("do-not-export",text)
            self.assertIn("[REDACTED]",text)

    def test_concurrent_idempotent_append(self):
        errors=[]
        def work():
            try:self.store.append("source","id",{"n":1},immutable=True)
            except Exception as e:errors.append(e)
        threads=[threading.Thread(target=work) for _ in range(4)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertFalse(errors)
        self.assertEqual(len(self.store.records("source")),1)

    def test_synthetic_cycle_separate_and_accounting(self):
        receipt=run_demo(Path(self.temp.name)/"demo",self.store)
        self.assertEqual(receipt["cycles"],2)
        self.assertTrue(receipt["unsafe_automatic_action_blocked"])
        self.assertEqual(self.store.records("forecast"),[])
        hypothetical=next(x for x in receipt["monetary_series"] if x["mode"]=="HYPOTHETICAL")
        self.assertAlmostEqual(hypothetical["net_units"],-.09)
        self.assertAlmostEqual(hypothetical["roi"],-.045)
        self.assertEqual(hypothetical["risked_units"],2)
        self.assertEqual(hypothetical["total_wagered"],4)
        self.assertEqual(hypothetical["strike_rate"],.5)
        real=next(x for x in receipt["monetary_series"] if x["mode"]=="REAL")
        self.assertIsNone(real["roi"])

    def test_integrity_detects_corrupt_raw(self):
        obj=self.store.retain(b"one")
        Path(obj["path"]).write_bytes(b"two")
        self.assertFalse(self.store.diagnostics()["passed"])
        self.assertTrue(any(a["severity"]=="P0" for a in self.store.alerts()))

    def test_real_wager_never_inferred_from_synthetic(self):
        row={"id":"fake","mode":"REAL","classification":"SYNTHETIC","placement_status":"PLACED"}
        with self.assertRaises(ValueError):self.store.record_bet(row)


    def test_missing_revision_detected(self):
        self.store.append("source","s",{"value":1})
        self.store.append("source","s",{"value":2})
        with self.store.connect() as db:
            db.execute("DROP TRIGGER records_no_delete")
            db.execute("DELETE FROM records WHERE revision=1")
        self.assertFalse(self.store.diagnostics()["passed"])
