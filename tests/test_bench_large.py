"""大项目任务：参考实现用手算的答案钉住；不在默认套餐里；能按 id 运行。 Large-project tasks: the reference is pinned by hand-computed answers; not in the default suites; runnable by id."""
import unittest

from masa.bench.runner import BY_ID, resolve_config
from masa.bench.tasks import SUITES, TASKS
from masa.bench.tasks_large import LARGE_TASKS, ref_bank, ref_grades, ref_inventory


class LargeTaskTests(unittest.TestCase):
    def test_bank_reference_matches_hand_computed_answers(self):
        out, code = ref_bank((), 'open alice 100\nopen bob 50\ndeposit alice 25\nwithdraw bob 20\ntransfer alice bob 60\nbalance alice\nbalance bob\nreport\n')
        self.assertEqual((out, code), ('ok\nok\nok\nok\nok\nalice 65\nbob 90\nalice 65\nbob 90\ntotal 155\n', 0))  # 100+25-60=65, 50-20+60=90, 总 155 / totals by hand
        self.assertEqual(ref_bank((), '')[0], '')
        self.assertEqual(ref_bank((), 'report\n')[0], 'total 0\n')
        # 金额先于账户检查；0 只允许开户；同一账户先于不存在 / amount first; 0 only for open; same account before unknown
        self.assertEqual(ref_bank((), 'deposit ghost 0\ntransfer a a 1\nopen a 0\nwithdraw a 0\n')[0], 'error: bad amount\nerror: same account\nok\nerror: bad amount\n')

    def test_inventory_reference_matches_hand_computed_answers(self):
        # apple 10+5=15 件每件 150，pear 5 件每件 200，总值 15*150+5*200=3250；低于 8 件的只有 pear / total value 3250; only pear is below 8
        self.assertEqual(ref_inventory((), 'add apple 10 150\nadd pear 5 200\nadd apple 5 150\nstock apple\nvalue\nlow 8\n')[0], 'ok\nok\nok\napple 15 150\nvalue 3250\npear\n')
        # 数字检查先于存在性检查；价格不同被拒 / number check before existence; a different price is refused
        self.assertEqual(ref_inventory((), 'remove ghost 0\nadd a 1 1\nadd a 1 2\n')[0], 'error: bad number\nok\nerror: price mismatch\n')
        self.assertEqual(ref_inventory((), 'add k 1 1\nremove k 1\nlow 1\nlow 0\n')[0], 'ok\nok\nk\nnone\n')

    def test_grades_reference_matches_hand_computed_answers(self):
        # (90*5 + 93*3)/8 = 729/8 = 91.125 正好在中间，向上进位得 91.13（Python 的 round 会得到 91.12）；(90*5 + 91*3)/8 = 90.375 -> 90.38
        out = ref_grades((), 'student amy\ncredits math 5\ncredits art 3\ngrade amy math 90\ngrade amy art 93\ngpa amy\ngrade amy art 91\ngpa amy\n')[0]
        self.assertEqual(out.splitlines()[-3:], ['amy 91.13', 'ok', 'amy 90.38'])
        self.assertEqual(ref_grades((), 'student a\ngrade a x 100\ngpa a\ncredits x 1\ngpa a\n')[0], 'ok\nok\na n/a\nok\na 100.00\n')  # 学分可以之后设置 / credits may come later
        self.assertEqual(ref_grades((), 'student b\nstudent a\ncredits c 1\ngrade a c 70\ngrade b c 70\ntop\n')[0].splitlines()[-1], 'a 70.00')  # 并列取名字小的 / ties go to the smaller name

    def test_the_large_tasks_are_runnable_by_id_but_not_in_any_suite(self):
        self.assertEqual([t.id for t in LARGE_TASKS], ['bank', 'inventory', 'grades'])
        for task in LARGE_TASKS:
            self.assertEqual(task.level, 10)
            self.assertIs(BY_ID[task.id], task)
            self.assertNotIn(task, TASKS)
            self.assertTrue(all(task.id not in suite['tasks'] for suite in SUITES.values()))
            self.assertGreaterEqual(len(task.cases), 5)
        self.assertEqual(resolve_config({'suite': 'custom', 'task_ids': ['inventory', 'grades']})['task_ids'], ['inventory', 'grades'])


if __name__ == '__main__':
    unittest.main()
