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
