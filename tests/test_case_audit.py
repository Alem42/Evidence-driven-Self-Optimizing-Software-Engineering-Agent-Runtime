"""Tester 用例审计：纯逻辑（多数、弃权、丢弃保护、失败只是少一票）与规划阶段的接入（默认关闭、写进账本、失败不挡规划、每次调用都经过持久角色层）。全部用假模型。
Tester case audit: the pure logic (majority, abstain, drop guard, a failure is one vote fewer) and the planning hook (off by default, written to the ledger, a failure never blocks planning, every call goes through the durable role layer)."""
import copy
import tempfile
import unittest
from pathlib import Path

from masa.application.checks.case_audit import apply, audit_cases
from masa.application.planning import ProjectPlanning
from masa.application.orchestration.routing import validate_policy
from masa.infrastructure.store import Store

from test_project_plan import CHECKS, SPEC
from test_runtime import FakeExecutor

CASES = [{'name': f'c{i}', 'input': f'in{i}', 'expected': f'out{i}', 'level': 'unit'} for i in range(5)]


def scripted(derived, same_by_sample):
    """derive 每次返回一份推导；judge 每次返回一份等价判断（None = 这次调用失败）。 derive returns a scripted derivation per call; judge a scripted verdict per call (None = the call fails)."""
    d, j = iter(derived), iter(same_by_sample)

    def derive(views):
        return next(d)

    def judge(pairs):
        value = next(j)
        if value is None:
            raise RuntimeError('judge down')
        return {p['index']: value[p['index']] for p in pairs}
    return derive, judge


class LogicTests(unittest.TestCase):
    def test_majority_decides_and_ties_abstain(self):
        derived = [{i: 'x' for i in range(5)}] * 3
        flags = [{0: True, 1: False, 2: False, 3: True, 4: True}, {0: True, 1: False, 2: True, 3: False, 4: True}, {0: True, 1: True, 2: False, 3: True, 4: False}]
        status = audit_cases(CASES, *scripted(derived, flags), samples=3)
        self.assertEqual(status[0], 'agree')
        self.assertEqual(status[1], 'disagree')  # 2/3 认为不同 / 2 of 3 say different
        self.assertEqual(status[2], 'disagree')
        self.assertEqual(status[3], 'agree')
        self.assertEqual(status[4], 'agree')

    def test_a_failing_sample_is_one_vote_fewer(self):
        derived = [{i: 'x' for i in range(5)}] * 3
        flags = [{i: False for i in range(5)}, None, {i: False for i in range(5)}]
        status = audit_cases(CASES, *scripted(derived, flags), samples=3)
        self.assertTrue(all(s == 'disagree' for s in status.values()))  # 2 票足够多数 / two votes are a majority of three
        none = audit_cases(CASES, *scripted(derived, [None, None, None]), samples=3)
        self.assertEqual(none, {i: 'abstain' for i in range(5)} if none else {})

    def test_dropping_never_leaves_a_thin_suite(self):
        self.assertEqual(apply(CASES, {0: 'disagree', 1: 'agree', 2: 'agree', 3: 'agree', 4: 'agree'})[1], [0])
        kept, dropped = apply(CASES, {0: 'disagree', 1: 'disagree', 2: 'disagree', 3: 'agree', 4: 'agree'})
        self.assertEqual((len(kept), dropped), (5, []))  # 只剩 2 个：一个都不丢 / only 2 would remain: drop none
        self.assertEqual(apply(CASES, {i: 'abstain' for i in range(5)})[1], [])  # 弃权的不丢 / abstentions are kept


class Provider:
    """规划阶段用的假模型：Planner/Tester 返回固定计划，审计角色按脚本回答，并记录每次调用的用途。 A fake for planning: fixed Planner/Tester output, scripted audit answers, and a log of purposes."""
    profile = {'provider': 'test', 'model': 'fake'}
    usage = {'total_tokens': 10}

    def __init__(self, suspect=(), fail=False):
        self.suspect, self.fail, self.log = set(suspect), fail, []

    def respond(self, context):
        purpose = context['purpose']
        self.log.append(purpose)
        if purpose == 'project_planner':
            return copy.deepcopy(SPEC)
        if purpose == 'project_tester':
            checks = copy.deepcopy(CHECKS)
            checks[0]['cases'] = [{'name': f'c{i}', 'input': f'in{i}', 'expected': f'out{i}', 'level': 'unit'} for i in range(5)]
            return checks
        if self.fail:
            raise RuntimeError('audit model down')
        if purpose == 'case_deriver':
            return {'answers': [{'index': c['index'], 'result': f"out{c['index']}"} for c in context['cases']]}
        return {'answers': [{'index': p['index'], 'same': p['index'] not in self.suspect} for p in context['pairs']]}


class PlanningHookTests(unittest.TestCase):
    def plan(self, provider, audit):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(Path(temp.name))
        self.addCleanup(store.close)
        planning = ProjectPlanning(store, FakeExecutor())
        rid = planning.generate(provider, 'goal', audit_provider=provider if audit else None)
        return store, rid

    def test_off_by_default_nothing_extra_is_called(self):
        provider = Provider()
        store, rid = self.plan(provider, audit=False)
        self.assertEqual(provider.log, ['project_planner', 'project_tester'])
        self.assertNotIn('case_audit', store.run(rid)['data']['project_plan'])
        self.assertFalse(validate_policy({})['test_audit'])

    def test_suspect_cases_are_dropped_and_recorded(self):
        provider = Provider(suspect={1})
        store, rid = self.plan(provider, audit=True)
        plan = store.run(rid)['data']['project_plan']
        self.assertEqual(plan['case_audit']['dropped'], [1])
        checks = store.read(plan['checks_ref'])
        self.assertEqual([c['name'] for c in checks[0]['cases']], ['c0', 'c2', 'c3', 'c4'])
        self.assertEqual(provider.log.count('case_deriver'), 3)  # 3 个样本 / three samples
        self.assertEqual(provider.log.count('case_judge'), 3)
        events = [e['payload'] for e in store.events(rid) if e['type'] == 'case_audit']
        self.assertEqual((events[0]['dropped'], events[0]['suspect']), ([1], 1))

    def test_everything_agrees_means_nothing_changes(self):
        provider = Provider()
        store, rid = self.plan(provider, audit=True)
        plan = store.run(rid)['data']['project_plan']
        self.assertEqual(plan['case_audit']['dropped'], [])
        self.assertEqual(len(store.read(plan['checks_ref'])[0]['cases']), 5)

    def test_a_broken_audit_model_never_blocks_planning(self):
        provider = Provider(fail=True)
        store, rid = self.plan(provider, audit=True)
        plan = store.run(rid)['data']['project_plan']
        self.assertEqual(plan['status'], 'awaiting_review')
        self.assertEqual(len(store.read(plan['checks_ref'])[0]['cases']), 5)  # 用例原样 / cases untouched


if __name__ == '__main__':
    unittest.main()
