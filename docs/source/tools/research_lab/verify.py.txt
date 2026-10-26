"""Declared offline engineering checks. Never loads real model artifacts or provider responses."""
from pathlib import Path
import json
import hashlib
import sys
import time
import unittest
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(HERE))
sys.path.insert(0,str(ROOT/"tests/research_lab"))

if __name__=="__main__":
    started=time.time()
    suite=unittest.defaultTestLoader.discover(str(ROOT/"tests/research_lab"),pattern="test_*.py")
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    receipt={"classification":"OFFLINE_ENGINEERING_FIXTURES","tests":result.testsRun,
             "failures":len(result.failures),"errors":len(result.errors),"skipped":len(result.skipped),
             "passed":result.wasSuccessful(),"elapsed_seconds":round(time.time()-started,3),
             "provider_calls":0,"real_model_execution":False,"real_outcome_access":False,
             "python":sys.version,"executable":sys.executable}
    receipt["code_sha256"]={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for folder in (HERE,ROOT/"tests/research_lab") for p in sorted(folder.rglob("*")) if p.is_file() and p.suffix in (".py",".js",".css",".html",".ps1")}
    out=ROOT/"scratch/research-lab-test-receipt.json"
    out.write_text(json.dumps(receipt,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(receipt,indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)
