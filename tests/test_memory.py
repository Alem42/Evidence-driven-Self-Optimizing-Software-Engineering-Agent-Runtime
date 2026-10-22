"""包工人的记忆策略与按窗口选择：离开最近几步窗口的信息不会静默丢失；窗口小就换更紧的策略，仍放不下就明确溢出。全部零 token。
Memory strategies of the package worker and the window-based choice: nothing that leaves the recent-steps window is lost silently; a small window gets a tighter strategy and, when even that does not fit, an explicit overflow. Zero tokens."""
import copy
import tempfile
import unittest
from pathlib import Path

from masa.application.planning import ProjectPlanning
from masa.application.v2 import memory
from masa.application.v2.workers import V2Generation
from masa.application.v2.workspace import WorkerWorkspace
from masa.domain.models import ContextOverflow
from masa.domain.tokens import estimate_tokens
from masa.infrastructure import windows
from masa.infrastructure.runner import Runner
from masa.infrastructure.store import Store
from masa.languages.registry import registry
from test_python_pack import APP, CHECKS, MAIN, RUNNER, SPEC, TEST, FakeModel
from test_v2_workers import ScriptedWorker, write

pack = registry.get('python')
FAILING = 'test check found 2 problem(s) in your files:\ncalc/app.py:4: got 2, want 1\ncalc/app.py:9: got 6, want 5\ndetails of the first failures: -2 +1'


def record(step, tool='read_file', ok=True, result='ok', path=None):
    return {'step': step, 'tool': tool, 'path': path, 'ok': ok, 'result': result}


class Stub:
    verdicts, signatures, best = [], [], None


class ViewTests(unittest.TestCase):
    def test_window_is_the_legacy_view_and_nothing_else(self):
        records = [record(i) for i in range(1, 9)]
        self.assertEqual(memory.view(memory.STRATEGIES['window'], records, Stub()), {'last_results': records[-3:]})

    def test_a_digest_strategy_equals_window_until_something_has_left_the_window(self):
        records = [record(i) for i in range(1, 4)]
        self.assertEqual(memory.view(memory.STRATEGIES['digest'], records, Stub()), {'last_results': records})

    def test_an_early_failure_is_still_visible_fifty_steps_later(self):
        """针刺测试：第 5 步的失败检查，到第 55 步仍然在（摘要行 + 钉住项），也能按步号回查原文。 Needle test: the failing check of step 5 is still visible at step 55."""
        records = [record(i, 'write_file', path='calc/app.py', result='wrote calc/app.py (120 characters)') for i in range(1, 5)]
        records.append(record(5, 'run_check', ok=False, result=FAILING))
        records += [record(i, 'read_file' if i % 2 else 'edit_file', path='calc/other.py') for i in range(6, 55)]
        for name in ('digest_wide', 'digest', 'digest_tight'):
            out = memory.view(memory.STRATEGIES[name], records, Stub())
            self.assertIn('got 2, want 1', ' '.join(out['progress_pins']['failing_check']['problems']), name)
            self.assertTrue(any('s5 run_check FAIL' in line or 'folded' in line for line in out['progress_digest']), name)
            self.assertTrue(out['memory']['recall'])

    def test_the_digest_stays_inside_its_budget_however_long_the_loop_is(self):
        records = [record(i, 'edit_file' if i % 3 else 'run_check', ok=bool(i % 3), result=FAILING if i % 3 == 0 else 'wrote calc/app.py (10 characters)', path='calc/app.py') for i in range(1, 400)]
        for name, strategy in memory.STRATEGIES.items():
            if name == 'window':
                continue
            lines = memory.digest_lines(records[:-strategy.keep], strategy)
            self.assertLessEqual(sum(len(line) + 1 for line in lines), strategy.digest_chars + 3 * (strategy.line_chars + 24), name)

    def test_pins_follow_the_latest_check_and_the_referee(self):
        stub = Stub()
        stub.verdicts, stub.signatures = ['referee: the implementation is wrong'], [('a',), ('a',), ('a',)]
        records = [record(1, 'run_check', ok=False, result=FAILING)] + [record(i) for i in range(2, 9)] + [record(9, 'run_check', ok=True, result='test check passed')]
        pins = memory.view(memory.STRATEGIES['digest'], records, stub)['progress_pins']
        self.assertNotIn('failing_check', pins)  # 后来的检查通过了：旧的失败清单不再有效 / a later check passed: the old failing list is obsolete
        self.assertEqual((pins['referee_verdicts'], pins['same_failure_repeats']), (['referee: the implementation is wrong'], 3))


