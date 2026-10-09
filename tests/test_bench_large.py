"""大项目任务：参考实现用手算的答案钉住；不在默认套餐里；能按 id 运行。 Large-project tasks: the reference is pinned by hand-computed answers; not in the default suites; runnable by id."""
import unittest

from masa.bench.runner import BY_ID, resolve_config
from masa.bench.tasks import SUITES, TASKS
from masa.bench.tasks_large import LARGE_TASKS, ref_bank


class LargeTaskTests(unittest.TestCase):
    def test_reference_matches_hand_computed_answers(self):
        out, code = ref_bank((), 'open alice 100\nopen bob 50\ndeposit alice 25\nwithdraw bob 20\ntransfer alice bob 60\nbalance alice\nbalance bob\nreport\n')
        self.assertEqual((out, code), ('ok\nok\nok\nok\nok\nalice 65\nbob 90\nalice 65\nbob 90\ntotal 155\n', 0))  # 100+25-60=65, 50-20+60=90, 总 155 / totals by hand
        self.assertEqual(ref_bank((), '')[0], '')
        self.assertEqual(ref_bank((), 'report\n')[0], 'total 0\n')
        # 金额先于账户检查；0 只允许开户；同一账户先于不存在 / amount first; 0 only for open; same account before unknown
        self.assertEqual(ref_bank((), 'deposit ghost 0\ntransfer a a 1\nopen a 0\nwithdraw a 0\n')[0], 'error: bad amount\nerror: same account\nok\nerror: bad amount\n')

    def test_the_large_task_is_runnable_by_id_but_not_in_any_suite(self):
        task = LARGE_TASKS[0]
        self.assertEqual((task.id, task.level), ('bank', 10))
        self.assertIs(BY_ID['bank'], task)
        self.assertNotIn(task, TASKS)
        self.assertTrue(all('bank' not in suite['tasks'] for suite in SUITES.values()))
        self.assertEqual(resolve_config({'suite': 'custom', 'task_ids': ['bank']})['task_ids'], ['bank'])
        self.assertGreaterEqual(len(task.cases), 5)


if __name__ == '__main__':
    unittest.main()
