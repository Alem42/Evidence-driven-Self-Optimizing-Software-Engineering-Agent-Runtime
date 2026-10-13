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
        if isinstance(action, Exception):  # 模型回答了但不可用：像真实提供者一样先记下诊断再失败 / an unusable answer: record the diagnostic first, like the real provider, then fail
            self.contract_diagnostic = getattr(action, 'diagnostic', None)
            raise action
        return validate_response(context, action, '')


def unusable(kind='invalid_json'):
    error = MasaError('model action JSON invalid at line 1, column 577; no automatic retry')
    error.diagnostic = {'action_type': kind, 'top_keys': [], 'reason': "Expecting property name enclosed in double quotes", 'position': 577}
    return error


def write(path, content):
    return {'tool': 'write_file', 'path': path, 'content': content}


GOOD = [write('calc/app.py', APP), write('calc/__main__.py', MAIN), write('tests/test_app.py', TEST), {'tool': 'run_check', 'kind': 'test'}]  # 测试通过后工具自己跑静态检查并封包 / after the tests pass the tool runs the static check itself and seals


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
        self.assertEqual([s['tool'] for s in steps], ['write_file', 'write_file', 'write_file', 'run_check'])
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

    def test_a_malformed_action_is_answered_by_the_tool_instead_of_failing_the_stage(self):
        # 真实运行：本地 glm 的 write_file 没有 content。以前这会让整个生成阶段失败重来；现在工具把原因回给模型，下一步它改正。 Real run: the local glm's write_file had no content. It used to fail and restart the whole stage; now the tool answers and the model corrects itself.
        actions = [{'tool': 'write_file', 'path': 'calc/app.py'}, {'tool': 'make_coffee'}, *GOOD]
        store, draft, worker, files, result, events = self.run_flow(actions)
        self.assertEqual(result['status'], 'succeeded')
        steps = [e['payload'] for e in events if e['type'] == 'worker_step']
        self.assertEqual([s['ok'] for s in steps[:2]], [False, False])
        self.assertIn('needs a "content" field', steps[0]['summary'])
        self.assertIn("unknown tool 'make_coffee'", steps[1]['summary'])
        self.assertEqual(steps[0]['keys'], ['path', 'tool'])

    def test_an_unusable_reply_is_a_refused_step_not_a_failed_stage(self):
        # 真实运行：模型在 JSON 末尾多写了 `,"}`，7 步工作随整个生成阶段作废。现在这一步被拒绝、带着原因让模型再来一次。 Real run: a stray `,"}` voided seven steps of work with the whole stage.
        actions = [write('calc/app.py', APP), unusable(), *GOOD[1:]]
        store, draft, worker, files, result, events = self.run_flow(actions)
        self.assertEqual(result['status'], 'succeeded')
        steps = [e['payload'] for e in events if e['type'] == 'worker_step']
        self.assertEqual((steps[1]['ok'], steps[1]['unusable_reply']), (False, 'invalid_json'))
        self.assertIn('not valid JSON', worker.contexts[2]['last_results'][-1]['result'])
        self.assertEqual(store.run(draft)['model_calls'], len(GOOD) + 1)

    def test_one_silent_model_call_does_not_fail_the_whole_task(self):
        # 真实运行：28 次调用里有一次服务没有回答，整个 bank 任务因此失败。现在这一步算失败，稍等后继续；连续 3 次才放弃。
        # Real run: one of 28 calls got no answer and the whole bank task failed. Now that step counts as failed, the loop waits a moment and goes on; only 3 in a row give up.
        from unittest.mock import patch
        from masa.domain.models import TransportFailure
        with patch('masa.application.v2.workers.time') as clock:
            store, draft, worker, files, result, events = self.run_flow([write('calc/app.py', APP), TransportFailure('no response received'), *GOOD[1:]])
        self.assertEqual(result['status'], 'succeeded')
        steps = [e['payload'] for e in events if e['type'] == 'worker_step']
        self.assertTrue(steps[1]['transport_failure'])
        self.assertEqual(clock.sleep.call_count, 1)
        with patch('masa.application.v2.workers.time'), self.assertRaises(MasaError):
            self.run_flow([TransportFailure('no response received')] * 3, execute=False)

    def test_four_unusable_replies_in_a_row_are_stuck(self):
        with self.assertRaisesRegex(MasaError, 'is stuck after 4 steps'):
            self.run_flow([unusable('incomplete')] * 4, execute=False)

    def test_the_local_structured_output_schema_requires_the_fields_of_each_tool(self):
        from masa.agents.schemas import response_schema
        schema = response_schema({'purpose': 'package_worker', 'language': 'python'})
        write = next(o for o in schema['oneOf'] if o['properties']['tool'] == {'const': 'write_file'})
        self.assertEqual(sorted(write['required']), ['content', 'path', 'tool'])
        self.assertEqual(len(schema['oneOf']), 6)

    def test_finish_before_writing_says_exactly_what_to_write_next(self):
        # 真实运行：本地模型第一步就 finish，连续四次，两次尝试都这样。 Real run: the local model answered finish at once, four times, in two attempts.
        actions = [{'tool': 'finish'}, *GOOD]
        store, draft, worker, files, result, events = self.run_flow(actions, execute=False)
        steps = [e['payload'] for e in events if e['type'] == 'worker_step']
        self.assertIn('Your next reply must be exactly: {"tool":"write_file","path":"calc/__main__.py"', steps[0]['summary'])

    def test_a_weak_model_cannot_pick_finish_before_anything_is_written(self):
        # 真实运行：本地 glm 在第一步连续四次选 finish（两次尝试、两个任务都如此）。约束解码的 schema 在写出文件之前只剩 write_file。
        # Real runs: the local glm chose finish four times in a row at step 1 (two attempts, two tasks). Before a file exists the constrained-decoding schema keeps only write_file.
        from masa.agents.schemas import response_schema
        store, draft, worker, files, result, events = self.run_flow(GOOD, execute=False)
        self.assertEqual(worker.contexts[0]['allowed_tools'], ['write_file'])
        self.assertEqual([o['properties']['tool']['const'] for o in response_schema(worker.contexts[0])['oneOf']], ['write_file'])
        self.assertNotIn('finish', worker.contexts[1]['allowed_tools'])  # 还有计划文件没写 / planned files are still missing
        self.assertNotIn('allowed_tools', worker.contexts[3])  # 都写完了：什么都可以 / everything written: no restriction

    def test_the_same_failing_check_is_not_run_again_before_something_changed(self):
        # 真实运行：本地模型连续 finish 了好几次，每次都是同样的失败，十分钟就这样过去了。 Real run: the local model called finish several times, always the same failure, and ten minutes went by.
        from masa.agents.schemas import response_schema
        buggy = APP.replace('sum(int(item) for item in text.split())', 'sum(int(item) for item in text.split()) + 1')
        actions = [write('calc/app.py', buggy), write('calc/__main__.py', MAIN), write('tests/test_app.py', TEST), {'tool': 'run_check', 'kind': 'test'}, {'tool': 'run_check', 'kind': 'test'}, {'tool': 'finish'},
                   {'tool': 'edit_file', 'path': 'calc/app.py', 'old': ' + 1', 'new': ''}, {'tool': 'run_check', 'kind': 'test'}]
        store, draft, worker, files, result, events = self.run_flow(actions)
        steps = [e['payload'] for e in events if e['type'] == 'worker_step']
        self.assertEqual([s['ok'] for s in steps[3:6]], [False, False, False])
        self.assertIn('nothing was changed since the last failing check', steps[4]['summary'])
        self.assertIn('nothing was changed since the last failing check', steps[5]['summary'])
        self.assertEqual(worker.contexts[4]['allowed_tools'], ['edit_file', 'write_file', 'read_file', 'note'])  # 约束解码下连 run_check/finish 都选不了 / under constrained decoding run_check and finish cannot even be chosen
        self.assertEqual({o['properties']['tool']['const'] for o in response_schema(worker.contexts[4])['oneOf']}, {'edit_file', 'write_file', 'read_file', 'note'})
        self.assertEqual(result['status'], 'succeeded')

    def test_a_second_note_in_a_row_is_pushed_back(self):
        actions = [{'tool': 'note', 'text': 'a'}, {'tool': 'note', 'text': 'b'}, *GOOD]
        store, draft, worker, files, result, events = self.run_flow(actions, execute=False)
        steps = [e['payload'] for e in events if e['type'] == 'worker_step']
        self.assertEqual([s['ok'] for s in steps[:2]], [True, False])
        self.assertIn('act now', steps[1]['summary'])

    def test_pointless_actions_are_pushed_back(self):
        actions = [write('calc/app.py', APP), write('calc/app.py', APP), {'tool': 'read_file', 'path': 'calc/app.py'}, *GOOD[1:]]
        store, draft, worker, files, result, events = self.run_flow(actions, execute=False)
        steps = [e['payload'] for e in events if e['type'] == 'worker_step']
        self.assertFalse(steps[1]['ok'])
        self.assertIn('already has exactly this content', steps[1]['summary'])
        self.assertIn('reading them again gains nothing', steps[2]['summary'])

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
            self.run_flow([write('calc/app.py', APP + '# ' + str(n) + NL) for n in range(MAX_STEPS)], execute=False)  # 每步都改一点、永远封不了包 / every step changes something and the package never seals

    def test_an_edit_needs_a_unique_match_and_quick_problems_are_reported_on_write(self):
        actions = [write('calc/app.py', 'def f(:' + NL), {'tool': 'edit_file', 'path': 'calc/app.py', 'old': 'zzz', 'new': 'x'}, *GOOD]
        store, draft, worker, files, result, events = self.run_flow(actions, execute=False)
        steps = [e['payload'] for e in events if e['type'] == 'worker_step']
        self.assertIn('Quick local check found problems', worker.contexts[1]['last_results'][0]['result'])
        self.assertFalse(steps[1]['ok'])
        self.assertIn('exactly once', steps[1]['summary'])


