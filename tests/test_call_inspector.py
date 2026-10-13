"""调用检视：用真实角色运行写出账本，再检查“模型看到了什么”的还原。 Call inspection over a ledger written by real role runs."""
import tempfile
import time
import unittest
from pathlib import Path

from masa.application import call_inspector
from masa.application.console import Console
from masa.domain.models import MasaError
from masa.domain.prompt_layout import ordered_keys
from masa.infrastructure.store import Store
from masa.runtime import user_input
from masa.runtime.roles import RoleRuntime
from test_user_input import new_run


class Provider:
    profile = {'provider': 'test', 'model': 'm-1'}
    config = {'model_type': 'cloud'}

    def __init__(self):
        self.usage = None

    def respond(self, context):
        self.usage = {'prompt_tokens': 200, 'completion_tokens': 20, 'total_tokens': 220, 'prompt_cache_hit_tokens': 128, 'prompt_cache_miss_tokens': 72}
        return {'files': {'a.txt': 'hello'}, 'note': 'done'}


class InspectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.addCleanup(self.store.close)
        self.rid = new_run(self.store, self.temp.name)
        self.roles = RoleRuntime(self.store)
        self.provider = Provider()

    def call(self, invocation, context):
        return self.roles.call(self.rid, self.provider, 'project_planner', context, invocation_id=invocation)

    def first_two_calls(self):
        base = {'goal': 'count words', 'spec': 'S' * 400, 'files': {'a.txt': 'x' * 100, 'b.txt': 'y' * 300}}
        self.call('initial', dict(base, history=['first try']))
        time.sleep(0.01)
        user_input.record(self.store, self.rid, 'note', text='use two decimals')
        self.call('second', dict(base, history=['first try', 'second try'], omitted=[{'id': 'old.txt', 'reason': 'context_budget'}]))

    def test_the_list_has_one_row_per_call_with_usage_and_cache(self):
        self.first_two_calls()
        rows = call_inspector.call_list(self.store, self.rid)['calls']
        self.assertEqual([(r['step'], r['invocation'], r['status']) for r in rows], [('project_planner', 'initial', 'completed'), ('project_planner', 'second', 'completed')])
        self.assertEqual((rows[0]['prompt_tokens'], rows[0]['cache_hit_tokens'], rows[0]['cache_miss_tokens']), (200, 128, 72))
        self.assertEqual((rows[0]['notes'], rows[1]['notes']), (0, 1))  # 第二次调用带着用户提示 / the second call carries the user's note
        self.assertEqual(rows[0]['model'], 'm-1')

    def test_the_detail_splits_the_context_into_sections_in_prompt_order(self):
        self.first_two_calls()
        detail = call_inspector.call_detail(self.store, self.rid, 'project_planner', 'second')
        names = [s['name'] for s in detail['sections']]
        self.assertEqual(names, ordered_keys({n: 0 for n in names}))  # 与发给模型的内容同一个顺序（稳定在前）/ the same order the model receives (stable first)
        self.assertLess(names.index('goal'), names.index('files'))
        self.assertLess(names.index('files'), names.index('history'))
        self.assertEqual(names[-1], 'user_notes')  # 用户提示是这次调用里最后才变的部分 / the notes are the last-changing part of this call
        self.assertTrue({'goal', 'spec', 'files', 'user_notes', 'omitted', 'history'} <= set(names))
        sections = {s['name']: s for s in detail['sections']}
        self.assertEqual(sections['spec']['chars'], 400)
        self.assertEqual([(e['name'], e['chars']) for e in sections['files']['entries']], [('a.txt', 100), ('b.txt', 300)])
        self.assertAlmostEqual(sum(s['share'] for s in detail['sections']), 1.0, places=2)
        self.assertGreater(detail['est_total_tokens'], detail['instruction']['est_tokens'])
        self.assertEqual(detail['user_notes'], ['use two decimals'])

    def test_omitted_items_and_their_reasons_are_listed(self):
        self.first_two_calls()
        detail = call_inspector.call_detail(self.store, self.rid, 'project_planner', 'second')
        self.assertEqual(detail['omitted'], [{'section': 'omitted', 'id': 'old.txt', 'reason': 'context_budget'}])
        self.assertEqual(call_inspector.call_detail(self.store, self.rid, 'project_planner', 'initial')['omitted'], [])

    def test_layered_context_decisions_made_just_before_the_call_are_attached(self):
        self.call('initial', {'goal': 'g'})
        time.sleep(0.01)
        self.store.event(self.rid, 'context_plan', {'where': 'repair', 'window': 8192, 'total': 9000, 'kept_tokens': 4000, 'outlined': ['b.go'], 'dropped': ['c.go'], 'over_budget': False})
        self.call('second', {'goal': 'g', 'more': 'x'})
        plans = call_inspector.call_detail(self.store, self.rid, 'project_planner', 'second')['context_plans']
        self.assertEqual([(p['where'], p['dropped'], p['outlined']) for p in plans], [('repair', ['c.go'], ['b.go'])])
        self.assertEqual(call_inspector.call_detail(self.store, self.rid, 'project_planner', 'initial')['context_plans'], [])

    def test_the_prefix_shared_with_the_previous_call_of_the_same_role_is_measured(self):
        self.first_two_calls()
        detail = call_inspector.call_detail(self.store, self.rid, 'project_planner', 'second')
        shared = detail['prefix_with_previous']
        self.assertEqual(shared['against']['invocation'], 'initial')
        self.assertTrue(0 < shared['common_chars'] < shared['of_chars'])
        self.assertIsNone(call_inspector.call_detail(self.store, self.rid, 'project_planner', 'initial')['prefix_with_previous'])  # 第一次调用没有可比较的对象 / nothing to compare the first call with

    def test_the_response_summary_and_errors(self):
        self.first_two_calls()
        response = call_inspector.call_detail(self.store, self.rid, 'project_planner', 'initial')['response']
        self.assertEqual(response['keys'], ['files', 'note'])
        with self.assertRaisesRegex(MasaError, 'call not found'):
            call_inspector.call_detail(self.store, self.rid, 'project_planner', 'nope')
        with self.assertRaisesRegex(MasaError, 'run not found'):
            call_inspector.call_list(self.store, 'no-such-run')

    def test_a_call_made_by_an_older_version_is_found_from_a_newer_one(self):
        self.first_two_calls()
        self.store.set_status(self.rid, 'paused')
        child = new_run(self.store, self.temp.name, parent=self.rid)
        self.assertEqual(len(call_inspector.call_list(self.store, child)['calls']), 2)
        self.assertEqual(call_inspector.call_detail(self.store, child, 'project_planner', 'initial')['run_id'], self.rid)


class ConsoleTests(unittest.TestCase):
    def test_the_console_serves_both_views(self):
        with tempfile.TemporaryDirectory() as temp:
            console = Console(Path(temp), Path('fake-runner'), Path('fake-go'), Path.cwd())
            try:
                store = Store(console.root)
                rid = new_run(store, temp)
                RoleRuntime(store).call(rid, Provider(), 'project_planner', {'goal': 'g'}, invocation_id='initial')
                store.close()
                self.assertEqual(len(console.call_list(rid)['calls']), 1)
                self.assertEqual(console.call_detail(rid, 'project_planner', 'initial')['step'], 'project_planner')
            finally:
                console.close()


if __name__ == '__main__':
    unittest.main()