class PinValidityTests(unittest.TestCase):
    """P0 复盘 §4.3：钉住项要按检查种类和代码版本保持有效，而不是“最近一次检查”。 P0 review 4.3: pins stay valid per kind of check and per code version, not "the latest check"."""
    STATIC_FAILING = 'static check found 1 problem(s) in your files:' + chr(10) + 'calc/app.py:1: unused import os'
    STATIC_OK = 'static check passed'

    def view(self, records, stub=None):
        padded = records + [record(i) for i in range(100, 108)]  # 把前面的记录挤出原文窗口，钉住项才会出现 / push the records out of the verbatim window so the pins appear
        return memory.view(memory.STRATEGIES['digest'], padded, stub or Stub()).get('progress_pins', {})

    def test_a_later_passing_static_check_does_not_hide_an_earlier_failing_test(self):
        pins = self.view([record(1, 'run_check', ok=False, result=FAILING), record(2, 'run_check', ok=True, result=self.STATIC_OK)])
        self.assertIn('got 2, want 1', ' '.join(pins['failing_check']['problems']))

    def test_two_kinds_failing_are_both_pinned_the_most_recent_first(self):
        pins = self.view([record(1, 'run_check', ok=False, result=FAILING), record(2, 'run_check', ok=False, result=self.STATIC_FAILING)])
        self.assertEqual(pins['failing_check']['check'], 'static')
        self.assertEqual([p['check'] for p in pins['also_failing']], ['test'])

    def test_a_failing_list_says_how_often_the_code_changed_since(self):
        pins = self.view([record(1, 'run_check', ok=False, result=FAILING), record(2, 'edit_file', path='calc/app.py'), record(3, 'write_file', path='calc/app.py')])
        self.assertEqual(pins['failing_check']['code_changed_since'], 2)

    def test_a_referee_verdict_is_marked_outdated_after_further_edits(self):
        stub = Stub()
        stub.verdicts, stub.verdict_edits, stub.edits = ['referee: test wrong'], [1], 4
        self.assertTrue(self.view([record(1, 'run_check', ok=False, result=FAILING)], stub)['referee_verdicts'][0].startswith('[given 3 edit(s) ago'))
        stub.edits = 2
        self.assertEqual(self.view([record(1, 'run_check', ok=False, result=FAILING)], stub)['referee_verdicts'], ['referee: test wrong'])

    def test_a_best_state_that_cannot_start_is_flagged(self):
        stub = Stub()
        stub.best, stub.best_rank = (1, {}), (True, 1)
        self.assertTrue(self.view([record(1, 'run_check', ok=False, result=FAILING)], stub)['best_state_has_blocking_errors'])


class WorkspaceStateTests(unittest.TestCase):
    def workspace(self):
        space = WorkerWorkspace.__new__(WorkerWorkspace)
        space.repair, space.best, space.best_rank, space.files, space.own_paths = True, None, None, {'calc/app.py': 'x'}, {'calc/app.py'}
        space.signatures, space.exact, space.edit_marks, space.edits, space.failed_kind = [('a',)], [('v',)], [0], 0, 'test'
        return space

    def test_an_import_failure_is_not_a_better_state_than_a_few_failed_assertions(self):
        space = self.workspace()
        space._record(3, 0)           # 3 条断言失败 / three failed assertions
        space._record(1, 1)           # 1 个导入错误：问题更少，但整个测试都起不来 / one import error: fewer problems, but nothing can run
        self.assertEqual(space.best[0], 3)
        space._record(2, 0)           # 2 条断言失败：更好 / two failed assertions: better
        self.assertEqual((space.best[0], space.best_rank), (2, (False, 2)))

    def test_a_passing_check_ends_the_failure_streak_of_its_own_kind_only(self):
        space = self.workspace()
        space._streak_ends('static')  # 另一种检查通过：不打断测试失败的连续 / another kind passed: the test streak goes on
        self.assertEqual(space.signatures, [('a',)])
        space._streak_ends('test')
        self.assertEqual((space.signatures, space.exact, space.failed_kind), ([('a',), None], [('v',), None], None))

    def test_recall_returns_the_full_output_not_the_truncated_copy(self):
        space = WorkerWorkspace.__new__(WorkerWorkspace)
        space.history = [record(i, result='short') for i in range(1, 8)]
        space.memory, space.last_tool = memory.STRATEGIES['digest'], None
        space.full_text = {2: 'F' * 5000}
        self.assertEqual(len(space.recall_step(2)['text'].split(chr(10), 1)[1]), 5000)