# ───────────── 多包：依赖顺序、推迟、卡住检测、失败后沿用已封包的包 / several packages: order, deferral, stuck detection, reuse after a failure ─────────────
PARSER = 'def parse(text):' + NL + '    return [int(item) for item in text.split()]' + NL
PARSER_TEST = NL.join(['import unittest', '', 'from calc.parser.parser import parse', '', '', 'class ParserTests(unittest.TestCase):', '    def test_parse(self):', '        self.assertEqual(parse("1 2"), [1, 2])', ''])
APP2 = NL.join(['from calc.parser.parser import parse', '', '', 'def run(args, stdin, stdout, stderr):', '    print(sum(parse(stdin.read())), file=stdout)', '    return 0', ''])
APP2_TEST = NL.join(['import io', 'import unittest', '', 'from calc.app.app import run', '', '', 'class AppTests(unittest.TestCase):', '    def test_run(self):', '        out = io.StringIO()',
                     '        self.assertEqual(run([], io.StringIO("5 5"), out, io.StringIO()), 0)', '        self.assertEqual(out.getvalue(), "10" + chr(10))', ''])
MAIN2 = MAIN.replace('from calc.app import run', 'from calc.app.app import run')
SPEC2 = {'summary': 'sum integers', 'module': 'calc', 'entrypoint': 'calc/__main__.py',
         'files': [{'path': 'pyproject.toml', 'purpose': 'metadata'}, {'path': 'calc/__init__.py', 'purpose': 'package'},
                   {'path': 'calc/app/__init__.py', 'purpose': 'package'}, {'path': 'calc/app/app.py', 'purpose': 'run: reads stdin, uses the parser package to parse the integers and prints their sum'},
                   {'path': 'calc/parser/__init__.py', 'purpose': 'package'}, {'path': 'calc/parser/parser.py', 'purpose': 'parse whitespace separated integers'},
                   {'path': 'calc/__main__.py', 'purpose': 'thin entry that calls the app package'}, {'path': 'tests/test_parser.py', 'purpose': 'tests of the parser'},
                   {'path': 'tests/test_app.py', 'purpose': 'tests of run'}],
         'acceptance': ['prints the sum of the integers on stdin followed by a newline']}


