"""Read-only verification of the preserved licensed-data activation custody."""
from pathlib import Path
import hashlib,json,subprocess,sys
root=Path(__file__).resolve().parents[2]
proof=json.loads((root/'docs/research_lab/licensed_bdl/CUSTODY_CLOSEOUT.json').read_bytes())
git=r'C:\Program Files\Git\cmd\git.exe'
def blob(rev,p):return subprocess.check_output([git,'show',rev+':'+p],cwd=root)
for p,h in proof['expected_files'].items():
 assert hashlib.sha256((root/p).read_bytes()).hexdigest()==h,p
 assert hashlib.sha256(blob('HEAD',p)).hexdigest()==h,p
for p in proof['unchanged_blob_paths']:
 assert blob(proof['previous_commit'],p)==blob(proof['activation_commit'],p),p
sys.path.insert(0,str(root/'src'))
from nfl_model import licensed_bdl_gate as g,gamd_gate as old,research_lab_gate as v3
for op in g.OPS:g.consumer_for(root,op)
print(json.dumps({'status':'PASS','committed_files':len(proof['expected_files']),'unchanged_blob_paths':len(proof['unchanged_blob_paths']),'source_calls':0,'fits':0,'registered_consumers':list(g.OPS)}))
