"""Bounded fit process; Windows job memory cap and kill-on-close custody."""
import ctypes
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path

RUNTIME_PATH='research/gamd_v1/runtime_v2/RUNTIME.json'

def validate_runtime(root,value):
 """Check installed bytes only; never install, import packages or run .pth files."""
 root=Path(root).resolve();site=root/'scratch/v3-env/Lib/site-packages'
 expected=root/'scratch/v3-python/cpython-3.13.17-windows-x86_64-none/python.exe'
 if (value.get('schema_version')!='puntproof-gamd-runtime-v2' or value.get('python_version')!='3.13.17'
     or Path(value['base_executable']['path']).resolve()!=expected or Path(value['site_packages']).resolve()!=site
     or value.get('site_pth_execution') is not False or value.get('global_site_packages') is not False or value.get('new_installs')!=0):
  raise ValueError('GAMD_RUNTIME_SCOPE')
 def check(item,absolute=False):
  p=Path(item['path']) if absolute else root/item['path']
  if not p.resolve().is_relative_to(root) or p.is_symlink() or getattr(p,'is_junction',lambda:False)():raise ValueError('GAMD_RUNTIME_PATH')
  if hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('GAMD_RUNTIME_HASH:'+str(p))
  return p
 check(value['base_executable'],True)
 if value['venv_config']['path']!='scratch/v3-env/pyvenv.cfg' or value['lock']['path']!='tools/research_lab/runtime/requirements-v3-win64.lock.txt':raise ValueError('GAMD_RUNTIME_CONFIG')
 cfg=check(value['venv_config']).read_text();lock=check(value['lock']).read_text()
 if 'version_info = 3.13.17' not in cfg or 'include-system-site-packages = false' not in cfg:raise ValueError('GAMD_RUNTIME_VENV')
 canonical=lambda n:re.sub('[-_.]+','-',n).lower()
 locked={canonical(n):v for n,v in re.findall(r'^([A-Za-z0-9_.-]+)==([^\s\\]+)',lock,re.M)}
 observed={}
 for item in value['distributions']:
  p=check(item['metadata'],True)
  if p.parent.parent.resolve()!=site or not p.parent.name.endswith('.dist-info') or p.name!='METADATA':raise ValueError('GAMD_RUNTIME_METADATA_PATH')
  raw=p.read_text(encoding='utf8');name=re.search(r'^Name: (.+)$',raw,re.M);version=re.search(r'^Version: (.+)$',raw,re.M)
  key=canonical(item['name'])
  if not name or not version or name[1].strip()!=item['name'] or version[1].strip()!=item['version'] or key in observed:raise ValueError('GAMD_RUNTIME_DISTRIBUTION')
  observed[key]=item['version']
 if observed!=locked:raise ValueError('GAMD_RUNTIME_LOCK_CENSUS')
 actual={p.resolve() for p in site.glob('*.dist-info/METADATA')}
 if actual!={Path(i['metadata']['path']).resolve() for i in value['distributions']}:raise ValueError('GAMD_RUNTIME_INSTALLED_CENSUS')
 return value

def load_runtime(root,operation=None):
 from nfl_model import gamd_gate as gate
 consumer,_=gate.consumer_for(root,operation or gate.DEVELOPMENT)
 matches=[r for r in consumer['implementation'] if r['path']==RUNTIME_PATH]
 if len(matches)!=1:raise ValueError('GAMD_RUNTIME_BINDING_REQUIRED')
 return validate_runtime(root,gate.read(root,matches[0],True))

def command(root,manifest,operation=None):
 value=load_runtime(root,operation)
 return [value['base_executable']['path'],'-I','-B','-S',str(Path(root)/'tools/research_lab/gamd/child.py'),str(root),str(manifest)]

def configure_child(root,operation):
 import sys
 value=load_runtime(root,operation)
 if Path(sys.executable).resolve()!=Path(value['base_executable']['path']).resolve() or '.'.join(map(str,sys.version_info[:3]))!=value['python_version'] or not sys.flags.isolated or not sys.flags.no_site:
  raise ValueError('GAMD_DIRECT_ISOLATED_INTERPRETER_REQUIRED')
 # append a single already authenticated directory; site.addsitedir executes .pth.
 sys.path.append(value['site_packages'])
 return value