def comment(old, new):
    """改一个注释：文件变了，但失败原样不变 / change a comment: the file changes, the failure does not"""
    return {'tool': 'edit_file', 'path': 'calc/app/app.py', 'old': old, 'new': new}


def check(kind='test'):
    return {'tool': 'run_check', 'kind': kind}


@unittest.skipUnless(RUNNER.is_file() and pack.probe().ok, 'built runner and a usable Python interpreter are required')
class SeveralPackagesTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        enter = patch('masa.application.v2.workers.SOLO_FILES', 0)  # 强制按包拆分 / force the per-package split
        enter.start()
        self.addCleanup(enter.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.addCleanup(self.store.close)
        self.runner = Runner(RUNNER)
        planning = ProjectPlanning(self.store, self.runner)

        class Planner(FakeModel):
            def respond(self, context):
                self.contexts.append(copy.deepcopy(context))
                return copy.deepcopy(SPEC2 if context['purpose'] == 'project_planner' else CHECKS)
        self.parent = planning.generate(Planner(), 'sum integers', language='python')
        meta = self.store.run(self.parent)['data']['project_plan']
        planning.approve(self.parent, {'spec_ref': meta['spec_ref'], 'checks_ref': meta['checks_ref'], 'spec': SPEC2, 'checks': CHECKS})
        self.service = V2Generation(self.store, self.runner)

    def generate(self, actions, **kwargs):
        worker = ScriptedWorker(actions)
        draft = self.service.generate(self.parent, worker, **kwargs)
        return draft, worker

    def events(self, rid, kind):
        return [e['payload'] for e in self.store.events(rid) if e['type'] == kind]

    def finish(self, draft):
        meta = self.store.run(draft)['data']['project_plan']
        files = self.store.read(meta['files_ref'])
        verified = self.service.approve(draft, {'files_ref': meta['files_ref'], 'files': files})
        return files, Runtime(self.store, self.runner).execute(verified)

    def failed_run(self):
        return [r for r in self.store.all_runs() if r['data'].get('parent_run_id') == self.parent and r['status'] == 'failed'][0]

    def test_packages_are_written_leaves_first_and_the_entry_last(self):
        actions = [write('calc/parser/parser.py', PARSER), write('tests/test_parser.py', PARSER_TEST), check('test'),
                   write('calc/app/app.py', APP2), write('tests/test_app.py', APP2_TEST), check('test'),
                   write('calc/__main__.py', MAIN2), check('test')]
        draft, worker = self.generate(actions)
        order = self.events(draft, 'package_order')[0]
        self.assertEqual(order['order'], ['calc/parser', 'calc/app', 'calc'])  # 规格里 app 在 parser 前面，但 app 的用途提到了 parser / the spec lists app before parser, but app's purpose mentions the parser
        self.assertEqual(order['depends_on']['calc/app'], ['calc/parser'])
        self.assertEqual([e['package'] for e in self.events(draft, 'package_sealed')], ['calc/parser', 'calc/app', 'calc'])
        files, result = self.finish(draft)
        self.assertEqual(result['status'], 'succeeded', result)

    def test_a_package_that_needs_an_unwritten_package_is_deferred_behind_it(self):
        from unittest.mock import patch
        actions = [write('calc/app/app.py', APP2), write('tests/test_app.py', APP2_TEST), check('test'),  # app 先写，测试因为 parser 还不存在而失败 / app first; its test fails because the parser does not exist yet
                   write('calc/parser/parser.py', PARSER), write('tests/test_parser.py', PARSER_TEST), check('test'),
                   check('test'),  # app 回来：什么都不用改 / app is back: nothing to change
                   write('calc/__main__.py', MAIN2), check('test')]
        with patch('masa.application.v2.workers.dependency_edges', lambda pack, spec, groups: {d: set() for d, _ in groups}):  # 不靠启发式排序，逼出“推迟” / no heuristic order: force the deferral
            draft, worker = self.generate(actions)
        deferred = self.events(draft, 'package_deferred')
        self.assertEqual([(d['package'], d['waiting_for']) for d in deferred], [('calc/app', ['calc/parser'])])
        self.assertEqual([e['package'] for e in self.events(draft, 'package_sealed')], ['calc/parser', 'calc/app', 'calc'])
        files, result = self.finish(draft)
        self.assertEqual(result['status'], 'succeeded', result)

    def stuck_actions(self):
        buggy = APP2.replace('print(sum(parse(stdin.read())), file=stdout)', 'print(sum(parse(stdin.read())) + 1, file=stdout)')
        return [write('calc/parser/parser.py', PARSER), write('tests/test_parser.py', PARSER_TEST), check('test'),
                write('calc/app/app.py', buggy), write('tests/test_app.py', APP2_TEST), check('test'), comment('return 0', 'return 0  # a'), check('test'), comment('# a', '# b'), check('test')]

    def test_the_same_failure_three_times_in_a_row_is_stuck_and_stops_early(self):
        from masa.application.v2.workers import Stuck
        with self.assertRaisesRegex(MasaError, 'package calc/app is stuck after 7 steps'):
            self.generate(self.stuck_actions())
        stuck = self.events(self.failed_run()['id'], 'worker_stuck')
        self.assertEqual((stuck[0]['package'], stuck[0]['step']), ('calc/app', 7))  # 在包内第 7 步就停了，没有烧到 16 步 / stopped at the 7th step of the package, not after 16
        self.assertIn('3 checks in a row', stuck[0]['why'])

    def test_an_unchanged_actual_value_points_at_the_expectation(self):
        # 真实运行（grades）：测试里的期望值算错了，工人五次尝试都只改实现，实际值一点没变。 Real run (grades): the expected value in the test was wrong and the worker only changed the implementation, the actual value never moved.
        from masa.application.v2.workers import Stuck
        worker = ScriptedWorker(self.stuck_actions())
        with self.assertRaises(MasaError):
            self.service.generate(self.parent, worker)
        seen = [c['last_results'][-1]['result'] for c in worker.contexts if c.get('last_results') and c['last_results'][-1]['tool'] == 'run_check' and not c['last_results'][-1]['ok']]
        self.assertTrue(any('ACTUAL value is exactly the same' in text for text in seen), seen)

    def test_the_referee_is_asked_when_the_same_assertion_keeps_failing_and_its_verdict_comes_first(self):
        # 真实运行（grades）：工人的测试期望算错了（91.13 而不是 91.33），5 次尝试都在改对的那一边。裁判在打乱成 A/B 的两个值里选。
        # Real run (grades): the worker's test expected a wrongly computed value (91.13 instead of 91.33) and five attempts kept changing the correct side. The referee chooses between the two values shuffled into A/B.
        class Referee:
            profile = {'provider': 'test', 'model': 'referee'}
            usage = {'total_tokens': 1}

            def __init__(self):
                self.contexts = []

            def respond(self, context):
                self.contexts.append(context)
                answers = []
                for case in context['cases']:  # 选出现在实现给的那个值 "10" / pick the value the implementation gave, "10"
                    answers.append({'index': case['index'], 'choice': 'A' if '10' in case['options']['A'] else 'B'})
                return {'answers': answers}
        referee = Referee()
        self.service.referee_provider = referee
        wrong_test = APP2_TEST.replace('"10" + chr(10)', '"11" + chr(10)')
        actions = [write('calc/parser/parser.py', PARSER), write('tests/test_parser.py', PARSER_TEST), check('test'),
                   write('calc/app/app.py', APP2), write('tests/test_app.py', wrong_test), check('test'), comment('return 0', 'return 0  # a'), check('test'),
                   {'tool': 'edit_file', 'path': 'tests/test_app.py', 'old': '"11"', 'new': '"10"'}, check('test'),
                   write('calc/__main__.py', MAIN2), check('test')]
        draft, worker = self.generate(actions)
        shown = [c['last_results'][-1]['result'] for c in worker.contexts if c.get('last_results') and c['last_results'][-1]['tool'] == 'run_check' and not c['last_results'][-1]['ok']]
        self.assertEqual(len(shown), 2)
        self.assertNotIn('Independent evidence', shown[0])  # 第一次失败不请裁判 / the first failure does not call the referee
        self.assertTrue(shown[1].startswith('Independent evidence'), shown[1][:200])  # 第二次：裁判的结论放在最前面 / the second one: the verdict comes first
        self.assertIn('WRONG', shown[1])
        self.assertEqual(len(referee.contexts), 3)  # 三次独立选择 / three independent choices
        self.assertEqual([e['verdicts'] for e in self.events(draft, 'assertion_referee')], [{'0': 'test_wrong'}])
        self.assertIn('acceptance', referee.contexts[0])

    def test_the_second_check_with_the_same_failure_carries_a_warning_to_try_something_else(self):
        from masa.application.v2.workers import Stuck
        worker = ScriptedWorker(self.stuck_actions())
        with self.assertRaises(MasaError):
            self.service.generate(self.parent, worker)
        seen = [c['last_results'][-1]['result'] for c in worker.contexts if c.get('last_results') and c['last_results'][-1]['tool'] == 'run_check' and not c['last_results'][-1]['ok']]
        self.assertTrue(any('same problem as the previous check' in text for text in seen), seen)

    def test_after_a_stuck_attempt_sealed_packages_are_reused_and_the_stuck_one_continues_from_its_files(self):
        from masa.application.v2.workers import Stuck
        with self.assertRaises(MasaError):
            self.generate(self.stuck_actions())
        self.service.seed_run = self.failed_run()['id']
        draft, worker = self.generate([{'tool': 'edit_file', 'path': 'calc/app/app.py', 'old': ' + 1', 'new': ''}, check('test'),
                                       write('calc/__main__.py', MAIN2), check('test')], retry_feedback='package calc/app is stuck')
        self.assertEqual([e['package'] for e in self.events(draft, 'package_reused')], ['calc/parser'])
        self.assertEqual([e['package'] for e in self.events(draft, 'package_sealed')], ['calc/app', 'calc'])
        self.assertEqual(self.store.run(draft)['model_calls'], 4)  # 只有 app 和入口花了调用；parser 没有重写 / only app and the entry cost calls; the parser was not rewritten
        self.assertIn('package calc/app is stuck', worker.contexts[0]['previous_attempt_error'])
        files, result = self.finish(draft)
        self.assertEqual(result['status'], 'succeeded', result)

    # ───────────── 修复：只重开出问题的包 / repair: reopen only the failing package ─────────────
    GOOD_PACKAGES = [write('calc/parser/parser.py', PARSER), write('tests/test_parser.py', PARSER_TEST), check('test'),
                     write('calc/app/app.py', APP2), write('tests/test_app.py', APP2_TEST), check('test'),
                     write('calc/__main__.py', MAIN2), check('test')]

    def failed_verification(self):
        """一个测试没通过的项目：好项目里的 app 被埋了一个 bug，真实验证失败。 A project whose verification really fails: a good project with a bug planted in app."""
        draft, _ = self.generate(self.GOOD_PACKAGES)
        meta = self.store.run(draft)['data']['project_plan']
        files = dict(self.store.read(meta['files_ref']))
        files['calc/app/app.py'] = APP2.replace('print(sum(parse(stdin.read())), file=stdout)', 'print(sum(parse(stdin.read())) + 1, file=stdout)')
        verified = self.service.approve(draft, {'files_ref': meta['files_ref'], 'files': files})
        result = Runtime(self.store, self.runner).execute(verified)
        self.assertEqual(result['status'], 'failed')
        return verified, files

    def test_a_repair_reopens_only_the_package_the_failure_points_at(self):
        verified, files = self.failed_verification()
        worker = ScriptedWorker([{'tool': 'edit_file', 'path': 'calc/app/app.py', 'old': ' + 1', 'new': ''}])  # 改完工具自己检查并封包，不用再来一步 / the tool checks and seals by itself after the change, no further step needed
        repair = self.service.repair(verified, worker, 'fix the app')
        meta = self.store.run(repair)['data']['project_plan']
        self.assertEqual(meta['changed_files'], ['calc/app/app.py'])
        fixed = self.store.read(meta['files_ref'])
        self.assertEqual(fixed['calc/app/app.py'], APP2)
        self.assertEqual({p: t for p, t in fixed.items() if p != 'calc/app/app.py'}, {p: t for p, t in files.items() if p != 'calc/app/app.py'})
        scope = self.events(repair, 'repair_scope')[0]
        self.assertEqual((scope['packages'], scope['files'], scope['widened']), (['calc/app'], ['calc/app/app.py'], False))
        self.assertEqual(self.store.run(repair)['model_calls'], 1)
        first = worker.contexts[0]
        self.assertIn('AssertionError', first['repair']['failure'])
        self.assertEqual(first['repair']['feedback'], 'fix the app')
        self.assertEqual([f['path'] for f in first['own_files']], ['calc/app/app.py'])
        self.assertNotIn('tests/test_app.py', first['files'])  # 冻结的测试不是工人的文件 / the frozen tests are not the worker's files

    def test_a_repair_worker_cannot_finish_before_it_changed_something(self):
        # 真实运行：修复工人第一步就 finish，连续三次，什么都没改。 Real run: the repair worker answered finish at once, three times, changing nothing.
        verified, files = self.failed_verification()
        worker = ScriptedWorker([{'tool': 'finish'}, {'tool': 'edit_file', 'path': 'calc/app/app.py', 'old': ' + 1', 'new': ''}, check('test')])
        repair = self.service.repair(verified, worker)
        steps = [e['payload'] for e in self.store.events(repair) if e['type'] == 'worker_step']
        self.assertFalse(steps[0]['ok'])
        self.assertIn('have not changed anything yet', steps[0]['summary'])
        self.assertEqual(self.store.read(self.store.run(repair)['data']['project_plan']['files_ref'])['calc/app/app.py'], APP2)

    def test_a_repair_that_never_beats_the_starting_point_is_returned_unchanged(self):
        # 真实运行：第一次修复把 2 个失败改成了 5 个，坏版本被拿去验证。现在只交出检查结果最好的状态；没有比起点好的就原样返回（协调器按“没有改动”处理）。
        # Real run: the first repair turned 2 failures into 5 and the worse version was verified. Now only the best state is handed over; nothing better than the start means unchanged (the coordinator treats it as a no-op).
        verified, files = self.failed_verification()
        def worse(word):  # 每次换一个报错文字：失败签名不同，不会被判成“卡住” / a different error text each time: other failure signatures, so not judged stuck
            return APP2.replace('print(sum(parse(stdin.read())), file=stdout)', f'raise RuntimeError("{word}")')
        worker = ScriptedWorker([write('calc/app/app.py', worse('alpha')), write('calc/app/app.py', worse('bravo')), write('calc/app/app.py', worse('charlie')), *[write('calc/app/app.py', worse(word)) for word in ('delta', 'echo', 'foxtrot', 'golf', 'hotel', 'india', 'juliet', 'kilo', 'lima')]])
        repair = self.service.repair(verified, worker)
        self.assertEqual(self.store.run(repair)['data']['project_plan']['changed_files'], [])
        done = self.events(repair, 'repair_worker_done')[0]
        self.assertEqual((done['improved'], done['changed']), (False, []))

    def test_a_repair_worker_can_read_the_frozen_failing_test(self):
        # 真实运行（grades，V1 生成 + V2 修复）：修复工人反复想读失败的测试文件，每次都被拒绝，最后卡住。
        # Real run (grades, V1 generation + V2 repair): the repair worker kept asking to read the failing test file, was refused every time and got stuck.
        verified, files = self.failed_verification()
        worker = ScriptedWorker([{'tool': 'read_file', 'path': 'tests/test_app.py'}, {'tool': 'edit_file', 'path': 'calc/app/app.py', 'old': ' + 1', 'new': ''}])
        repair = self.service.repair(verified, worker)
        steps = [e['payload'] for e in self.store.events(repair) if e['type'] == 'worker_step']
        self.assertTrue(steps[0]['ok'])
        self.assertIn('class AppTests', steps[0]['summary'] + files['tests/test_app.py'])
        self.assertIn('tests/test_app.py', worker.contexts[1]['interfaces'])  # 读过之后一直留在 interfaces 里 / it stays in interfaces afterwards
        self.assertNotIn('tests/test_app.py', worker.contexts[0]['interfaces'])

    def test_a_repair_worker_that_is_stuck_without_improvement_returns_unchanged_instead_of_failing(self):
        # 真实运行（bank，V1 生成 + V2 修复）：最高等级的修复工人卡住后，整个任务以异常失败。现在原样返回，由协调器按“没有改动”处理（诊断、改测试或停止）。
        # Real run (bank, V1 generation + V2 repair): a repair worker stuck at the top level failed the whole task with an exception. Now it returns unchanged and the coordinator handles it as "no change" (diagnosis, test revision or stop).
        verified, files = self.failed_verification()
        worker = ScriptedWorker([comment('return 0', 'return 0  # a'), comment('# a', '# b'), comment('# b', '# c'), *[{'tool': 'note', 'text': 'x'}] * 3])
        repair = self.service.repair(verified, worker)
        self.assertEqual(self.store.run(repair)['data']['project_plan']['changed_files'], [])
        done = self.events(repair, 'repair_worker_done')[0]
        self.assertEqual((done['outcome'], done['improved']), ('stuck', False))
        self.assertEqual(len(self.events(repair, 'worker_stuck')), 1)

    def test_a_repair_hands_over_the_best_state_even_when_the_worker_keeps_editing(self):
        verified, files = self.failed_verification()
        fixed = [{'tool': 'edit_file', 'path': 'calc/app/app.py', 'old': ' + 1', 'new': ''}]
        # 先改好（但它没有封包：把测试写成不通过之外的另一种失败来避免自动封包不现实，所以这里让工人在步数用完前改好又改坏）
        # fix it, then break it again and run out of steps without sealing: the fixed state must still be what is handed over
        worker = ScriptedWorker([*fixed, check('test')])
        repair = self.service.repair(verified, worker)
        self.assertEqual(self.store.read(self.store.run(repair)['data']['project_plan']['files_ref'])['calc/app/app.py'], APP2)
        self.assertEqual(self.events(repair, 'repair_worker_done')[0]['best_problems'], 0)

    def test_the_entry_package_does_not_own_the_files_of_the_packages_below_it(self):
        import types
        from masa.application.v2.workers import WorkerWorkspace
        stub = types.SimpleNamespace(own_paths={'calc/__main__.py'}, directory='calc', pack=pack)
        self.assertTrue(WorkerWorkspace._is_own(stub, 'calc/__main__.py'))
        self.assertTrue(WorkerWorkspace._is_own(stub, 'calc/helpers.py'))
        self.assertFalse(WorkerWorkspace._is_own(stub, 'calc/parser/parser.py'))  # 真实运行里入口包因此追着 parser 的错误耗了 13 步 / in a real run the entry package spent 13 steps on the parser's error

    def test_a_third_attempt_reuses_what_the_second_attempt_itself_reused(self):
        with self.assertRaises(MasaError):
            self.generate(self.stuck_actions())
        first = self.failed_run()['id']
        self.service.seed_run = first
        with self.assertRaises(MasaError):  # 第二次：沿用 parser，app 又卡住 / second attempt: parser reused, app stuck again
            self.generate([comment('# b', '# c'), check('test'), comment('# c', '# d'), check('test'), comment('# d', '# e'), check('test')])
        second = [r for r in self.store.all_runs() if r['data'].get('parent_run_id') == self.parent and r['status'] == 'failed' and r['id'] != first][0]['id']
        self.service.seed_run = second
        draft, worker = self.generate([{'tool': 'edit_file', 'path': 'calc/app/app.py', 'old': ' + 1', 'new': ''}, check('test'), write('calc/__main__.py', MAIN2), check('test')])
        self.assertEqual([e['package'] for e in self.events(draft, 'package_reused')], ['calc/parser'])

    def test_long_directory_names_never_merge_worker_steps(self):
        # 真实运行：修复的包名很长，截断到 100 个字符后所有步骤共用同一个调用 id，第二步起就报“角色输入已变”。 Real run: a repair's long package list was cut at 100 characters and every step shared one invocation id.
        verified, files = self.failed_verification()
        worker = ScriptedWorker([{'tool': 'edit_file', 'path': 'calc/app/app.py', 'old': ' + 1', 'new': ''}, {'tool': 'note', 'text': 'x'}])
        with __import__('unittest.mock').mock.patch.object(V2Generation, '_repair_targets', lambda self_, spec, files_, evidence: (['calc/app'] + ['calc/' + 'x' * 30] * 4, ['calc/app'])):
            repair = self.service.repair(verified, worker)
        ids = [r['invocation_id'] for r in self.store.db.execute('select invocation_id from role_invocations where run_id=?', (repair,)).fetchall()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 1)  # 改完就封包了 / sealed right after the change

    def test_a_repair_never_writes_a_test_and_a_second_attempt_is_widened_to_every_package(self):
        verified, files = self.failed_verification()
        worker = ScriptedWorker([write('tests/test_app.py', APP2_TEST.replace('"10"', '"11"')), write('tests/test_app.py', APP2_TEST.replace('"10"', '"11"'))])
        with self.assertRaisesRegex(MasaError, 'repair cannot change tests'):  # 协调器据此转去修订测试 / the coordinator turns to revising the tests because of this message
            self.service.repair(verified, worker)
        worker = ScriptedWorker([{'tool': 'edit_file', 'path': 'calc/app/app.py', 'old': ' + 1', 'new': ''}, check('test')])
        repair = self.service.repair(verified, worker)
        scope = self.events(repair, 'repair_scope')[0]
        self.assertTrue(scope['widened'])
        self.assertEqual(scope['packages'], ['calc', 'calc/app', 'calc/parser'])


class EdgeTests(unittest.TestCase):
    def test_the_direct_dependencies_are_shown_in_full_and_the_others_as_signatures(self):
        import types
        from masa.application.v2.workers import WorkerWorkspace
        body = 'def parse(text):' + NL + '    """Split."""' + NL + '    return text.split()' + NL
        files = {'calc/parser/parser.py': body, 'calc/other/other.py': body, 'calc/app/app.py': 'x = 1' + NL}
        stub = types.SimpleNamespace(files=files, own_paths={'calc/app/app.py'}, pack=pack, spec={'module': 'calc', 'files': [{'path': p, 'purpose': 'x'} for p in files]}, dependencies={'calc/parser'}, shown=set())
        view = WorkerWorkspace.interfaces(stub)
        self.assertEqual(view['calc/parser/parser.py'], body)  # 依赖：全文 / dependency: full source
        self.assertNotEqual(view['calc/other/other.py'], body)  # 其它：只有签名 / others: signatures only
        self.assertIn('def parse', view['calc/other/other.py'])
        self.assertNotIn('calc/app/app.py', view)
        stub.shown.add('calc/other/other.py')  # 工人读过的文件从此以全文留在 interfaces 里 / a file the worker read stays in full from then on
        self.assertEqual(WorkerWorkspace.interfaces(stub)['calc/other/other.py'], body)

    def test_a_v2_task_gets_a_larger_default_call_budget_unless_the_user_set_one(self):
        # 10 文件的项目一次运行要 80–150 次调用，默认 40 次会在中途停掉任务。 A 10-file project takes 80-150 calls and the default 40 would stop it half way.
        from masa.application.console import Console
        self.assertEqual(Console._v2_budget({'runtime': 'v2'}), {'max_model_calls': 150})
        self.assertEqual(Console._v2_budget({'runtime': 'v2', 'budget': {'max_cost': 1.0}}), {'max_cost': 1.0, 'max_model_calls': 150})
        self.assertEqual(Console._v2_budget({'runtime': 'v2', 'budget': {'max_model_calls': 20}}), {'max_model_calls': 20})
        self.assertIsNone(Console._v2_budget({}))
        self.assertEqual(Console._v2_budget({'budget': {'max_cost': 1.0}}), {'max_cost': 1.0})

    def test_v2_repair_only_keeps_the_v1_generation_and_the_v2_repair(self):
        from masa.application.generation import ProjectGeneration
        from masa.application.v2.workers import V2RepairOnly
        generation = V2RepairOnly.__new__(V2RepairOnly)
        generation.pack = pack
        self.assertFalse(V2RepairOnly.workers)  # 生成走 V1 的路径 / generation takes V1's path
        self.assertEqual(generation.admission_need('goal', {'spec': {'acceptance': []}}, 'g'), ['goal', {'spec': {'acceptance': []}}, 'g'])
        self.assertIs(V2RepairOnly._propose_repair, V2Generation._propose_repair)  # 修复走 V2 工人 / repair uses the V2 worker
        self.assertGreater(generation._repair_calls({}), ProjectGeneration._repair_calls(generation, {}))

    def test_v2_workers_start_at_the_lowest_cloud_level_unless_the_user_chose(self):
        # 真实运行：本地 glm 当不了包工人；V2 与“V1 生成 + V2 修复”的工人从最低云端等级起步。 Real runs: the local glm cannot be a package worker; V2 and "V1 generation + V2 repair" workers start at the lowest cloud level.
        import types
        from masa.application.console import Console
        profiles = [('a', {'level': 1, 'model_type': 'local'}), ('b', {'level': 2, 'model_type': 'cloud'}), ('c', {'level': 3, 'model_type': 'cloud'})]
        console = types.SimpleNamespace(settings=types.SimpleNamespace(ready_profiles=lambda: profiles), _v2_policy=Console._v2_policy)
        run = lambda body: Console._runtime_policy(console, body)
        self.assertEqual(run({'runtime': 'v2repair'})['start_level_by_role'], {'project_repair': 2})
        self.assertEqual(run({'runtime': 'v2'})['start_level_by_role'], {'project_repair': 2, 'project_developer': 2})
        self.assertEqual(run({'runtime': 'v2', 'policy': {'start_level_by_role': {'project_developer': 3}}})['start_level_by_role'], {'project_developer': 3, 'project_repair': 2})
        self.assertEqual(run({'runtime': 'v2', 'model_ids': ['a', 'c']})['start_level_by_role'], {'project_repair': 3, 'project_developer': 3})
        self.assertIsNone(run({}))  # 标准流程完全不变 / the standard flow is untouched
        console.settings.ready_profiles = lambda: [('a', {'level': 1, 'model_type': 'local'})]
        self.assertNotIn('start_level_by_role', run({'runtime': 'v2'}) or {})  # 没有云端模型就不加 / nothing is added without a cloud model

    def test_a_v2_generation_tries_each_level_once_unless_the_user_chose(self):
        from masa.application.console import Console
        self.assertEqual(Console._v2_policy({'runtime': 'v2'}), {'assertion_referee': True, 'attempts_per_level': {'generation': 1}})
        self.assertEqual(Console._v2_policy({'runtime': 'v2', 'policy': {'diagnose': False}}), {'assertion_referee': True, 'diagnose': False, 'attempts_per_level': {'generation': 1}})
        self.assertEqual(Console._v2_policy({'runtime': 'v2', 'policy': {'attempts_per_level': {'generation': 2}}}), {'attempts_per_level': {'generation': 2}, 'assertion_referee': True})
        self.assertIsNone(Console._v2_policy({}))
        self.assertEqual(Console._v2_policy({'policy': {'diagnose': False}}), {'diagnose': False})

    def test_a_worker_is_admitted_by_its_own_small_context_not_by_the_whole_spec(self):
        # 真实运行：10 文件的规格放不进本地模型的窗口，于是 105 次工人调用全在云端；工人每一步的上下文其实只有约 12 KB。
        # Real run: a 10-file spec did not fit the local window, so all 105 worker calls went to the cloud; a worker step's context is only about 12 KB.
        from masa.application.generation import ProjectGeneration
        approved = {'spec': {'acceptance': ['a']}, 'checks': [{'operation': 'py_test', 'cases': [{'name': 'c'}]}] + [{'operation': 'x', 'cases': []}] * 2}
        growth = 'x' * 4000 * 9
        whole = ProjectGeneration.admission_need(None, 'goal', approved, growth)
        generation = V2Generation.__new__(V2Generation)
        generation.pack = pack
        small = generation.admission_need('goal', approved, growth)
        size = lambda need: sum(len(str(item)) for item in need)
        self.assertEqual(whole, ['goal', approved, growth])
        self.assertLess(size(small), 9000)
        self.assertEqual(ProjectGeneration.repair_need(None, {'a': 'b'}, 'e' * 30000), [{'a': 'b'}, 'e' * 20000])

    def test_a_wiring_package_waits_for_all_the_others_even_when_the_purposes_are_in_another_language(self):
        from masa.application.v2.workers import dependency_edges, order_packages
        spec = {'module': 'bank', 'entrypoint': 'bank/__main__.py'}
        groups = [('bank/app', [{'path': 'bank/app/app.py', 'purpose': '主流程：逐行读取、调用解析、存储和报表'}]),
                  ('bank/parser', [{'path': 'bank/parser/parser.py', 'purpose': '解析命令行'}]),
                  ('bank/store', [{'path': 'bank/store/store.py', 'purpose': '账户存储'}]),
                  ('bank', [{'path': 'bank/__main__.py', 'purpose': '入口'}])]
        self.assertEqual(dependency_edges(pack, spec, groups)['bank/app'], {'bank/parser', 'bank/store'})
        ordered, _ = order_packages(pack, spec, groups)
        self.assertEqual([d for d, _ in ordered], ['bank/parser', 'bank/store', 'bank/app', 'bank'])


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
                           {'tool': 'run_check', 'kind': 'test'},  # go vet 与 gofmt 由工具自己跑，通过就封包 / the tool runs go vet and gofmt itself and seals
                           write('cmd/app/main.go', GO_FILES['cmd/app/main.go']), {'tool': 'run_check', 'kind': 'test'}]

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
