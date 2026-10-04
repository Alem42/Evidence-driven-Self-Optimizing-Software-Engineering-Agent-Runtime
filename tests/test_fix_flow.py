"""复现真实案例 c903b2b7：测试有 import cycle，实现有语法错误。系统必须让两类问题都轮到，
无改动的修订不能浪费轮次，卡住时 Diagnoser 判断，自动流程停下后能继续，并且不留空挂的本地模型。
Reproduces the real task c903b2b7: a test import cycle AND an implementation syntax error. Both classes must get a turn,
no-op revisions must not waste rounds, the Diagnoser judges when stuck, a stopped run can be continued, and no model idles."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from masa.application.coordinator import WorkflowCoordinator
from masa.application.router import Router
from masa.application.routing import DEFAULT_POLICY, validate_policy
from masa.infrastructure.jobs import Jobs
from masa.infrastructure.store import Store
from test_project_generation import FILES
from test_project_plan import CHECKS, SPEC
from test_routing import BUDGET, fake_entry
from test_runtime import FakeExecutor

IMPL = 'internal/app/app.go'
TEST = 'internal/app/app_test.go'
SYNTAX = './internal/app/app.go:47:17: syntax error: unexpected name not, expected {'
CYCLE = 'imports example.com/task/internal/app from app_test.go: import cycle not allowed in test'


class World:
    """被测世界的状态：实现是否能编译、测试是否还有 import cycle。 The state of the world under test."""

    def __init__(self, impl_ok=False, test_ok=False):
        self.impl_ok, self.test_ok = impl_ok, test_ok
        self.verifications = 0
        self.log = []


class WorldExecutor(FakeExecutor):
    def __init__(self, world):
        super().__init__(exit_code=1)
        self.world = world

    def execute(self, request, workspace, cancelled):
        result = super().execute(request, workspace, cancelled)
        if request['operation'] == 'go_test':
            self.world.verifications += 1
            lines = ([] if self.world.impl_ok else [SYNTAX]) + ([] if self.world.test_ok else [CYCLE])
            result['exit_code'] = 1 if lines else 0
            result['stdout'] = '\n'.join(lines)
        else:
            result['exit_code'] = 0
            result['stdout'] = ''
        return result


class Model:
    """脚本化的假模型：能记录调用、模拟显存驻留，并按设定决定修复是否真的有效。"""

    def __init__(self, name, kind, world, *, fixes_impl=True, fixes_test=True, noop_tests=0, diagnosis=None):
        self.profile = {'provider': 'test', 'model': name}
        self.config = {'model_type': kind}
        self.name, self.world = name, world
        self.fixes_impl, self.fixes_test, self.noop_tests = fixes_impl, fixes_test, noop_tests
        self.diagnosis = diagnosis
        self.usage = {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}
        self.loaded = False
        self.contexts = []
        self.calls = 0

    def unload(self):
        self.world.log.append(f'unload {self.name}')
        self.loaded = False
        return True

    def respond(self, context):
        purpose = context['purpose']
        self.contexts.append(context)
        self.world.log.append(f'{purpose} {self.name}')
        if self.profile and self.config['model_type'] == 'local':
            self.loaded = True
        self.calls += 1
        if purpose == 'project_repair':
            if self.fixes_impl:
                self.world.impl_ok = True
            return {IMPL: f'package app\n\nfunc Value() int {{ return 42 }} // by {self.name} #{self.calls}\n'}
        if purpose == 'project_test_revision':
            if self.noop_tests > 0:
                self.noop_tests -= 1
                return {TEST: context['original_files'][TEST]}  # 原样返回 / unchanged
            if self.fixes_test:
                self.world.test_ok = True
            return {TEST: context['original_files'][TEST] + f'\n// revised by {self.name} #{self.calls}\n'}
        if purpose == 'project_diagnoser':
            return self.diagnosis or {'owner': 'implementation', 'rationale': 'r', 'implementation_instructions': 'fix it', 'test_instructions': ''}
        return {'project_triage': {'verdict': 'ok', 'reasons': [], 'suggestions': []}, 'project_planner': SPEC, 'project_tester': CHECKS,
                'project_developer': FILES}[purpose]


class FixFlowCase(unittest.TestCase):
    def build(self, local, cloud, world, *, policy=None, mode='ladder', request=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(Path(temp.name))
        self.addCleanup(store.close)
        providers = {'local': local, 'cloud': cloud}
        snapshot = {'version': 1, 'mode': 'ladder', 'policy': validate_policy({**DEFAULT_POLICY, 'prefer_highest_roles': ['project_diagnoser'], **(policy or {})}), 'budget': dict(BUDGET),
                    'candidates': [fake_entry('local', 1, local.name, 'local'), fake_entry('cloud', 2, cloud.name, 'cloud')]}
        jobs = Jobs(Path(temp.name))
        jobs['job'] = {'status': 'running', 'run_id': None, 'mode': 'auto', 'phase': 'planning', 'attempt': 0, 'started': 0,
                       'request': {'goal': 'Build a CLI', **(request or {})}}
        router = Router(store, snapshot, lambda entry: providers[entry['id']])
        return store, jobs, router

    def run_task(self, local, cloud, world, **kwargs):
        store, jobs, router = self.build(local, cloud, world, **kwargs)
        with patch('masa.intelligence.repair_context.build_repair_context', lambda s, e, r, f, ev, fb: (f, None)):
            WorkflowCoordinator(store, WorldExecutor(world), None, jobs['job'], router=router).run()
        return store, jobs['job']

    def trace(self, store):
        return [e['payload'] for r in store.all_runs() for e in store.events(r['id']) if e['type'] == 'workflow_node']

    def events(self, store, kind):
        return [e['payload'] for r in store.all_runs() for e in store.events(r['id']) if e['type'] == kind]


class OwnershipLoopTests(FixFlowCase):
    def test_both_defects_get_a_turn_and_the_task_passes(self):
        """旧行为：连续 4 轮“修订测试”，实现的语法错误从未被修。现在：先修实现，再修测试，两轮收敛。"""
        world = World()
        local, cloud = Model('small', 'local', world), Model('big', 'cloud', world)
        store, job = self.run_task(local, cloud, world)
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
        order = [p for p in world.log if p in ('project_repair small', 'project_test_revision small')]
        self.assertEqual(order, ['project_repair small', 'project_test_revision small'])  # 先实现、后测试 / implementation first
        path = [(s['node'], s['to']) for s in self.trace(store)]
        self.assertEqual(path, [('classify', 'repair'), ('repair', 'drafted'), ('classify', 'revise'), ('revise', 'drafted')])
        self.assertEqual(cloud.calls, 0)  # 不需要升级 / no escalation needed

    def test_the_same_case_in_fixed_mode_also_converges(self):
        world = World()
        model = Model('solo', 'local', world)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(Path(temp.name))
        self.addCleanup(store.close)
        jobs = Jobs(Path(temp.name))
        jobs['job'] = {'status': 'running', 'run_id': None, 'mode': 'auto', 'phase': 'planning', 'attempt': 0, 'started': 0, 'request': {'goal': 'Build a CLI'}}
        with patch('masa.intelligence.repair_context.build_repair_context', lambda s, e, r, f, ev, fb: (f, None)):
            WorkflowCoordinator(store, WorldExecutor(world), model, jobs['job']).run()
        self.assertEqual(store.run(jobs['job']['result']['id'])['status'], 'succeeded')

    def test_each_fix_call_receives_a_focused_instruction_for_its_own_side(self):
        world = World()
        local, cloud = Model('small', 'local', world), Model('big', 'cloud', world)
        self.run_task(local, cloud, world)
        repair = next(c for c in local.contexts if c['purpose'] == 'project_repair')
        revision = next(c for c in local.contexts if c['purpose'] == 'project_test_revision')
        self.assertIn('app.go:47', repair['feedback'])
        self.assertNotIn('import cycle', repair['feedback'].split('（另一侧')[0])
        self.assertIn('不能 import 这个包本身', revision['feedback'])


class NoOpRevisionTests(FixFlowCase):
    def test_an_unchanged_revision_is_rejected_without_a_verification_and_escalates(self):
        """6 次 changed=[] 的修订曾被批准、验证、再失败。现在：不验证，直接换更强的模型。"""
        world = World(impl_ok=True)  # 只剩测试问题 / only the test defect is left
        local = Model('small', 'local', world, noop_tests=5)
        cloud = Model('big', 'cloud', world, diagnosis={'owner': 'test', 'rationale': '测试没改', 'implementation_instructions': '',
                                                        'test_instructions': '真正修改测试文件'})
        store, job = self.run_task(local, cloud, world)
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
        self.assertEqual(world.verifications, 2)  # 初次失败 + 修好后通过；无改动的草稿没有被验证 / the unchanged draft was never verified
        self.assertGreaterEqual(len(self.events(store, 'noop_revision')), 1)
        self.assertGreaterEqual(cloud.calls, 1)

    def test_when_even_the_strongest_model_changes_nothing_the_workflow_halts(self):
        world = World(impl_ok=True)
        local = Model('small', 'local', world, noop_tests=99)
        cloud = Model('big', 'cloud', world, noop_tests=99)
        store, job = self.run_task(local, cloud, world, policy={'diagnose': False})
        self.assertEqual(job['status'], 'completed')
        self.assertIn('halted', job['note'])
        self.assertEqual(world.verifications, 1)  # 一次验证都没有浪费 / not a single wasted verification
        self.assertEqual(store.run(job['result']['id'])['status'], 'failed')
        self.assertTrue(any(e['reason'] == 'halted' for e in self.events(store, 'task_stopped')))


class DiagnoserTests(FixFlowCase):
    def test_a_noop_triggers_the_diagnoser_at_the_strongest_level_and_its_instructions_reach_the_fixer(self):
        world = World(impl_ok=True)
        verdict = {'owner': 'test', 'rationale': '测试 import 了自己的包', 'implementation_instructions': '',
                   'test_instructions': '删除对 example.com/task/internal/app 的 import'}
        local = Model('small', 'local', world, noop_tests=1)
        cloud = Model('big', 'cloud', world, diagnosis=verdict)
        store, job = self.run_task(local, cloud, world)
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
        self.assertIn('project_diagnoser big', world.log)  # 诊断者直接用最强的模型 / the diagnoser uses the strongest model
        self.assertNotIn('project_diagnoser small', world.log)
        diagnosis = self.events(store, 'diagnosis')
        self.assertEqual(diagnosis[0]['owner'], 'test')
        later = [c for m in (local, cloud) for c in m.contexts if c['purpose'] == 'project_test_revision' and '删除对' in c.get('feedback', '')]
        self.assertTrue(later, '诊断的指导必须传到修订测试的调用里 / the instructions must reach the revision call')
        self.assertTrue(any(s['node'] == 'diagnose' for s in self.trace(store)))

    def test_a_spec_level_verdict_stops_the_task_for_a_human_instead_of_burning_budget(self):
        world = World(impl_ok=True)
        verdict = {'owner': 'spec', 'rationale': '验收标准 2 与 4 互相矛盾', 'implementation_instructions': '', 'test_instructions': ''}
        local = Model('small', 'local', world, noop_tests=1)
        cloud = Model('big', 'cloud', world, diagnosis=verdict)
        store, job = self.run_task(local, cloud, world)
        self.assertIn('halted', job['note'])
        self.assertEqual(store.run(job['result']['id'])['status'], 'failed')
        self.assertEqual(self.events(store, 'diagnosis')[0]['owner'], 'spec')

    def test_a_failing_diagnoser_never_blocks_the_repair(self):
        world = World(impl_ok=True)
        local = Model('small', 'local', world, noop_tests=1)
        cloud = Model('big', 'cloud', world)
        cloud.diagnosis = {'owner': 'nonsense'}  # 契约不合法 / invalid contract
        store, job = self.run_task(local, cloud, world)
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
        self.assertTrue(self.events(store, 'diagnosis_failed'))

    def test_the_diagnoser_is_off_in_fixed_mode_and_limited_per_task(self):
        world = World(impl_ok=True)
        local, cloud = Model('small', 'local', world, noop_tests=3), Model('big', 'cloud', world)
        store, job = self.run_task(local, cloud, world, policy={'diagnose_max': 1})
        self.assertLessEqual(sum(1 for p in world.log if p.startswith('project_diagnoser')), 1)


class ContinueTests(FixFlowCase):
    def test_a_stopped_run_can_be_continued_and_the_router_knows_who_already_tried(self):
        """自动流程停下后点“继续自动修复”：跳过规划与生成，失败链从版本链重建，直接升级而不是让本地再试一遍。"""
        world = World()
        local_only = Model('small', 'local', world, fixes_impl=False, fixes_test=False)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(Path(temp.name))
        self.addCleanup(store.close)
        jobs = Jobs(Path(temp.name))
        jobs['first'] = {'status': 'running', 'run_id': None, 'mode': 'auto', 'phase': 'planning', 'attempt': 0, 'started': 0, 'request': {'goal': 'Build a CLI'}}
        with patch('masa.intelligence.repair_context.build_repair_context', lambda s, e, r, f, ev, fb: (f, None)):
            WorkflowCoordinator(store, WorldExecutor(world), local_only, jobs['first']).run()  # 固定模式，永远修不好 / fixed mode, never fixes
        self.assertIn('limit', jobs['first']['note'])
        failed = jobs['first']['result']['id']
        before = local_only.calls
        mark = len(world.log)  # 继续之前的日志长度 / the log length before continuing

        local, cloud = Model('small', 'local', world, fixes_impl=False, fixes_test=False), Model('big', 'cloud', world)
        snapshot = {'version': 1, 'mode': 'ladder', 'policy': validate_policy({**DEFAULT_POLICY, 'prefer_highest_roles': ['project_diagnoser']}), 'budget': dict(BUDGET),
                    'candidates': [fake_entry('local', 1, 'small', 'local'), fake_entry('cloud', 2, 'big', 'cloud')]}
        jobs['again'] = {'status': 'running', 'run_id': failed, 'mode': 'auto', 'phase': 'verification', 'attempt': 0, 'started': 0,
                         'request': {'goal': 'Build a CLI', 'continue_from': failed}}
        router = Router(store, snapshot, lambda entry: {'local': local, 'cloud': cloud}[entry['id']])
        with patch('masa.intelligence.repair_context.build_repair_context', lambda s, e, r, f, ev, fb: (f, None)):
            WorkflowCoordinator(store, WorldExecutor(world), None, jobs['again'], router=router).run()
        self.assertEqual(store.run(jobs['again']['result']['id'])['status'], 'succeeded', jobs['again'].get('note'))
        # 实现这一类：本地已经失败过多次 → 直接让更强的模型来，不再把同一件事交给本地。
        # Implementation class: the local model already failed repeatedly → go straight to the stronger model.
        continued = world.log[mark:]
        first_fix = next(p for p in continued if p.startswith('project_repair'))
        self.assertEqual(first_fix, 'project_repair big')
        self.assertGreaterEqual(cloud.calls, 1)
        self.assertEqual(local_only.calls, before)
        self.assertTrue(self.events(store, 'history_seeded'))
        planning_calls = [p for p in world.log if p in ('project_planner small', 'project_developer small')]
        self.assertEqual(len(planning_calls), 2)  # 只有第一次运行做过规划/生成 / planning and generation happened once only


class ResidencyTests(FixFlowCase):
    def test_the_local_model_is_released_when_the_router_switches_to_the_cloud_and_when_the_task_ends(self):
        world = World()
        local = Model('small', 'local', world, fixes_impl=False, fixes_test=False)
        cloud = Model('big', 'cloud', world)
        store, job = self.run_task(local, cloud, world)
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
        log = world.log
        first_cloud = next(i for i, p in enumerate(log) if p.endswith('big'))
        self.assertIn('unload small', log[:first_cloud], '切到云端之前必须先释放本地模型 / unload the local model before the cloud call')
        self.assertFalse(local.loaded)
        self.assertTrue(self.events(store, 'models_released'))  # 释放被记录在案 / the release is on record

    def test_models_are_released_even_when_the_task_fails_or_waits(self):
        world = World()
        local = Model('small', 'local', world)
        cloud = Model('big', 'cloud', world)
        store, jobs, router = self.build(local, cloud, world, request={'goal': '用 Python 写一个脚本'})  # 被预检拦下 / blocked by triage
        router.provider('local')  # 由这个任务的路由器管理的提供方 / a provider managed by this task's router
        local.loaded = True
        WorkflowCoordinator(store, WorldExecutor(world), None, jobs['job'], router=router).run()
        self.assertFalse(local.loaded)  # 即使任务一开始就被拦下，也不会留下模型 / nothing is left behind even when blocked


class ProgressExecutor(FakeExecutor):
    """每次验证都有 world.remaining 条互不相同的实现编译错误。 Each verification reports world.remaining distinct implementation compile errors."""
    def __init__(self, world):
        super().__init__(exit_code=1)
        self.world = world

    def execute(self, request, workspace, cancelled):
        result = super().execute(request, workspace, cancelled)
        if request['operation'] == 'go_test':
            self.world.verifications += 1
            lines = [f'./internal/app/app.go:{10 + n}:1: undefined: name{n}' for n in range(self.world.remaining)]
            result['exit_code'] = 1 if lines else 0
            result['stdout'] = chr(10).join(lines)
        else:
            result['exit_code'] = 0
            result['stdout'] = ''
        return result


class ProgressModel(Model):
    def __init__(self, name, kind, world, step):
        super().__init__(name, kind, world)
        self.step = step

    def respond(self, context):
        if context['purpose'] == 'project_repair':
            self.world.remaining = max(0, self.world.remaining - self.step)
        return super().respond(context)


class ProgressRoundsTests(FixFlowCase):
    def run_progress(self, start, step):
        world = World()
        world.remaining = start
        local, cloud = ProgressModel('local-m', 'local', world, step), ProgressModel('cloud-m', 'cloud', world, step)
        store, jobs, router = self.build(local, cloud, world, policy={'diagnose': False, 'stuck_after': 10})
        with patch('masa.intelligence.repair_context.build_repair_context', lambda s, e, r, f, ev, fb: (f, None)):
            WorkflowCoordinator(store, ProgressExecutor(world), None, jobs['job'], router=router).run()
        return world, jobs['job'], store

    def test_a_task_that_keeps_converging_gets_more_rounds_than_the_base_limit(self):
        # 真实任务（文本统计 CLI）在第 4 轮时只差 1 个断言，却被固定上限截断。
        # A real task was one assertion away when the fixed limit cut it off.
        world, job, store = self.run_progress(start=7, step=1)
        self.assertEqual(world.remaining, 0)
        self.assertGreater(world.verifications, 5)
        self.assertGreaterEqual(len(self.events(store, 'rounds_extended')), 1)

    def test_later_rounds_receive_a_ledger_based_summary_of_what_earlier_rounds_did(self):
        world = World()
        world.remaining = 4
        local, cloud = ProgressModel('local-m', 'local', world, 1), ProgressModel('cloud-m', 'cloud', world, 1)
        store, jobs, router = self.build(local, cloud, world, policy={'diagnose': False, 'stuck_after': 10})
        with patch('masa.intelligence.repair_context.build_repair_context', lambda s, e, r, f, ev, fb: (f, None)):
            WorkflowCoordinator(store, ProgressExecutor(world), None, jobs['job'], router=router).run()
        feedback = [str(c.get('feedback', '')) for m in (local, cloud) for c in m.contexts if c['purpose'] == 'project_repair']
        self.assertFalse(any('Previous repair rounds' in f for f in feedback[:1]))
        later = [f for f in feedback if 'Previous repair rounds' in f]
        self.assertTrue(later)
        self.assertTrue(any('unresolved 4 -> 3 (improved)' in f for f in later))

    def test_a_task_that_makes_no_progress_still_stops_at_the_base_limit(self):
        world, job, store = self.run_progress(start=3, step=0)
        self.assertEqual(world.verifications, 5)
        self.assertIn('repair limit', job.get('note') or '')
        self.assertEqual(self.events(store, 'rounds_extended'), [])


WRONG_EXPECTATION = 'internal/app/app_test.go:12: bytes = 20, want 19'


class WrongTestExecutor(FakeExecutor):
    """实现是对的，只有测试的一个期望值算错；测试修订之后才通过。The implementation is right; one expected value in the test is wrong until the tests are revised."""
    def __init__(self, world):
        super().__init__(exit_code=1)
        self.world = world

    def execute(self, request, workspace, cancelled):
        result = super().execute(request, workspace, cancelled)
        if request['operation'] == 'go_test':
            self.world.verifications += 1
            result['exit_code'] = 0 if self.world.test_ok else 1
            result['stdout'] = '' if self.world.test_ok else WRONG_EXPECTATION
        else:
            result['exit_code'] = 0
            result['stdout'] = ''
        return result


class UnchangedImplementationModel(Model):
    """认为实现没错：修复实现时原样返回文件。 Believes the implementation is right: returns it unchanged."""
    def respond(self, context):
        if context['purpose'] == 'project_repair':
            self.contexts.append(context)
            self.world.log.append(f"project_repair {self.name}")
            self.calls += 1
            return {IMPL: context['original_files'][IMPL]}
        return super().respond(context)


class FlipToTestsTests(FixFlowCase):
    def test_when_the_strongest_model_will_not_change_the_implementation_the_tests_are_revised_once(self):
        world = World()
        local, cloud = UnchangedImplementationModel('local-m', 'local', world), UnchangedImplementationModel('cloud-m', 'cloud', world)
        store, jobs, router = self.build(local, cloud, world, policy={'diagnose': False, 'prefer_highest_roles': ['project_diagnoser', 'project_test_revision']})  # 与默认策略一致 / matches the default policy
        with patch('masa.intelligence.repair_context.build_repair_context', lambda s, e, r, f, ev, fb: (f, None)):
            WorkflowCoordinator(store, WrongTestExecutor(world), None, jobs['job'], router=router).run()
        job = jobs['job']
        self.assertTrue(world.test_ok)  # 测试被修订，任务通过 / the test was revised and the task passed
        self.assertNotIn('halted', job.get('note') or '')
        nodes = [step['node'] for step in self.trace(store)]
        self.assertIn('revise', nodes)
        self.assertLess(nodes.index('repair'), nodes.index('revise'))
        revised = [c for c in cloud.contexts + local.contexts if c['purpose'] == 'project_test_revision']
        self.assertTrue(any('recompute' in str(c.get('feedback', '')) for c in revised))
        self.assertTrue(any(c['purpose'] == 'project_test_revision' for c in cloud.contexts))  # 最高等级改测试 / the top level revises tests


if __name__ == '__main__':
    unittest.main()
