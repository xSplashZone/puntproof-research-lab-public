"""Offline GAMD/coverage verification in the existing locked numerical runtime."""
import os,sys,subprocess,json,unittest,socket,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
if '--child' not in sys.argv:
 python=ROOT/'scratch/v3-env/Scripts/python.exe'
 result=subprocess.run([str(python),'-I','-B',str(Path(__file__).resolve()),'--child'],cwd=ROOT,env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'},capture_output=True,text=True)
 print(result.stdout);print(result.stderr,file=sys.stderr);raise SystemExit(result.returncode)
sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'tools/research_lab'));sys.path.insert(0,str(ROOT/'tests/research_lab'))
native_connect=socket.socket.connect
def guarded_connect(sock,address):
 if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1'):raise AssertionError('External network disabled in authored GAMD tests')
 return native_connect(sock,address)
socket.socket.connect=guarded_connect
started=time.monotonic();suite=unittest.TestSuite()
for pattern in ('test_gamd.py','test_coverage_review.py','test_gamd_integration.py','test_gamd_gate.py','test_http.py','test_game_shadow.py'):
 if not (ROOT/'tests/research_lab'/pattern).is_file():raise ValueError('MISSING_TEST:'+pattern)
 suite.addTests(unittest.defaultTestLoader.discover(str(ROOT/'tests/research_lab'),pattern=pattern))
node=Path(r'C:\Users\matth\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe')
subprocess.run([str(node),'--check',str(ROOT/'tools/research_lab/static/app.js')],check=True)
result=unittest.TextTestRunner(verbosity=2).run(suite)
receipt={'classification':'AUTHORED_ENGINEERING_ONLY_NOT_REAL_RESEARCH','tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'passed':result.wasSuccessful(),'provider_calls':0,'real_model_fits':0,'python':sys.version,'installed_versions':{name:__import__('importlib.metadata',fromlist=['version']).version(name) for name in ('numpy','scipy','scikit-learn','joblib')},'elapsed_seconds':time.monotonic()-started}
(ROOT/'scratch/gamd-runtime-v2-20261027/TEST_RECEIPT.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8');print(json.dumps(receipt))
raise SystemExit(not result.wasSuccessful())
