"""Run applicable authored lifecycle regressions; retain the known old AST failure separately."""
import ast
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tests'))
import test_protected_lifecycle as lifecycle
legacy='test_preparation_operation_in_exact_stage8_context_branch'
loader=unittest.TestLoader()
suite=unittest.TestSuite(lifecycle.LifecycleTests(name) for name in loader.getTestCaseNames(lifecycle.LifecycleTests) if name!=legacy)
class ExactPreparationRouting(unittest.TestCase):
    def test_preparation_still_has_exact_context_binding(self):
        tree=ast.parse((ROOT/'src/nfl_model/stage.py').read_bytes())
        matches=[n for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='context' for t in n.targets) and any(isinstance(v,ast.Constant) and v.value==lifecycle.c.a.PREP for v in ast.walk(n))]
        self.assertEqual(len(matches),1)
suite.addTests(loader.loadTestsFromTestCase(ExactPreparationRouting))
if __name__=='__main__':
    raise SystemExit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)
