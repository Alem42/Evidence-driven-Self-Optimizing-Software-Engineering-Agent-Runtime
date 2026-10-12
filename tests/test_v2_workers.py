"""Runtime V2 的包工人循环：脚本化的“模型”+ 真实运行器（零 token）。 The Runtime V2 package worker loop with a scripted "model" and the real runner (zero tokens)."""
import copy
import tempfile
import unittest
from pathlib import Path

from masa.agents.protocol import validate_response
from masa.application.planning import ProjectPlanning
from masa.application.v2.workers import MAX_STEPS, V2Generation
from masa.domain.models import MasaError
from masa.infrastructure.runner import Runner
from masa.infrastructure.store import Store
from masa.languages.registry import registry
from masa.runtime.engine import Runtime
from test_python_pack import APP, CHECKS, FILES, MAIN, RUNNER, SPEC, TEST, FakeModel

NL = chr(10)
pack = registry.get('python')


class ScriptedWorker(FakeModel):
    """按脚本逐步给出动作的包工人；每个动作都经过与真实模型相同的解码后契约。 Gives the scripted actions one by one, each through the same decoded contract as a real model."""

    def __init__(self, actions):
        super().__init__()
        self.actions, self.seen = list(actions), []

    def respond(self, context):
        if context['purpose'] != 'package_worker':
            return super().respond(context)
        self.contexts.append(copy.deepcopy(context))
        if not self.actions:
            raise AssertionError('the script ran out of actions')
        action = self.actions.pop(0)
        action = action(context) if callable(action) else action
        return validate_response(context, action, '')


def write(path, content):
    return {'tool': 'write_file', 'path': path, 'content': content}


GOOD = [write('calc/app.py', APP), write('calc/__main__.py', MAIN), write('tests/test_app.py', TEST), {'tool': 'run_check', 'kind': 'test'}, {'tool': 'finish'}]


@unittest.skipUnless(RUNNER.is_file() and pack.probe().ok, 'built runner and a usable Python interpreter are required')
class WorkerLoopTests(unittest.TestCase):
    def run_flow(self, actions, execute=True):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        store = Store(Path(self.temp.name))
        self.addCleanup(store.close)
        runner = Runner(RUNNER)
        planning = ProjectPlanning(store, runner)
        parent = planning.generate(FakeModel(), 'sum integers', language='python')
        meta = store.run(parent)['data']['project_plan']
        planning.approve(parent, {'spec_ref': meta['spec_ref'], 'checks_ref': meta['checks_ref'], 'spec': SPEC, 'checks': CHECKS})
        service = V2Generation(store, runner)
        worker = ScriptedWorker(actions)
        draft = service.generate(parent, worker)
        meta = store.run(draft)['data']['project_plan']
        files = store.read(meta['files_ref'])
        verified = service.approve(draft, {'files_ref': meta['files_ref'], 'files': files})
        result = Runtime(store, runner).execute(verified) if execute else None
        events = store.events(draft)
        return store, draft, worker, files, result, events

    def test_a_worker_writes_checks_and_seals_its_package_and_the_gate_passes(self):
        store, draft, worker, files, result, events = self.run_flow(GOOD)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(files['calc/app.py'], APP)
        self.assertEqual(store.read(store.run(draft)['data']['project_plan']['spec_approval_ref'])['spec']['module'], 'calc')
        steps = [e['payload'] for e in events if e['type'] == 'worker_step']
        self.assertEqual([s['tool'] for s in steps], ['write_file', 'write_file', 'write_file', 'run_check', 'finish'])
        self.assertTrue(all(s['ok'] for s in steps))
        sealed = [e['payload'] for e in events if e['type'] == 'package_sealed']
        self.assertEqual([s['package'] for s in sealed], ['calc'])
        self.assertEqual(store.run(draft)['data']['project_plan']['generation_mode'], 'workers-v1')
        self.assertEqual(store.run(draft)['model_calls'], len(GOOD))  # 每一步一次调用，没有别的 / one call per step, nothing else
        self.assertTrue(all(c.get('language') == 'python' for c in worker.contexts))
        self.assertIn('Python 3 project', worker.contexts[0]['language_notes'])

    def test_the_package_is_sealed_automatically_once_every_check_passes_on_the_current_files(self):
        actions = [write('calc/app.py', APP), write('calc/__main__.py', MAIN), write('tests/test_app.py', TEST), {'tool': 'run_check', 'kind': 'test'}, {'tool': 'run_check', 'kind': 'static'}]
        store, draft, worker, files, result, events = self.run_flow(actions)
        self.assertEqual(result['status'], 'succeeded')
        steps = [e['payload'] for e in events if e['type'] == 'worker_step']
        self.assertEqual(steps[-1]['tool'], 'run_check')
        self.assertIn('package sealed', steps[-1]['summary'])  # 没有调用 finish，也封包了 / sealed without a finish call

    def test_pointless_actions_are_pushed_back(self):
        actions = [write('calc/app.py', APP), write('calc/app.py', APP), {'tool': 'read_file', 'path': 'calc/app.py'}, *GOOD[1:]]
        store, draft, worker, files, result, events = self.run_flow(actions, execute=False)
        steps = [e['payload'] for e in events if e['type'] == 'worker_step']
        self.assertFalse(steps[1]['ok'])
        self.assertIn('already has exactly this content', steps[1]['summary'])
        self.assertIn('no need to read them', steps[2]['summary'])

    def test_a_failing_check_is_shown_and_the_worker_fixes_it(self):
        buggy = APP.replace('sum(int(item) for item in text.split())', 'sum(int(item) for item in text.split()) + 1')
        actions = [write('calc/app.py', buggy), write('calc/__main__.py', MAIN), write('tests/test_app.py', TEST), {'tool': 'run_check', 'kind': 'test'},
                   {'tool': 'edit_file', 'path': 'calc/app.py', 'old': ' + 1', 'new': ''}, {'tool': 'run_check', 'kind': 'test'}, {'tool': 'finish'}]
        store, draft, worker, files, result, events = self.run_flow(actions)
        self.assertEqual(result['status'], 'succeeded')
        shown = worker.contexts[4]['last_results'][-1]  # 第一次检查之后模型看到的结果 / what the model saw after the first check
        self.assertEqual((shown['tool'], shown['ok']), ('run_check', False))
        self.assertIn('problem(s) in your files', shown['result'])
        self.assertIn('AssertionError: 7 != 6', shown['result'])
        self.assertNotIn(' + 1', files['calc/app.py'])

    def test_writes_outside_the_package_are_refused_and_do_nothing(self):
        actions = [write('calc/other.py', 'x = 1' + NL), write('pyproject.toml', 'evil'), *GOOD]
        store, draft, worker, files, result, events = self.run_flow(actions, execute=False)
        self.assertNotIn('calc/other.py', files)
        self.assertTrue(files['pyproject.toml'].startswith('[project]'))  # 清单仍是规格生成的 / the manifest is still the spec's
        refused = [e['payload'] for e in events if e['type'] == 'worker_step'][:2]
        self.assertEqual([r['ok'] for r in refused], [False, False])
        self.assertIn('outside your package', refused[0]['summary'])

    def test_finish_is_refused_while_the_checks_still_fail(self):
        actions = [write('calc/app.py', 'def total(text):' + NL + '    return 0' + NL + NL + NL + 'def run(a, i, o, e):' + NL + '    return 0' + NL), write('calc/__main__.py', MAIN),
                   write('tests/test_app.py', TEST), {'tool': 'finish'}, write('calc/app.py', APP), {'tool': 'finish'}]
        store, draft, worker, files, result, events = self.run_flow(actions)
        steps = [e['payload'] for e in events if e['type'] == 'worker_step']
        self.assertFalse(steps[3]['ok'])
        self.assertIn('cannot finish yet', steps[3]['summary'])
        self.assertTrue(steps[5]['ok'])
        self.assertEqual(result['status'], 'succeeded')

    def test_a_package_that_never_converges_stops_after_the_step_limit(self):
        with self.assertRaisesRegex(MasaError, 'did not converge'):
            self.run_flow([{'tool': 'note', 'text': 'thinking'}] * MAX_STEPS, execute=False)

    def test_an_edit_needs_a_unique_match_and_quick_problems_are_reported_on_write(self):
        actions = [write('calc/app.py', 'def f(:' + NL), {'tool': 'edit_file', 'path': 'calc/app.py', 'old': 'zzz', 'new': 'x'}, *GOOD]
        store, draft, worker, files, result, events = self.run_flow(actions, execute=False)
        steps = [e['payload'] for e in events if e['type'] == 'worker_step']
        self.assertIn('Quick local check found problems', worker.contexts[1]['last_results'][0]['result'])
        self.assertFalse(steps[1]['ok'])
        self.assertIn('exactly once', steps[1]['summary'])


