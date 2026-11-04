"""R1 的工作负载：一个命令行库存程序，先从零生成，再在已验证的项目上逐个加 12 个功能（工作项）。
R1's workload: a command-line stock program, generated from scratch, then extended by 12 features one at a time on the verified project (work items).

为什么是这样的负载 / Why this workload (ROUTE_REVIEW §5.1 R1):
- 系统以前只能表达“一次规划、一次生成”的小项目；需要几小时的工作（在已有代码上连续改）根本无法输入。这里每一步都是真实的“改已有代码”，功能之间相互作用（撤销 × 事务 × 订单 × 多仓库）。
  The system could only express one-plan, one-generation small projects; hours of work (changing existing code again and again) could not even be entered. Every step here really changes existing code and the features interact
  (undo x transactions x orders x warehouses).
- 功能请求把输出格式写死（不是模糊任务）：失败反映的是引擎（规模、上下文、修复、回归），不是“读法不同”。
  The requests pin the output formats (this is NOT a vague task): a failure reflects the engine (scale, context, repair, regressions), not a different reading.
- 独立判官：参考实现 `run(level, args, stdin)` 按“做到第几个功能”给出期望；每一步对已有的全部功能的场景重判，所以回归（新功能把旧功能改坏）也看得见。
  The independent oracle: the reference `run(level, args, stdin)` gives the expectation for "features up to this level"; every step re-judges the scenarios of ALL features so far, so regressions show up too.
"""
import copy
import re
from decimal import Decimal

from masa.bench.tasks import Task

BASE_GOAL = (
    '写一个命令行库存程序：从标准输入逐行读取命令并依次执行，直到输入结束。每行用空白分隔成若干字段，第一个字段是命令名（大写）。空行忽略。\n'
    'sku 和其它名字只由字母和数字组成，区分大小写；数量是正整数（只由数字组成且大于 0）。\n'
    '命令：\n'
    '1. `ADD <sku> <数量>`：增加该 sku 的库存，不输出任何内容。\n'
    '2. `REMOVE <sku> <数量>`：减少库存，不输出；如果库存不够（包括从未出现过的 sku），这是一个错误，命令不生效。\n'
    '3. `STOCK <sku>`：输出一行 `<sku>: <数量>`；从未出现过的 sku 输出数量 0。\n'
    '4. `REPORT`：按 sku 的字符编码顺序（区分大小写，例如 A 在 B 前、B 在 a 前）每行输出一个数量大于 0 的 sku：`<sku>: <数量>`；如果没有任何数量大于 0 的 sku，输出一行 `(empty)`。\n'
    '错误处理：无法识别的命令、参数个数不对、参数格式不对、库存不够，都是错误：向标准错误输出一行说明，该命令不生效，然后继续执行后面的行（错误不在标准输出上输出任何东西）。\n'
    '全部输入处理完后，如果出现过任何错误，进程退出码为 1，否则为 0。')

