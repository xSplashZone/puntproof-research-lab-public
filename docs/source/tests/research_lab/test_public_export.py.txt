import importlib.util,json,unittest
from pathlib import Path
p=Path(__file__).resolve().parents[2]/'tools/research_lab/publish_testing.py';s=importlib.util.spec_from_file_location('publish_testing',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class PublicExportTests(unittest.TestCase):
    def test_deny_by_default_removes_records_and_secrets(self):
        original={'home':{'counts':{'forecasts_generated':161}},'settings':{'token':'DO_NOT_EXPORT'},'forecasts':[{'player':'DO_NOT_EXPORT'}],'source_imports':[{'samples':['DO_NOT_EXPORT']}],'bets':[{'secret':'DO_NOT_EXPORT'}]}
        public=m.public_state(original,'authored capture')
        self.assertNotIn('DO_NOT_EXPORT',json.dumps(public));self.assertEqual(public['home']['counts']['forecasts_generated'],161);self.assertEqual(public['forecasts'],[]);self.assertTrue(public['cloud_mode'])
    def test_unknown_performance_stays_unmeasured(self):
        result=m.public_state({'home':{'counts':{}}},'authored')
        self.assertEqual(result['overview']['genuine_roi'],'ROI_NOT_ESTABLISHED');self.assertIn('Not measured',result['home']['performance']['summary'])
