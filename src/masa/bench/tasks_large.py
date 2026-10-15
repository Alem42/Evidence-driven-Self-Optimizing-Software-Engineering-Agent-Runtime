"""大项目任务（约 10 个文件、多包）：用来观察规模变大以后系统哪里先出问题。不在默认套餐里（等级 10，单独的列表），按任务 id 才能运行。
Large-project tasks (about 10 files, several packages): to see what breaks first as scale grows. Not part of the default suites (level 10, a separate list); run them by task id.

参考实现与其它任务一样，由 Python 算出期望，并在 tests/test_bench_large.py 里用手算的答案钉住。三个任务各有一类“跨包要一致”的规则：
bank 的错误优先级、inventory 的数字检查先于存在性检查、grades 的加权平均与“正好在中间向上进位”。
The reference is Python like the other tasks, pinned by hand-computed answers in tests/test_bench_large.py. Each of the three tasks has a rule several packages must agree on:
bank's error precedence, inventory's number-before-existence checks, grades' weighted average with round-half-up.
"""
import re

from masa.bench.tasks import Task

_DIGITS = re.compile(r'^[0-9]+$')


def _amount(text, allow_zero=False):
    if not _DIGITS.match(text):
        return None
    value = int(text)
    return None if value == 0 and not allow_zero else value


# ───────────────────────── bank ─────────────────────────
def ref_bank(args, stdin):
    accounts, out = {}, []
    for line in stdin.splitlines():
        parts = line.split()
        if not parts:
            continue
        cmd = parts[0]
        if cmd == 'open' and len(parts) == 3:
            value = _amount(parts[2], True)
            if value is None:
                out.append('error: bad amount')
            elif parts[1] in accounts:
                out.append('error: exists')
            else:
                accounts[parts[1]] = value
                out.append('ok')
        elif cmd in ('deposit', 'withdraw') and len(parts) == 3:
            value = _amount(parts[2])
            if value is None:
                out.append('error: bad amount')
            elif parts[1] not in accounts:
                out.append('error: unknown account')
            elif cmd == 'withdraw' and accounts[parts[1]] < value:
                out.append('error: insufficient')
            else:
                accounts[parts[1]] += value if cmd == 'deposit' else -value
                out.append('ok')
        elif cmd == 'transfer' and len(parts) == 4:
            value = _amount(parts[3])
            if value is None:
                out.append('error: bad amount')
            elif parts[1] == parts[2]:
                out.append('error: same account')
            elif parts[1] not in accounts or parts[2] not in accounts:
                out.append('error: unknown account')
            elif accounts[parts[1]] < value:
                out.append('error: insufficient')
            else:
                accounts[parts[1]] -= value
                accounts[parts[2]] += value
                out.append('ok')
        elif cmd == 'balance' and len(parts) == 2:
            out.append(f'{parts[1]} {accounts[parts[1]]}' if parts[1] in accounts else 'error: unknown account')
        elif cmd == 'report' and len(parts) == 1:
            out += [f'{name} {accounts[name]}' for name in sorted(accounts)]
            out.append(f'total {sum(accounts.values())}')
        else:
            out.append('error: bad command')
    return ''.join(line + '\n' for line in out), 0


BANK_GOAL = ('写一个银行账本命令行程序，按行从标准输入读取命令并把结果写到标准输出，退出码恒为 0，空行忽略。'
             '代码要拆成几个独立的包，每个包各自带单元测试，main 只负责把它们接起来：命令解析、账户存储、转账规则、报表格式化各一个包。'
             '命令（名字是不含空白的词，金额是不带正负号的十进制整数）：'
             'open <名字> <初始金额> 开户，初始金额可以是 0，成功输出 ok，名字已存在输出 error: exists；'
             'deposit <名字> <金额> 存入、withdraw <名字> <金额> 取出，金额必须大于 0，成功输出 ok，账户不存在输出 error: unknown account，取出超过余额输出 error: insufficient；'
             'transfer <从> <到> <金额> 转账，金额必须大于 0，成功输出 ok；从与到相同输出 error: same account，任一账户不存在输出 error: unknown account，余额不足输出 error: insufficient；'
             'balance <名字> 输出 “<名字> <余额>”，不存在输出 error: unknown account；'
             'report 按名字的字典序每个账户一行 “<名字> <余额>”，最后一行 “total <所有余额之和>”（没有账户时只有 total 0）；'
             '金额不合法（不是纯数字，或在要求大于 0 的命令里为 0）一律输出 error: bad amount，且金额的检查先于账户是否存在、是否重名、是否同一账户的检查；'
             '命令名未知或参数个数不对输出 error: bad command。')