GO = Path(__file__).resolve().parents[1] / '.tools/go/bin/go.exe'


@unittest.skipUnless(RUNNER.is_file() and GO.is_file(), 'built Go runner required')
class GoWorkerTests(unittest.TestCase):
    """同一个工人循环对 Go 同样有效（语言无关）：两个包，入口最后。 The same worker loop works for Go (language-neutral): two packages, the entry last."""

    def test_two_go_packages_are_sealed_in_order_and_the_gate_passes(self):
        from unittest.mock import patch
        self.enter = patch('masa.application.v2.workers.SOLO_FILES', 0)  # 强制按包拆分（小任务默认合成一个包）/ force the per-package split (small tasks are one package by default)
        self.enter.start()
        self.addCleanup(self.enter.stop)
        from test_project_generation import FILES as GO_FILES
        from test_project_plan import CHECKS as GO_CHECKS, SPEC as GO_SPEC, PlannerProvider
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                runner = Runner(RUNNER, GO)
                planning = ProjectPlanning(store, runner)
                parent = planning.generate(PlannerProvider(), 'Build a small CLI')
                meta = store.run(parent)['data']['project_plan']
                planning.approve(parent, {'spec_ref': meta['spec_ref'], 'checks_ref': meta['checks_ref'], 'spec': GO_SPEC, 'checks': GO_CHECKS})
                actions = [write('internal/app/app.go', GO_FILES['internal/app/app.go']), write('internal/app/app_test.go', GO_FILES['internal/app/app_test.go']),
                           {'tool': 'run_check', 'kind': 'test'}, {'tool': 'finish'},
                           write('cmd/app/main.go', GO_FILES['cmd/app/main.go']), {'tool': 'finish'}]

                class GoWorker(ScriptedWorker):
                    def respond(self, context):
                        if context['purpose'] in ('project_planner', 'project_tester'):
                            return PlannerProvider.respond(self, context)
                        return super().respond(context)
                service = V2Generation(store, runner)
                worker = GoWorker(actions)
                draft = service.generate(parent, worker)
                meta = store.run(draft)['data']['project_plan']
                files = store.read(meta['files_ref'])
                self.assertEqual(files, GO_FILES)
                self.assertEqual([e['payload']['package'] for e in store.events(draft) if e['type'] == 'package_sealed'], ['internal/app', 'cmd/app'])
                verified = service.approve(draft, {'files_ref': meta['files_ref'], 'files': files})
                self.assertEqual(Runtime(store, runner).execute(verified)['status'], 'succeeded')
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
