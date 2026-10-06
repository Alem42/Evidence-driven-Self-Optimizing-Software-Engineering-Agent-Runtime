"""P2 指挥者：开关关闭时行为不变；开启后单候选走快路径（零调用）、有歧义时才问、非法/崩溃/超限都回退到规则并留痕。全部用脚本化假模型。
P2 Conductor: unchanged when off; on, a single obvious candidate takes the fast path (zero calls), it is asked only on ambiguity, and illegal / crashing / over-limit
answers fall back to the rules and leave a trace. Everything runs on scripted fake models."""
import unittest

from masa.application import conductor, flow
from masa.application.flow import FlowError
from masa.application.routing import DEFAULT_POLICY, validate_policy
from masa.application.workflows import FIX_V1
from masa.domain.models import MasaError
from masa.roles import registry

from test_fix_flow import FixFlowCase, Model, World, WorldExecutor, TEST

ASSERT_LINE = '    internal/app/app_test.go:12: got 3, want 4'
HIGH = ['project_diagnoser', 'project_conductor', 'test_skeptic', 'code_reviewer']
ON = {'conductor': True, 'prefer_highest_roles': HIGH}
WRONG = {'cases': [{'case': 'add 1+2', 'requirement_says': '3', 'test_expects': '4', 'matches': False}], 'verdict': 'tests_wrong', 'instructions': '把 add 1+2 的期望改成 3'}
GOOD = {'cases': [], 'verdict': 'tests_ok', 'instructions': ''}


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
    """在 Model 之上增加指挥者/怀疑者/审阅者的脚本。script 的值可以是字典、字典列表（依次返回）或会抛异常的函数。"""

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


class ConductorCase(FixFlowCase):
    def run_assert(self, local_script=None, cloud_script=None, policy=None, cloud_diagnosis=None):
        world = AssertWorld()
        local = Scripted('small', 'local', world, script=local_script)
        cloud = Scripted('big', 'cloud', world, script=cloud_script, diagnosis=cloud_diagnosis or {
            'owner': 'test', 'rationale': '期望写错', 'implementation_instructions': '', 'test_instructions': '改期望', 'expectation_checks': []})
        store, jobs, router = self.build(local, cloud, world, policy=policy)
        from unittest.mock import patch
        from masa.application.coordinator import WorkflowCoordinator
        with patch('masa.intelligence.repair_context.build_repair_context', lambda s, e, r, f, ev, fb: (f, None)):
            WorkflowCoordinator(store, AssertExecutor(world), None, jobs['job'], router=router).run()
        return world, store, jobs['job'], local, cloud

    def nodes(self, store):
        return [(s['node'], s['to']) for s in self.trace(store)]


class SwitchOffTests(ConductorCase):
    def test_off_by_default_nothing_changes(self):
        """默认关闭：路径、模型调用与改动前一致，没有任何指挥者事件。 Off by default: same path and calls as before, no conductor events."""
        self.assertFalse(DEFAULT_POLICY['conductor'])
        world, store, job, local, cloud = self.run_assert()
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
        self.assertEqual(self.nodes(store)[:2], [('classify', 'diagnose'), ('diagnose', 'revise')])  # 规则：断言失败先诊断 / the rules: diagnose first
        self.assertFalse([p for p in world.log if p.split()[0] in ('project_conductor', 'test_skeptic', 'code_reviewer')])
        for kind in ('conductor_decided', 'conductor_rejected', 'code_review'):
            self.assertEqual(self.events(store, kind), [])

    def test_on_but_a_single_obvious_candidate_costs_zero_calls(self):
        """开启后，归属明确（实现有语法错误 + 测试有 import cycle）走快路径，零指挥者调用，路径与关闭时相同。"""
        world = World()
        local, cloud = Scripted('small', 'local', world), Scripted('big', 'cloud', world)
        store, job = self.run_task(local, cloud, world, policy=ON)
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
        self.assertEqual([(s['node'], s['to']) for s in self.trace(store)], [('classify', 'repair'), ('repair', 'drafted'), ('classify', 'revise'), ('revise', 'drafted')])
        self.assertFalse([p for p in world.log if p.startswith('project_conductor')])
        self.assertEqual(self.events(store, 'conductor_decided'), [])


