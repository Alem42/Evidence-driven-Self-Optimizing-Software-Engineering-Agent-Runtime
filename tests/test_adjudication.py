"""裁决入口（Diagnoser）的走向：测试期望有错时先改测试；独立证据直接改测试；没有证据不改测试。零 token。
The adjudication entry: a wrong expectation is corrected first; independent evidence revises the tests at once; without evidence the tests stay. Zero tokens."""
import unittest

from assert_flow import AssertCase


class AdjudicationTests(AssertCase):
    def test_a_diagnosis_of_both_corrects_the_test_before_the_implementation(self):
        """测试期望有错又有实现缺陷：先改测试，工人才不会为迎合错的测试去改实现（EVIDENCE E4）。"""
        both = {'owner': 'both', 'rationale': 'r', 'implementation_instructions': 'fix the code', 'test_instructions': 'fix the expectation', 'expectation_checks': []}
        world, store, job, local, cloud = self.run_assert(cloud_diagnosis=both)
        path = self.nodes(store)
        self.assertEqual(path[:2], [('classify', 'diagnose'), ('diagnose', 'revise')], path)  # 断言失败的第一轮就请裁决者，裁决后先改测试 / the first assertion failure is adjudicated, then the test is corrected first
        self.assertNotIn(('diagnose', 'repair'), path)
        self.assertEqual({e['basis'] for e in self.events(store, 'test_revision_basis')}, {'diagnosis'})  # 改测试的依据写进了账本 / the basis of the revision is in the ledger

    def test_an_implementation_verdict_repairs_the_implementation(self):
        impl = {'owner': 'implementation', 'rationale': 'r', 'implementation_instructions': 'fix the code', 'test_instructions': '', 'expectation_checks': []}
        world, store, job, local, cloud = self.run_assert(cloud_diagnosis=impl)
        self.assertEqual(self.nodes(store)[:2], [('classify', 'diagnose'), ('diagnose', 'repair')])


class DiagnoserInputTests(AssertCase):
    """Diagnoser 的输入：它调用的实现源码、Architect 当初的用例与假设、它自己以前的裁决（P0 复盘 §5.2：“进入 Diagnoser 的方式遮挡了 Planner 和 Tester”）。
    What the Diagnoser sees: the implementation under test, the cases and assumptions the Architect wrote, its own earlier rulings."""

    def test_the_diagnoser_sees_the_architect_materials_the_code_under_test_and_its_earlier_ruling(self):
        import copy
        from test_project_plan import CHECKS, SPEC
        checks = copy.deepcopy(CHECKS)
        checks[0]['cases'] = [{'name': 'counts', 'input': 'two words', 'expected': 'stdout 2', 'level': 'cli'}]
        impl = {'owner': 'implementation', 'rationale': 'first ruling', 'implementation_instructions': 'fix it', 'test_instructions': '', 'expectation_checks': []}
        world, store, job, local, cloud = self.run_assert(local_script={'project_architect': {'spec': SPEC, 'checks': checks, 'assumptions': ['empty input counts 0']}}, cloud_diagnosis=impl)
        contexts = [c for c in cloud.contexts if c['purpose'] == 'project_diagnoser']
        self.assertGreaterEqual(len(contexts), 1)
        first = contexts[0]
        self.assertEqual(first['architect_cases'], [{'name': 'counts', 'input': 'two words', 'expected': 'stdout 2'}])
        self.assertEqual(first['assumptions'], ['empty input counts 0'])
        self.assertIn('internal/app/app_test.go', first['files'])
        self.assertIn('internal/app/app.go', first['files'])  # 被测的实现也给了 / the implementation under test is supplied too
        self.assertNotIn('previous_rulings', first)
        if len(contexts) > 1:
            self.assertEqual(contexts[1]['previous_rulings'][0]['rationale'], 'first ruling')


if __name__ == '__main__':
    unittest.main()
