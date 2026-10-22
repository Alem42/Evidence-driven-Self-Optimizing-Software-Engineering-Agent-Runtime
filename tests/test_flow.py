"""声明式工作流引擎与归属分析。Declarative workflow engine and failure-ownership analysis."""
import unittest

from masa.application.checks import ownership
from masa.application.orchestration.flow import FlowEngine, FlowError, GUARDS, guard, next_edge, validate
from masa.application.orchestration.workflows import FIX_V1

# ───── c903b2b7 的真实验证输出（节选）/ real verification output from task c903b2b7 (excerpt) ─────
CYCLE_AND_SYNTAX = [
    ('go_test', {'status': 'completed', 'exit_code': 1, 'stderr': '', 'stdout': '\n'.join([
        '# example.com/task/internal/processor',
        'package example.com/task/internal/processor',
        '\timports example.com/task/internal/processor from processor_test.go: import cycle not allowed in test',
        'FAIL\texample.com/task/internal/processor [setup failed]',
        '# example.com/task/internal/processor',
        'internal\\processor\\processor.go:47:17: syntax error: unexpected name not, expected {',
        'FAIL\texample.com/task/cmd/app [build failed]'])}),
    ('go_vet', {'status': 'completed', 'exit_code': 1, 'stdout': '', 'stderr': 'vet.exe: internal\\processor\\processor.go:47:17: expected \';\', found not'}),
]
ONLY_SYNTAX = [('go_test', {'status': 'completed', 'exit_code': 1, 'stderr': '',
                            'stdout': '# example.com/task/internal/processor\ninternal\\processor\\processor.go:47:17: syntax error: unexpected name not, expected {\n'})]
MIXED_LATER = [('go_test', {'status': 'completed', 'exit_code': 1, 'stderr': '', 'stdout': '\n'.join([
    'cmd\\app\\main.go:14:12: flag.Parse() (no value) used as value',
    '    processor_test.go:246: error message: open doesnotexist.csv: The system cannot find the file specified.',
    '    processor_test.go:115: expected 1 row, got 3'])})]
ASSERT_ONLY = [('go_test', {'status': 'completed', 'exit_code': 1, 'stderr': '', 'stdout': '    processor_test.go:115: expected 1 row, got 3\n--- FAIL: TestX (0.00s)\n'})]
TEST_ONLY = [('go_test', {'status': 'completed', 'exit_code': 1, 'stderr': '', 'stdout': 'internal/app/app_test.go:7:2: "os" imported and not used\n'})]