class OnTests(ConductorCase):
    def test_ambiguity_asks_the_conductor_and_its_choice_is_followed(self):
        """第一轮由规则处理（诊断→修订，零指挥者调用）；第二轮补丁没有进展（停滞）：问指挥者；它选 skeptic → 怀疑者逐条核对 → 判测试有误 → 修订测试 → 通过。事件留痕。"""
        choice = {'next': 'skeptic', 'reason': '只剩断言失败，先核对期望', 'brief': '重点看 add 的期望'}
        world, store, job, local, cloud = self.run_assert(cloud_script={'project_conductor': choice, 'test_skeptic': WRONG}, policy=ON)
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
        self.assertEqual(self.nodes(store)[:3], [('classify', 'diagnose'), ('diagnose', 'revise'), ('revise', 'drafted')])  # 第一轮：规则，没有指挥者 / round 1: the rules, no conductor
        self.assertEqual(self.nodes(store)[3:7], [('classify', 'conduct'), ('conduct', 'skeptic'), ('skeptic', 'revise'), ('revise', 'drafted')])
        decided = self.events(store, 'conductor_decided')
        self.assertEqual([d['next'] for d in decided], ['skeptic'])
        self.assertEqual(decided[0]['calls'], 1)
        self.assertEqual(self.events(store, 'skeptic_verdict')[0]['verdict'], 'tests_wrong')
        self.assertEqual(sum(1 for p in world.log if p.startswith('project_diagnoser')), 1)  # 只有第一轮规则诊断过 / only the rule-driven diagnosis of round 1
        # 指挥者的指示和怀疑者的结论都传到了修订测试的调用里。 Both reach the revision call.
        revisions = [c for c in cloud.contexts + local.contexts if c['purpose'] == 'project_test_revision']
        self.assertTrue(any('把 add 1+2 的期望改成 3' in c['feedback'] for c in revisions))
        self.assertTrue(any('重点看 add 的期望' in c['feedback'] for c in revisions))

    def test_the_briefing_is_built_by_code_and_contains_no_source(self):
        choice = {'next': 'repair', 'reason': 'r', 'brief': ''}
        world, store, job, local, cloud = self.run_assert(cloud_script={'project_conductor': choice}, policy=ON)
        context = next(c for c in cloud.contexts if c['purpose'] == 'project_conductor')
        brief = context['briefing']
        self.assertEqual(set(brief), {'goal', 'acceptance', 'failure', 'earlier_rounds', 'state', 'budget_left', 'candidates', 'earlier_decisions'})
        self.assertEqual({c['id'] for c in brief['candidates']}, {'repair', 'revise', 'diagnose', 'skeptic'})
        self.assertTrue(all(c['description'] for c in brief['candidates']))
        self.assertNotIn('package app', str(brief))  # 没有源码 / no source code
        self.assertLess(len(str(brief)), 6000)

    def test_an_illegal_proposal_is_rejected_traced_and_the_rules_take_over(self):
        for bad in ({'next': 'launch_missiles', 'reason': 'r', 'brief': ''}, {'next': 'rewrite', 'reason': 'r', 'brief': ''},  # 越界 / 现在不是候选 (out of range / not a candidate now)
                    {'next': 'halt', 'reason': 'r', 'brief': ''}):
            with self.subTest(bad['next']):
                world, store, job, local, cloud = self.run_assert(cloud_script={'project_conductor': bad}, policy=ON)
                self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
                rejected = self.events(store, 'conductor_rejected')
                self.assertEqual(len(rejected), 1)
                self.assertTrue(rejected[0]['reason'].startswith('not_a_candidate'))
                self.assertEqual(rejected[0]['fallback'], 'rules')
                self.assertEqual(self.events(store, 'conductor_decided'), [])
                self.assertEqual(self.nodes(store)[3:6], [('classify', 'conduct'), ('conduct', 'classify'), ('classify', 'diagnose')])  # 回到规则（同一失败重复 → 再诊断）/ back to the rules (the failure repeats, so diagnose again)

    def test_a_malformed_or_crashing_conductor_never_stops_the_repair(self):
        def crash(context):
            raise MasaError('conductor timed out')
        for name, answer in (('crash', crash), ('extra field', {'next': 'repair', 'reason': 'r', 'brief': '', 'x': 1}), ('not an object', ['repair'])):
            with self.subTest(name):
                world, store, job, local, cloud = self.run_assert(cloud_script={'project_conductor': answer}, policy=ON)
                self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
                self.assertEqual(len(self.events(store, 'conductor_rejected')), 1)
                self.assertEqual(self.events(store, 'conductor_decided'), [])

    def test_the_call_limit_is_enforced(self):
        choice = {'next': 'repair', 'reason': 'r', 'brief': ''}
        world, store, job, local, cloud = self.run_assert(cloud_script={'project_conductor': choice}, policy={**ON, 'conductor_max_calls': 1})
        self.assertLessEqual(sum(1 for p in world.log if p.startswith('project_conductor')), 1)
        self.assertTrue(conductor.due({'conductor': True, 'conductor_max_calls': 2}, {'stall': 1}, 1, False, ['repair', 'revise']))
        self.assertFalse(conductor.due({'conductor': True, 'conductor_max_calls': 2}, {'stall': 1}, 2, False, ['repair', 'revise']))
        self.assertFalse(conductor.due({'conductor': True, 'conductor_max_calls': 2}, {'primary': 'ambiguous'}, 0, False, ['repair', 'revise']))  # 归属不明本身不再触发 / ownership unclear alone no longer triggers

    def test_cascade_starts_low_and_escalates_after_an_illegal_proposal(self):
        bad = {'next': 'nonsense', 'reason': 'r', 'brief': ''}
        good = {'next': 'revise', 'reason': '期望写错', 'brief': ''}
        world, store, job, local, cloud = self.run_assert(local_script={'project_conductor': bad}, cloud_script={'project_conductor': good},
                                                          policy={**ON, 'conductor_cascade': True})
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
        asked = [p for p in world.log if p.startswith('project_conductor')]
        self.assertEqual(asked, ['project_conductor small', 'project_conductor big'])
        decided = self.events(store, 'conductor_decided')
        self.assertEqual([(d['next'], d['escalated']) for d in decided], [('revise', True)])
        self.assertEqual(len(self.events(store, 'conductor_rejected')), 1)

    def test_without_cascade_the_conductor_goes_straight_to_the_top_level(self):
        good = {'next': 'revise', 'reason': 'r', 'brief': ''}
        world, store, job, local, cloud = self.run_assert(local_script={'project_conductor': good}, cloud_script={'project_conductor': good}, policy=ON)
        self.assertEqual([p for p in world.log if p.startswith('project_conductor')], ['project_conductor big'])


