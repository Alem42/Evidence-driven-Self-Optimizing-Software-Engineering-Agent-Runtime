"""一次授权后的有界自动流程。 Bounded project workflow after one explicit opt-in."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from masa.application.console import Console, test_revision_needed, test_format_only, repeated_assertion_signature, repair_advice
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
    def test_build_path_and_overflow_are_test_revisions(self):
        """复现真实项目的测试路径与常量错误，同时避免误判实现诊断。 Reproduce setup failures without confusing implementation errors."""
        import json
        build='\n'.join(json.dumps({'Output':line}) for line in [
            '    main_test.go:103: build failed: exit status 1\n',
            '    no Go files in C:\\work\\cmd\n'])
        cases=[build,'internal\\sum\\sum_test.go:15:47: constant 9223372036854775808 overflows int']
        for output in cases:
            self.assertTrue(test_revision_needed(output))
            self.assertEqual(repair_advice([('go_test',{'exit_code':1,'stdout':output})])['action'],'revise_tests')
        self.assertFalse(test_revision_needed('main.go:8:2: undefined: io\nmain_test.go:30: got 1 want 2'))
        self.assertFalse(test_revision_needed('main_test.go:30: expected 2 got 1'))

    def test_repeated_assertion_signature_ignores_go_json_metadata(self):
        """断言不变时识别停滞，时间戳与行号不影响判断。 / Stable assertions survive JSON timestamps and line shifts."""
        def check(line, timestamp):
            output='    merge_test.go:'+line+': got [{-5 2}], want three intervals\\n'
            import json
            return [('go_test', {'exit_code':1, 'stdout':json.dumps({'Time':timestamp,'Output':output})})]
        self.assertEqual(repeated_assertion_signature(check('75','first')),
                         repeated_assertion_signature(check('79','second')))
        self.assertEqual(repeated_assertion_signature([('go_test',{'exit_code':0,'stdout':''})]),())

    def test_auto_retries_tester_without_repeating_planner(self):
        """复用已验证规格，只重试失败的 Tester 响应。 Reuse a validated spec and retry only Tester failures."""
        class FlakyTester(CompositeProvider):
            def __init__(self):self.planner_calls=0;self.tester_calls=0
            def respond(self,context):
                if context['purpose']=='project_planner':self.planner_calls+=1
                if context['purpose']=='project_tester':
                    self.tester_calls+=1
                    if self.tester_calls<3:return [{'invalid':'shape'}]
                return super().respond(context)
        with tempfile.TemporaryDirectory() as temp:
            provider=FlakyTester()
            console=Console(Path(temp),Path('fake-runner'),Path('fake-go'),Path.cwd())
            console.settings.provider=lambda profile_id=None:provider
            with patch('masa.application.console.Runner',lambda *args:FakeExecutor()):
                started=console.start_autonomous_project_job({'goal':'Build a CLI'})
                console.job_thread.join(timeout=10)
            job=console.project_job(started['job_id'])
            self.assertEqual(job['status'],'completed')
            self.assertEqual((provider.planner_calls,provider.tester_calls),(1,3))

    def test_failed_test_source_requires_test_revision(self):
        """冻结测试导致的编译错误不能反复交给实现修复。 A frozen test compile error cannot be fixed by implementation-only repair."""
        self.assertTrue(test_revision_needed('random_test.go:7:2: "os" imported and not used'))
        self.assertTrue(test_revision_needed('import cycle not allowed in test'))
        self.assertTrue(test_revision_needed('random_test.go:174: exec: executable file not found in %PATH%'))
        self.assertFalse(test_revision_needed('random.go:7:2: "os" imported and not used'))
        self.assertTrue(test_revision_needed('',[('go_fmt_check',{'exit_code':1,'stdout':'cmd\\app\\main_test.go\n'})]))
        self.assertFalse(test_revision_needed('',[('go_fmt_check',{'exit_code':1,'stdout':'cmd\\app\\main.go\ncmd\\app\\main_test.go\n'})]))
        self.assertTrue(test_format_only([('go_test',{'status':'completed','exit_code':0}),
                                          ('go_fmt_check',{'status':'completed','exit_code':1,'stdout':'cmd\\app\\main_test.go\n'})]))
        self.assertFalse(test_format_only([('go_test',{'status':'completed','exit_code':1}),
                                           ('go_fmt_check',{'status':'completed','exit_code':1,'stdout':'cmd\\app\\main_test.go\n'})]))

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