CHANGES = (
    '增加两个命令：`PRICE <sku> <单价>` 设置（或覆盖）该 sku 的单价，不输出；单价是非负数，最多两位小数（例如 3、3.5、0.25），其它写法（负数、三位小数、`1.`、非数字）是错误。'
    '`VALUE` 输出一行 `total value: <金额>`，金额 = 所有设置过单价的 sku 的 当前库存×单价 之和，固定保留两位小数（例如 `total value: 0.00`）。',
    '修改 REPORT 的输出格式：每行改为 `<sku>: <数量> @ <单价>`，单价固定两位小数；没有设置过单价的 sku 显示 `<sku>: <数量> @ -`。其它规则（哪些 sku 出现、顺序、`(empty)`）不变。',
    '增加订单。`ORDER <订单号> <sku> <数量>`：创建一个订单并立即从库存中扣减数量，不输出；订单号由字母和数字组成；订单号已存在或库存不够都是错误（订单不创建，库存不变）。'
    '`ORDERS`：按订单号的字符编码顺序每行输出一个订单：`<订单号> <sku> <数量> <状态>`，新订单的状态是 `open`；没有任何订单时输出一行 `(no orders)`。',
    '增加订单状态变更。`CANCEL <订单号>`：取消一个状态为 open 的订单，把它的数量加回库存，状态变为 `cancelled`；`SHIP <订单号>`：把一个状态为 open 的订单的状态变为 `shipped`（库存不变）。'
    '两个命令都不输出；订单不存在或状态不是 open 都是错误。',
    '增加 `LOW <阈值>` 命令（阈值是非负整数，0 也合法）：按 sku 的字符编码顺序，每行 `<sku>: <数量>` 列出所有“出现过”且当前库存数量小于阈值的 sku；没有这样的 sku 时输出一行 `(none)`。'
    '“出现过”指曾经被 ADD 成功增加过、或被 PRICE 成功设置过单价的 sku（数量已经减到 0 的也算）。',
    '增加 `UNDO` 命令：撤销最近一条成功生效的修改状态的命令（ADD、REMOVE、PRICE、ORDER、CANCEL、SHIP），把全部状态（库存、单价、订单、哪些 sku 出现过）恢复到那条命令执行之前；'
    '可以连续 UNDO 多次，每次再往前撤销一条。失败的命令和只读命令（STOCK、REPORT、VALUE、ORDERS、LOW）不算。没有可以撤销的命令时 UNDO 是错误。UNDO 不输出。',
    '增加命令行参数 `--strict`：遇到第一个错误时，向标准错误输出一行说明后立即停止处理（后面的行都不执行），进程退出码为 2。不带参数时行为不变。'
    '除 `--strict` 以外的任何命令行参数都是错误：向标准错误输出一行说明，不读取输入，退出码 2。',
    '命令名不再区分大小写（`add`、`Add` 与 `ADD` 相同；sku 和订单号仍然区分大小写）。去掉首尾空白后以 `#` 开头的行是注释，直接忽略。',
    '增加事务：`BEGIN` 开始一个事务；之后的修改命令照常立即生效（事务内的查询能看到它们）；`COMMIT` 结束事务并保留修改；`ROLLBACK` 结束事务并把全部状态恢复到 BEGIN 时的样子。'
    '规则：事务不能嵌套（事务中再 BEGIN 是错误）；没有进行中的事务时 COMMIT 或 ROLLBACK 是错误；事务中的 UNDO 是错误；提交后整个事务在撤销历史里只算一条命令，之后一次 UNDO 撤销整个事务的全部修改'
    '（没有任何修改的事务提交后不进入撤销历史）；被 ROLLBACK 的事务不进入撤销历史。输入结束时如果事务还没结束，自动回滚，并向标准错误输出一行警告——这个警告不算错误，不影响退出码。这三个命令都不输出。',
    '增加 `STATS` 命令，输出三行：`skus: <库存数量大于 0 的 sku 个数>`、`units: <所有 sku 的库存数量之和>`、`orders: open=<个数> shipped=<个数> cancelled=<个数>`。',
    '增加 `EXPORT` 命令：以 CSV 格式输出库存：第一行是 `sku,qty,price`，之后按 sku 的字符编码顺序，每个出现过的 sku（含义同 LOW）一行 `<sku>,<数量>,<单价>`，单价固定两位小数，没有设置过单价时这一列为空（例如 `b,4,`）。',
    '增加多仓库。ADD 和 REMOVE 可以带第三个参数 `@<仓库名>`（仓库名由字母和数字组成），不带时是默认仓库 `main`；库存不够指的是该仓库里不够。'
    '新命令 `MOVE <sku> <数量> @<源仓库> @<目标仓库>` 把库存从一个仓库移到另一个（源仓库不够、或源和目标相同都是错误），不输出；MOVE 也是可撤销的修改命令。'
    '`STOCK <sku>` 仍输出所有仓库的合计；新写法 `STOCK <sku> @<仓库名>` 输出一行 `<sku>@<仓库名>: <数量>`。REPORT、VALUE、LOW、STATS、EXPORT 都按所有仓库的合计计算。'
    '订单只从 main 仓库扣减（main 里不够就是错误），CANCEL 把数量加回 main。',
)