class ReviewerTests(ConductorCase):
    REVIEW = {'risks': [{'file': 'internal/app/app.go', 'risk': '空输入未处理', 'severity': 'medium'}], 'summary': '边界较薄'}

    def test_review_runs_only_when_on_and_never_changes_the_verdict(self):
        choice = {'next': 'revise', 'reason': 'r', 'brief': ''}
        world, store, job, local, cloud = self.run_assert(cloud_script={'project_conductor': choice, 'code_reviewer': self.REVIEW}, policy=ON)
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded')
        review = self.events(store, 'code_review')
        self.assertEqual(len(review), 1)
        self.assertIn('空输入未处理', review[0]['risks'][0])
        world, store, job, local, cloud = self.run_assert(cloud_script={'code_reviewer': self.REVIEW})  # 关闭 / off
        self.assertEqual(self.events(store, 'code_review'), [])
        self.assertFalse([p for p in world.log if p.startswith('code_reviewer')])

    def test_a_failing_reviewer_leaves_the_passed_result_untouched(self):
        def crash(context):
            raise MasaError('reviewer down')
        choice = {'next': 'revise', 'reason': 'r', 'brief': ''}
        world, store, job, local, cloud = self.run_assert(cloud_script={'project_conductor': choice, 'code_reviewer': crash}, policy=ON)
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded')
        self.assertEqual(self.events(store, 'code_review'), [])
        self.assertEqual(len(self.events(store, 'code_review_skipped')), 1)


