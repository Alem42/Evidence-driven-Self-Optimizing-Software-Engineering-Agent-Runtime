"""期望值的盲推导 + 多数表决 + 机械比较（无 I/O，模型调用用假函数）。 Blind derivation + majority + mechanical comparison (no I/O; model calls are fakes)."""
import threading
import unittest

from masa.application.checks.consensus import audit, canonical, derive_all, majority


class MajorityTests(unittest.TestCase):
    def test_strict_majority_only(self):
        self.assertEqual(majority(['a', 'a', 'b'], 3), 'a')
        self.assertIsNone(majority(['a', 'b', 'c'], 3))  # 没有多数 / no majority
        self.assertIsNone(majority(['a', 'a', 'b', 'b'], 4))  # 平票 / a tie
        self.assertIsNone(majority(['a', 'b'], 2))
        self.assertEqual(majority(['a', 'a', None], 3), 'a')  # 失败的样本不投票但占分母 / failed samples do not vote but count in the total
        self.assertIsNone(majority(['a', None, None], 3))  # 1/3 不是多数 / 1 of 3 is no majority
        self.assertIsNone(majority([], 3))

    def test_canonical_is_tolerant_of_noise_not_content(self):
        self.assertEqual(canonical('1 2 3  \n\n', 0), canonical('1 2 3\n', 0))
        self.assertNotEqual(canonical('1 2 3\n', 0), canonical('1 2 4\n', 0))
        self.assertNotEqual(canonical('x\n', 0), canonical('x\n', 1))


class AuditTests(unittest.TestCase):
    def derive_from(self, answers):
        """每次调用按顺序返回一份预设的推导；None 表示这次调用失败。 Each call returns the next scripted derivation; None means the call fails."""
        queue, lock = list(answers), threading.Lock()

        def derive(cases):
            with lock:
                item = queue.pop(0)
            if item is None:
                raise RuntimeError('model down')
            return [{'index': i, 'stdout': out, 'exit_code': code} for i, (out, code) in item.items()]
        return derive

    def test_agree_disagree_and_abstain(self):
        samples = [{0: ('3\n', 0), 1: ('7\n', 0), 2: ('a\n', 0)}, {0: ('3\n', 0), 1: ('7\n', 0), 2: ('b\n', 0)}, {0: ('3\n', 0), 1: ('8\n', 0), 2: ('c\n', 0)}]
        derivations = derive_all(self.derive_from(samples), [], samples=3, workers=1)
        stated = {0: ('3\n', 0), 1: ('9\n', 0), 2: ('a\n', 0)}
        result = audit(stated, derivations)
        self.assertEqual(result[0]['status'], 'agree')
        self.assertEqual((result[1]['status'], result[1]['derived']), ('disagree', ('7', 0)))  # 声明 9，多数推导是 7 / stated 9, the majority derived 7
        self.assertEqual(result[2]['status'], 'abstain')  # 三个答案各不相同 / three different answers
        self.assertEqual(result[0]['agree'], 3)

    def test_a_failed_sample_is_one_vote_fewer_not_a_crash(self):
        samples = [{0: ('3\n', 0)}, None, {0: ('3\n', 0)}]
        derivations = derive_all(self.derive_from(samples), [], samples=3, workers=1)
        self.assertEqual(sum(d is None for d in derivations), 1)
        self.assertEqual(audit({0: ('3\n', 0)}, derivations)[0]['status'], 'agree')  # 2/3 仍是多数 / 2 of 3 is still a majority
        self.assertEqual(audit({0: ('3\n', 0)}, [None, None, {0: ('3\n', 0)}])[0]['status'], 'abstain')

    def test_derivations_really_run_in_parallel(self):
        barrier = threading.Barrier(3, timeout=5)

        def derive(cases):
            barrier.wait()  # 三个线程必须同时在场才能通过 / passes only if all three threads are present at once
            return [{'index': 0, 'stdout': 'x\n', 'exit_code': 0}]
        derivations = derive_all(derive, [], samples=3, workers=3)
        self.assertEqual(sum(d is not None for d in derivations), 3)

    def test_the_stated_value_is_compared_after_normalisation(self):
        derivations = [{0: canonical('5 \n\n', 0)}] * 3
        self.assertEqual(audit({0: ('5\n', 0)}, derivations)[0]['status'], 'agree')
        self.assertEqual(audit({0: ('5\n', 1)}, derivations)[0]['status'], 'disagree')


if __name__ == '__main__':
    unittest.main()
