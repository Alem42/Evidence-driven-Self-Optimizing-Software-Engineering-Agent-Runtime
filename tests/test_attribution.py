"""归因修复（H1–H5）的回归测试，每一条都来自 LCS 那次真实运行的账本。全部零 token。
Regression tests for the attribution fixes (H1-H5), each taken from the ledger of the real LCS run. Zero tokens."""
import types
import unittest

from masa.agents.protocol import validate_response
from masa.application.console_jobs import JobsMixin
from masa.application.v2.workspace import WorkerWorkspace
from masa.domain.models import MasaError
from masa.domain.proposals import acceptance_problem, validate_diagnosis
from test_python_pack import SPEC


class DiagnosisTests(unittest.TestCase):
    """H4：真实运行里 Pro 诊断因 requirement_says 超过 300 字符整条被拒，白花一次调用。 H4: a Pro diagnosis was rejected whole because requirement_says exceeded 300 characters."""

    def test_overlong_fields_are_truncated_not_rejected(self):
        check = {'case': 'abc', 'requirement_says': 'x' * 301, 'test_expects': '1', 'matches': False}
        out = validate_diagnosis({'owner': 'test', 'rationale': 'r' * 2500, 'implementation_instructions': '', 'test_instructions': 'fix it', 'expectation_checks': [check]})
        self.assertEqual((len(out['expectation_checks'][0]['requirement_says']), len(out['rationale'])), (300, 2000))
        self.assertEqual(out['expectation_checks'][0]['test_expects'], '1')  # 其余字段原样保留 / the other fields are kept

    def test_a_malformed_diagnosis_is_still_rejected(self):
        with self.assertRaises(MasaError):
            validate_diagnosis({'owner': 'nobody', 'rationale': 'r', 'implementation_instructions': '', 'test_instructions': ''})


class AcceptanceTests(unittest.TestCase):
    """H5：LCS 的验收第 2 条以“……最长公共子序列长度 ”结尾，没有数值。 H5: acceptance item 2 of the LCS run ended without the value."""
    CUT = "标准输入内容为 'a b c\nd e f\n' 时，两行包含空格，程序输出最长公共子序列长度 "

    def test_a_cut_off_item_is_flagged_and_complete_ones_are_not(self):
        self.assertIn('item 1', acceptance_problem(['输入为空时输出 0。', self.CUT]))
        for fine in ('Prints the sum of the integers on stdin.', '输入 "a b" 时输出 3。', 'An empty input prints 0 and exits with status 0', '空输入输出错误信息'):
            self.assertIsNone(acceptance_problem([fine]), fine)
        self.assertIn('item 0', acceptance_problem(['The total is:']))
        self.assertIn('item 0', acceptance_problem(['The result equals']))

    def test_the_planner_contract_rejects_a_cut_off_acceptance_item(self):
        spec = {**SPEC, 'acceptance': [*SPEC['acceptance'], self.CUT]}
        with self.assertRaises(MasaError) as caught:
            validate_response({'purpose': 'project_planner', 'language': 'python'}, spec, '')
        self.assertIn('looks cut off', str(caught.exception))
        self.assertEqual(validate_response({'purpose': 'project_planner', 'language': 'python'}, SPEC, '')['module'], SPEC['module'])


class PolicyTests(unittest.TestCase):
    """H1：v2repair 默认开用例审计与断言裁判；V1 不变；请求里显式给的值不改。 H1: v2repair turns the audit and the referee on by default; V1 is unchanged; explicit values stay."""

    def policy(self, body):
        stub = types.SimpleNamespace(_v2_policy=JobsMixin._v2_policy, settings=types.SimpleNamespace(ready_profiles=lambda: []))
        return JobsMixin._runtime_policy(stub, body)

    def test_defaults_per_runtime(self):
        self.assertEqual({k: v for k, v in self.policy({'runtime': 'v2repair'}).items() if k in ('test_audit', 'assertion_referee')}, {'test_audit': True, 'assertion_referee': True})
        self.assertIsNone(self.policy({}))
        self.assertIsNone(self.policy({'runtime': 'v1'}))
        self.assertEqual(self.policy({'runtime': 'v2'})['memory_strategy'], 'auto')  # 工人循环按窗口自动选记忆策略 / the worker loops pick their memory tier from the window
        self.assertEqual(self.policy({'runtime': 'v2repair', 'policy': {'memory_strategy': 'window'}})['memory_strategy'], 'window')
        self.assertFalse(self.policy({'runtime': 'v2repair', 'policy': {'test_audit': False}})['test_audit'])


class StableActualValueTests(unittest.TestCase):
    """H2 / S1：实际值连续 3 次相同且其间改过至少 2 次，才算“实际值稳定”。 S1: identical actual values in 3 observations with at least 2 edits in between."""

    def space(self, exact, marks):
        space = WorkerWorkspace.__new__(WorkerWorkspace)
        space.exact, space.edit_marks = exact, marks
        return space

    def test_the_signal_needs_three_identical_observations_and_two_edits(self):
        same = ('lcs.go|got 2, want 1',)
        self.assertTrue(self.space([same, same, same], [1, 2, 3]).actual_stable())
        self.assertFalse(self.space([same, same], [1, 2]).actual_stable())  # 观察不够 / too few observations
        self.assertFalse(self.space([same, same, same], [1, 1, 2]).actual_stable())  # 其间只改过 1 次 / only one edit in between
        self.assertFalse(self.space([same, ('lcs.go|got 3, want 1',), same], [1, 2, 3]).actual_stable())  # 实际值变过 / the actual value moved


class NoopLimitTests(unittest.TestCase):
    """v2repair 的工人修复连续两次“卡住且没有改善”就转去修订测试；V1 默认流程仍是三次。 Two stuck worker repairs flip to test revision in v2repair; the V1 default flow keeps three."""

    def test_the_limit_is_two_for_v2repair_and_three_by_default(self):
        from masa.application.orchestration import workflows  # noqa: F401  (注册 guard / registers the guards)
        from masa.application.orchestration.flow import GUARDS
        stuck_twice = {'noop': True, 'noop_count': 2, 'can_diagnose': False}
        self.assertTrue(GUARDS['noop_retry']({**stuck_twice}, {}))  # 默认：还可以再试一次 / default: one more try
        self.assertFalse(GUARDS['flip_to_tests']({**stuck_twice}, {}))
        self.assertFalse(GUARDS['noop_retry']({**stuck_twice, 'noop_limit': 2}, {}))
        self.assertTrue(GUARDS['flip_to_tests']({**stuck_twice, 'noop_limit': 2}, {}))


if __name__ == '__main__':
    unittest.main()
