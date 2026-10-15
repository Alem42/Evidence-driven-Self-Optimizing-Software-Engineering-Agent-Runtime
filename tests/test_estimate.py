"""新任务页的开跑前估计：难度、token 三档、价格区间、“系统能不能处理”。纯逻辑 + 假的本地模型，零 token。
The New Task page's pre-run estimate: difficulty, the three token tiers, the price range and "can the system handle it". Pure logic plus a fake local model, no tokens.
"""
import tempfile
import types
import unittest
from pathlib import Path

from masa.application import estimate
from masa.domain.models import MasaError
from masa.languages.registry import registry

FLASH = {'name': 'Flash', 'model': 'deepseek-flash', 'model_type': 'cloud', 'input_price_per_million': 0.3, 'output_price_per_million': 1.2}
PRO = {'name': 'Pro', 'model': 'deepseek-pro', 'model_type': 'cloud', 'input_price_per_million': 3.0, 'output_price_per_million': 6.0}
NO_PRICE = {'name': 'Mystery', 'model': 'x', 'model_type': 'cloud'}
OK_RULES = {'verdict': 'ok', 'findings': []}


class SizingTests(unittest.TestCase):
    def test_tokens_follow_the_benchmark_limit_formula_and_the_three_tiers(self):
        self.assertEqual(estimate.tokens_for_level(5), 115_000)
        self.assertEqual(estimate.tier_tokens(5), {'economy': 69_000, 'standard': 115_000, 'rich': 288_000})
        self.assertEqual(estimate.tokens_for_level(99), 190_000)  # 钳到 1..10 / clamped to 1..10
        self.assertEqual(estimate.tokens_for_level(0), 55_000)

    def test_the_rule_of_thumb_follows_the_goal_length(self):
        self.assertEqual(estimate.rules_level('打印 hello'), 1)
        self.assertEqual(estimate.rules_level('x' * 200), 6)
        self.assertEqual(estimate.rules_level('x' * 700), 10)
        self.assertEqual(estimate.rules_level(''), 1)

    def test_the_price_is_a_range_from_the_cheapest_to_the_dearest_priced_model(self):
        price = estimate.price_range(100_000, [FLASH, PRO, NO_PRICE])
        # 0.85*0.3+0.15*1.2 = 0.435 / 百万；0.85*3+0.15*6 = 3.45 / 百万 / per million
        self.assertEqual((price['low'], price['high']), (0.0435, 0.345))
        self.assertEqual(price['unpriced'], 1)
        self.assertEqual([m['model'] for m in price['models']], ['Flash', 'Pro'])
        self.assertEqual(estimate.price_range(1000, [NO_PRICE])['low'], None)
        self.assertEqual(estimate.price_range(1000, [])['unpriced'], 0)


class BuildTests(unittest.TestCase):
    def test_the_local_model_answer_sets_level_files_and_can_make_it_risky(self):
        answer = {'level': 4, 'files': 5, 'fits': 'risky', 'reason': '没说空输入打印什么'}
        result = estimate.build('x', OK_RULES, answer, [FLASH], source='local-model')
        self.assertEqual((result['level'], result['files'], result['can_handle'], result['source']), (4, 5, 'risky', 'local-model'))
        self.assertIn('没说空输入', result['reasons'][-1])
        self.assertEqual([t['key'] for t in result['tiers']], ['economy', 'standard', 'rich'])
        self.assertEqual(result['tiers'][1]['tokens'], 100_000)

    def test_without_the_model_the_rules_decide_and_the_source_says_so(self):
        result = estimate.build('x' * 120, OK_RULES, None, [FLASH])
        self.assertEqual((result['source'], result['level'], result['files'], result['can_handle']), ('rules', 4, None, 'yes'))

    def test_an_infeasible_rule_verdict_wins_over_a_confident_model(self):
        rules = {'verdict': 'infeasible', 'findings': [{'level': 'infeasible', 'reason': '需要图形界面'}]}
        result = estimate.build('做一个窗口', rules, {'level': 2, 'files': 3, 'fits': 'yes', 'reason': ''}, [FLASH])
        self.assertEqual(result['can_handle'], 'no')
        self.assertIn('图形界面', result['reasons'][0])

    def test_a_toolchain_that_is_not_ready_or_no_cloud_model_is_said_plainly(self):
        self.assertEqual(estimate.build('x', OK_RULES, None, [FLASH], ready=False)['can_handle'], 'no')
        none = estimate.build('x', OK_RULES, None, [])
        self.assertEqual(none['can_handle'], 'risky')
        self.assertTrue(any('没有可用的云端模型' in r for r in none['reasons']))
        self.assertEqual(none['tiers'][0]['price']['low'], None)


