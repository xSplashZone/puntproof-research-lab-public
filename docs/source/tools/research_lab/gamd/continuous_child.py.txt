"""Authenticated bounded child for the existing coordinator."""
import sys,re,json,os,hashlib
from pathlib import Path

def main():
 token=re.fullmatch(rb'EXECUTE ([0-9a-f]{64})\n',sys.stdin.buffer.readline(128))
 if token is None:raise ValueError('AUTHENTICATED_PARENT_REQUIRED')
 root=Path(sys.argv[1]).resolve();mp=Path(sys.argv[2]).resolve();sys.path[:0]=[str(root/'src'),str(root/'tools/research_lab')]
 from gamd.limits import configure_child
 configure_child(root,None)
 from nfl_model import continuous_gamd_gate as g
 from core import Store
 from gamd.continuous_registry import Queue
 import socket
 def deny(*a,**kw):raise RuntimeError('NO_PROVIDER_OR_NETWORK_ACCESS_IN_RESEARCH_CHILD')
 socket.socket.connect=deny
 mr=g.ref(root,mp);m=g.read(root,mr)
 if m['operation']==g.FORWARD:
  claim=g.read(root,g.ref(root,mp.parent/'CLAIM.json'));g.need(claim['parent_pid']==os.getppid() and claim['manifest']==mr and claim['nonce_sha256']==hashlib.sha256(token[1]).hexdigest(),'FORWARD_PARENT')
  from gamd.continuous_forward import predict
  with g.authority(root,g.FORWARD,mr):predict(root,m,mr)
  return
 queue=Queue(Store(root/'data/private/research-lab'))
 with g.fitting_authority(root,mr,token[1].decode()) as m:
  plan=m['candidate'];phase=lambda state:queue.phase(m['attempt_id'],state)
  phase('PREFLIGHT_PASSED');rows=[json.loads(x) for x in g.checked(root,plan['projection']).splitlines()]
  if any(r['season']==2025 for r in rows):raise ValueError('PROTECTED_YEAR')
  if plan.get('recovery'):
   from gamd import trainer as legacy
   original_fit,original_score=legacy._fit,legacy.score_law;opened=[False]
   def observed_fit(*args,**kwargs):
    phase('FIT_STARTED');value=original_fit(*args,**kwargs);phase('FIT_COMPLETED');return value
   def observed_score(*args,**kwargs):
    if not opened[0]:phase('EVALUATION_OPENED');opened[0]=True
    return original_score(*args,**kwargs)
   # Observe exact old functions without changing any scientific argument or output.
   legacy._fit,legacy.score_law=observed_fit,observed_score
   try:result=legacy.run_trial(rows,plan['channel'],plan['family'],root/m['output']/'artifacts',m['spec_hash'])
   finally:legacy._fit,legacy.score_law=original_fit,original_score
   result['classification']='LINKED_PREFIT_ENGINEERING_RECOVERY_THEN_EXPOSED_DEVELOPMENT';result['recovery']=plan['recovery'];result['feature_schema']=plan['feature_schema']
  elif plan['process']=='SCHEDULED_REFIT':
   from gamd.continuous_refit import run
   result=run(rows,{**plan,'output':m['output']},root/m['output']/'artifacts',phase)
  else:
   from gamd.continuous_train import run
   result=run(rows,{**plan,'output':m['output']},root/m['output']/'artifacts',phase)
  result['manifest_sha256']=mr['sha256'];g.checked(root,mr);g.write_once(root/m['output']/'TERMINAL.json',result)
if __name__=='__main__':main()
