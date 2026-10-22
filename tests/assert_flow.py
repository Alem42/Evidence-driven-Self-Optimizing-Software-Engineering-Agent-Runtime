"""断言失败场景的测试辅助：只剩断言失败（测试期望写错）的世界、执行器与按 purpose 脚本化的假模型。原先在 test_conductor 里，指挥者删除后留给 test_referee 使用。
Test helpers for the assertion-only failure scenario (the expectation is wrong): the world, its executor and a fake model scripted per purpose. They lived in test_conductor; test_referee still needs them."""
from test_fix_flow import FixFlowCase, Model, World, WorldExecutor, TEST  # noqa: F401

ASSERT_LINE = '    internal/app/app_test.go:12: got 3, want 4'


class AssertWorld(World):
    """只剩断言失败：测试的期望写错了（修订测试才能修好）。 Only an assertion fails: the test expectation is wrong."""

    def __init__(self, need=2):
        super().__init__(impl_ok=True, test_ok=False)
        self.need = need  # 第几次修复（实现或测试）才真的修好：第一次没有进展才会出现“停滞”，指挥者才会被问 / which fix really repairs it; the first makes no progress, which is the stall that makes the conductor due
        self.fix_calls = 0


class AssertExecutor(WorldExecutor):
    def execute(self, request, workspace, cancelled):
        result = super().execute(request, workspace, cancelled)
        if request['operation'] == 'go_test':
            fixed = self.world.fix_calls >= self.world.need
            result['stdout'] = '' if fixed else ASSERT_LINE
            result['exit_code'] = 0 if fixed else 1
        return result


class Scripted(Model):
    """在 Model 之上增加按 purpose 的脚本。script 的值可以是字典、字典列表（依次返回）或会抛异常的函数。"""

    def __init__(self, *args, script=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.script = dict(script or {})

    def respond(self, context):
        purpose = context['purpose']
        if purpose in self.script:
            self.contexts.append(context)
            self.world.log.append(f'{purpose} {self.name}')
            self.calls += 1
            value = self.script[purpose]
            if callable(value):
                return value(context)
            if isinstance(value, list):
                return value.pop(0) if len(value) > 1 else value[0]
            return value
        out = super().respond(context)
        if purpose in ('project_repair', 'project_test_revision'):
            self.world.fix_calls = getattr(self.world, 'fix_calls', 0) + 1
        return out


class AssertCase(FixFlowCase):
    def run_assert(self, local_script=None, cloud_script=None, policy=None, cloud_diagnosis=None):
        world = AssertWorld()
        local = Scripted('small', 'local', world, script=local_script)
        cloud = Scripted('big', 'cloud', world, script=cloud_script, diagnosis=cloud_diagnosis or {
            'owner': 'test', 'rationale': '期望写错', 'implementation_instructions': '', 'test_instructions': '改期望', 'expectation_checks': []})
        store, jobs, router = self.build(local, cloud, world, policy=policy)
        from unittest.mock import patch
        from masa.application.orchestration.coordinator import WorkflowCoordinator
        with patch('masa.intelligence.repair_context.build_repair_context', lambda s, e, r, f, ev, fb: (f, None)):
            WorkflowCoordinator(store, AssertExecutor(world), None, jobs['job'], router=router).run()
        return world, store, jobs['job'], local, cloud

    def nodes(self, store):
        return [(s['node'], s['to']) for s in self.trace(store)]