class OwnershipTests(unittest.TestCase):
    def test_the_real_case_has_both_owners_and_the_implementation_comes_first(self):
        result = ownership.analyse(CYCLE_AND_SYNTAX)
        owners = {(i['owner'], i['kind']) for i in result['items']}
        self.assertIn(('implementation', 'compile'), owners)
        self.assertIn(('test', 'setup'), owners)
        self.assertTrue(result['implementation_blocking'] and result['test_blocking'])
        self.assertEqual(result['primary'], 'implementation')  # 旧分类器在这里选了“修订测试”，实现里的语法错误从未被修 / the old classifier chose tests
        syntax = next(i for i in result['items'] if i['kind'] == 'compile')
        self.assertEqual((syntax['path'], syntax['line']), ('internal/processor/processor.go', 47))

    def test_single_owner_cases(self):
        self.assertEqual(ownership.analyse(ONLY_SYNTAX)['primary'], 'implementation')
        self.assertEqual(ownership.analyse(TEST_ONLY)['primary'], 'test')
        self.assertFalse(ownership.analyse(TEST_ONLY)['implementation_blocking'])

    def test_assertion_only_failures_are_ambiguous(self):
        result = ownership.analyse(ASSERT_ONLY)
        self.assertEqual(result['primary'], 'ambiguous')
        self.assertEqual(result['assertion_count'], 1)
        self.assertEqual(result['items'][0]['owner'], 'ambiguous')

    def test_a_compile_error_outranks_assertion_noise(self):
        result = ownership.analyse(MIXED_LATER)
        self.assertEqual(result['primary'], 'implementation')
        self.assertEqual(result['assertion_count'], 2)

    def test_passing_checks_and_empty_input_produce_nothing(self):
        self.assertEqual(ownership.analyse([('go_test', {'status': 'completed', 'exit_code': 0, 'stdout': 'x.go:1:1: syntax error'})])['items'], [])
        self.assertEqual(ownership.analyse([])['primary'], 'ambiguous')

    def test_gofmt_failures_are_attributed_by_path(self):
        result = ownership.analyse([('go_fmt_check', {'status': 'completed', 'exit_code': 1, 'stdout': 'cmd\\app\\main.go\ninternal\\a\\a_test.go\n'})])
        self.assertEqual({(i['owner'], i['path']) for i in result['items']}, {('implementation', 'cmd/app/main.go'), ('test', 'internal/a/a_test.go')})

    def test_hints_are_focused_and_give_concrete_test_advice(self):
        analysis = ownership.analyse(CYCLE_AND_SYNTAX)
        impl = ownership.hint(analysis, 'implementation')
        self.assertIn('processor.go:47', impl)
        self.assertIn('冻结', impl)
        self.assertNotIn('import cycle', impl.split('（另一侧')[0])  # 实现的提示里不列测试的问题 / the implementation hint lists only its own problems
        tests = ownership.hint(analysis, 'test')
        self.assertIn('import cycle', tests)
        self.assertIn('不能 import 这个包本身', tests)  # 具体做法 / concrete advice
        self.assertEqual(ownership.hint(ownership.analyse(ONLY_SYNTAX), 'test'), '')

    def test_describe_is_human_readable(self):
        lines = ownership.describe(ownership.analyse(CYCLE_AND_SYNTAX))
        self.assertTrue(any(l.startswith('实现 · 编译/语法错误') for l in lines))
        self.assertTrue(any(l.startswith('测试 · 测试准备错误') for l in lines))


@guard('go_b_test')
def _go_b(facts, params):
    return bool(facts.get('go_b'))


def mini(**over):
    base = {'id': 't', 'version': 1, 'entry': 'a',
            'nodes': {'a': {'action': 'one'}, 'b': {'action': 'two'}, 'end': {'end': 'done'}},
            'edges': [{'from': 'a', 'to': 'b', 'when': 'go_b_test'}, {'from': 'a', 'to': 'end'}, {'from': 'b', 'to': 'end'}]}
    base.update(over)
    return base


