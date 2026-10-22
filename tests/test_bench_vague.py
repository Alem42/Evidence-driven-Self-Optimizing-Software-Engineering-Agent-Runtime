"""VagueBench 自身的检验：尺子必须接受参考实现和各种合理的写法，必须拒绝错误的输出。零 token。
The ruler checks itself: it must accept the reference and every reasonable way of writing the answer, and reject wrong outputs. Zero tokens."""
import unittest

from masa.bench import tasks_vague as v


def task(task_id):
    return next(t for t in v.VAGUE_TASKS if t.id == task_id)


def verdict(task_id, stdin, out, err='', code=0):
    return task(task_id).verify([], stdin, out, err, code)[0]


class ReferenceTests(unittest.TestCase):
    def test_every_reference_output_is_accepted_on_every_case(self):
        for t in v.VAGUE_TASKS:
            for args, stdin in t.cases:
                out, code = t.ref(args, stdin)
                with self.subTest(task=t.id, case=stdin[:30]):
                    ok, reason = t.verify(args, stdin, out, '', code)
                    self.assertTrue(ok, reason)

    def test_the_tasks_read_like_vague_user_requests(self):
        for t in v.VAGUE_TASKS:
            self.assertLessEqual(len(t.goal), 260, t.id)  # 1–3 句话 / one to three sentences
            self.assertIn('vague', t.probes)

    def test_ids_are_unique_and_registered_for_the_runner(self):
        from masa.bench.runner import BY_ID
        ids = [t.id for t in v.VAGUE_TASKS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(i in BY_ID for i in ids))


class ToleranceTests(unittest.TestCase):
    """留白的地方，任何合理的写法都接受。 Whatever the statement left open, any reasonable reading is accepted."""

    def test_expenses_accepts_other_formats_orders_and_rounding(self):
        csv = '交通,0\n娱乐,100.0\n餐饮,55.75\n合计,155.75\n'
        self.assertTrue(verdict('v_expenses', v.EXPENSES, csv))
        self.assertTrue(verdict('v_expenses', v.EXPENSES, 'Total spent: 155.75\n餐饮 -> 55.75 yuan\n交通 -> 0.00 yuan\n娱乐 -> 100 yuan\n'))
        self.assertTrue(verdict('v_expenses', '', '没有记录\n', code=0))  # 空输入怎么处理是留白 / empty input is left open

    def test_calc_accepts_integers_floats_and_any_error_wording(self):
        self.assertTrue(verdict('v_calc', v.CALC_CASES[0][0], '6\n121\n'))
        self.assertTrue(verdict('v_calc', v.CALC_CASES[0][0], '6.0\n121.0\n'))
        self.assertTrue(verdict('v_calc', v.CALC_CASES[0][0], '= 6\n= 121.000000\n'))
        self.assertTrue(verdict('v_calc', v.CALC_ERRORS[0], '', err='error: division by zero', code=1))
        self.assertTrue(verdict('v_calc', v.CALC_ERRORS[0], '错误：除零\n5\n', code=0))
        self.assertTrue(verdict('v_calc', v.CALC_ERRORS[1], 'error: y is not defined\n', code=0))

    def test_deporder_accepts_any_valid_order_and_layout(self):
        for out in ('core\nlib\nutil\napp\n', 'core, util, lib, app\n', '执行顺序: core -> lib -> util -> app\n'):
            self.assertTrue(verdict('v_deporder', v.DEP_CASES[0], out), out)
        self.assertTrue(verdict('v_deporder', v.DEP_CASES[2], '', err='cycle detected', code=1))
        self.assertTrue(verdict('v_deporder', v.DEP_CASES[2], '发现循环依赖: a b\n', code=0))

    def test_markdown_accepts_b_or_strong_and_attributes(self):
        out = '<h1 id="t">标题</h1>\n<p>你好 <b>世界</b></p>\n<ul>\n  <li>苹果</li>\n  <li>香蕉</li>\n</ul>\n'
        self.assertTrue(verdict('v_markdown', v.MD_A, out))

    def test_shop_accepts_any_report_layout(self):
        self.assertTrue(verdict('v_shop', v.SHOP_CASES[0][0], 'ok\nok\nok\n库存不足，订单 o2 被拒绝\n库存报告\n  apple × 7\n  pear × 5\n'))
        self.assertTrue(verdict('v_shop', v.SHOP_CASES[0][0], '已进货 apple 10\n已进货 pear 5\n订单 o1 成功\n订单 o2 失败\n商品 apple 数量 7\n商品 pear 数量 5\n'))


