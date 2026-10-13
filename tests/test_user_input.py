"""人在回路（第 1、2 级）：追加提示与暂停/继续/停止。 Human in the loop (levels 1 and 2): notes and pause / resume / stop."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from masa.application.console import Console
from masa.application.orchestration.coordinator import WorkflowCoordinator
from masa.domain.models import Budget, MasaError
from masa.infrastructure.store import Store
from masa.runtime import user_input
from masa.runtime.engine import Runtime
from masa.runtime.graph import harness_policy
from masa.runtime.roles import RoleRuntime
from test_fix_flow import FixFlowCase, Model, World, WorldExecutor
from test_runtime import FakeExecutor


def new_run(store, root, parent=None, goal='g'):
    seed = Path(root) / ('seed-' + str(time.time_ns()))
    seed.mkdir()
    (seed / 'go.mod').write_text('module example.com/x\n\ngo 1.27.0\n')
    if parent:
        store.set_status(parent, 'paused')  # 修订只能基于已暂停/已结束的版本 / a revision needs a paused or finished parent
    return Runtime(store, FakeExecutor()).create(seed, goal, Budget(model_calls=10, deadline_seconds=86400), graph=harness_policy(), project_plan={'status': 'planning'}, parent_run_id=parent)


class NoteHelpersTests(unittest.TestCase):
    def test_a_note_is_trimmed_and_bounded(self):
        self.assertEqual(user_input.clean_note('  keep two decimals  '), 'keep two decimals')
        for bad in ('', '   ', 'x' * 601, 'a' + chr(0), None, 5):
            with self.subTest(bad=repr(bad)[:20]), self.assertRaises(MasaError):
                user_input.clean_note(bad)

    def test_notes_follow_the_version_chain_oldest_first_without_duplicates_and_capped(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                first = new_run(store, temp)
                second = new_run(store, temp, parent=first)
                user_input.record(store, first, 'note', text='A')
                user_input.record(store, second, 'note', text='B')
                user_input.record(store, second, 'note', text='A')  # 重复 / duplicate
                user_input.record(store, second, 'control', action='pause')  # 控制不是提示 / a control is not a note
                self.assertEqual(user_input.notes_for(store, second), ['A', 'B'])  # 旧的在前，重复只留一条 / oldest first, duplicate kept once
                self.assertEqual(user_input.notes_for(store, first), ['A'])  # 旧版本看不到新版本里的提示 / an older version does not see later notes
                for n in range(15):
                    user_input.record(store, second, 'note', text=f'n{n}')
                self.assertEqual(len(user_input.notes_for(store, second)), user_input.MAX_NOTES)
                self.assertEqual(user_input.notes_for(store, second)[-1], 'n14')
            finally:
                store.close()


class RoleInjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.addCleanup(self.store.close)
        self.rid = new_run(self.store, self.temp.name)
        self.roles = RoleRuntime(self.store)
        self.seen = []
        outer = self

        class Provider:
            profile = {'provider': 'test', 'model': 'm'}
            config = {'model_type': 'cloud'}
            usage = None

            def respond(self, context):
                outer.seen.append(dict(context))
                return {'ok': len(outer.seen)}
        self.provider = Provider()

    def call(self, invocation):
        return self.roles.call(self.rid, self.provider, 'project_planner', {'goal': 'g'}, invocation_id=invocation)

    def test_a_note_reaches_only_calls_that_start_after_it(self):
        self.call('initial')
        self.assertNotIn('user_notes', self.seen[0])  # 默认路径：上下文里根本没有这个键 / the default path has no such key at all
        user_input.record(self.store, self.rid, 'note', text='use two decimals')
        self.call('second')
        self.assertEqual(self.seen[1]['user_notes'], ['use two decimals'])

    def test_replaying_an_existing_invocation_keeps_its_original_context(self):
        self.call('initial')
        user_input.record(self.store, self.rid, 'note', text='late note')
        again = self.call('initial')  # 已完成的调用被重放（恢复）：不能因为后来有了提示就报“输入变了”，也不能再调用模型
        self.assertEqual((again, len(self.seen)), ({'ok': 1}, 1))

    def test_every_later_call_sees_all_notes(self):
        user_input.record(self.store, self.rid, 'note', text='one')
        self.call('initial')
        user_input.record(self.store, self.rid, 'note', text='two')
        self.call('next')
        self.assertEqual(self.seen[0]['user_notes'], ['one'])
        self.assertEqual(self.seen[1]['user_notes'], ['one', 'two'])


class PromptTests(unittest.TestCase):
    def test_the_instruction_mentions_notes_only_when_there_are_notes(self):
        from masa.infrastructure.llm import ChatProvider
        sent = []

        class Opener:
            def open(self, request, timeout=None):
                sent.append(request.data.decode('utf-8'))
                raise OSError('stop here')

        provider = ChatProvider({'base_url': 'https://api.example.com/v1', 'model': 'm', 'model_type': 'cloud', 'context_limit': 32768, 'max_output_tokens': 256}, 'k')
        with patch('masa.infrastructure.llm.urllib.request.build_opener', lambda *a: Opener()):
            for context in ({'purpose': 'project_triage', 'goal': 'x'}, {'purpose': 'project_triage', 'goal': 'x', 'user_notes': ['n']}):
                try:
                    provider.respond(context)
                except Exception:
                    pass
        self.assertNotIn('user_notes', sent[0].split('"role":"user"')[0])
        self.assertIn('user_notes, oldest first', sent[1].split('"role":"user"')[0])


class ConsoleInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.console = Console(Path(self.temp.name), Path('fake-runner'), Path('fake-go'), Path.cwd())
        self.addCleanup(self.console.close)
        store = Store(self.console.root)
        self.rid = new_run(store, self.temp.name)
        store.close()

    def events(self):
        store = Store(self.console.root)
        try:
            return [e for e in store.events(self.rid) if e['type'] == 'user_input']
        finally:
            store.close()

    def test_a_note_is_written_to_the_ledger(self):
        self.assertTrue(self.console.user_input(self.rid, {'kind': 'note', 'text': 'be careful with rounding'})['ok'])
        self.assertEqual(self.events()[0]['payload'], {'kind': 'note', 'text': 'be careful with rounding'})

    def test_invalid_input_is_rejected_and_nothing_is_written(self):
        for body in ({}, {'kind': 'note', 'text': ''}, {'kind': 'control', 'action': 'explode'}, {'kind': 'other'}):
            with self.subTest(body=body), self.assertRaises(MasaError):
                self.console.user_input(self.rid, body)
        with self.assertRaises(MasaError):
            self.console.user_input('no-such-run', {'kind': 'note', 'text': 'x'})
        self.assertEqual(self.events(), [])

    def test_pause_and_resume_flip_the_flag_of_the_running_job_of_the_same_task(self):
        with self.assertRaisesRegex(MasaError, 'no running automatic task'):
            self.console.user_input(self.rid, {'kind': 'control', 'action': 'pause'})
        store = Store(self.console.root)
        child = new_run(store, self.temp.name, parent=self.rid)  # 正在运行的是这个任务的更新版本 / the job runs a newer version of the same task
        store.close()
        self.console.jobs['j'] = {'status': 'running', 'mode': 'auto', 'run_id': child}
        self.console.user_input(self.rid, {'kind': 'control', 'action': 'pause'})
        self.assertTrue(self.console.jobs['j']['paused'])
        self.console.user_input(self.rid, {'kind': 'control', 'action': 'resume'})
        self.assertFalse(self.console.jobs['j']['paused'])
        self.assertEqual([e['payload'].get('action') for e in self.events()], ['pause', 'resume'])  # 事件记在传入的 run 上；这里传的是旧版本 / events go on the run passed in (the older version)

    def test_stop_requests_cancellation(self):
        self.console.user_input(self.rid, {'kind': 'control', 'action': 'stop'})
        store = Store(self.console.root)
        try:
            self.assertTrue(store.run(self.rid)['cancel_requested'])
        finally:
            store.close()


class PauseGateTests(FixFlowCase):
    def coordinator(self):
        world = World()
        store, jobs, router = self.build(Model('local-m', 'local', world), Model('cloud-m', 'cloud', world), world)
        return WorkflowCoordinator(store, WorldExecutor(world), None, jobs['job'], router=router), jobs['job'], store

    def test_a_paused_task_waits_at_the_boundary_until_it_is_resumed(self):
        coordinator, job, _ = self.coordinator()
        job['paused'] = True
        done = threading.Event()
        threading.Thread(target=lambda: (coordinator._pause_gate(), done.set()), daemon=True).start()
        self.assertFalse(done.wait(1.0))  # 暂停期间一直停着 / holds while paused
        job['paused'] = False
        self.assertTrue(done.wait(3.0))  # 继续后立刻放行 / released at once after resume

    def test_a_task_that_is_not_paused_is_never_delayed(self):
        coordinator, _, _ = self.coordinator()
        started = time.monotonic()
        coordinator._pause_gate()
        self.assertLess(time.monotonic() - started, 0.1)

    def test_stop_wins_over_pause(self):
        coordinator, job, store = self.coordinator()
        rid = new_run(store, self.temp_root(store))
        job['run_id'] = rid
        job['paused'] = True

        def stop_later():
            time.sleep(0.8)
            other = Store(store.root)  # SQLite 连接不能跨线程 / a SQLite connection cannot cross threads
            try:
                other.cancel(rid)
            finally:
                other.close()
        threading.Thread(target=stop_later, daemon=True).start()
        started = time.monotonic()
        coordinator._pause_gate()  # 在主线程里等：停止一到就放行 / holds in this thread; released once the stop arrives
        self.assertGreaterEqual(time.monotonic() - started, 0.7)
        self.assertLess(time.monotonic() - started, 4.0)

    @staticmethod
    def temp_root(store):
        return str(store.root)


if __name__ == '__main__':
    unittest.main()
