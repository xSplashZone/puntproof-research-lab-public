import unittest,subprocess,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
class ContinuousUiTests(unittest.TestCase):
 def run_js(self,body):
  s=(ROOT/'tools/research_lab/static/app.js').read_text(encoding='utf8');a=s.index('  function researchCounts(');b=s.index('  function functionInfo(',a)
  script="const state={gamd:{completed:9,fitted_artifacts:14,rejected:2,continuous:{attempts:[{plan:{process:'SCHEDULED_REFIT'},result:{completed_folds:0}},{plan:{process:'EXPERIMENTAL_DEVELOPMENT'},status:'REJECTED',result:{completed_folds:1}}]}}}; const num=(x)=>String(x),human=x=>x;"+s[a:b]+body
  r=subprocess.run(['node','-e',script],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr);return json.loads(r.stdout)
 def test_functions_list_counts_without_undefined_variable(self):
  v=self.run_js('console.log(JSON.stringify(functionsList()))');self.assertIn('10 evaluated',v[-1]['summary'])
 def test_scheduled_fits_are_not_evaluations(self):
  self.assertEqual(self.run_js('console.log(JSON.stringify(researchCounts()))'),{'artifacts':16,'evaluated':10,'rejected':3,'refits':1})
 def test_same_row_error_and_unavailable_control(self):
  s=self.run_js("console.log(JSON.stringify(continuousEvidence({plan:{process:'EXPERIMENTAL_DEVELOPMENT'},result:{metrics:{n:40,mae:99,coverage80:.8},frozen_controls:{same_population_n:30,candidate:{mae:2},F:{mae:3}}}})))")
  self.assertIn('candidate MAE 2, frozen F 3',s);self.assertIn('Full evaluation: 40',s)
 def test_scheduled_null_metrics_are_not_zero_accuracy(self):
  s=self.run_js("console.log(JSON.stringify(continuousEvidence({plan:{process:'SCHEDULED_REFIT'},result:{metrics:null}})))");self.assertIn('no evaluation',s)

 def test_admitted_bdl_warning_is_not_storage_only(self):
  s=(ROOT/'tools/research_lab/static/app.js').read_text(encoding='utf8');a=s.index('  function displayIssue(');b=s.index('  function researchCounts(',a)
  script="const state={gamd:{licensed_bdl:{runtime:{internal_policy:'UPDATED'}}},home:{counts:{candidates_tested:2}}};"+s[a:b]+"console.log(JSON.stringify([displayIssue({id:'snap'}),displayIssue({id:'experiment'})]));"
  r=subprocess.run(['node','-e',script],capture_output=True,text=True);self.assertEqual(r.returncode,0,r.stderr);v=json.loads(r.stdout);self.assertIn('admitted for private',v[0]['summary']);self.assertNotIn('storage-only',v[0]['summary']);self.assertIn('2 sealed',v[1]['summary'])
