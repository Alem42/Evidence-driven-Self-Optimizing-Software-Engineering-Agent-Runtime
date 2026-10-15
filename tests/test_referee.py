"""断言裁判：解析失败断言、打乱成 A/B 的二选一、多数表决、对 Diagnoser 和修复指令的证据；默认关闭；以及“测试修订没动失败断言”的提示。全部用假模型。
Assertion referee: parsing failing assertions, the shuffled A/B forced choice, the majority vote, the evidence for the Diagnoser and the fix instructions; off by default; plus the hint when a test revision changed nothing."""
import unittest

from masa.application.checks.referee import case_for, evidence_text, parse_assertion, public, tally, source_of_test_function
from masa.application.orchestration.routing import validate_policy

from test_conductor import ASSERT_LINE, ConductorCase, Scripted  # noqa: F401


class ParseTests(unittest.TestCase):
    def test_common_go_failure_lines_are_parsed_and_unparseable_ones_are_not(self):
        a = parse_assertion('Apply("transfer alice bob 1000") = "error: unknown account", want "error: insufficient"')
        self.assertEqual((a['call'], a['got'], a['want']), ('Apply("transfer alice bob 1000")', '"error: unknown account"', '"error: insufficient"'))
        b = parse_assertion('got 3, want 4')
        self.assertEqual((b['got'], b['want']), ('3', '4'))
        self.assertEqual(parse_assertion('stdout = "6\\n", want "8\\n"')['call'], 'stdout')
        for bad in ('panic: runtime error', 'got 3, want 3', 'something else entirely', '', 'got , want 4'):
            self.assertIsNone(parse_assertion(bad), bad)

    def test_the_source_of_test_function_is_cut_around_the_failing_line(self):
        source = 'package a\n\nfunc TestOne(t *testing.T) {\n\tx := 1\n}\n\nfunc TestTwo(t *testing.T) {\n\ts := Open()\n\tif s.Get() != 4 {\n\t\tt.Fatal()\n\t}\n}\n'
        text = source_of_test_function({'internal/a/a_test.go': source}, 'a_test.go', 9)
        self.assertIn('func TestTwo', text)
        self.assertIn('s := Open()', text)
        self.assertNotIn('TestOne', text)
        self.assertEqual(source_of_test_function({'x.go': source}, 'a_test.go', 9), '')
        self.assertEqual(source_of_test_function({'a_test.go': source}, 'a_test.go', 999), '')


class ChoiceTests(unittest.TestCase):
    ITEM = {'message': 'got 3, want 4', 'path': 'a_test.go', 'line': 9}

    def test_the_order_is_deterministic_per_message_and_roughly_balanced(self):
        first = case_for(0, self.ITEM, {})
        self.assertEqual(first, case_for(0, self.ITEM, {}))  # 重放得到同样的顺序 / a replay sees the same order
        self.assertEqual(set(first['options'].values()), {'3', '4'})
        self.assertNotIn('_a_is', public(first))  # 发给模型的内容里没有“哪个是实现给的” / the model never sees which one the implementation gave
        sides = {case_for(0, {**self.ITEM, 'message': f'got {i}, want {i + 1}'}, {})['_a_is'] for i in range(40)}
        self.assertEqual(sides, {'got', 'want'})  # 两种顺序都会出现 / both orders occur

    def test_the_majority_decides_and_a_tie_or_neither_is_unclear(self):
        case = case_for(0, self.ITEM, {})
        a_is_got = case['_a_is'] == 'got'
        pick_got = 'A' if a_is_got else 'B'
        pick_want = 'B' if a_is_got else 'A'
        self.assertEqual(tally([case], [{0: pick_got}, {0: pick_got}, {0: pick_want}])[0]['verdict'], 'test_wrong')
        self.assertEqual(tally([case], [{0: pick_want}, {0: pick_want}, None])[0]['verdict'], 'implementation_wrong')
        self.assertEqual(tally([case], [{0: pick_got}, {0: pick_want}, {0: 'neither'}])[0]['verdict'], 'unclear')
        self.assertEqual(tally([case], [{0: 'neither'}] * 3)[0]['verdict'], 'unclear')
        self.assertEqual(tally([case], [None, None, None])[0]['verdict'], 'unclear')

    def test_the_evidence_text_names_the_wrong_side_and_is_empty_when_unclear(self):
        case = case_for(0, self.ITEM, {})
        pick_got = 'A' if case['_a_is'] == 'got' else 'B'
        text = evidence_text([case], tally([case], [{0: pick_got}] * 3))
        self.assertIn('WRONG', text)
        self.assertIn("test's expected value (4)", text)
        self.assertEqual(evidence_text([case], tally([case], [{0: 'neither'}] * 3)), '')


