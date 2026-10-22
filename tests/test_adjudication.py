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

    def test_an_implementation_verdict_repairs_the_implementation(self):
        impl = {'owner': 'implementation', 'rationale': 'r', 'implementation_instructions': 'fix the code', 'test_instructions': '', 'expectation_checks': []}
        world, store, job, local, cloud = self.run_assert(cloud_diagnosis=impl)
        self.assertEqual(self.nodes(store)[:2], [('classify', 'diagnose'), ('diagnose', 'repair')])


if __name__ == '__main__':
    unittest.main()
