"""Offline verification in the already accepted isolated numerical runtime."""
from pathlib import Path
import sys,os,subprocess,json,time,unittest,socket,hashlib
ROOT=Path(__file__).resolve().parents[3]
if '--child' not in sys.argv:
 command=[str(ROOT/'scratch/v3-env/Scripts/python.exe'),'-I','-B',str(Path(__file__).resolve()),'--child']
 result=subprocess.run(command,cwd=ROOT,env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'},capture_output=True,text=True)
 print(result.stdout);print(result.stderr,file=sys.stderr);raise SystemExit(result.returncode)
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tools/research_lab'),str(ROOT/'tests/research_lab')]
connect=socket.socket.connect
def local_only(sock,address):
 if not isinstance(address,tuple) or address[0] not in ('127.0.0.1','::1'):raise AssertionError('No external network in authored checks')
 return connect(sock,address)
socket.socket.connect=local_only
suite=unittest.TestSuite();patterns=('test_licensed_bdl.py','test_gamd.py','test_coverage_review.py','test_gamd_integration.py','test_gamd_gate.py','test_http.py','test_game_shadow.py','test_runtime_contract.py','test_runtime.py')
for p in patterns:suite.addTests(unittest.defaultTestLoader.discover(str(ROOT/'tests/research_lab'),pattern=p))
start=time.monotonic();result=unittest.TextTestRunner(verbosity=2).run(suite)
subprocess.run([r'C:\Users\matth\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe','--check',str(ROOT/'tools/research_lab/static/app.js')],check=True)
receipt={'status':'PASS' if result.wasSuccessful() else 'FAIL','tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'real_fits':0,'provider_calls':0,'classification':'AUTHORED_ENGINEERING_ONLY','seconds':time.monotonic()-start,'python':sys.version,'versions':{n:__import__('importlib.metadata',fromlist=['version']).version(n) for n in ('numpy','scipy','scikit-learn','joblib')}}
(ROOT/'scratch/licensed-bdl-20261027/TEST_RECEIPT.json').write_text(json.dumps(receipt,indent=2));print(json.dumps(receipt));raise SystemExit(not result.wasSuccessful())
