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
    def test_private_toolchain_import_is_a_test_preparation_error(self):
        """真实本地模型的私有测试工具导入须修测试，不改冻结边界。 Private toolchain imports route to test repair without relaxing frozen boundaries."""
        from masa.application.checks.check_policy import test_revision_needed
        self.assertTrue(test_revision_needed('sum_test.go:8:2: use of internal package internal/testenv not allowed'))
        self.assertFalse(test_revision_needed('sum.go:8:2: use of internal package internal/testenv not allowed'))
        self.assertTrue(test_revision_needed('main_test.go:59:50: cannot use "0" (untyped string constant) as int value in argument to formatExitCode'))
        self.assertTrue(test_revision_needed('main_test.go:111:17: invalid operation: exitCode != expectedStderr (mismatched types int and string)'))
        self.assertFalse(test_revision_needed('main.go:59:50: cannot use "0" as int'))
        self.assertFalse(test_revision_needed('main_test.go:36: expected error message to contain cannot use'))

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
            def __init__(self):self.planner_calls=0;self.tester_calls=0;self.tester_contexts=[]
            def respond(self,context):
                if context['purpose']=='project_planner':self.planner_calls+=1
                if context['purpose']=='project_tester':
                    self.tester_calls+=1
                    self.tester_contexts.append(dict(context))
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
            # 重试时必须告诉 Tester 上次为什么被拒绝（评测里 hello 因为三次完全相同的上下文而连续失败）。
            # A retry must tell the Tester why the last answer was rejected (a benchmark task failed on three identical contexts).
            self.assertNotIn('previous_attempt_error',provider.tester_contexts[0])
            for context in provider.tester_contexts[1:]:
                self.assertIn('previous_attempt_error',context)
                self.assertIn('EXACTLY the fields operation, purpose',context['previous_attempt_error'])

    def test_failed_test_source_requires_test_revision(self):
        """冻结测试导致的编译错误不能反复交给实现修复。 A frozen test compile error cannot be fixed by implementation-only repair."""
        self.assertTrue(test_revision_needed('random_test.go:7:2: "os" imported and not used'))
        self.assertTrue(test_revision_needed('cmd/app/main_test.go:22:1: missing return'))
        self.assertFalse(test_revision_needed('cmd/app/main.go:22:1: missing return'))
        self.assertTrue(test_revision_needed('import cycle not allowed in test'))
        self.assertTrue(test_revision_needed('random_test.go:174: exec: executable file not found in %PATH%'))
        self.assertTrue(test_revision_needed('main_test.go:153: failed to run binary: exec: "randint-test.exe": cannot run executable found relative to current directory'))
        self.assertFalse(test_revision_needed('main.go:153: exec: cannot run executable found relative to current directory'))
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