def probe_runtime(root,directory,value,*,stop=None):
 """Exercise direct parent PID, nonce and JobObject with no football or fitting."""
 import secrets
 nonce=secrets.token_hex(32);directory=Path(directory);directory.mkdir(parents=True,exist_ok=False)
 program="""import ctypes,json,os,sys
expected=int(sys.argv[1]);nonce=sys.argv[2]
assert sys.stdin.buffer.readline()==('EXECUTE '+nonce+'\\n').encode('ascii')
assert os.getppid()==expected, 'DIRECT_PARENT_PID'
assert sys.flags.isolated and sys.flags.no_site, 'ISOLATED_NO_SITE'
in_job=ctypes.c_int()
kernel=ctypes.WinDLL('kernel32',use_last_error=True)
kernel.GetCurrentProcess.restype=ctypes.c_void_p
kernel.IsProcessInJob.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_int)]
assert kernel.IsProcessInJob(kernel.GetCurrentProcess(),None,ctypes.byref(in_job)) and in_job.value, 'JOB_MEMBERSHIP'
sys.path.append(sys.argv[3])
import numpy,sklearn
print(json.dumps({'status':'PASS','parent_pid':os.getppid(),'child_pid':os.getpid(),'python':'.'.join(map(str,sys.version_info[:3])),'numpy':numpy.__version__,'scikit-learn':sklearn.__version__,'nonce_verified':True,'job_verified':True,'football_rows':0,'model_fits':0,'provider_calls':0}))
"""
 argv=[value['base_executable']['path'],'-I','-B','-S','-c',program,str(os.getpid()),nonce,value['site_packages']]
 result=run(argv,root,directory/'stdout.log',directory/'stderr.log',seconds=30,memory_mb=512,stop=stop,handshake=('EXECUTE '+nonce+'\n').encode('ascii'))
 if result['status']!='COMPLETED':raise ValueError('GAMD_DIRECT_RUNTIME_PROBE_FAILED:'+str(directory))
 receipt=json.loads((directory/'stdout.log').read_text(encoding='utf8'))
 versions={re.sub('[-_.]+','-',r['name']).lower():r['version'] for r in value['distributions']}
 if receipt['parent_pid']!=os.getpid() or receipt['python']!=value['python_version'] or receipt['numpy']!=versions['numpy'] or receipt['scikit-learn']!=versions['scikit-learn']:
  raise ValueError('GAMD_DIRECT_RUNTIME_PROBE_IDENTITY')
 return receipt

class MemoryJob:
 def __init__(self,process,megabytes):
  self.handle=None
  if os.name!='nt':raise RuntimeError('WINDOWS_MEMORY_LIMIT_REQUIRED')
  from ctypes import wintypes as w
  U=ctypes.c_ulonglong;P=ctypes.c_size_t
  class Basic(ctypes.Structure):
   _fields_=[('process_time',U),('job_time',U),('flags',w.DWORD),('min_ws',P),('max_ws',P),('active',w.DWORD),('affinity',P),('priority',w.DWORD),('scheduling',w.DWORD)]
  class IO(ctypes.Structure):_fields_=[(x,U) for x in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
  class Extended(ctypes.Structure):_fields_=[('basic',Basic),('io',IO),('process_memory',P),('job_memory',P),('peak_process',P),('peak_job',P)]
  dll=ctypes.WinDLL('kernel32',use_last_error=True)
  dll.CreateJobObjectW.restype=w.HANDLE;dll.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR]
  dll.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
  dll.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];dll.CloseHandle.argtypes=[w.HANDLE]
  handle=dll.CreateJobObjectW(None,None)
  info=Extended();info.basic.flags=0x2000|0x100|0x200;info.process_memory=int(megabytes)*1024**2;info.job_memory=info.process_memory
  if not handle or not dll.SetInformationJobObject(handle,9,ctypes.byref(info),ctypes.sizeof(info)) or not dll.AssignProcessToJobObject(handle,w.HANDLE(int(process._handle))):
   if handle:dll.CloseHandle(handle)
   raise RuntimeError('FIT_MEMORY_GUARD_UNAVAILABLE')
  self.handle=handle;self.dll=dll
 def close(self):
  if self.handle:self.dll.CloseHandle(self.handle);self.handle=None

def run(argv,cwd,stdout_path,stderr_path,*,seconds=300,memory_mb=4096,stop=None,handshake=b'EXECUTE\n'):
 if not 1<=seconds<=300 or not 512<=memory_mb<=4096:raise ValueError('FIT_BUDGET_OUT_OF_BOUNDS')
 if not isinstance(handshake,bytes) or (handshake!=b'EXECUTE\n' and re.fullmatch(rb'EXECUTE [0-9a-f]{64}\n',handshake) is None):raise ValueError('INVALID_FIT_HANDSHAKE')
 env=dict(os.environ);env.update(OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1')
 started=time.monotonic();job=None
 with Path(stdout_path).open('xb') as out,Path(stderr_path).open('xb') as err:
  p=subprocess.Popen(argv,cwd=str(cwd),env=env,stdin=subprocess.PIPE,stdout=out,stderr=err,creationflags=subprocess.CREATE_NO_WINDOW)
  try:
   # Child waits on stdin before any numerical/source execution.
   job=MemoryJob(p,memory_mb);p.stdin.write(handshake);p.stdin.flush();p.stdin.close()
   while p.poll() is None:
    if stop is not None and stop.is_set():p.kill();p.wait();return {'status':'INTERRUPTED','elapsed_seconds':time.monotonic()-started}
    if time.monotonic()-started>seconds:p.kill();p.wait();return {'status':'TIMEOUT','elapsed_seconds':time.monotonic()-started}
    time.sleep(.2)
   return {'status':'COMPLETED' if p.returncode==0 else 'FAILED','exit_code':p.returncode,'elapsed_seconds':time.monotonic()-started}
  finally:
   if p.poll() is None:p.kill();p.wait()
   if job:job.close()
