"""One bounded existing GAMD worker child; no independent application."""
import sys,re,json
from pathlib import Path

def main():
 match=re.fullmatch(rb'EXECUTE ([0-9a-f]{64})\n',sys.stdin.buffer.readline(128))
 if match is None:raise ValueError('AUTHENTICATED_PARENT_REQUIRED')
 root=Path(sys.argv[1]).resolve();mp=Path(sys.argv[2]).resolve();sys.path[:0]=[str(root/'src'),str(root/'tools/research_lab')]
 from gamd.limits import configure_child
 configure_child(root,None)
 from nfl_model import licensed_bdl_gate as g
 mr=g.ref(root,mp)
 with g.fitting_authority(root,mr,match[1].decode()) as m:
  from gamd.licensed_train import run
  rows=[json.loads(line) for line in g.checked(root,m['projection']).splitlines()]
  if any(r['season'] not in (2023,2024) for r in rows):raise ValueError('EXACT_FIT_YEARS')
  result=run(rows,m,root/m['output']/'artifacts');result['manifest_sha256']=mr['sha256'];g.checked(root,mr);g.write_once(root/m['output']/'TERMINAL.json',result)
if __name__=='__main__':main()