# ───────────────────────── inventory ─────────────────────────
def ref_inventory(args, stdin):
    stock, out = {}, []
    for line in stdin.splitlines():
        parts = line.split()
        if not parts:
            continue
        cmd = parts[0]
        if cmd == 'add' and len(parts) == 4:
            qty, price = _amount(parts[2]), _amount(parts[3])
            if qty is None or price is None:
                out.append('error: bad number')
            elif parts[1] in stock and stock[parts[1]][1] != price:
                out.append('error: price mismatch')
            else:
                old = stock.get(parts[1], (0, price))
                stock[parts[1]] = (old[0] + qty, price)
                out.append('ok')
        elif cmd == 'remove' and len(parts) == 3:
            qty = _amount(parts[2])
            if qty is None:
                out.append('error: bad number')
            elif parts[1] not in stock:
                out.append('error: unknown sku')
            elif stock[parts[1]][0] < qty:
                out.append('error: insufficient')
            else:
                stock[parts[1]] = (stock[parts[1]][0] - qty, stock[parts[1]][1])
                out.append('ok')
        elif cmd == 'price' and len(parts) == 3:
            price = _amount(parts[2])
            if price is None:
                out.append('error: bad number')
            elif parts[1] not in stock:
                out.append('error: unknown sku')
            else:
                stock[parts[1]] = (stock[parts[1]][0], price)
                out.append('ok')
        elif cmd == 'stock' and len(parts) == 2:
            out.append(f'{parts[1]} {stock[parts[1]][0]} {stock[parts[1]][1]}' if parts[1] in stock else 'error: unknown sku')
        elif cmd == 'value' and len(parts) == 1:
            out.append(f'value {sum(q * p for q, p in stock.values())}')
        elif cmd == 'low' and len(parts) == 2:
            threshold = _amount(parts[1], True)
            if threshold is None:
                out.append('error: bad number')
            else:
                names = sorted(name for name, (q, _) in stock.items() if q < threshold)
                out += names or ['none']
        else:
            out.append('error: bad command')
    return ''.join(line + '\n' for line in out), 0


INVENTORY_GOAL = ('写一个库存管理命令行程序，按行从标准输入读取命令并把结果写到标准输出，退出码恒为 0，空行忽略。'
                  '代码要拆成几个独立的包，每个包各自带单元测试，main 只负责把它们接起来：命令解析、库存存储、报表各一个包。'
                  '命令（sku 是不含空白的词，数量、价格是不带正负号的十进制整数，价格单位是分）：'
                  'add <sku> <数量> <价格> 入库，数量和价格都必须大于 0；sku 已存在但价格不同输出 error: price mismatch，否则数量累加，成功输出 ok；'
                  'remove <sku> <数量> 出库，数量必须大于 0，sku 不存在输出 error: unknown sku，库存不足输出 error: insufficient，成功输出 ok；'
                  'price <sku> <价格> 改价，价格必须大于 0，sku 不存在输出 error: unknown sku，成功输出 ok；'
                  'stock <sku> 输出 “<sku> <数量> <价格>”，不存在输出 error: unknown sku；'
                  'value 输出 “value <所有 sku 的 数量×价格 之和>”；'
                  'low <阈值> 阈值可以是 0，按 sku 字典序每行输出一个数量严格小于阈值的 sku，一个都没有则输出 none；'
                  '数字不合法（不是纯数字，或在要求大于 0 的位置为 0）一律输出 error: bad number，且数字的检查先于 sku 是否存在、价格是否一致的检查；'
                  '命令名未知或参数个数不对输出 error: bad command。')


