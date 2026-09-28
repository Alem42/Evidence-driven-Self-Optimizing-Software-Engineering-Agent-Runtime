"""一次授权后的有界自动流程。 Bounded project workflow after one explicit opt-in."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from masa.application.console import Console, test_revision_needed
from masa.application.projects import Projects
from masa.infrastructure.store import Store
from test_project_plan import SPEC, CHECKS
from test_project_generation import FILES
from test_runtime import FakeExecutor


class CompositeProvider:
    profile={'provider':'test','model':'fake'}
    usage={'total_tokens':1}

    def respond(self, context):
        """三个角色返回合法确定性结果。 Return deterministic valid responses for three roles."""
        return {'project_planner':SPEC,'project_tester':CHECKS,'project_developer':FILES}[context['purpose']]


class AutomaticProjectTests(unittest.TestCase):
    def test_failed_test_source_requires_test_revision(self):
        """冻结测试导致的编译错误不能反复交给实现修复。 A frozen test compile error cannot be fixed by implementation-only repair."""
        self.assertTrue(test_revision_needed('random_test.go:7:2: "os" imported and not used'))
        self.assertTrue(test_revision_needed('import cycle not allowed in test'))
        self.assertTrue(test_revision_needed('random_test.go:174: exec: executable file not found in %PATH%'))
        self.assertFalse(test_revision_needed('random.go:7:2: "os" imported and not used'))

    def test_opt_in_advances_all_roles_and_real_gate(self):
        """自动审批仍需 Runtime 工具和 Gate 成功。 Auto-approval still requires tools and independent Gate success."""
        with tempfile.TemporaryDirectory() as temp:
            console=Console(Path(temp),Path('fake-runner'),Path('fake-go'),Path.cwd())
            console.settings.provider=lambda profile_id=None:CompositeProvider()
            with patch('masa.application.console.Runner',lambda *args:FakeExecutor()):
                started=console.start_autonomous_project_job({'goal':'Build a CLI'})
                console.job_thread.join(timeout=10)
            job=console.project_job(started['job_id'])
            self.assertEqual(job['status'],'completed')
            store=Store(Path(temp))
            try:
                rid=job['result']['id']
                self.assertEqual(store.run(rid)['status'],'succeeded')
                stages=Projects(store).view(rid)['stages']
                self.assertEqual(stages[-1]['role'],'gate')
                self.assertEqual(stages[-1]['status'],'succeeded')
                self.assertEqual([s['label'] for s in stages if s['kind']=='human'],['自动采用方案','自动采用代码'])
            finally:store.close()
