"""可行性预检：确定会失败的需求在花任何模型调用之前就被拦下；本地模型复核只能告警。
Feasibility triage: requirements certain to fail are stopped before any model call; the local review can only warn."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from masa.application.console import Console
from masa.application.coordinator import WorkflowCoordinator
from masa.application.router import Router
from masa.application.routing import DEFAULT_POLICY, validate_policy
from masa.application.triage import INFEASIBLE, OK, RISKY, assess
from masa.domain.models import MasaError
from masa.domain.proposals import validate_triage
from masa.infrastructure.jobs import Jobs
from masa.infrastructure.store import Store
from test_project_generation import FILES
from test_project_plan import CHECKS, SPEC
from test_routing import BUDGET, fake_entry
from test_runtime import FakeExecutor


def rules(goal):
    return {f['rule'] for f in assess(goal)['findings']}


class RuleTests(unittest.TestCase):
    def test_requirements_the_harness_can_never_satisfy_are_infeasible(self):
        cases = {
            'language': ['用 Python 写一个脚本，统计文件行数', 'Implement this in Rust with a CLI'],
            'third_party': ['使用 cobra 实现命令行，并用 viper 读配置', 'import github.com/gin-gonic/gin and serve routes'],
            'network_service': ['写一个爬虫，抓取网页标题', '连接 MySQL 数据库并查询用户表', 'call an external API and print the result'],
            'gui': ['做一个图形界面的计算器', '写一个 React 前端页面'],
            'library_shape': ['实现 Go 函数 Clamp(value, min, max int) (int, error)。保留 package solution，只使用标准库。',
                              'Implement Clamp(value,min,max int) (int,error). Package solution; standard library only.'],
        }
        for rule, goals in cases.items():
            for goal in goals:
                with self.subTest(rule=rule, goal=goal):
                    result = assess(goal)
                    self.assertEqual(result['verdict'], INFEASIBLE)
                    self.assertIn(rule, rules(goal))
                    self.assertTrue(all(f['suggestion'] for f in result['findings']))  # 每条都有可操作的改写建议

    def test_negations_cancel_a_hit(self):
        for goal in ('只用 Go 标准库，无第三方依赖，写一个整数求和命令行', '不使用 gin 或 cobra，纯标准库实现 CLI',
                     'A Go CLI without third-party libraries, no network and no database', '不需要图形界面，命令行输出结果即可'):
            with self.subTest(goal=goal):
                self.assertEqual(assess(goal)['verdict'], OK, assess(goal)['findings'])

    def test_ordinary_cli_requirements_pass(self):
        for goal in ('写一个随机数生成器', '整数求和 Go 标准库 CLI，命令行参数为十进制整数，输出所有整数之和并换行。无参数输出 0。',
                     '区间归并：参数为若干 start:end 区间，输出归并结果。', 'Build a small standard-library Go CLI that counts nonempty lines in a file.',
                     '写一个简单的动态规划，自己确定输入和输出，题目写在注释中', '文本行统计 CLI：linecount PATH，读取 UTF-8 文件，输出行数。'):
            with self.subTest(goal=goal):
                self.assertEqual(assess(goal)['verdict'], OK, assess(goal)['findings'])

    def test_risky_requirements_warn_but_are_not_blocked(self):
        for goal, rule in (('写一个可一键运行的2048小游戏', 'interactive'), ('实现一个高并发的任务调度器，用 goroutine 和 channel', 'concurrency'),
                           ('做一个完整的电商平台', 'large_scope'), ('命令行工具要优雅好看', 'unverifiable'), ('x', 'too_short'), ('写一个 CLI。' * 700, 'long_goal')):
            with self.subTest(rule=rule):
                result = assess(goal)
                self.assertEqual(result['verdict'], RISKY, result['findings'])
                self.assertIn(rule, rules(goal))

    def test_empty_goal_is_handled(self):
        self.assertEqual(assess('')['verdict'], RISKY)
        self.assertEqual(assess(None)['verdict'], RISKY)

    def test_the_local_models_opinion_can_never_block(self):
        self.assertEqual(validate_triage({'verdict': 'infeasible', 'reasons': ['需要网络'], 'suggestions': []})['verdict'], 'risky')
        self.assertIn('降级', validate_triage({'verdict': 'infeasible', 'reasons': [], 'suggestions': []})['reasons'][0])
        self.assertEqual(validate_triage({'verdict': 'ok'})['reasons'], [])
        for bad in ({'verdict': 'maybe'}, {'verdict': 'ok', 'extra': 1}, {'verdict': 'ok', 'reasons': 'x'}, {'verdict': 'ok', 'reasons': ['x'] * 6}, [], None):
            with self.assertRaises(MasaError):
                validate_triage(bad)


class Provider:
    def __init__(self, name, kind, triage=None, triage_error=None):
        self.profile = {'provider': 'test', 'model': name}
        self.config = {'model_type': kind}
        self.usage = {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}
        self.triage, self.triage_error = triage, triage_error
        self.purposes = []

    def respond(self, context):
        purpose = context['purpose']
        self.purposes.append(purpose)
        if purpose == 'project_triage':
            if self.triage_error:
                raise self.triage_error
            return self.triage or {'verdict': 'ok', 'reasons': [], 'suggestions': []}
        return {'project_planner': SPEC, 'project_tester': CHECKS, 'project_developer': FILES}[purpose]


class IntegrationTests(unittest.TestCase):
    def run_task(self, goal, local, cloud=None, mode='ladder', force=False):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(Path(temp.name))
        self.addCleanup(store.close)
        cloud = cloud or Provider('big', 'cloud')
        providers = {'local': local, 'cloud': cloud}
        jobs = Jobs(Path(temp.name))
        jobs['job'] = {'status': 'running', 'run_id': None, 'mode': 'auto', 'phase': 'planning', 'attempt': 0, 'started': 0,
                       'request': {'goal': goal, 'force': force}}
        if mode == 'ladder':
            snapshot = {'version': 1, 'mode': 'ladder', 'policy': validate_policy(DEFAULT_POLICY), 'budget': dict(BUDGET),
                        'candidates': [fake_entry('local', 1, 'small', 'local'), fake_entry('cloud', 2, 'big', 'cloud')]}
            router = Router(store, snapshot, lambda entry: providers[entry['id']])
            WorkflowCoordinator(store, FakeExecutor(), None, jobs['job'], router=router).run()
        else:
            WorkflowCoordinator(store, FakeExecutor(), local, jobs['job']).run()
        return store, jobs['job'], providers

    def test_an_infeasible_goal_is_stopped_with_zero_model_calls_and_leaves_a_readable_record(self):
        local = Provider('small', 'local')
        store, job, providers = self.run_task('实现 Go 函数 Clamp(value, min, max int)。保留 package solution。', local)
        self.assertEqual(job['status'], 'completed')
        self.assertIn('triage', job['note'])
        self.assertEqual(local.purposes + providers['cloud'].purposes, [])  # 一个模型都没调用 / no model was called
        run = store.run(job['result']['id'])
        self.assertEqual(run['status'], 'failed')
        plan = run['data']['project_plan']
        self.assertTrue(plan['error'].startswith('infeasible:'))
        self.assertEqual(plan['triage']['verdict'], INFEASIBLE)
        self.assertIn('triage_blocked', [e['type'] for e in store.events(run['id'])])

    def test_force_lets_the_user_proceed_anyway(self):
        local = Provider('small', 'local')
        store, job, _ = self.run_task('实现 Go 函数 Clamp。保留 package solution。', local, force=True)
        self.assertIn('project_planner', local.purposes)
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded')  # 照常规划、生成、验证 / planned, generated and verified as usual
        self.assertFalse(any(e['type'] == 'triage_blocked' for r in store.all_runs() for e in store.events(r['id'])))

    def test_a_risky_goal_proceeds_with_the_warning_attached_to_the_plan(self):
        local = Provider('small', 'local')
        store, job, _ = self.run_task('写一个可一键运行的2048小游戏', local)
        plans = [r['data'].get('project_plan', {}) for r in store.all_runs() if r['data'].get('project_plan', {}).get('triage')]
        self.assertTrue(plans)
        self.assertEqual(plans[0]['triage']['verdict'], RISKY)
        self.assertEqual(plans[0]['triage']['findings'][0]['rule'], 'interactive')

    def test_the_local_review_only_uses_a_local_model_and_only_raises_a_warning(self):
        local = Provider('small', 'local', triage={'verdict': 'risky', 'reasons': ['依赖随机数，难以验证'], 'suggestions': ['固定种子']})
        cloud = Provider('big', 'cloud')
        store, job, _ = self.run_task('写一个随机数生成器', local, cloud)
        self.assertIn('project_triage', local.purposes)
        self.assertNotIn('project_triage', cloud.purposes)  # 绝不花 API / never spends the API
        plan = next(r['data']['project_plan'] for r in store.all_runs() if r['data'].get('project_plan', {}).get('triage'))
        self.assertEqual(plan['triage']['model']['verdict'], 'risky')
        self.assertEqual(plan['triage']['model']['by'], 'small')
        self.assertEqual(plan['triage']['verdict'], RISKY)  # 规则说 ok，本地模型把它升级为风险 / raised from ok by the local model
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded')

    def test_a_failing_local_review_never_blocks_planning(self):
        local = Provider('small', 'local', triage_error=RuntimeError('boom'))
        store, job, _ = self.run_task('写一个随机数生成器', local)
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded')
        plan = next(r['data']['project_plan'] for r in store.all_runs() if r['data'].get('project_plan', {}).get('triage'))
        self.assertEqual(plan['triage']['model']['verdict'], 'skipped')

    def test_fixed_mode_runs_the_rules_but_never_calls_a_triage_model(self):
        local = Provider('small', 'local')
        store, job, _ = self.run_task('写一个随机数生成器', local, mode='fixed')
        self.assertNotIn('project_triage', local.purposes)
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded')

    def test_the_console_exposes_the_rules_for_a_live_preview(self):
        with tempfile.TemporaryDirectory() as temp:
            console = Console(Path(temp), Path('x'), Path('y'), Path.cwd())
            try:
                self.assertEqual(console.triage({'goal': '用 Python 写一个脚本'})['verdict'], INFEASIBLE)
                self.assertEqual(console.triage({'goal': '写一个随机数生成器'})['verdict'], OK)
                with self.assertRaises(MasaError):
                    console.triage({'goal': 5})
            finally:
                console.close()


if __name__ == '__main__':
    unittest.main()