class FlowEngineTests(unittest.TestCase):
    def test_the_fix_workflow_is_valid_and_every_node_is_reachable(self):
        validate(FIX_V1)
        self.assertEqual({n for n, s in FIX_V1['nodes'].items() if s.get('end')}, {'drafted', 'halt'})

    def test_validation_rejects_broken_definitions(self):
        bad = [
            mini(entry='nope'),
            mini(edges=[{'from': 'a', 'to': 'ghost'}]),
            mini(edges=[{'from': 'a', 'to': 'b', 'when': 'no_such_guard'}, {'from': 'a', 'to': 'end'}, {'from': 'b', 'to': 'end'}]),
            mini(edges=[{'from': 'a', 'to': 'b', 'when': 'go_b_test'}, {'from': 'b', 'to': 'end'}]),  # a 没有兜底边 / no fallback
            mini(nodes={'a': {'action': 'one'}, 'b': {'action': 'two'}, 'orphan': {'action': 'x'}, 'end': {'end': 'done'}},
                 edges=[{'from': 'a', 'to': 'end'}, {'from': 'b', 'to': 'end'}, {'from': 'orphan', 'to': 'end'}]),  # 不可达 / unreachable
            mini(edges=[{'from': 'end', 'to': 'a'}, {'from': 'a', 'to': 'end'}, {'from': 'b', 'to': 'end'}]),
            {'id': 'x'},
        ]
        for defn in bad:
            with self.subTest(defn=str(defn)[:60]), self.assertRaises(FlowError):
                validate(defn)

    def test_edges_fire_in_declaration_order_and_executors_merge_facts(self):
        defn = mini(edges=[{'from': 'a', 'to': 'b', 'when': 'go_b_test'}, {'from': 'a', 'to': 'end'}, {'from': 'b', 'to': 'end'}])
        steps = []
        engine = FlowEngine(defn, {'one': lambda f: {'go_b': True}, 'two': lambda f: {'second': 1}}, on_step=steps.append)
        result = engine.run({})
        self.assertEqual([s.node for s in result['trace']], ['a', 'b'])
        self.assertEqual(result['trace'][0].why, 'go_b_test')
        self.assertEqual((result['end'], result['outcome'], result['facts']['second']), ('end', 'done', 1))
        skipped = FlowEngine(defn, {'one': lambda f: {'go_b': False}, 'two': lambda f: None}).run({})
        self.assertEqual([s.node for s in skipped['trace']], ['a'])
        self.assertEqual(len(steps), 2)

    def test_a_cycle_is_bounded(self):
        defn = {'id': 'loop', 'version': 1, 'entry': 'a', 'nodes': {'a': {'action': 'x'}, 'b': {'action': 'x'}, 'end': {'end': 'done'}},
                'edges': [{'from': 'a', 'to': 'b'}, {'from': 'b', 'to': 'a', 'when': 'go_b_test'}, {'from': 'b', 'to': 'end', 'when': 'always'}]}
        engine = FlowEngine(defn, {'x': lambda f: {'go_b': True}}, max_steps=5)
        with self.assertRaisesRegex(FlowError, 'exceeded'):
            engine.run({})

    def test_missing_executors_are_reported_up_front(self):
        with self.assertRaisesRegex(FlowError, 'no executor'):
            FlowEngine(mini(), {'one': lambda f: None})

    def test_guards_cannot_be_registered_twice(self):
        with self.assertRaises(FlowError):
            guard('go_b_test')(lambda f, p: True)
        self.assertIn('halted', GUARDS)

    def test_the_fix_workflow_routes_by_the_facts(self):
        def route(**facts):
            return next_edge(FIX_V1, 'classify', facts)['to']
        self.assertEqual(route(format_only=True, primary='implementation'), 'format')
        self.assertEqual(route(needs_diagnosis=True, primary='implementation'), 'diagnose')
        self.assertEqual(route(primary='implementation'), 'repair')
        self.assertEqual(route(primary='test'), 'revise')
        self.assertEqual(route(primary='ambiguous'), 'repair')
        # 诊断过之后，走向由诊断结论决定而不是规则 primary。 After a diagnosis, the verdict decides.
        self.assertEqual(route(primary='implementation', diagnosed=True, needs_diagnosis=True), 'repair')
        diag = lambda owner: next_edge(FIX_V1, 'diagnose', {'diagnosis': {'owner': owner}})['to']  # noqa: E731
        # 测试期望有错（含“两者都有问题”）时先改测试，再改实现。 A wrong expectation is corrected first, also when the implementation has defects too.
        self.assertEqual((diag('test'), diag('spec'), diag('unclear'), diag('implementation'), diag('both')), ('revise', 'halt', 'halt', 'repair', 'revise'))
        self.assertEqual(next_edge(FIX_V1, 'repair', {'halted': True})['to'], 'halt')
        self.assertEqual(next_edge(FIX_V1, 'repair', {})['to'], 'drafted')

    def test_a_stuck_repair_never_becomes_a_test_revision_without_evidence(self):
        """“修不动实现”不能证明“测试错”（P0 复盘 §5.3）。有裁决者时，修不动 → 先裁决 → 已裁决过就停下交给人；只有独立证据才直接改测试。
        “The implementation cannot be fixed” does not prove “the test is wrong”: with an adjudicator a stall yields a ruling, then a stop; only independent evidence revises the tests at once."""
        after = lambda node, **facts: next_edge(FIX_V1, node, facts)['to']  # noqa: E731
        stalled = {'halted': True, 'halt_kind': 'noop', 'adjudicator': True}
        self.assertEqual(after('repair', **stalled, can_diagnose=True, diagnosed=False), 'diagnose')   # 先裁决 / adjudicate first
        self.assertEqual(after('repair', **stalled, can_diagnose=False, diagnosed=True), 'halt')       # 裁决过了仍修不动 → 交给人 / already ruled, still stuck: a human
        self.assertEqual(after('repair', **stalled, can_diagnose=False, diagnosed=False), 'halt')      # 裁决预算用完 → 交给人，不推断测试错 / budget gone: a human, no inference
        wants_tests = {'halted': True, 'halt_kind': 'wants_tests', 'adjudicator': True, 'can_diagnose': True}
        self.assertEqual(after('repair', **wants_tests), 'diagnose')                                    # 模型想改冻结的测试只是它的意见 / the model wanting to edit frozen tests is only its opinion
        # 独立证据（裁判多数 + 实际值不变）直接转修订测试，即使还能裁决。 Independent evidence revises the tests at once, even when a ruling is still possible.
        self.assertEqual(after('repair', halted=True, halt_kind='expectation_suspected', adjudicator=True, can_diagnose=True), 'revise')
        self.assertEqual(after('rewrite', halted=True, halt_kind='expectation_suspected', adjudicator=True, can_diagnose=True), 'revise')
        self.assertEqual(after('repair', halted=True, halt_kind='expectation_suspected', flipped=True), 'halt')  # 只转一次 / only once
        # 没有任何裁决者（固定单模型、诊断被关）：保留旧捷径。 Without any adjudicator the old shortcut survives.
        self.assertEqual(after('repair', halted=True, halt_kind='noop', adjudicator=False), 'revise')
        self.assertEqual(after('repair', noop=True, noop_count=3, noop_limit=3, adjudicator=False), 'revise')
        self.assertEqual(after('repair', noop=True, noop_count=1, noop_limit=3, adjudicator=False), 'repair')     # 先重试（路由器会升级模型）/ retry first (the router escalates)
        # 有裁决者时，一次没有改动先请 Diagnoser 看；看过之后再重试，次数用完才停。 With an adjudicator, one unchanged fix first asks the Diagnoser; then retries; then a stop.
        self.assertEqual(after('repair', noop=True, noop_count=1, noop_limit=3, adjudicator=True, can_diagnose=True), 'diagnose')
        self.assertEqual(after('repair', noop=True, noop_count=1, noop_limit=3, adjudicator=True, diagnosed=True), 'repair')
        self.assertEqual(after('repair', noop=True, noop_count=3, noop_limit=3, adjudicator=True, diagnosed=True), 'halt')

    def test_every_node_is_reachable_under_the_default_configuration(self):
        """默认配置（有裁决者）下每个节点都真的走得到：不再有被别的边永远抢先的“死节点”（旧的 arbitrate）。 Every node can fire under the default configuration."""
        fired = set()
        situations = [
            ('classify', dict(format_only=True)), ('classify', dict(needs_diagnosis=True)), ('classify', dict(rewrite_due=True)),
            ('classify', dict(primary='implementation')), ('classify', dict(primary='test')), ('classify', dict(primary='ambiguous')),
            ('diagnose', dict(diagnosis={'owner': 'spec'})), ('diagnose', dict(diagnosis={'owner': 'test'})), ('diagnose', dict(rewrite_due=True, diagnosis={'owner': 'implementation'})),
            ('diagnose', dict(diagnosis={'owner': 'implementation'})),
            ('repair', dict(halted=True, halt_kind='expectation_suspected')), ('repair', dict(halted=True, halt_kind='noop', can_diagnose=True, adjudicator=True)),
            ('repair', dict(halted=True, halt_kind='noop', adjudicator=True)), ('repair', dict(noop=True, noop_count=1, can_diagnose=True)),
            ('repair', dict(noop=True, noop_count=1, noop_limit=3, diagnosed=True)), ('repair', dict(noop=True, noop_count=3, noop_limit=3, diagnosed=True)), ('repair', {}),
            ('format', {}),
        ]
        for node, facts in situations:
            fired.add(next_edge(FIX_V1, node, {'adjudicator': True, **facts})['to'])
        self.assertEqual(fired, set(FIX_V1['nodes']) - {'classify'})


if __name__ == '__main__':
    unittest.main()