class FlowTests(ConductorCase):
    def referee_script(self, side):
        """裁判脚本：按选项里的值选字母（side='got' 选实现给的 3，'want' 选测试写的 4）。 Pick the letter whose value is the implementation's 3 (side='got') or the test's 4."""
        value = '3' if side == 'got' else '4'

        def answer(context):
            return {'answers': [{'index': c['index'], 'choice': 'A' if c['options']['A'] == value else 'B'} for c in context['cases']]}
        return answer

    def test_off_by_default_nothing_is_called(self):
        self.assertFalse(validate_policy({})['assertion_referee'])
        world, store, job, local, cloud = self.run_assert()
        self.assertEqual(self.events(store, 'assertion_referee'), [])
        self.assertFalse([p for p in world.log if p.startswith('assertion_referee')])

    def test_when_on_the_votes_reach_the_diagnoser_and_the_fix_instructions(self):
        world, store, job, local, cloud = self.run_assert(cloud_script={'assertion_referee': self.referee_script('got')}, policy={'assertion_referee': True})
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
        calls = sum(1 for p in world.log if p.startswith('assertion_referee'))
        self.assertTrue(calls >= 3 and calls % 3 == 0)  # 每次诊断 3 个独立样本 / three independent samples per diagnosis
        event = self.events(store, 'assertion_referee')[0]
        self.assertEqual((event['cases'], event['verdicts'], event['samples']), (1, {'0': 'test_wrong'}, 3))
        diagnoser = next(c for c in cloud.contexts if c['purpose'] == 'project_diagnoser')
        self.assertIn('WRONG', diagnoser['independent_evidence'])
        revisions = [c for c in cloud.contexts + local.contexts if c['purpose'] == 'project_test_revision']
        self.assertTrue(any('Independent evidence' in c['feedback'] for c in revisions))

    def test_an_unchanged_failure_after_a_test_revision_is_called_out_in_the_next_fix(self):
        """测试修订之后同一条断言原样还在失败：下一次修复的指令里要明确说出来，不要再做装饰性修改。 The same assertion still fails after a test revision: the next fix says so explicitly."""
        def neither(context):
            return {'answers': [{'index': c['index'], 'choice': 'neither'} for c in context['cases']]}
        world, store, job, local, cloud = self.run_assert(cloud_script={'assertion_referee': neither}, policy={'assertion_referee': True})
        fixes = [c for c in cloud.contexts + local.contexts if c['purpose'] in ('project_repair', 'project_test_revision')]
        self.assertTrue(any('did NOT change' in c.get('feedback', '') for c in fixes))
        world, store, job, local, cloud = self.run_assert()  # 关闭时没有这句话 / absent when off
        fixes = [c for c in cloud.contexts + local.contexts if c['purpose'] in ('project_repair', 'project_test_revision')]
        self.assertFalse(any('did NOT change' in c.get('feedback', '') for c in fixes))

    def test_a_failing_referee_changes_nothing(self):
        def crash(context):
            raise RuntimeError('referee down')
        world, store, job, local, cloud = self.run_assert(cloud_script={'assertion_referee': crash}, policy={'assertion_referee': True})
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
        self.assertEqual(self.events(store, 'assertion_referee')[0]['samples'], 0)
        diagnoser = next(c for c in cloud.contexts if c['purpose'] == 'project_diagnoser')
        self.assertNotIn('independent_evidence', diagnoser)


if __name__ == '__main__':
    unittest.main()
