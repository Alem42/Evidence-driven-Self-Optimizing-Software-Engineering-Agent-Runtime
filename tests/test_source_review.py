"""Source review uses real bounded Go parser results."""
import tempfile
import unittest
from pathlib import Path
from masa.application.source_review import review_sources
from masa.application.planning import ProjectPlanning
from masa.application.generation import ProjectGeneration
from masa.infrastructure.store import Store
from masa.infrastructure.runner import Runner
from masa.domain.models import MasaError
from test_project_plan import PlannerProvider,SPEC,CHECKS
from test_project_generation import DeveloperProvider
from test_runtime import FakeExecutor

class SourceReviewTests(unittest.TestCase):
    def test_real_parser_detects_syntax_and_empty_tests(self):
        """真实 AST 发现语法/空测试，审查不改变草稿状态。 Real AST findings leave draft publication unchanged."""
        root=Path.cwd();runner=Runner(root/'.tools/bin/masa-runner.exe',root/'.tools/go/bin/go.exe')
        with tempfile.TemporaryDirectory() as temp:
            s=Store(Path(temp));p=ProjectPlanning(s,FakeExecutor());plan=p.generate(PlannerProvider(),'Build CLI');m=s.run(plan)['data']['project_plan']
            p.approve(plan,{'spec_ref':m['spec_ref'],'checks_ref':m['checks_ref'],'spec':SPEC,'checks':CHECKS})
            draft=ProjectGeneration(s,FakeExecutor()).generate(plan,DeveloperProvider());m=s.run(draft)['data']['project_plan'];files=s.read(m['files_ref'])
            original_test=files['internal/app/app_test.go']
            files['internal/app/app_test.go']='package app\nimport "testing"\nfunc TestEmpty(t *testing.T) {}\n'
            report=review_sources(s,runner,draft,{'files_ref':m['files_ref'],'files':files})
            self.assertTrue(any(f['code']=='empty_test' for f in report['findings']))
            files['internal/app/app_test.go']='package app\nfunc TestBroken( {\n'
            report=review_sources(s,runner,draft,{'files_ref':m['files_ref'],'files':files})
            self.assertTrue(any(f['code']=='syntax_error' for f in report['findings']))
            self.assertEqual(s.run(draft)['data']['project_plan']['status'],'awaiting_review')
            self.assertEqual(s.run(draft)['tool_calls'],2)
            self.assertEqual(s.read(m['files_ref'])['internal/app/app_test.go'],original_test)
            with self.assertRaisesRegex(MasaError,'stale'):review_sources(s,runner,draft,{'files_ref':'stale','files':files})
            self.assertEqual(s.run(draft)['tool_calls'],2)
            review_sources(s,runner,draft,{'files_ref':m['files_ref'],'files':files})
            with self.assertRaisesRegex(MasaError,'budget exhausted'):
                review_sources(s,runner,draft,{'files_ref':m['files_ref'],'files':files})
            self.assertEqual(s.run(draft)['tool_calls'],3)
            s.close()
