"""大项目任务（约 10 个文件、多包）：用来观察规模变大以后系统哪里先出问题。不在默认套餐里（等级 10，单独的列表），按任务 id 才能运行。
Large-project tasks (about 10 files, several packages): to see what breaks first as scale grows. Not part of the default suites (level 10, a separate list); run them by task id.

参考实现与其它任务一样，由 Python 算出期望，并在 tests/test_bench_large.py 里用手算的答案钉住。
The reference is Python like the other tasks, pinned by a hand-computed answer in tests/test_bench_large.py.
"""
import re

from masa.bench.tasks import Task

_DIGITS = re.compile(r'^[0-9]+$')


def _amount(text, allow_zero=False):
    if not _DIGITS.match(text):
        return None
    value = int(text)
    return None if value == 0 and not allow_zero else value


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

LARGE_TASKS = (
    Task('bank', 10, '多包银行账本', BANK_GOAL, (
        ((), 'open alice 100\nopen bob 50\ndeposit alice 25\nwithdraw bob 20\ntransfer alice bob 60\nbalance alice\nbalance bob\nreport\n'),
        ((), 'open alice 10\nopen alice 5\ndeposit carol 5\nwithdraw alice 99\ndeposit alice 0\ndeposit alice -3\ndeposit alice abc\nfoo\n\nbalance\nbalance zed\n'),
        ((), 'open a 5\nopen b 0\ntransfer a a 1\ntransfer a c 1\ntransfer c a 1\ntransfer a b 6\ntransfer a b 5\nreport\n'),
        ((), 'open zed 1\nopen amy 2\nopen bob 3\nreport\nopen x 007\nbalance x\nreport extra\n'),
        ((), ''),
        ((), 'open big 1000000000000\ndeposit big 1000000000000\nopen s 1\nreport\nwithdraw big 2000000000000\nwithdraw big 1\nbalance big\n'),
    ), ref_bank, probes=('context', 'multi-package')),
)
