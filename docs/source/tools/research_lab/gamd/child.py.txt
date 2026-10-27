"""One immutable GAMD fit request. Not a standalone alternate application."""
import sys,json,hashlib,re
from pathlib import Path

def main():
 if len(sys.argv)!=3:raise ValueError('Exact root and manifest required')
 handshake=sys.stdin.buffer.readline(128)
 match=re.fullmatch(rb'EXECUTE ([0-9a-f]{64})\n',handshake)
 if match is None:raise ValueError('PARENT_RESOURCE_GUARD_AND_NONCE_REQUIRED')
 token=match[1].decode('ascii')
 root=Path(sys.argv[1]).resolve();manifest=Path(sys.argv[2]).resolve()
 sys.path.insert(0,str(root/'src'));sys.path.insert(0,str(root/'tools/research_lab'))
 from nfl_model import gamd_gate
 manifest_ref=gamd_gate.ref(root,manifest)
 operation=gamd_gate.read(root,manifest_ref)['operation']
 from gamd.limits import configure_child
 configure_child(root,operation)
 if operation==gamd_gate.FORWARD:
  from gamd.forward import execute_child
  execute_child(root,manifest_ref,token)
  return
 with gamd_gate.fitting_authority(root,manifest,token) as admitted:
  # Every football input byte and numerical operation is inside the consumed
  # child context. Never call the parent's PLANNED-only require_fit here.
  from gamd.trainer import run_trial
  from gamd.registry import write_once
  raw=gamd_gate.checked(root,admitted['projection'])
  rows=[]
  for line in raw.splitlines():
   row=json.loads(line)
   if row['season'] not in (2021,2022,2023,2024):raise ValueError('FIT_YEAR_BOUNDARY')
   rows.append(row)
  del raw
  output=root/admitted['output'];output.relative_to(root/'data/private/research-lab/gamd')
  result=run_trial(rows,admitted['channel'],admitted['family'],output/'artifacts',spec_hash=admitted['spec_hash'])
  gamd_gate.checked(root,manifest_ref)
  result.update(projection_file_sha256=admitted['projection']['sha256'],manifest_sha256=manifest_ref['sha256'])
  write_once(output/'TERMINAL.json',result)
if __name__=='__main__':main()
