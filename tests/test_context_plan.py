"""上下文预算与分层：小任务原样全给（行为不变），大任务按层裁剪，必需项永不丢，裁剪有记录。
Context budget and tiers: small tasks get everything (no behaviour change), large ones are trimmed by tier, must-have items are never dropped, every drop is recorded."""
import unittest

from masa.application.orchestration.context_plan import item, plan, scale_of

WINDOW = 10_000  # token


from masa.domain.tokens import estimate_tokens

_PER_WORD = estimate_tokens('word ' * 1000) / 1000


def words(n):
    return 'word ' * int(n / _PER_WORD)  # 按估算器校准：约 n 个 token / calibrated to the estimator: about n tokens


class PlanTests(unittest.TestCase):
    def test_a_small_task_keeps_everything_untouched(self):
        items = [item('spec', 'must', words(200)), item('files', 'droppable', words(400)), item('summary', 'important', words(100))]
        result = plan(items, WINDOW)
        self.assertEqual(result['scale'], 'small')
        self.assertEqual((result['dropped'], result['trimmed'], result['over_budget']), ([], {}, False))
        self.assertTrue(all(v is None for v in result['keep'].values()))  # 没有任何截断 / no truncation at all

    def test_a_large_task_gives_up_droppable_first_then_trims_important(self):
        items = [item('spec', 'must', words(1500)), item('failing', 'must', words(1500)), item('summary', 'important', words(1500)),
                 item('other_a', 'droppable', words(3000)), item('other_b', 'droppable', words(1200))]
        result = plan(items, WINDOW)
        self.assertEqual(result['scale'], 'large')
        self.assertIn('other_a', result['dropped'])  # 大的可丢项先丢 / the biggest droppable first
        self.assertLessEqual(result['kept_tokens'], result['budget'])
        self.assertIn('spec', result['keep'])
        self.assertIn('failing', result['keep'])  # 必需项永不丢 / must-have items are never dropped
        self.assertGreater(result['total'], result['kept_tokens'])

    def test_important_items_are_trimmed_before_being_dropped_and_dropped_when_trimming_is_hopeless(self):
        items = [item('must', 'must', words(4600)), item('summary', 'important', words(1500))]
        result = plan(items, WINDOW)
        self.assertIn('summary', result['trimmed'])
        self.assertLess(result['keep']['summary'], len(words(1500)))
        items = [item('must', 'must', words(4990)), item('summary', 'important', words(1500))]
        self.assertIn('summary', plan(items, WINDOW)['dropped'])

    def test_must_have_alone_over_budget_is_reported_not_hidden(self):
        result = plan([item('spec', 'must', words(9000))], WINDOW)
        self.assertTrue(result['over_budget'])
        self.assertEqual(result['dropped'], [])

    def test_the_scale_rule_is_deterministic(self):
        self.assertEqual(scale_of(3500, 10_000), 'small')
        self.assertEqual(scale_of(3501, 10_000), 'large')


if __name__ == '__main__':
    unittest.main()


GO = 'package app\n\nimport "strings"\n\n// Doc.\nfunc Add(a, b int) int {\n\tif a > b {\n\t\treturn a + b\n\t}\n\treturn b + a\n}\n\ntype Store struct {\n\titems map[string]int\n}\n\nfunc (s *Store) Put(k string) {\n\ts.items[k]++\n}\n\nvar _ = strings.ToUpper\n'


class OutlineTests(unittest.TestCase):
    def test_outline_keeps_declarations_and_drops_bodies(self):
        from masa.application.orchestration.context_plan import outline_go
        outline = outline_go(GO)
        for line in ('package app', 'import "strings"', 'func Add(a, b int) int', 'type Store struct', 'func (s *Store) Put(k string)', 'var _ = strings.ToUpper'):
            self.assertIn(line, outline)
        self.assertNotIn('return a + b', outline)
        self.assertNotIn('s.items[k]++', outline)
        self.assertLess(len(outline), len(GO))

    def test_droppable_degrades_to_an_outline_before_being_dropped(self):
        from masa.application.orchestration.context_plan import outline_go
        body = GO + ('// filler\n' * 400)
        items = [item('must.go', 'must', words(3000)), item('big.go', 'droppable', words(3000) + body, outline_go(body)), item('other.go', 'droppable', words(2500))]
        result = plan(items, WINDOW)
        self.assertEqual(result['scale'], 'large')
        self.assertEqual(result['keep']['big.go'], 'outline')  # 有轮廓：降级而不是丢 / has an outline: degraded, not dropped
        self.assertIn('other.go', result['dropped'])  # 没有轮廓：丢 / no outline: dropped
        self.assertEqual(result['keep']['must.go'], None)
        self.assertLessEqual(result['kept_tokens'], result['budget'])


class GenerationFitTests(unittest.TestCase):
    """接入生成阶段：关闭或小任务时原样；大任务时无关文件变轮廓、同目录与测试保持完整。 Wired into generation: untouched when off or small; unrelated files become outlines when large."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from masa.application.generation import ProjectGeneration
        from masa.infrastructure.store import Store
        from test_runtime import FakeExecutor
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.addCleanup(self.store.close)
        self.generation = ProjectGeneration(self.store, FakeExecutor())
        self.files = {'go.mod': 'module x\n', **{f'internal/p{i}/p{i}.go': GO + ('// pad\n' * 300) for i in range(12)}, 'internal/p0/p0_test.go': 'package p0\n'}
        from masa.infrastructure.workspaces import copy_snapshot  # noqa: F401  (确保导入可用 / import check)
        from masa.runtime.engine import Runtime
        from masa.domain.models import Budget
        src = Path(self.temp.name) / 'src'
        src.mkdir()
        (src / 'go.mod').write_text('module x\n')
        self.rid = Runtime(self.store, FakeExecutor()).create(src, 'g', Budget())

    def provider(self, limit):
        return type('P', (), {'config': {'context_limit': limit}})()

    def test_off_returns_everything_untouched(self):
        shown, outlines = self.generation._fit_files(self.provider(16384), self.rid, self.files, {'internal/p0/p0.go'}, 'x')
        self.assertEqual((shown, outlines), (self.files, {}))

    def test_small_task_on_a_big_window_is_untouched_even_when_on(self):
        self.generation.context_budget = True
        shown, outlines = self.generation._fit_files(self.provider(1_000_000), self.rid, self.files, {'internal/p0/p0.go'}, 'x')
        self.assertEqual((shown, outlines), (self.files, {}))
        self.assertEqual([e for e in self.store.events(self.rid) if e['type'] == 'context_plan'], [])

    def test_large_task_on_a_small_window_outlines_unrelated_files_and_records_it(self):
        self.generation.context_budget = True
        must = {'go.mod', 'internal/p0/p0.go', 'internal/p0/p0_test.go'}
        shown, outlines = self.generation._fit_files(self.provider(16384), self.rid, self.files, must, 'generation:internal/p0/p0.go')
        self.assertTrue(must <= set(shown))  # 必需的完整保留 / must-have files stay whole
        self.assertTrue(outlines)  # 其余降级成轮廓 / the rest degrade to outlines
        self.assertTrue(all('func Add' in text and 'return a + b' not in text for text in outlines.values()))
        event = [e['payload'] for e in self.store.events(self.rid) if e['type'] == 'context_plan'][0]
        self.assertEqual(event['window'], 16384)
        self.assertGreater(event['total'], event['kept_tokens'])