class DefinitionTests(unittest.TestCase):
    def test_policy_defaults_and_bounds(self):
        policy = validate_policy({})
        self.assertEqual((policy['conductor'], policy['conductor_max_calls'], policy['conductor_cascade']), (False, 6, False))
        self.assertTrue(validate_policy({'conductor': True, 'conductor_max_calls': 3})['conductor'])
        for bad in ({'conductor': 'yes'}, {'conductor_max_calls': 0}, {'conductor_max_calls': 13}, {'conductor_cascade': 1}):
            with self.assertRaises(MasaError):
                validate_policy(bad)

    def test_the_new_roles_are_pure_data_and_their_guards_exist(self):
        for role in ('project_conductor', 'test_skeptic', 'code_reviewer'):
            spec = registry.get(role)
            self.assertIsNotNone(spec, role)
            self.assertEqual(spec.permissions['writes'], 'none')  # 全部只读 / all read-only
            self.assertEqual(spec.validator, 'json_schema')  # 不需要写 Python 校验器 / no Python validator needed
        conductor.check_optional_roles()
        self.assertEqual(registry.get('test_skeptic').when['guard'], 'only_assertions')

    def test_llm_choice_nodes_are_validated(self):
        base = {'id': 'x', 'version': 1, 'entry': 'a', 'nodes': {'a': {'action': 'a', 'kind': 'llm_choice', 'candidates': ['b', 'c']}, 'b': {'end': 'b'}, 'c': {'end': 'c'}},
                'edges': [{'from': 'a', 'to': 'b', 'when': 'chosen', 'params': {'node': 'b'}}, {'from': 'a', 'to': 'c', 'when': 'chosen', 'params': {'node': 'c'}}, {'from': 'a', 'to': 'b'}]}
        flow.validate(base)
        for broken in (dict(base, nodes={**base['nodes'], 'a': {'action': 'a', 'kind': 'llm_choice', 'candidates': ['b', 'zzz']}}),  # 候选不存在 / unknown candidate
                       dict(base, nodes={**base['nodes'], 'a': {'action': 'a', 'kind': 'llm_choice', 'candidates': ['b']}}),  # 少于两个候选 / fewer than two
                       dict(base, edges=[base['edges'][0], base['edges'][2]])):  # 缺少 chosen 边 / missing chosen edge
            with self.assertRaises(FlowError):
                flow.validate(broken)

    def test_fix_v1_still_validates_and_the_conductor_edge_comes_first(self):
        flow.validate(FIX_V1)
        classify_edges = [e for e in FIX_V1['edges'] if e['from'] == 'classify']
        self.assertEqual(classify_edges[0]['when'], 'conductor_due')

    def test_candidate_set_and_briefing_helpers_are_pure(self):
        flags = {'can_diagnose': True, 'diagnosed': False, 'rewrite_possible': False, 'skeptic_possible': True, 'halt_possible': False}
        self.assertEqual(conductor.candidate_nodes(flags), ['repair', 'revise', 'diagnose', 'skeptic'])
        # 真实回归：指挥者选 halt 终止了一个本来可能通过的任务；现在 halt 永远不是候选。 Real regression: the conductor halted a task that might have passed; halt is never a candidate now.
        self.assertEqual(conductor.candidate_nodes({**flags, 'diagnosed': True, 'halt_possible': True}), ['repair', 'revise', 'skeptic'])
        # 真实评测：第一轮的“归属不明”不触发（规则和指挥者选的一样，白花钱）；规则试过没进展才触发。 Real evaluation: round-1 ambiguity does not trigger; a rules attempt without progress does.
        self.assertFalse(conductor.is_ambiguous({'primary': 'ambiguous'}))
        self.assertTrue(conductor.is_ambiguous({'primary': 'ambiguous', 'repeats': 2}))
        self.assertTrue(conductor.is_ambiguous({'primary': 'implementation', 'stall': 1}))
        self.assertFalse(conductor.is_ambiguous({'primary': 'implementation'}))
        # 真实回归：reason 超过 200 字时整个提议曾被拒绝；现在选择保留、文字截断。 Real regression: an over-long reason used to reject the whole proposal; now the choice is kept and the text truncated.
        kept = conductor.check_choice({'next': 'repair', 'reason': 'r' * 450, 'brief': 'y' * 301}, ['repair', 'revise'])[0]
        self.assertEqual((kept['next'], len(kept['reason']), len(kept['brief'])), ('repair', 200, 300))
        self.assertEqual(conductor.check_choice({'next': 'repair', 'reason': ' x ', 'brief': ''}, ['repair', 'revise'])[0]['reason'], 'x')


if __name__ == '__main__':
    unittest.main()