class RejectionTests(unittest.TestCase):
    """错的输出必须被拒绝，否则尺子本身就会制造“假通过”。 Wrong outputs must be rejected or the ruler itself manufactures false passes."""

    def test_expenses(self):
        self.assertFalse(verdict('v_expenses', v.EXPENSES, '餐饮: 55.75\n交通: 12\n娱乐: 100\n总计: 167.75\n'))  # 退款没有扣 / the refund was not subtracted
        self.assertFalse(verdict('v_expenses', v.EXPENSES, '餐饮: 55.75\n娱乐: 100\n总计: 155.75\n'))              # 少了一个类别 / a category is missing
        self.assertFalse(verdict('v_expenses', v.EXPENSES, '餐饮: 55.75\n交通: 0\n娱乐: 100\n'))                   # 没有总计 / no grand total
        self.assertFalse(verdict('v_expenses', v.EXPENSES, '', err='Traceback (most recent call last):\n', code=1))

    def test_loglevels(self):
        right = 'INFO 3\nWARN 1\nERROR 5\n最多的错误: 支付超时 (3次)\n'
        self.assertTrue(verdict('v_loglevels', v.LOG_A, right))
        self.assertFalse(verdict('v_loglevels', v.LOG_A, right.replace('ERROR 5', 'ERROR 4')))
        self.assertFalse(verdict('v_loglevels', v.LOG_A, 'INFO 3\nWARN 1\nERROR 5\n最多的错误: 库存不足 (3次)\n'))
        self.assertFalse(verdict('v_loglevels', v.LOG_A, 'INFO 3\nWARN 1\nERROR 5\n'))

    def test_calc(self):
        self.assertFalse(verdict('v_calc', v.CALC_CASES[0][0], '6\n120\n'))
        self.assertFalse(verdict('v_calc', v.CALC_CASES[0][0], '6\n'))
        self.assertFalse(verdict('v_calc', v.CALC_CASES[2][0], '5\n'))                         # 优先级错 / wrong precedence
        self.assertFalse(verdict('v_calc', v.CALC_ERRORS[0], 'inf\n5\n', code=0))             # 除零打印成了值 / division by zero printed as a value
        self.assertFalse(verdict('v_calc', v.CALC_ERRORS[1], '1\n', code=0))                  # 未定义变量静默当 0 / an undefined variable silently treated as 0

    def test_deporder(self):
        self.assertFalse(verdict('v_deporder', v.DEP_CASES[0], 'app\nlib\nutil\ncore\n'))     # 依赖在后 / dependencies after the task
        self.assertFalse(verdict('v_deporder', v.DEP_CASES[0], 'core\nlib\napp\n'))           # 少了任务 / a task is missing
        self.assertFalse(verdict('v_deporder', v.DEP_CASES[2], 'a\nb\n', code=0))             # 环被当成正常输出 / a cycle passed as a normal order

    def test_markdown(self):
        self.assertFalse(verdict('v_markdown', v.MD_A, '<h1>标题</h1>\n<p>你好 **世界**</p>\n<ul><li>苹果</li><li>香蕉</li></ul>\n'))  # 粗体没转换 / bold not converted
        self.assertFalse(verdict('v_markdown', v.MD_A, '<h2>标题</h2>\n<p>你好 <strong>世界</strong></p>\n<ul><li>苹果</li><li>香蕉</li></ul>\n'))  # 标题级别错 / wrong heading level
        self.assertFalse(verdict('v_markdown', v.MD_A, '<h1>标题</h1>\n<p>你好 <strong>世界</strong></p>\n<p>- 苹果 - 香蕉</p>\n'))  # 列表没转换 / list not converted

    def test_shop(self):
        self.assertFalse(verdict('v_shop', v.SHOP_CASES[0][0], 'ok\nok\nok\nrejected\napple: 7\npear: 5\n'.replace('apple: 7', 'apple: 10')))  # 下单没扣库存 / the order did not reduce stock
        self.assertFalse(verdict('v_shop', v.SHOP_CASES[0][0], 'ok\nok\nok\nrejected\napple: 7\npear: -1\n'))  # 被拒绝的订单也扣了库存 / the rejected order still reduced stock
        self.assertFalse(verdict('v_shop', v.SHOP_CASES[1][0], 'ok\nok\napple: 3\nok\napple: 3\n'))           # 取消没有退回库存 / cancel did not return stock
        self.assertFalse(verdict('v_shop', v.SHOP_CASES[2][0], 'ok\nok\nok\nok\nok\napple: -1\npear: 4\n'))  # 负数进货被接受 / a negative stock was accepted

    def test_semver(self):
        self.assertTrue(verdict('v_semver', v.SEMVER_A, '0.9.9\n1.2.0\n1.2.9\n1.2.10\n1.10.0\n'))
        self.assertFalse(verdict('v_semver', v.SEMVER_A, '0.9.9\n1.10.0\n1.2.0\n1.2.10\n1.2.9\n'))             # 字典序 / lexicographic
        self.assertFalse(verdict('v_semver', v.SEMVER_B, '0.9.0\n1.0.0\n1.0.0-alpha\n1.0.0-beta\n1.0.1\n2.0.0-rc\n'))  # 预发布排在正式版后面 / prerelease after the release


if __name__ == '__main__':
    unittest.main()