NAME = re.compile(r'[A-Za-z0-9]+')
COUNT = re.compile(r'[0-9]+')
MONEY = re.compile(r'[0-9]+(\.[0-9]{1,2})?')


class _Error(Exception):
    pass


class _Stop(Exception):
    pass


class Reference:
    """参考实现：level = 已经做到第几个功能（0 = 基础程序）。 The reference: level = how many features are in (0 = the base program)."""

    def __init__(self, level):
        self.level = level
        self.state = {'wh': {}, 'prices': {}, 'known': set(), 'orders': {}}
        self.history, self.tx, self.out = [], None, []

    # ── helpers ──
    def total(self, sku):
        return sum(w.get(sku, 0) for w in self.state['wh'].values())

    def held(self, warehouse, sku):
        return self.state['wh'].get(warehouse, {}).get(sku, 0)

    def put(self, warehouse, sku, delta):
        self.state['wh'].setdefault(warehouse, {})
        self.state['wh'][warehouse][sku] = self.state['wh'][warehouse].get(sku, 0) + delta

    def mutate(self):
        """一条修改命令马上要生效：不在事务里就把之前的状态压进撤销历史。 A mutating command is about to take effect: outside a transaction, push the previous state."""
        if self.tx is None:
            self.history.append(copy.deepcopy(self.state))

    @staticmethod
    def name(value):
        if not NAME.fullmatch(value):
            raise _Error()
        return value

    @staticmethod
    def count(value, positive=True):
        if not COUNT.fullmatch(value) or (positive and int(value) == 0):
            raise _Error()
        return int(value)

    def warehouse(self, token):
        if not token.startswith('@') or not NAME.fullmatch(token[1:]):
            raise _Error()
        return token[1:]

    @staticmethod
    def money(value):
        return f'{value:.2f}'

    # ── commands ──
    def run_line(self, parts):
        level = self.level
        command, args = parts[0], parts[1:]
        if level >= 8:
            command = command.upper()
        multi = level >= 12
        if command in ('ADD', 'REMOVE'):
            if not (len(args) == 2 or (multi and len(args) == 3)):
                raise _Error()
            sku, qty = self.name(args[0]), self.count(args[1])
            where = self.warehouse(args[2]) if len(args) == 3 else 'main'
            if command == 'REMOVE' and self.held(where, sku) < qty:
                raise _Error()
            self.mutate()
            self.put(where, sku, qty if command == 'ADD' else -qty)
            if command == 'ADD':
                self.state['known'].add(sku)
        elif command == 'STOCK':
            if not (len(args) == 1 or (multi and len(args) == 2)):
                raise _Error()
            sku = self.name(args[0])
            if len(args) == 2:
                where = self.warehouse(args[1])
                self.out.append(f'{sku}@{where}: {self.held(where, sku)}')
            else:
                self.out.append(f'{sku}: {self.total(sku)}')
        elif command == 'REPORT':
            if args:
                raise _Error()
            skus = sorted(s for s in {s for w in self.state['wh'].values() for s in w} if self.total(s) > 0)
            if not skus:
                self.out.append('(empty)')
            for sku in skus:
                if level >= 2:
                    price = self.state['prices'].get(sku)
                    self.out.append(f'{sku}: {self.total(sku)} @ ' + (self.money(price) if price is not None else '-'))
                else:
                    self.out.append(f'{sku}: {self.total(sku)}')
        elif command == 'PRICE' and level >= 1:
            if len(args) != 2 or not MONEY.fullmatch(args[1]):
                raise _Error()
            sku = self.name(args[0])
            self.mutate()
            self.state['prices'][sku] = Decimal(args[1])
            self.state['known'].add(sku)
        elif command == 'VALUE' and level >= 1:
            if args:
                raise _Error()
            value = sum((self.total(s) * p for s, p in self.state['prices'].items()), Decimal(0))
            self.out.append('total value: ' + self.money(value))
        elif command == 'ORDER' and level >= 3:
            if len(args) != 3:
                raise _Error()
            oid, sku, qty = self.name(args[0]), self.name(args[1]), self.count(args[2])
            if oid in self.state['orders'] or self.held('main', sku) < qty:
                raise _Error()
            self.mutate()
            self.put('main', sku, -qty)
            self.state['orders'][oid] = [sku, qty, 'open']
        elif command == 'ORDERS' and level >= 3:
            if args:
                raise _Error()
            if not self.state['orders']:
                self.out.append('(no orders)')
            for oid in sorted(self.state['orders']):
                sku, qty, status = self.state['orders'][oid]
                self.out.append(f'{oid} {sku} {qty} {status}')
        elif command in ('CANCEL', 'SHIP') and level >= 4:
            if len(args) != 1:
                raise _Error()
            order = self.state['orders'].get(self.name(args[0]))
            if order is None or order[2] != 'open':
                raise _Error()
            self.mutate()
            order = self.state['orders'][args[0]]
            if command == 'CANCEL':
                self.put('main', order[0], order[1])
                order[2] = 'cancelled'
            else:
                order[2] = 'shipped'
        elif command == 'LOW' and level >= 5:
            if len(args) != 1:
                raise _Error()
            limit = self.count(args[0], positive=False)
            rows = [f'{s}: {self.total(s)}' for s in sorted(self.state['known']) if self.total(s) < limit]
            self.out.extend(rows or ['(none)'])
        elif command == 'UNDO' and level >= 6:
            if args or self.tx is not None or not self.history:
                raise _Error()
            self.state = self.history.pop()
        elif command == 'BEGIN' and level >= 9:
            if args or self.tx is not None:
                raise _Error()
            self.tx = copy.deepcopy(self.state)
        elif command in ('COMMIT', 'ROLLBACK') and level >= 9:
            if args or self.tx is None:
                raise _Error()
            if command == 'ROLLBACK':
                self.state = self.tx
            elif self.state != self.tx:
                self.history.append(self.tx)
            self.tx = None
        elif command == 'STATS' and level >= 10:
            if args:
                raise _Error()
            skus = {s for w in self.state['wh'].values() for s in w}
            statuses = [o[2] for o in self.state['orders'].values()]
            self.out += [f'skus: {sum(1 for s in skus if self.total(s) > 0)}', f'units: {sum(self.total(s) for s in skus)}',
                         f"orders: open={statuses.count('open')} shipped={statuses.count('shipped')} cancelled={statuses.count('cancelled')}"]
        elif command == 'EXPORT' and level >= 11:
            if args:
                raise _Error()
            self.out.append('sku,qty,price')
            for sku in sorted(self.state['known']):
                price = self.state['prices'].get(sku)
                self.out.append(f'{sku},{self.total(sku)},' + (self.money(price) if price is not None else ''))
        elif command == 'MOVE' and level >= 12:
            if len(args) != 4:
                raise _Error()
            sku, qty, source, target = self.name(args[0]), self.count(args[1]), self.warehouse(args[2]), self.warehouse(args[3])
            if source == target or self.held(source, sku) < qty:
                raise _Error()
            self.mutate()
            self.put(source, sku, -qty)
            self.put(target, sku, qty)
        else:
            raise _Error()