# ───────────────────────── grades ─────────────────────────
def ref_grades(args, stdin):
    from decimal import ROUND_HALF_UP, Decimal
    students, credits, grades, out = set(), {}, {}, []

    def gpa(name):
        pairs = [(score, credits[course]) for (who, course), score in grades.items() if who == name and course in credits]
        total = sum(c for _, c in pairs)
        if not total:
            return None
        return (Decimal(sum(s * c for s, c in pairs)) / Decimal(total)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    for line in stdin.splitlines():
        parts = line.split()
        if not parts:
            continue
        cmd = parts[0]
        if cmd == 'student' and len(parts) == 2:
            if parts[1] in students:
                out.append('error: exists')
            else:
                students.add(parts[1])
                out.append('ok')
        elif cmd == 'credits' and len(parts) == 3:
            value = _amount(parts[2])
            if value is None:
                out.append('error: bad number')
            else:
                credits[parts[1]] = value
                out.append('ok')
        elif cmd == 'grade' and len(parts) == 4:
            score = _amount(parts[3], True)
            if score is None or score > 100:
                out.append('error: bad number')
            elif parts[1] not in students:
                out.append('error: unknown student')
            else:
                grades[(parts[1], parts[2])] = score
                out.append('ok')
        elif cmd == 'gpa' and len(parts) == 2:
            if parts[1] not in students:
                out.append('error: unknown student')
            else:
                value = gpa(parts[1])
                out.append(f'{parts[1]} {value}' if value is not None else f'{parts[1]} n/a')
        elif cmd == 'top' and len(parts) == 1:
            ranked = sorted(((gpa(name), name) for name in students if gpa(name) is not None), key=lambda item: (-item[0], item[1]))
            out.append(f'{ranked[0][1]} {ranked[0][0]}' if ranked else 'none')
        else:
            out.append('error: bad command')
    return ''.join(line + '\n' for line in out), 0


GRADES_GOAL = ('写一个成绩管理命令行程序，按行从标准输入读取命令并把结果写到标准输出，退出码恒为 0，空行忽略。'
               '代码要拆成几个独立的包，每个包各自带单元测试，main 只负责把它们接起来：命令解析、成绩存储、绩点计算各一个包。'
               '命令（名字和课程名是不含空白的词，学分和分数是不带正负号的十进制整数）：'
               'student <名字> 登记学生，成功输出 ok，已存在输出 error: exists；'
               'credits <课程> <学分> 设置（或改写）课程学分，学分必须大于 0，成功输出 ok；'
               'grade <名字> <课程> <分数> 记录（或覆盖）成绩，分数范围 0 到 100（含），学生不存在输出 error: unknown student，成功输出 ok；课程的学分可以之后才设置；'
               'gpa <名字> 输出 “<名字> <绩点>”，绩点 = 只统计已设置学分的课程，按学分加权的平均分，四舍五入到两位小数（正好在中间时向上进位，例如 91.125 输出 91.13），'
               '没有可统计的课程时输出 “<名字> n/a”，学生不存在输出 error: unknown student；'
               'top 输出绩点最高的学生 “<名字> <绩点>”，并列时取名字字典序最小的，没有任何学生有绩点时输出 none；'
               '数字不合法（不是纯数字、分数大于 100、学分为 0）一律输出 error: bad number，且数字的检查先于学生是否存在的检查；'
               '命令名未知或参数个数不对输出 error: bad command。')

LARGE_TASKS = (
    Task('bank', 10, '多包银行账本', BANK_GOAL, (
        ((), 'open alice 100\nopen bob 50\ndeposit alice 25\nwithdraw bob 20\ntransfer alice bob 60\nbalance alice\nbalance bob\nreport\n'),
        ((), 'open alice 10\nopen alice 5\ndeposit carol 5\nwithdraw alice 99\ndeposit alice 0\ndeposit alice -3\ndeposit alice abc\nfoo\n\nbalance\nbalance zed\n'),
        ((), 'open a 5\nopen b 0\ntransfer a a 1\ntransfer a c 1\ntransfer c a 1\ntransfer a b 6\ntransfer a b 5\nreport\n'),
        ((), 'open zed 1\nopen amy 2\nopen bob 3\nreport\nopen x 007\nbalance x\nreport extra\n'),
        ((), ''),
        ((), 'open big 1000000000000\ndeposit big 1000000000000\nopen s 1\nreport\nwithdraw big 2000000000000\nwithdraw big 1\nbalance big\n'),
    ), ref_bank, probes=('context', 'multi-package')),
    Task('inventory', 10, '多包库存管理', INVENTORY_GOAL, (
        ((), 'add apple 10 150\nadd pear 5 200\nadd apple 5 150\nstock apple\nvalue\nlow 8\nremove apple 3\nstock apple\n'),
        ((), 'add a 1 1\nadd a 2 3\nadd b 0 5\nadd b 5 x\nremove ghost 1\nremove a 2\nprice ghost 4\nprice a 0\nstock ghost\nfoo\nlow\nlow z\n'),
        ((), 'low 3\nvalue\nadd z 2 7\nadd y 2 9\nlow 3\nlow 2\nlow 0\n'),
        ((), 'add k 1000000000 1000000000\nvalue\nprice k 1\nvalue\nremove k 1000000000\nstock k\nlow 1\n'),
        ((), ''),
    ), ref_inventory, probes=('context', 'multi-package')),
    Task('grades', 10, '多包成绩管理', GRADES_GOAL, (
        ((), 'student amy\nstudent bob\ncredits math 5\ncredits art 3\ngrade amy math 90\ngrade amy art 93\ngpa amy\ngrade bob math 90\ngrade bob art 91\ngpa bob\ntop\n'),
        ((), 'student amy\nstudent amy\ngrade ghost math 50\ngrade amy math 101\ngrade amy math -1\ncredits math 0\ncredits math x\ngrade amy math 80\ngpa amy\ngpa ghost\ngpa\ntop\n'),
        ((), 'top\nstudent zed\nstudent ann\ncredits c 2\ngrade zed c 70\ngrade ann c 70\ntop\ngrade ann c 71\ntop\ncredits c 4\ngpa ann\n'),
        ((), 'student a\ngrade a x 100\ngpa a\ncredits x 1\ngpa a\ngrade a x 0\ngpa a\n'),
        ((), ''),
    ), ref_grades, probes=('context', 'multi-package', 'rounding')),
)