class FakeLocal:
    profile = {'provider': 'test', 'model': 'fake-local'}

    def __init__(self, answer=None, error=None):
        self.answer, self.error, self.contexts = answer, error, []
        self.loaded = False

    def respond(self, context, timeout=None):
        self.contexts.append(context)
        if self.error:
            raise self.error
        return self.answer

    def unload(self):
        self.loaded = False


class ConsoleEstimateTests(unittest.TestCase):
    def setUp(self):
        from masa.application.console import Console
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.console = Console(Path(self.temp.name), Path(self.temp.name) / 'no-runner.exe', Path(self.temp.name) / 'no-go.exe', Path(__file__).resolve().parents[1])
        self.addCleanup(lambda: registry.configure(default='go', configs={}, extra_dirs=[]))
        self.addCleanup(lambda: self.console.__dict__.get('_estimate_timer') and self.console._estimate_timer.cancel())
        self.profiles = [('c', dict(FLASH)), ('l', {'name': 'glm', 'model': 'glm', 'model_type': 'local'})]
        self.console.settings = types.SimpleNamespace(ready_profiles=lambda: self.profiles, provider=lambda ident: self.local)
        self.local = FakeLocal({'level': 5, 'files': 6, 'fits': 'yes', 'reason': '经典算法'})

    def test_rules_mode_never_touches_the_model(self):
        result = self.console.estimate({'goal': '写一个 CLI：输出 hello', 'language': 'python', 'mode': 'rules'})
        self.assertEqual((result['mode'], result['source']), ('rules', 'rules'))
        self.assertEqual(self.local.contexts, [])

    def test_model_mode_asks_the_local_model_once_per_goal(self):
        body = {'goal': '写一个 CLI：求最长公共子序列', 'language': 'python', 'mode': 'model'}
        first = self.console.estimate(body)
        self.assertEqual((first['source'], first['level'], first['files'], first['model']), ('local-model', 5, 6, 'glm'))
        self.assertEqual(self.local.contexts[0]['purpose'], 'task_estimator')
        self.assertEqual(self.console.estimate(body)['level'], 5)
        self.assertEqual(len(self.local.contexts), 1)  # 同一个目标不重复问 / the same goal is not asked twice

    def test_a_failing_or_missing_local_model_falls_back_to_the_rules_with_a_note(self):
        self.local.error = MasaError('Ollama request failed')
        result = self.console.estimate({'goal': '写一个 CLI：输出 hello', 'language': 'python', 'mode': 'model'})
        self.assertEqual(result['source'], 'rules')
        self.assertIn('按规则估计', result['note'])
        self.profiles = [('c', dict(FLASH))]
        self.console.__dict__.pop('_estimates', None)
        self.assertIn('没有可用的本地模型', self.console.estimate({'goal': '写一个 CLI：输出 hello', 'language': 'python', 'mode': 'model'})['note'])

    def test_a_running_task_keeps_the_local_model(self):
        self.console.job_thread = types.SimpleNamespace(is_alive=lambda: True)
        result = self.console.estimate({'goal': '写一个 CLI：输出 hello', 'language': 'python', 'mode': 'model'})
        self.assertEqual(result['source'], 'rules')
        self.assertEqual(self.local.contexts, [])
        self.console.job_thread = None

    def test_bad_goals_are_rejected(self):
        for goal in ('', '   ', 'x' * 16001, None, 5):
            with self.subTest(goal=goal), self.assertRaises(MasaError):
                self.console.estimate({'goal': goal})

    def test_the_estimator_role_is_registered_with_a_closed_schema(self):
        from masa.agents.protocol import validate_response
        context = {'purpose': 'task_estimator', 'goal': 'x', 'language': 'python'}
        good = {'level': 3, 'files': 4, 'fits': 'yes', 'reason': 'ok'}
        self.assertEqual(validate_response(context, dict(good), ''), good)
        for bad in ({**good, 'level': 11}, {**good, 'fits': 'maybe'}, {**good, 'extra': 1}, {'level': 3}):
            with self.subTest(bad=bad), self.assertRaises(MasaError):
                validate_response(context, bad, '')


if __name__ == '__main__':
    unittest.main()
