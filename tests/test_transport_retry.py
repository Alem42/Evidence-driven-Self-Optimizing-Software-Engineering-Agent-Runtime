"""免费本地调用的传输失败：等服务恢复后重试，不计入失败链、不触发升级；付费云调用与“已收到响应的失败”不在此列。
Transport failures of FREE local calls: wait for the server and retry without counting a failure or escalating.
Paid cloud calls and failures where a response WAS received are never auto-retried."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from masa.application.orchestration.coordinator import WorkflowCoordinator
from masa.application.orchestration.router import Router
from masa.application.orchestration.routing import DEFAULT_POLICY, validate_policy
from masa.domain.models import MasaError, TransportFailure
from masa.infrastructure.jobs import Jobs
from masa.infrastructure.llm import ChatProvider
from masa.infrastructure.store import Store
from masa.runtime.roles import RoleRuntime
from test_project_plan import CHECKS, SPEC
from test_project_generation import FILES
from test_routing import BUDGET, fake_entry
from test_runtime import FakeExecutor


class FlakyLocal:
    """前 n 次调用修复角色时抛传输失败；服务“恢复”由 wait_until_reachable 模拟。"""

    def __init__(self, name, fail_first, fixes=None):
        self.profile = {'provider': 'test', 'model': name}
        self.config = {'model_type': 'local'}
        self.fail_first, self.fixes = fail_first, fixes
        self.calls, self.waits = 0, 0
        self.usage = {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}

    def wait_until_reachable(self, seconds):
        self.waits += 1
        return True

    def respond(self, context):
        purpose = context['purpose']
        if purpose == 'project_repair':
            self.calls += 1
            if self.calls <= self.fail_first:
                raise TransportFailure('connection refused')
            if self.fixes:
                self.fixes()
            return {'internal/app/app.go': 'package app\n\nfunc Value() int { return 42 } // fixed\n'}
        return {'project_triage': {'verdict': 'ok', 'reasons': [], 'suggestions': []}, 'project_planner': SPEC, 'project_tester': CHECKS, 'project_developer': FILES}[purpose]


class Cloud:
    def __init__(self):
        self.profile = {'provider': 'test', 'model': 'big'}
        self.config = {'model_type': 'cloud'}
        self.usage = {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}

    def respond(self, context):
        raise AssertionError('the cloud model must not be used when the local server just needs a moment')


class CoordinatorTransportTests(unittest.TestCase):
    def run_task(self, local, executor, policy=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(Path(temp.name))
        self.addCleanup(store.close)
        providers = {'local': local, 'cloud': Cloud()}
        snapshot = {'version': 1, 'mode': 'ladder', 'policy': validate_policy({**DEFAULT_POLICY, 'prefer_highest_roles': ['project_diagnoser'], **(policy or {})}), 'budget': dict(BUDGET),
                    'candidates': [fake_entry('local', 1, 'small', 'local'), fake_entry('cloud', 2, 'big', 'cloud')]}
        jobs = Jobs(Path(temp.name))
        jobs['job'] = {'status': 'running', 'run_id': None, 'mode': 'auto', 'phase': 'planning', 'attempt': 0, 'started': 0,
                       'request': {'goal': 'Build a CLI'}}
        router = Router(store, snapshot, lambda entry: providers[entry['id']])
        with patch('masa.intelligence.repair_context.build_repair_context', lambda s, e, r, f, ev, fb: (f, None)):
            WorkflowCoordinator(store, executor, None, jobs['job'], router=router).run()
        return store, jobs['job']

    def test_a_server_that_is_briefly_down_is_waited_for_without_escalating_or_counting_a_failure(self):
        executor = FakeExecutor(exit_code=1)
        local = FlakyLocal('small', fail_first=2, fixes=lambda: setattr(executor, 'exit_code', 0))
        store, job = self.run_task(local, executor)
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded')
        self.assertEqual(local.calls, 3)  # 两次传输失败 + 一次成功 / two transport failures then success
        self.assertEqual(local.waits, 2)
        self.assertFalse(any(d['escalated'] for r in store.all_runs() for e in store.events(r['id']) if e['type'] == 'route_decided' for d in [e['payload']]))
        self.assertEqual([v for k, v in job.get('route_history', {}).items() if k.startswith('fix')], [])  # 传输失败不是模型的错 / not the model's fault
        kinds = [e['type'] for r in store.all_runs() for e in store.events(r['id'])]
        self.assertEqual(kinds.count('transport_retry'), 2)

    def test_a_server_that_never_comes_back_stops_after_the_retry_limit(self):
        executor = FakeExecutor(exit_code=1)
        local = FlakyLocal('small', fail_first=99)
        local.wait_until_reachable = lambda seconds: setattr(local, 'waits', local.waits + 1) or False  # 一直不可达 / never reachable
        with self.assertRaises(TransportFailure):
            self.run_task(local, executor)
        self.assertEqual(local.waits, 1)  # 等过一次，仍不可达就停下 / waited once, then gave up

    def test_fixed_mode_does_not_wait_or_retry(self):
        executor = FakeExecutor(exit_code=1)
        local = FlakyLocal('small', fail_first=1)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(Path(temp.name))
        self.addCleanup(store.close)
        jobs = Jobs(Path(temp.name))
        jobs['job'] = {'status': 'running', 'run_id': None, 'mode': 'auto', 'phase': 'planning', 'attempt': 0, 'started': 0, 'request': {'goal': 'x'}}
        with patch('masa.intelligence.repair_context.build_repair_context', lambda s, e, r, f, ev, fb: (f, None)):
            with self.assertRaises(TransportFailure):
                WorkflowCoordinator(store, executor, local, jobs['job']).run()
        self.assertEqual(local.waits, 0)


class LedgerRetryTests(unittest.TestCase):
    """RoleRuntime 账本层：哪些未完成的调用可以作为新尝试重试。"""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = Store(Path(temp.name))
        self.addCleanup(self.store.close)
        from masa.application.planning import ProjectPlanning
        from masa.runtime.engine import Runtime
        from masa.domain.models import Budget
        from masa.runtime.graph import harness_policy
        seed = Path(temp.name) / 'seed'
        seed.mkdir()
        (seed / 'go.mod').write_text('module example.com/x\n\ngo 1.27.0\n')
        self.rid = Runtime(self.store, FakeExecutor()).create(seed, 'g', Budget(model_calls=3, deadline_seconds=86400), graph=harness_policy(),
                                                              project_plan={'status': 'planning'})
        self.roles = RoleRuntime(self.store)
        self.ProjectPlanning = ProjectPlanning

    def provider(self, kind, flag, behaviour):
        class P:
            profile = {'provider': 'test', 'model': 'm-' + kind}
            config = {'model_type': kind}
            retry_unknown_calls = flag
            usage = None

            def respond(self, context):
                return behaviour()
        return P()

    def call(self, provider, rid=None):
        return self.roles.call(rid or self.rid, provider, 'project_planner', {'goal': 'g'})

    def fail_with(self, exc):
        def boom():
            raise exc
        return boom

    def test_transport_failed_local_call_is_retried_only_when_the_policy_allows_it(self):
        with self.assertRaises(TransportFailure):
            self.call(self.provider('local', True, self.fail_with(TransportFailure('down'))))
        with self.assertRaisesRegex(MasaError, 'result unavailable'):
            self.call(self.provider('local', False, lambda: {'ok': 1}))  # 策略不允许 / not allowed by the policy
        self.assertEqual(self.call(self.provider('local', True, lambda: {'ok': 1})), {'ok': 1})
        self.assertEqual(self.store.run(self.rid)['model_calls'], 1)  # 重试不占用预算 / the retry did not spend budget

    def test_a_received_but_rejected_response_is_never_replayed_by_the_ledger(self):
        with self.assertRaises(MasaError):
            self.call(self.provider('local', True, self.fail_with(MasaError('invalid JSON'))))
        with self.assertRaisesRegex(MasaError, 'result unavailable'):
            self.call(self.provider('local', True, lambda: {'ok': 1}))

    def test_paid_cloud_calls_are_never_replayed(self):
        with self.assertRaises(TransportFailure):
            self.call(self.provider('cloud', True, self.fail_with(TransportFailure('down'))))
        with self.assertRaisesRegex(MasaError, 'result unavailable'):
            self.call(self.provider('cloud', True, lambda: {'ok': 1}))

    def test_an_orphaned_running_local_call_is_abandoned_and_retried(self):
        self.store.db.execute("INSERT INTO role_invocations VALUES(?,?,?,?,?,?,?,?,?,?)", (
            self.rid, 'project_planner', 'initial', 1, self.store.put({'purpose': 'project_planner', 'goal': 'g'}),
            self.store.put({'provider': 'test', 'model': 'm-local'}), 'running', None, 0.0, None))
        self.store.db.execute("UPDATE runs SET model_calls=1 WHERE id=?", (self.rid,))
        self.store._event(self.rid, 'model_requested', {'step_id': 'project_planner', 'invocation_id': 'initial', 'attempt_no': 1,
                                                          'route': {'provider': 'test', 'model': 'm-local'}})
        self.store.db.commit()
        self.assertEqual(self.call(self.provider('local', True, lambda: {'ok': 2})), {'ok': 2})
        kinds = [e['type'] for e in self.store.events(self.rid)]
        self.assertIn('model_abandoned', kinds)
        from masa.application.usage import task_report
        statuses = sorted(c['status'] for c in task_report(self.store, self.rid)['calls'])
        self.assertEqual(statuses, ['abandoned', 'completed'])


class WaitTests(unittest.TestCase):
    def provider(self, model_type='local'):
        base = {'base_url': 'http://127.0.0.1:11434', 'model': 'm', 'model_type': model_type, 'protocol': 'ollama' if model_type == 'local' else 'openai'}
        return ChatProvider(base, '' if model_type == 'local' else 'k')

    def test_wait_polls_until_the_server_returns_and_respects_the_deadline(self):
        provider = self.provider()
        answers = iter([False, False, True])
        provider.reachable = lambda timeout=3: next(answers)
        now = [0.0]
        slept = []
        self.assertTrue(provider.wait_until_reachable(60, sleep=lambda s: (slept.append(s), now.__setitem__(0, now[0] + s)), clock=lambda: now[0]))
        self.assertEqual(slept, [3, 3])
        provider.reachable = lambda timeout=3: False
        now[0] = 0.0
        self.assertFalse(provider.wait_until_reachable(10, sleep=lambda s: now.__setitem__(0, now[0] + s), clock=lambda: now[0]))

    def test_cloud_providers_never_wait(self):
        self.assertFalse(self.provider('cloud').wait_until_reachable(60))


if __name__ == '__main__':
    unittest.main()