class WindowTests(unittest.TestCase):
    def setUp(self):
        windows.forget()

    def fetch(self, show=None, ps=None):
        def fetch(url, body=None, timeout=3):
            return show if url.endswith('/api/show') else ps
        return fetch

    CONFIG = {'base_url': 'http://127.0.0.1:11434/v1', 'model': 'm:latest', 'model_type': 'local', 'protocol': 'ollama', 'context_limit': 16384}

    def test_the_smallest_known_source_wins_and_unknown_ones_are_skipped(self):
        show = {'model_info': {'llama.context_length': 8192}}
        self.assertEqual(windows.resolve(self.CONFIG, fetch=self.fetch(show, {'models': []}))['effective'], 8192)
        windows.forget()
        # 已加载实例的窗口只说明“现在装的是什么”：我们的请求带 num_ctx = 配置值，会把它重新加载，所以不压低有效窗口。 The loaded instance only says what is loaded now: our request carries num_ctx = configured and reloads it, so it does not lower the effective window.
        got = windows.resolve(self.CONFIG, fetch=self.fetch({'model_info': {'x.context_length': 131072}}, {'models': [{'name': 'm:latest', 'context_length': 4096}]}))
        self.assertEqual((got['effective'], got['source'], got['declared'], got['runtime']), (16384, 'configured', 131072, 4096))
        windows.forget()
        unconfigured = {k: v for k, v in self.CONFIG.items() if k != 'context_limit'}  # 没配置值就没发 num_ctx：此时已加载实例的大小才是我们会得到的 / without a configured value no num_ctx is sent: the loaded instance's size is what we get
        got = windows.resolve(unconfigured, fetch=self.fetch({'model_info': {'x.context_length': 131072}}, {'models': [{'name': 'm:latest', 'context_length': 4096}]}))
        self.assertEqual((got['effective'], got['source']), (4096, 'runtime'))
        windows.forget()
        offline = windows.resolve(self.CONFIG, fetch=lambda *a, **k: None)  # 服务不可达：只剩配置值，不是 0 也不是无穷 / unreachable: only the configured value remains, neither 0 nor infinite
        self.assertEqual((offline['effective'], offline['source']), (16384, 'configured'))

    def test_a_cloud_model_uses_its_declared_window_when_the_profile_has_one(self):
        got = windows.resolve({'model_type': 'cloud', 'protocol': 'openai', 'model': 'x', 'context_limit': 262144, 'context_window': 65536})
        self.assertEqual((got['effective'], got['source']), (65536, 'declared'))

    def test_the_strategy_follows_the_input_budget(self):
        spec = memory.start_strategy
        reserve, instruction = 4096, 2500
        for window, expected in ((16_384, 'digest_tight'), (32_768, 'digest'), (131_072, 'digest_wide'), (None, 'digest')):
            budget = memory.input_budget(window, reserve, instruction)
            self.assertEqual(spec('auto', budget).name, expected, window)
        self.assertEqual(spec('window', 1).name, 'window')  # 显式指定的不被改写 / an explicit choice is never rewritten

    def test_fit_steps_to_a_tighter_tier_and_overflows_only_when_nothing_fits(self):
        records = [record(i, result='x' * 2500) for i in range(1, 30)]
        build = lambda strategy: {'goal': 'g', **memory.view(strategy, records, Stub())}  # noqa: E731
        loose = estimate_tokens(build(memory.STRATEGIES['digest_wide']))
        tight = estimate_tokens(build(memory.STRATEGIES['digest_tight']))
        self.assertLess(tight, loose)
        chosen, _ = memory.fit(memory.STRATEGIES['digest_wide'], (loose + tight) // 2, build)
        self.assertNotEqual(chosen.name, 'digest_wide')
        with self.assertRaises(ContextOverflow):
            memory.fit(memory.STRATEGIES['digest_wide'], 50, build, model='tiny', limit=2048)


class WorkspaceRecallTests(unittest.TestCase):
    def workspace(self, strategy):
        space = WorkerWorkspace.__new__(WorkerWorkspace)
        space.history = [record(i, result=f'result of step {i}') for i in range(1, 8)]
        space.memory, space.last_tool = strategy, None
        return space

    def test_recall_returns_the_old_result_once_in_a_row_and_only_when_offered(self):
        space = self.workspace(memory.STRATEGIES['digest'])
        first = space.recall_step(2)
        self.assertTrue(first['ok'])
        self.assertIn('result of step 2', first['text'])
        space.last_tool = 'recall_step'
        self.assertFalse(space.recall_step(3)['ok'])  # 连着回查被拒绝 / recalling twice in a row is refused
        space.last_tool = None
        self.assertFalse(space.recall_step(99)['ok'])
        self.assertFalse(self.workspace(memory.STRATEGIES['window']).recall_step(2)['ok'])

    def test_the_response_schema_offers_recall_only_with_a_memory_strategy_that_has_it(self):
        from masa.agents.schemas import response_schema
        base = {'purpose': 'package_worker', 'language': 'python'}
        tools = lambda ctx: {(o['properties']['tool'])['const'] for o in response_schema(ctx)['oneOf']}  # noqa: E731
        self.assertNotIn('recall_step', tools(base))
        self.assertIn('recall_step', tools({**base, 'memory': {'strategy': 'digest', 'recall': True}}))


@unittest.skipUnless(RUNNER.is_file() and pack.probe().ok, 'built runner and a usable Python interpreter are required')
class LoopTests(unittest.TestCase):
    def run_flow(self, actions, strategy):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(Path(temp.name))
        self.addCleanup(store.close)
        runner = Runner(RUNNER)
        planning = ProjectPlanning(store, runner)
        parent = planning.generate(FakeModel(), 'sum integers', language='python')
        meta = store.run(parent)['data']['project_plan']
        planning.approve(parent, {'spec_ref': meta['spec_ref'], 'checks_ref': meta['checks_ref'], 'spec': SPEC, 'checks': CHECKS})
        service = V2Generation(store, runner)
        service.memory_strategy = strategy
        worker = ScriptedWorker(actions)
        service.generate(parent, worker)
        return worker, store

    SCRIPT = [write('calc/app.py', APP), write('calc/__main__.py', MAIN), write('tests/test_app.py', TEST), {'tool': 'note', 'text': 'plan'},
              {'tool': 'recall_step', 'step': 1}, {'tool': 'run_check', 'kind': 'test'}]

    def test_the_digest_strategy_adds_a_digest_and_recall_without_touching_the_first_steps(self):
        worker, store = self.run_flow(copy.deepcopy(self.SCRIPT), 'digest')
        self.assertNotIn('progress_digest', worker.contexts[3])  # 还没有结果离开窗口：与 window 相同 / nothing has left the window yet: same as window
        late = worker.contexts[5]
        self.assertTrue(any(line.startswith('s1 write_file calc/app.py') for line in late['progress_digest']))
        self.assertEqual(late['memory']['strategy'], 'digest')
        recalled = late['last_results'][-1]
        self.assertEqual((recalled['tool'], recalled['ok']), ('recall_step', True))
        self.assertIn('wrote calc/app.py', recalled['result'])
        self.assertTrue([e for e in store.events(next(r['id'] for r in store.all_runs() if r['data'].get('project_plan', {}).get('generation_mode') == 'workers-v1')) if e['type'] == 'memory_strategy'])

    def test_the_default_window_strategy_never_adds_memory_fields(self):
        script = [a for a in copy.deepcopy(self.SCRIPT) if a.get('tool') != 'recall_step']
        worker, store = self.run_flow(script, 'window')
        for context in worker.contexts:
            self.assertFalse({'progress_digest', 'progress_pins', 'memory', 'memory_notes'} & set(context))


if __name__ == '__main__':
    unittest.main()