def run(level, args, stdin):
    """参考实现的入口：返回 (标准输出, 退出码)。 The reference entry: returns (stdout, exit code)."""
    ref = Reference(level)
    strict = False
    if level >= 7:
        for arg in args:
            if arg != '--strict':
                return '', 2
            strict = True
    errors = 0
    try:
        # 与 Python 文本模式的标准输入一致：CRLF 与单独的 CR 都是换行（R3 的一个对抗输入用了单独的 CR，参考实现以前把它当成同一行，造成一次假的“假通过”）。
        # Like Python's text-mode stdin: CRLF and a lone CR both end a line (an R3 adversarial input used a lone CR and the reference used to treat it as one line, producing a spurious "false pass").
        for raw in stdin.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
            line = raw.strip()
            if not line or (level >= 8 and line.startswith('#')):
                continue
            try:
                ref.run_line(line.split())
            except _Error:
                errors += 1
                if strict:
                    raise _Stop() from None
    except _Stop:
        return '\n'.join(ref.out) + ('\n' if ref.out else ''), 2
    return '\n'.join(ref.out) + ('\n' if ref.out else ''), 1 if errors else 0


def _s(*lines):
    return '\n'.join(lines) + '\n'


# 每个功能的场景（只有输入；期望由参考实现按当前等级给出）。 Scenarios per feature (inputs only; the reference gives the expectation for the current level).
SCENARIOS = ((
    ((), _s('ADD a 3', 'ADD b 2', 'ADD a 1', 'REPORT', 'STOCK a', 'STOCK zz')),
    ((), _s('REPORT')),
    ((), _s('ADD a 2', 'REMOVE a 3', 'REMOVE a 2', 'REPORT', 'STOCK a')),
    ((), _s('FOO', 'ADD a x', 'ADD a 0', 'ADD a', 'ADD b 5', 'REPORT')),
    ((), '\n\nADD B 1\nADD a 1\nADD A 2\n  REPORT  \n'),
    ((), _s('REMOVE q 1')),
), (
    ((), _s('ADD a 2', 'PRICE a 1.5', 'VALUE')),
    ((), _s('VALUE')),
    ((), _s('PRICE a 2', 'ADD a 3', 'ADD b 4', 'PRICE b 0.25', 'VALUE', 'PRICE a 1', 'VALUE')),
    ((), _s('PRICE a -1', 'PRICE a 1.234', 'PRICE a abc', 'PRICE a 1.', 'VALUE')),
    ((), _s('ADD a 1', 'PRICE a 3.10', 'REMOVE a 1', 'VALUE')),
), (
    ((), _s('ADD a 2', 'PRICE a 1.5', 'ADD b 1', 'REPORT')),
    ((), _s('PRICE z 3', 'REPORT')),
    ((), _s('ADD a 1', 'PRICE a 10', 'REPORT')),
), (
    ((), _s('ADD a 5', 'ORDER o1 a 2', 'ORDERS', 'STOCK a')),
    ((), _s('ORDERS')),
    ((), _s('ADD a 1', 'ORDER o1 a 2', 'ORDER o2 a 1', 'ORDER o2 a 1', 'ORDERS', 'STOCK a')),
    ((), _s('ADD b 3', 'ADD a 3', 'ORDER z9 a 1', 'ORDER A1 b 1', 'ORDER m b 1', 'ORDERS')),
    ((), _s('ADD a 1', 'ORDER o1 a 1', 'REPORT')),
), (
    ((), _s('ADD a 5', 'ORDER o1 a 2', 'CANCEL o1', 'ORDERS', 'STOCK a')),
    ((), _s('ADD a 5', 'ORDER o1 a 2', 'SHIP o1', 'ORDERS', 'STOCK a')),
    ((), _s('ADD a 5', 'ORDER o1 a 2', 'SHIP o1', 'CANCEL o1', 'SHIP o1', 'CANCEL nope', 'ORDERS')),
    ((), _s('ADD a 2', 'ORDER o1 a 2', 'CANCEL o1', 'ORDER o2 a 2', 'ORDERS')),
), (
    ((), _s('ADD a 5', 'ADD b 1', 'PRICE c 2', 'LOW 3')),
    ((), _s('ADD a 5', 'LOW 5', 'LOW 6')),
    ((), _s('LOW 0', 'LOW x')),
    ((), _s('ADD a 2', 'REMOVE a 2', 'LOW 1')),
), (
    ((), _s('ADD a 5', 'REMOVE a 2', 'UNDO', 'STOCK a', 'UNDO', 'STOCK a', 'REPORT')),
    ((), _s('UNDO')),
    ((), _s('ADD a 1', 'PRICE a 2', 'PRICE a 3', 'UNDO', 'VALUE', 'UNDO', 'VALUE')),
    ((), _s('ADD a 3', 'ORDER o1 a 1', 'CANCEL o1', 'UNDO', 'ORDERS', 'STOCK a', 'UNDO', 'ORDERS', 'STOCK a')),
    ((), _s('ADD a 1', 'REMOVE a 5', 'REPORT', 'UNDO', 'REPORT')),
    ((), _s('ADD a 2', 'UNDO', 'LOW 1')),
), (
    (('--strict',), _s('ADD a 1', 'FOO', 'ADD b 1', 'REPORT')),
    (('--strict',), _s('ADD a 1', 'REPORT')),
    (('--verbose',), _s('ADD a 1', 'REPORT')),
    (('--strict',), _s('ADD a 2', 'STOCK a', 'REMOVE a 3', 'STOCK a')),
    ((), _s('FOO', 'STOCK a')),
), (
    ((), _s('add a 2', 'Add b 1', 'report')),
    ((), '# comment\n   # indented comment\nADD a 1\nstock a\nstock A\n'),
    ((), _s('ADD a 1', 'add A 2', 'REPORT')),
    ((), _s('Undo')),
), (
    ((), _s('ADD a 5', 'BEGIN', 'REMOVE a 2', 'STOCK a', 'ROLLBACK', 'STOCK a')),
    ((), _s('ADD a 5', 'BEGIN', 'REMOVE a 2', 'ADD b 1', 'COMMIT', 'REPORT', 'UNDO', 'REPORT')),
    ((), _s('BEGIN', 'BEGIN', 'COMMIT', 'COMMIT', 'ROLLBACK')),
    ((), _s('ADD a 1', 'BEGIN', 'ADD a 1', 'UNDO', 'STOCK a', 'COMMIT', 'STOCK a')),
    ((), _s('ADD a 1', 'BEGIN', 'ADD a 4')),
    ((), _s('ADD a 1', 'BEGIN', 'COMMIT', 'UNDO', 'STOCK a')),
    (('--strict',), _s('BEGIN', 'ADD a 1', 'FOO', 'STOCK a')),
    ((), _s('ADD a 2', 'BEGIN', 'ORDER o1 a 1', 'ROLLBACK', 'ORDERS', 'UNDO', 'STOCK a')),
), (
    ((), _s('STATS')),
    ((), _s('ADD a 2', 'ADD b 3', 'ADD c 1', 'REMOVE c 1', 'ORDER o1 a 1', 'ORDER o2 b 1', 'SHIP o2', 'ORDER o3 b 1', 'CANCEL o3', 'STATS')),
), (
    ((), _s('ADD b 4', 'ADD a 1', 'PRICE a 2.5', 'PRICE c 1', 'EXPORT')),
    ((), _s('EXPORT')),
    ((), _s('ADD a 1', 'REMOVE a 1', 'EXPORT')),
), (
    ((), _s('ADD a 5', 'ADD a 3 @east', 'STOCK a', 'STOCK a @east', 'STOCK a @main', 'STOCK a @west')),
    ((), _s('ADD a 2 @east', 'REMOVE a 1', 'REMOVE a 1 @east', 'STOCK a')),
    ((), _s('ADD a 5', 'MOVE a 2 @main @east', 'STOCK a @main', 'STOCK a @east', 'MOVE a 9 @main @east', 'MOVE a 1 @east @east', 'UNDO', 'STOCK a @east')),
    ((), _s('ADD a 1', 'ADD a 5 @east', 'ORDER o1 a 2', 'ORDER o2 a 1', 'CANCEL o2', 'STOCK a @main', 'REPORT')),
    ((), _s('ADD a 1 @x', 'ADD b 2 @y', 'STATS', 'LOW 2', 'EXPORT')),
    ((), _s('ADD a 1 east')),
),)


def task_at(level):
    """做到第 level 个功能时的判官任务：已有全部功能的场景，期望由参考实现给出。 The oracle task at a level: every scenario so far, expectations from the reference."""
    cases = tuple((list(args), stdin) for feature in SCENARIOS[:level + 1] for args, stdin in feature)
    return Task(f'stock@{level}', 10, f'stock workload level {level}', BASE_GOAL, cases, lambda args, stdin: run(level, args, stdin), ('workload',))


def feature_of_case(level):
    """判官用例下标 → 它属于第几个功能。 Oracle case index -> the feature it belongs to."""
    return [index for index, feature in enumerate(SCENARIOS[:level + 1]) for _ in feature]
