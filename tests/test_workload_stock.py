"""R1 工作负载的参考实现自检：手算的期望（不是从参考实现抄来的），覆盖每个功能和几处功能之间的相互作用。零 token。
Self-check of the R1 workload's reference implementation against hand-computed expectations (not copied from the reference), covering every feature and some interactions. Zero tokens."""
import unittest

from masa.bench import workload_stock as w


def lines(*items):
    return '\n'.join(items) + '\n' if items else ''


class ReferenceTests(unittest.TestCase):
    def check(self, level, stdin, out, code, args=()):
        self.assertEqual(w.run(level, list(args), stdin), (out, code))

    def test_base(self):
        self.check(0, lines('ADD a 3', 'ADD b 2', 'ADD a 1', 'REPORT', 'STOCK a', 'STOCK zz'), lines('a: 4', 'b: 2', 'a: 4', 'zz: 0'), 0)
        self.check(0, lines('REPORT'), lines('(empty)'), 0)
        self.check(0, lines('ADD a 2', 'REMOVE a 3', 'REMOVE a 2', 'REPORT', 'STOCK a'), lines('(empty)', 'a: 0'), 1)
        self.check(0, '\n\nADD B 1\nADD a 1\nADD A 2\n  REPORT  \n', lines('A: 2', 'B: 1', 'a: 1'), 0)
        self.check(0, lines('add a 1'), '', 1)  # 第 8 个功能之前命令名区分大小写 / case-sensitive before feature 8

    def test_prices_and_report_format(self):
        self.check(1, lines('PRICE a 2', 'ADD a 3', 'ADD b 4', 'PRICE b 0.25', 'VALUE', 'PRICE a 1', 'VALUE'), lines('total value: 7.00', 'total value: 4.00'), 0)
        self.check(1, lines('PRICE a -1', 'PRICE a 1.234', 'PRICE a abc', 'PRICE a 1.', 'VALUE'), lines('total value: 0.00'), 1)
        self.check(1, lines('ADD a 2', 'PRICE a 1.5', 'ADD b 1', 'REPORT'), lines('a: 2', 'b: 1'), 0)
        self.check(2, lines('ADD a 2', 'PRICE a 1.5', 'ADD b 1', 'REPORT'), lines('a: 2 @ 1.50', 'b: 1 @ -'), 0)

    def test_orders(self):
        self.check(3, lines('ADD a 1', 'ORDER o1 a 2', 'ORDER o2 a 1', 'ORDER o2 a 1', 'ORDERS', 'STOCK a'), lines('o2 a 1 open', 'a: 0'), 1)
        self.check(3, lines('ADD b 3', 'ADD a 3', 'ORDER z9 a 1', 'ORDER A1 b 1', 'ORDER m b 1', 'ORDERS'), lines('A1 b 1 open', 'm b 1 open', 'z9 a 1 open'), 0)
        self.check(4, lines('ADD a 5', 'ORDER o1 a 2', 'SHIP o1', 'CANCEL o1', 'SHIP o1', 'CANCEL nope', 'ORDERS'), lines('o1 a 2 shipped'), 1)
        self.check(4, lines('ADD a 5', 'ORDER o1 a 2', 'CANCEL o1', 'ORDERS', 'STOCK a'), lines('o1 a 2 cancelled', 'a: 5'), 0)

    def test_low_and_undo(self):
        self.check(5, lines('ADD a 5', 'ADD b 1', 'PRICE c 2', 'LOW 3'), lines('b: 1', 'c: 0'), 0)
        self.check(5, lines('LOW 0', 'LOW x'), lines('(none)'), 1)
        self.check(6, lines('ADD a 3', 'ORDER o1 a 1', 'CANCEL o1', 'UNDO', 'ORDERS', 'STOCK a', 'UNDO', 'ORDERS', 'STOCK a'),
                   lines('o1 a 1 open', 'a: 2', '(no orders)', 'a: 3'), 0)
        self.check(6, lines('ADD a 2', 'UNDO', 'LOW 1'), lines('(none)'), 0)  # 撤销也恢复“出现过” / undo restores "known" too
        self.check(6, lines('ADD a 1', 'REMOVE a 5', 'REPORT', 'UNDO', 'REPORT'), lines('a: 1 @ -', '(empty)'), 1)

    def test_strict_and_case(self):
        self.check(7, lines('ADD a 2', 'STOCK a', 'REMOVE a 3', 'STOCK a'), lines('a: 2'), 2, args=['--strict'])
        self.check(7, lines('ADD a 1', 'REPORT'), '', 2, args=['--verbose'])
        self.check(8, '# comment\n   # indented comment\nADD a 1\nstock a\nstock A\n', lines('a: 1', 'A: 0'), 0)

    def test_transactions(self):
        self.check(9, lines('ADD a 5', 'BEGIN', 'REMOVE a 2', 'ADD b 1', 'COMMIT', 'REPORT', 'UNDO', 'REPORT'), lines('a: 3 @ -', 'b: 1 @ -', 'a: 5 @ -'), 0)
        self.check(9, lines('BEGIN', 'BEGIN', 'COMMIT', 'COMMIT', 'ROLLBACK'), '', 1)
        self.check(9, lines('ADD a 1', 'BEGIN', 'ADD a 4'), '', 0)  # 自动回滚只是警告 / the automatic rollback is only a warning
        self.check(9, lines('ADD a 1', 'BEGIN', 'COMMIT', 'UNDO', 'STOCK a'), lines('a: 0'), 0)
        self.check(9, lines('ADD a 2', 'BEGIN', 'ORDER o1 a 1', 'ROLLBACK', 'ORDERS', 'UNDO', 'STOCK a'), lines('(no orders)', 'a: 0'), 0)

    def test_stats_export_and_warehouses(self):
        self.check(10, lines('ADD a 2', 'ADD b 3', 'ADD c 1', 'REMOVE c 1', 'ORDER o1 a 1', 'ORDER o2 b 1', 'SHIP o2', 'ORDER o3 b 1', 'CANCEL o3', 'STATS'),
                   lines('skus: 2', 'units: 3', 'orders: open=1 shipped=1 cancelled=1'), 0)
        self.check(11, lines('ADD b 4', 'ADD a 1', 'PRICE a 2.5', 'PRICE c 1', 'EXPORT'), lines('sku,qty,price', 'a,1,2.50', 'b,4,', 'c,0,1.00'), 0)
        self.check(12, lines('ADD a 5', 'MOVE a 2 @main @east', 'STOCK a @main', 'STOCK a @east', 'MOVE a 9 @main @east', 'MOVE a 1 @east @east', 'UNDO', 'STOCK a @east'),
                   lines('a@main: 3', 'a@east: 2', 'a@east: 0'), 1)
        self.check(12, lines('ADD a 1', 'ADD a 5 @east', 'ORDER o1 a 2', 'ORDER o2 a 1', 'CANCEL o2', 'STOCK a @main', 'REPORT'), lines('a@main: 1', 'a: 6 @ -'), 1)
        self.check(11, lines('ADD a 1 @east'), '', 1)  # 第 12 个功能之前没有仓库参数 / no warehouse argument before feature 12

    def test_task_at_covers_every_feature_so_far(self):
        task = w.task_at(3)
        self.assertEqual(len(task.cases), sum(len(f) for f in w.SCENARIOS[:4]))
        self.assertEqual(len(w.CHANGES) + 1, len(w.SCENARIOS))
        self.assertEqual(w.feature_of_case(1)[:7], [0, 0, 0, 0, 0, 0, 1])


if __name__ == '__main__':
    unittest.main()
