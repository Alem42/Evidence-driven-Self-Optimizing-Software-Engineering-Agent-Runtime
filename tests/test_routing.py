"""模型路由与任务级预算：纯策略决策表 + 协调器里的升级阶梯。
Routing and task budgets: the pure policy table plus the escalation ladder inside the coordinator (offline, deterministic)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from masa.application.coordinator import WorkflowCoordinator
from masa.application.router import Router, RoutingStop, build_snapshot
from masa.application.routing import (Candidate, DEFAULT_POLICY, estimate_tokens, route, spend_from_report, unlimited_budget,
                                      validate_budget)
from masa.domain.models import MasaError
from masa.infrastructure.jobs import Jobs
from masa.infrastructure.store import Store
from test_project_generation import FILES
from test_project_plan import CHECKS, SPEC
from test_runtime import FakeExecutor
from test_usage import FakeSettings  # noqa: F401  (shared helper)

LOCAL = Candidate('local', 1, 0, 'local', 'small', context_limit=16384, max_output=2048)
CLOUD = Candidate('cloud', 2, 0, 'cloud', 'big', context_limit=65536, max_output=4096, price_in=1.0, price_out=2.0)
SPEND = {'calls': 0, 'cloud_tokens': 0, 'active_seconds': 0, 'cost': 0.0}
BUDGET = {'max_model_calls': 40, 'max_cloud_tokens': 200_000, 'max_active_seconds': 3600, 'max_cost': None}


def decide(history, candidates=(LOCAL, CLOUD), spend=SPEND, budget=BUDGET, chain='fix', need=1000, policy=DEFAULT_POLICY, **kw):
    return route('project_repair', chain, list(candidates), history, spend, budget, policy, need, **kw)


class RoutePolicyTests(unittest.TestCase):
    def test_starts_at_the_lowest_eligible_level(self):
        d = decide([])
        self.assertEqual((d.action, d.candidate, d.reason), ('use', 'local', 'start_lowest_eligible'))

    def test_escalates_after_the_attempts_of_a_non_top_level(self):
        # fix 链每级 1 次：本地失败一次就升级。 The fix chain gives each non-top level one attempt.
        d = decide([{'candidate': 'local', 'level': 1}])
        self.assertEqual((d.candidate, d.reason, d.escalated), ('cloud', 'escalate', True))
        # planning 链每级 2 次（初次 + 自修）：失败一次仍留在本地。 Planning allows initial + one self-repair.
        d = decide([{'candidate': 'local', 'level': 1}], chain='planning')
        self.assertEqual((d.candidate, d.reason), ('local', 'retry_same_level'))
        d = decide([{'candidate': 'local', 'level': 1}] * 2, chain='planning')
        self.assertEqual((d.candidate, d.escalated), ('cloud', True))

    def test_the_top_level_is_never_capped_by_attempts(self):
        history = [{'candidate': 'local', 'level': 1}] + [{'candidate': 'cloud', 'level': 2}] * 5
        d = decide(history)
        self.assertEqual((d.action, d.candidate, d.reason), ('use', 'cloud', 'top_level'))

    def test_budget_exhaustion_is_a_stop_not_an_exception(self):
        spend = {**SPEND, 'cloud_tokens': 199_000}
        d = decide([{'candidate': 'local', 'level': 1}], spend=spend)
        self.assertEqual((d.action, d.reason), ('stop', 'budget_cloud_tokens'))
        d = decide([], spend={**SPEND, 'calls': 40})
        self.assertEqual((d.action, d.reason), ('stop', 'budget_calls'))
        d = decide([], spend={**SPEND, 'active_seconds': 3600})
        self.assertEqual((d.action, d.reason), ('stop', 'budget_time'))

    def test_local_can_continue_when_only_the_cloud_budget_is_gone(self):
        d = decide([], spend={**SPEND, 'cloud_tokens': 200_000})
        self.assertEqual((d.action, d.candidate), ('use', 'local'))

    def test_context_admission_skips_models_that_cannot_hold_the_input(self):
        d = decide([], need=20_000)  # 20k + 2k output > 16k local limit
        self.assertEqual((d.candidate, d.reason), ('cloud', 'start_lowest_eligible'))
        d = decide([], need=70_000)
        self.assertEqual((d.action, d.reason), ('stop', 'context'))

    def test_blocked_current_level_escalates_instead_of_spinning(self):
        d = decide([{'candidate': 'local', 'level': 1}], chain='planning', need=20_000)
        self.assertEqual((d.candidate, d.escalated), ('cloud', True))

    def test_escalation_limit_and_missing_higher_level(self):
        three = (LOCAL, CLOUD, Candidate('premium', 3, 0, 'cloud', 'best', context_limit=65536, max_output=4096))
        history = [{'candidate': 'local', 'level': 1}, {'candidate': 'cloud', 'level': 2}]
        d = decide(history, three, policy={**DEFAULT_POLICY, 'max_escalations': 1})
        self.assertEqual((d.action, d.reason), ('stop', 'escalation_limit'))
        d = decide([{'candidate': 'local', 'level': 1}], (LOCAL,), policy={**DEFAULT_POLICY, 'attempts_per_level': {'fix': 1}})
        self.assertEqual(d.action, 'use')  # a single level is the top level: keep using it
        d = decide([{'candidate': 'local', 'level': 1}] * 3, (LOCAL, Candidate('l2', 1, 1, 'local', 'x')))
        self.assertEqual(d.action, 'use')

    def test_cost_budget_requires_prices(self):
        unpriced = Candidate('cloud', 2, 0, 'cloud', 'big', context_limit=65536, max_output=4096)
        d = decide([{'candidate': 'local', 'level': 1}], (LOCAL, unpriced), budget={**BUDGET, 'max_cost': 1.0})
        self.assertEqual((d.action, d.reason), ('stop', 'price_unknown'))
        d = decide([{'candidate': 'local', 'level': 1}], budget={**BUDGET, 'max_cost': 1.0})
        self.assertEqual(d.candidate, 'cloud')
        d = decide([{'candidate': 'local', 'level': 1}], spend={**SPEND, 'cost': 0.99}, budget={**BUDGET, 'max_cost': 1.0}, need=2000)
        self.assertEqual((d.action, d.reason), ('stop', 'budget_cost'))

    def test_role_restrictions_and_unavailable_candidates(self):
        only_cloud_plans = Candidate('cloud', 2, 0, 'cloud', 'big', roles=('project_planner',), context_limit=65536, max_output=4096)
        d = decide([], (LOCAL, only_cloud_plans))
        self.assertEqual(d.candidate, 'local')
        d = decide([{'candidate': 'local', 'level': 1}], (LOCAL, only_cloud_plans))
        self.assertEqual(d.action, 'use')  # cloud may not repair, so local is the top level for this role
        d = decide([], unavailable=frozenset({'local', 'cloud'}))
        self.assertEqual((d.action, d.reason), ('stop', 'no_candidate'))


class SpendTests(unittest.TestCase):
    def test_unknown_usage_is_reserved_never_zero(self):
        report = {'calls': [
            {'kind': 'cloud', 'model': 'big', 'total_tokens': 1000, 'prompt_tokens': 800, 'completion_tokens': 200},
            {'kind': 'cloud', 'model': 'big', 'total_tokens': None, 'reserved_tokens': 5000},
            {'kind': 'local', 'model': 'small', 'total_tokens': 9999}],
            'totals': {'calls': 3, 'model_ms': 4000}, 'tools': [{'kind': 'tool', 'duration_ms': 1000}, {'kind': 'gate', 'duration_ms': 50}]}
        spend = spend_from_report(report, {'big': (1.0, 2.0)})
        self.assertEqual(spend['cloud_tokens'], 6000)
        self.assertEqual(spend['active_seconds'], 5.0)
        self.assertEqual(spend['reserved_calls'], 1)
        self.assertAlmostEqual(spend['cost'], (800 * 1 + 200 * 2) / 1e6 + 5000 * 2 / 1e6)
        self.assertFalse(spend_from_report(report, {})['cost_known'])

    def test_budget_validation_and_estimates(self):
        self.assertEqual(validate_budget({'max_cloud_tokens': 5000})['max_cloud_tokens'], 5000)
        self.assertIsNone(validate_budget({'max_cost': None})['max_cost'])
        for bad in ({'max_model_calls': 0}, {'max_cloud_tokens': 'x'}, {'nope': 1}, {'max_cost': -1}):
            with self.assertRaises(MasaError):
                validate_budget(bad)
        self.assertGreater(estimate_tokens('中文' * 100), estimate_tokens('ab' * 100))


# ───────────── 协调器集成：本地修复失败 → 升级 → 成功 / Coordinator integration ─────────────

def fake_entry(ident, level, model, model_type, **extra):
    return {'id': ident, 'level': level, 'priority': 0, 'model_type': model_type, 'model': model, 'roles': [],
            'context_limit': 32768, 'max_output_tokens': 1024, 'price_in': None, 'price_out': None, 'digest': None,
            'snapshot': None, **extra}


class ScriptedProvider:
    """按角色返回确定性结果的假模型；repair 的行为可注入。 A deterministic fake model with injectable repair behaviour."""

    def __init__(self, model, on_repair):
        self.profile = {'provider': 'test', 'model': model}
        self.on_repair = on_repair
        self.repairs = 0
        self.usage = {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}

    def respond(self, context):
        purpose = context['purpose']
        if purpose == 'project_repair':
            self.repairs += 1
            self.on_repair()
            return {'internal/app/app.go': f'package app\n\nfunc Value() int {{ return 42 }} // by {self.profile["model"]} #{self.repairs}\n'}
        return {'project_planner': SPEC, 'project_tester': CHECKS, 'project_developer': FILES}[purpose]


class EscalationIntegrationTests(unittest.TestCase):
    def run_task(self, executor, local, cloud, budget=None, local_first_budget=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(Path(temp.name))
        self.addCleanup(store.close)
        providers = {'local': local, 'cloud': cloud}
        snapshot = {'version': 1, 'mode': 'ladder', 'policy': dict(DEFAULT_POLICY), 'budget': {**BUDGET, **(budget or {})},
                    'candidates': [fake_entry('local', 1, 'small', 'local'), fake_entry('cloud', 2, 'big', 'cloud')]}
        jobs = Jobs(Path(temp.name))
        jobs['job'] = {'status': 'running', 'run_id': None, 'mode': 'auto', 'phase': 'planning', 'attempt': 0, 'started': 0,
                       'request': {'goal': 'Build a CLI'}}
        router = Router(store, snapshot, lambda entry: providers[entry['id']])
        with patch('masa.intelligence.repair_context.build_repair_context', lambda store, ex, rid, files, ev, fb: (files, None)):
            WorkflowCoordinator(store, executor, None, jobs['job'], router=router).run()
        return store, jobs['job']

    def decisions(self, store, job):
        runs = store.all_runs()
        return [e['payload'] for r in runs for e in store.events(r['id']) if e['type'] == 'route_decided']

    def test_local_repair_fails_then_cloud_repairs_and_the_chain_is_recorded(self):
        executor = FakeExecutor(exit_code=1)
        local = ScriptedProvider('small', lambda: None)  # never fixes anything
        cloud = ScriptedProvider('big', lambda: setattr(executor, 'exit_code', 0))
        store, job = self.run_task(executor, local, cloud)
        self.assertEqual(job['status'], 'completed')
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded')
        self.assertEqual((local.repairs, cloud.repairs), (1, 1))  # one local self-repair, then escalate
        repairs = [d for d in self.decisions(store, job) if d['role'] == 'project_repair']
        self.assertEqual([(d['model'], d['reason']) for d in repairs], [('small', 'start_lowest_eligible'), ('big', 'escalate')])
        self.assertTrue(repairs[1]['escalated'])
        # 报告里能看到升级链与预算。 The report exposes the chain and the budget.
        from masa.application.usage import task_report
        report = task_report(store, job['result']['id'])
        self.assertEqual(report['routing']['escalations'], 1)
        self.assertEqual({m['model'] for m in report['by_model']}, {'small', 'big'})
        self.assertEqual(report['routing']['mode'], 'ladder')

    def test_the_local_model_alone_is_enough_when_it_fixes_the_failure(self):
        executor = FakeExecutor(exit_code=1)
        local = ScriptedProvider('small', lambda: setattr(executor, 'exit_code', 0))
        cloud = ScriptedProvider('big', lambda: None)
        store, job = self.run_task(executor, local, cloud)
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded')
        self.assertEqual(cloud.repairs, 0)
        self.assertFalse(any(d['escalated'] for d in self.decisions(store, job)))

    def test_exhausted_cloud_budget_stops_with_evidence_instead_of_spending(self):
        executor = FakeExecutor(exit_code=1)
        local = ScriptedProvider('small', lambda: None)
        cloud = ScriptedProvider('big', lambda: setattr(executor, 'exit_code', 0))
        store, job = self.run_task(executor, local, cloud, budget={'max_cloud_tokens': 1000})  # < one reservation (4k)
        self.assertEqual(job['status'], 'completed')
        self.assertIn('API token 预算', job['note'])
        self.assertEqual(cloud.repairs, 0)
        self.assertEqual(store.run(job['result']['id'])['status'], 'failed')
        events = [e['type'] for r in store.all_runs() for e in store.events(r['id'])]
        self.assertIn('task_stopped', events)

    def test_route_history_and_budget_marker_are_persisted_in_the_job(self):
        """失败链历史写在持久任务里（重启后可据此继续）；这里只验证落盘内容。 Chain history lives in the durable job; this checks what is persisted."""
        executor = FakeExecutor(exit_code=1)
        local = ScriptedProvider('small', lambda: None)
        cloud = ScriptedProvider('big', lambda: setattr(executor, 'exit_code', 0))
        store, job = self.run_task(executor, local, cloud)
        self.assertEqual(job['route_history']['fix'][0]['candidate'], 'local')
        self.assertTrue(job['budget_emitted'])


class SnapshotTests(unittest.TestCase):
    def test_snapshot_contains_no_secrets_and_requires_a_usable_profile(self):
        settings = FakeSettings([('a', {'level': 1, 'priority': 0, 'model_type': 'local', 'model': 'small', 'roles': [],
                                        'context_limit': 8192, 'max_output_tokens': 2048})])
        snapshot = build_snapshot(settings, budget={'max_cloud_tokens': 1000}, digests={'small': 'sha-1'})
        self.assertEqual(snapshot['budget']['max_cloud_tokens'], 1000)
        self.assertEqual(snapshot['candidates'][0]['digest'], 'sha-1')
        self.assertNotIn('api_key', str(snapshot))
        with self.assertRaises(MasaError):
            build_snapshot(FakeSettings([]))

    def test_changed_local_weights_make_a_candidate_unavailable(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                snap = {'version': 1, 'mode': 'ladder', 'policy': dict(DEFAULT_POLICY), 'budget': unlimited_budget(),
                        'candidates': [fake_entry('local', 1, 'small', 'local', digest='old'), fake_entry('cloud', 2, 'big', 'cloud')]}
                router = Router(store, snap, lambda e: None, live_digests={'small': 'new'})
                self.assertEqual(router.unavailable, frozenset({'local'}))
                same = Router(store, snap, lambda e: None, live_digests={'small': 'old'})
                self.assertEqual(same.unavailable, frozenset())
                unreachable = Router(store, snap, lambda e: None, live_digests=None)
                self.assertEqual(unreachable.unavailable, frozenset())
            finally:
                store.close()

    def test_fixed_mode_is_a_one_candidate_ladder_with_no_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                class P:
                    profile = {'model': 'm'}
                router = Router.fixed(store, P())
                d, provider, _ = router.decide('project_repair', 'fix', [{'candidate': 'fixed', 'level': 1}] * 9, need=10 ** 7)
                self.assertEqual(d.action, 'use')
                self.assertIsInstance(provider, P)
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
