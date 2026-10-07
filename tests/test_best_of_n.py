"""best-of-N：修复阶段多采样、逐个真实验证、选最好的；关闭时与原来一致；预算与已通过时不乱花钱。全部用脚本化假模型。
best-of-N: more samples in the repair stage, each really verified, the best wins; unchanged when off; no wasted spend once a candidate passes. Scripted fake models only."""
import unittest

from masa.application.orchestration.routing import validate_policy
from masa.domain.models import MasaError

from test_fix_flow import FixFlowCase, IMPL, Model, World


class Flaky(Model):
    """第 k 次修复调用才真的修好实现。 The k-th repair call is the first one that really fixes the implementation."""

    def __init__(self, *args, good_from=2, **kwargs):
        super().__init__(*args, fixes_impl=False, **kwargs)
        self.good_from, self.repairs = good_from, 0

    def respond(self, context):
        if context['purpose'] == 'project_repair':
            self.repairs += 1
            self.world.log.append(f"project_repair {self.name}")
            self.contexts.append(context)
            self.calls += 1
            if self.repairs >= self.good_from:
                self.world.impl_ok = True
            return {IMPL: f'package app\n\nfunc Value() int {{ return {self.repairs} }} // sample {self.repairs}\n'}
        return super().respond(context)


class BestOfNTests(FixFlowCase):
    def run_it(self, n, good_from=2):
        world = World(impl_ok=False, test_ok=True)  # 只有实现的语法错误 / only the implementation syntax error
        local, cloud = Flaky('small', 'local', world, good_from=good_from), Model('big', 'cloud', world)
        store, job = self.run_task(local, cloud, world, policy={'best_of_n': n} if n else None)
        return world, store, job, local

    def test_extra_samples_are_verified_and_the_passing_one_wins(self):
        world, store, job, local = self.run_it(3, good_from=2)
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
        events = self.events(store, 'best_of_n')
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]['tried'], events[0]['passed'], events[0]['chosen']), (2, [False, True], 1))
        self.assertEqual(local.repairs, 2)  # 第 2 个样本通过后不再采样 / sampling stops once a candidate passes
        # 初始失败的验证 1 次 + 两个候选各 1 次；被选中的候选没有被重复验证（否则是 4）。 The initial failing verification + one per candidate; the chosen one is not verified again (else 4).
        self.assertEqual(world.verifications, 3)

    def test_a_passing_first_candidate_costs_nothing_extra(self):
        world, store, job, local = self.run_it(3, good_from=1)
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
        self.assertEqual(local.repairs, 1)
        event = self.events(store, 'best_of_n')[0]
        self.assertEqual((event['tried'], event['passed']), (1, [True]))

    def test_when_off_nothing_changes(self):
        for n in (None, 1):
            world, store, job, local = self.run_it(n, good_from=2)
            self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
            self.assertEqual(self.events(store, 'best_of_n'), [])
            self.assertEqual(job.get('preverified', {}), {})

    def test_the_best_unresolved_count_wins_when_nothing_passes(self):
        world = World(impl_ok=False, test_ok=False)  # 两侧都有问题：第 1 个候选改善不了，第 2 个改善 / both sides broken
        local = Flaky('small', 'local', world, good_from=2)
        cloud = Model('big', 'cloud', world, fixes_test=False)
        store, job = self.run_task(local, cloud, world, policy={'best_of_n': 2})
        events = self.events(store, 'best_of_n')
        self.assertTrue(events)
        first = events[0]
        self.assertEqual(first['chosen'], first['unresolved'].index(min(first['unresolved'])) if not any(first['passed']) else first['passed'].index(True))

    def test_test_revisions_are_never_sampled_more_than_once(self):
        # 真实评测里 csv_sum / lcs 的 best_of_n 事件出现在 test_revision 阶段：按“通过”选测试会偏向迁就缺陷实现的测试。
        # In the real evaluation best_of_n fired in the test_revision stage; selecting tests by "passes" would favour tests that bend to a defective implementation.
        world = World(impl_ok=True, test_ok=False)
        local, cloud = Model('small', 'local', world, fixes_test=True), Model('big', 'cloud', world)
        store, job = self.run_task(local, cloud, world, policy={'best_of_n': 3})
        self.assertEqual(store.run(job['result']['id'])['status'], 'succeeded', job.get('note'))
        self.assertEqual(self.events(store, 'best_of_n'), [])

    def test_the_policy_is_bounded(self):
        self.assertEqual(validate_policy({})['best_of_n'], 1)
        self.assertEqual(validate_policy({'best_of_n': 3})['best_of_n'], 3)
        for bad in (0, 6, True, 'x', 2.5):
            with self.assertRaises(MasaError):
                validate_policy({'best_of_n': bad})


if __name__ == '__main__':
    unittest.main()
