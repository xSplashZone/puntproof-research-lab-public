"""Offline authored engineering verification; no real data or model artifacts."""
from pathlib import Path
import sys,subprocess,json,unittest,os,socket,hashlib,ast
ROOT=Path(__file__).resolve().parents[3]
if __name__=='__main__':
 if '--child' not in sys.argv:
  p=subprocess.run([str(ROOT/'scratch/v3-python/cpython-3.13.17-windows-x86_64-none/python.exe'),'-I','-B','-S',str(Path(__file__).resolve()),'--child'],cwd=ROOT,env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','MKL_NUM_THREADS':'1'});raise SystemExit(p.returncode)
 sys.path[:0]=[str(ROOT/p) for p in ('src','tools/research_lab','tests/research_lab','scratch/v3-env/Lib/site-packages')]
 original_connect=socket.socket.connect
 def deny(sock,address,*a,**k):
  if address[0] not in ('127.0.0.1','::1','localhost'):raise AssertionError('No provider network in authored verification')
  return original_connect(sock,address,*a,**k)
 socket.socket.connect=deny
 for code in (ROOT/'tools/research_lab/gamd').glob('continuous_*.py'):ast.parse(code.read_text(encoding='utf8'),filename=str(code))
 suite=unittest.defaultTestLoader.discover(str(ROOT/'tests/research_lab'),pattern='test_*.py')
 class Result(unittest.TextTestResult):
  passed=[]
  def addSuccess(self,test):self.passed.append(test.id());super().addSuccess(test)
 result=unittest.TextTestRunner(verbosity=1,resultclass=Result).run(suite)
 output={'status':'PASS' if result.wasSuccessful() else 'FAIL','tests':result.testsRun,'passed_tests':sorted(result.passed),'provider_calls':0,'real_fits':0,'real_outcomes_opened':0,'classification':'AUTHORED_ENGINEERING_ONLY'}
 p=ROOT/'scratch/gamd-continuation-20261027/CONTINUOUS_TEST_RECEIPT.json';p.write_text(json.dumps(output,sort_keys=True,indent=2)+'\n',encoding='utf8')
 print(json.dumps(output|{'passed_tests_count':len(result.passed),'passed_tests':[]}));raise SystemExit(0 if result.wasSuccessful() else 1)
