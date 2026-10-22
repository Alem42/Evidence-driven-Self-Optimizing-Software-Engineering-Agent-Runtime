"""崩溃恢复的三类结局（按崩溃点做故障注入）：结果已落账本→复用并继续；不确定的付费调用→暂停（待处理）、不重发；只有明确允许才发起新的付费尝试。
Crash recovery has three outcomes, tested by injecting a crash at each point: result in the ledger -> reuse and go on; an uncertain paid call -> pause (pending), never resend; a new paid attempt only when explicitly allowed.
不要求所有崩溃点同时满足“无需处理、最终完成、绝不重复付费”：进程退出前远端可能已受理请求，本地无从确定。
It is not required that every crash point is "no handling, finishes, never pays twice": the provider may have accepted the request before the process died and locally that cannot be known."""
import tempfile
import unittest
from pathlib import Path

from masa.domain.models import Budget, MasaError, TransportFailure
from masa.infrastructure.store import Store
from masa.runtime.engine import Runtime
from masa.runtime.roles import RoleRuntime
from test_runtime import FakeExecutor


class Crash(BaseException):
    """进程在这里死掉：BaseException 不会被角色层当作模型失败处理，账本里留下 running。 The process dies here: a BaseException is not handled as a model failure, so the ledger keeps `running`."""


class Provider:
    def __init__(self, kind='cloud', behaviour='ok', paid_retry=False, free_retry=False):
        self.profile = {'model': 'm', 'model_type': kind}
        self.config = {'model_type': kind}
        self.retry_unknown_calls, self.retry_unknown_paid = free_retry, paid_retry
        self.behaviour, self.calls = behaviour, 0

    def respond(self, context):
        self.calls += 1
        if self.behaviour == 'crash':
            raise Crash
        if self.behaviour == 'silent':
            raise TransportFailure('no response received')
        if self.behaviour == 'rejected':
            raise MasaError('response rejected by validation')
        return {'answer': context['purpose']}


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'source'
        source.mkdir()
        (source / 'go.mod').write_text('module demo\n\ngo 1.27.0\n', encoding='utf-8')
        (source / 'main.go').write_text('package demo\n', encoding='utf-8')
        store = Store(self.root)
        self.rid = Runtime(store, FakeExecutor()).create(source, 'Build CLI', Budget(model_calls=10))
        store.close()

    def call(self, provider, purpose='project_planner'):
        store = Store(self.root)  # 每次都是新的连接：像进程重启后一样 / a fresh connection each time, like after a restart
        try:
            return RoleRuntime(store).call(self.rid, provider, purpose, {'goal': 'g'})
        finally:
            store.close()

    def ledger(self):
        store = Store(self.root)
        try:
            return store.run(self.rid)['model_calls'], [e['type'] + ':' + str(e['payload'].get('reason', '')) for e in store.events(self.rid)]
        finally:
            store.close()

    def resolve(self, purpose='project_planner', invocation='initial'):
        store = Store(self.root)
        try:
            store.event(self.rid, 'uncertain_call_resolved', {'step_id': purpose, 'invocation_id': invocation, 'action': 'retry'})
        finally:
            store.close()


class RecoveryClassTests(Base):
    def test_a_stored_result_is_reused_and_nothing_is_sent_again(self):
        first = Provider()
        self.assertEqual(self.call(first), {'answer': 'project_planner'})
        again = Provider()
        self.assertEqual(self.call(again), {'answer': 'project_planner'})
        self.assertEqual((first.calls, again.calls, self.ledger()[0]), (1, 0, 1))

    def test_an_uncertain_paid_call_pauses_and_is_never_resent_by_default(self):
        with self.assertRaises(Crash):
            self.call(Provider('cloud', 'crash'))
        resumed = Provider('cloud')
        with self.assertRaisesRegex(MasaError, 'pending queue'):
            self.call(resumed)
        self.assertEqual((resumed.calls, self.ledger()[0]), (0, 1))  # 没有第二次请求，也没有第二次计费 / no second request, no second charge

    def test_a_silent_paid_call_is_just_as_uncertain(self):
        with self.assertRaises(TransportFailure):
            self.call(Provider('cloud', 'silent'))
        with self.assertRaisesRegex(MasaError, 'pending queue'):
            self.call(Provider('cloud'))

    def test_an_explicit_retry_decision_allows_exactly_one_new_paid_attempt(self):
        with self.assertRaises(Crash):
            self.call(Provider('cloud', 'crash'))
        self.resolve()
        retry = Provider('cloud')
        self.assertEqual(self.call(retry), {'answer': 'project_planner'})
        calls, events = self.ledger()
        self.assertEqual((retry.calls, calls), (1, 2))  # 新的一次付费尝试占用调用次数预算 / the new paid attempt uses the call budget
        self.assertIn('model_abandoned:explicit retry of an uncertain paid call', events)
        # 同一个决定不能反复使用：第二次崩溃后又回到暂停 / one decision is used once: after a second crash it pauses again
        store = Store(self.root)
        store.db.execute("UPDATE role_invocations SET status='running',output_ref=NULL WHERE run_id=?", (self.rid,))
        store.db.commit()
        store.close()
        with self.assertRaisesRegex(MasaError, 'pending queue'):
            self.call(Provider('cloud'))

    def test_the_policy_can_allow_a_new_paid_attempt_without_a_per_call_decision(self):
        with self.assertRaises(Crash):
            self.call(Provider('cloud', 'crash'))
        retry = Provider('cloud', paid_retry=True)
        self.assertEqual(self.call(retry), {'answer': 'project_planner'})
        self.assertEqual(retry.calls, 1)

    def test_a_rejected_response_is_a_known_outcome_and_is_never_retried_this_way(self):
        with self.assertRaises(MasaError):
            self.call(Provider('cloud', 'rejected'))
        again = Provider('cloud', paid_retry=True)
        with self.assertRaisesRegex(MasaError, 'pending queue'):
            self.call(again)
        self.assertEqual(again.calls, 0)

    def test_a_free_local_call_is_retried_without_a_second_budget_charge(self):
        with self.assertRaises(Crash):
            self.call(Provider('local', 'crash', free_retry=True))
        retry = Provider('local', free_retry=True)
        self.assertEqual(self.call(retry), {'answer': 'project_planner'})
        self.assertEqual((retry.calls, self.ledger()[0]), (1, 1))


if __name__ == '__main__':
    unittest.main()
