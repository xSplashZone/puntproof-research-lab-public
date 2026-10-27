"""Independent saved forecast accounting; never advances or edits V3 state."""
import json
from core import now

def run(c,receipt):
 from nfl_model import continuous_gamd_gate as g
 from gamd.trainer import score_law,summarize_scores
 # Outcome inputs are the authenticated source producer's verified complete games.
 history=[json.loads(x) for x in g.checked(c.root,receipt['history']).splitlines()]
 index={tuple(r[k] for k in ('season','week','game_id','gsis','team','channel')):r for r in history};complete=set(receipt['verified_complete_games']);evaluations=[]
 for record in c.store.records('continuous_benchmark'):
  key=record['manifest']['sha256']
  existing=c.store.latest('continuous_evaluation',key)
  if existing:evaluations.append(existing);continue
  manifest=g.read(c.root,record['manifest']);seal=g.read(c.root,g.ref(c.root,c.root/manifest['output']/'SEAL.json'))
  if seal['forecasts']!=record['forecasts']:raise ValueError('SEALED_FORECAST_RECORD_MISMATCH')
  if manifest['game_id'] not in complete:continue
  scores=[];unknown=[];void=[]
  for f in seal['forecasts']:
   r=index.get(tuple(f['identity']))
   if r and r['appeared'] is False:void.append(f['identity']);continue
   if not r or r['appeared'] is not True or r['yards'] is None or r['opportunity'] is None:unknown.append(f['identity']);continue
   s=score_law(f['law'],r['yards']);s.update(identity=f['identity'],actual=r['yards'],actual_workload=r['opportunity'],workload_absolute_error=abs(f['expected_opportunities']-r['opportunity']),predicted_mean=f['point_mean'],predicted_median=f['point_median']);scores.append(s)
  if unknown:
   c.store.set_setting('continuous_benchmark_outcomes',{'status':'WAITING_EXACT_PLAYER_VALUES','unknown':unknown,'source_receipt':receipt['receipt'],'source_cutoff':receipt['source_cutoff']});continue
  value={'status':'SETTLED','forecast_manifest':record['manifest'],'source_receipt':receipt['receipt'],'settled_at':now(),'scores':scores,'metrics':summarize_scores(scores),'verified_dnp':void,'unknown':unknown,'genuine_roi':None,'paper_bets':0,'classification':'PROSPECTIVE_EXPERIMENTAL_BENCHMARK','new_learning_update':False,'learning_note':'New complete past-data source versions separately trigger bounded scheduled refitting; this score is not a promotion.'}
  c.store.append('continuous_evaluation',key,value,immutable=True);evaluations.append(value)
 return evaluations
