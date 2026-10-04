"""评测任务库：10 个难度等级（L0–L9）的 Go 命令行任务，每个任务带“独立判官”的参考实现。
Benchmark task library: Go CLI tasks across 10 difficulty levels (L0-L9), each with a reference implementation used as an independent oracle.

设计原则 / Design:
- 题面把输入输出写得足够精确（评测要逐字比对），但保持用户平时会写的口吻，不暗示实现方法。
  Statements pin down I/O exactly (output is compared verbatim) yet read like something a user would type, with no implementation hints.
- 期望输出由 Python 参考实现 `ref(args, stdin) -> (stdout, exit_code)` 算出，而不是手写，避免评测自己写错。
  Expected output comes from a Python reference, never hand-typed, so the benchmark itself cannot be wrong.
- 判官独立于被测系统：系统自己的 Gate 通过 ≠ 评测通过；两者不一致就是“假通过”，这本身是被测的指标。
  The oracle is independent of the system under test: a Gate pass is not a benchmark pass; a disagreement is a "false pass" and is itself a metric.
- 难度从“冒烟”（L0）到“目前的模型多半做不出来”（L9），等级要细，才能看出 runtime 的改动有没有让上限抬高。
  Levels run from smoke (L0) to "current models probably cannot" (L9), fine-grained so runtime changes show up as movement of the ceiling.
"""
import heapq
import re
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Task:
    id: str
    level: int
    title: str
    goal: str
    cases: tuple  # ((args, stdin), ...)
    ref: object  # callable(args, stdin) -> (stdout, exit_code)
    probes: tuple = ()  # 这个任务特别容易触发哪些 runtime 机制 / runtime mechanisms this task tends to exercise

    @property
    def max_seconds(self):
        """单任务时间上限：等级越高给得越多。 Per-task wall limit, more for harder levels."""
        return 180 + 90 * self.level

    @property
    def max_cloud_tokens(self):
        """单任务云端 token 上限。 Per-task cloud token limit."""
        return 40_000 + 15_000 * self.level


def _lines(text):
    return text.split('\n')[:-1] if text.endswith('\n') else text.split('\n')


# ───────────────────────── 参考实现 / reference implementations ─────────────────────────
def ref_hello(args, stdin):
    return 'hello, masa\n', 0


def ref_upper(args, stdin):
    return ''.join(line.upper() + '\n' for line in stdin.splitlines()), 0


def ref_join(args, stdin):
    return ' '.join(args) + '\n', 0


_INT = re.compile(r'^[+-]?[0-9]+$')


def ref_sum(args, stdin):
    words = stdin.split()
    if any(not _INT.match(w) for w in words):
        return '', 1
    return f'{sum(int(w) for w in words)}\n', 0


def ref_fizz(args, stdin):
    n = 15
    if args:
        if not _INT.match(args[0]) or int(args[0]) <= 0:
            return '', 2
        n = int(args[0])
    out = []
    for i in range(1, n + 1):
        out.append('FizzBuzz' if i % 15 == 0 else 'Fizz' if i % 3 == 0 else 'Buzz' if i % 5 == 0 else str(i))
    return ''.join(x + '\n' for x in out), 0


def ref_wc(args, stdin):
    data = stdin.encode('utf-8')
    return f'{stdin.count(chr(10))} {len(stdin.split())} {len(data)}\n', 0


def ref_wordfreq(args, stdin):
    counts = Counter(w.lower() for w in stdin.split())
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    return ''.join(f'{w} {c}\n' for w, c in top), 0


def ref_csv_sum(args, stdin):
    rows = stdin.splitlines()[1:]
    totals = {}
    for row in rows:
        parts = row.split(',')
        if len(parts) != 2:
            continue
        try:
            amount = float(parts[1])
        except ValueError:
            continue
        totals[parts[0]] = totals.get(parts[0], 0.0) + amount
    ordered = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    return ''.join(f'{k}: {v:.2f}\n' for k, v in ordered), 0


_ROMAN = [(1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'), (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')]


def _to_roman(n):
    out = ''
    for value, symbol in _ROMAN:
        while n >= value:
            out += symbol
            n -= value
    return out


def _from_roman(text):
    values = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
    if not text or any(c not in values for c in text):
        return None
    total = 0
    for i, c in enumerate(text):
        total += -values[c] if i + 1 < len(text) and values[c] < values[text[i + 1]] else values[c]
    return total if 1 <= total <= 3999 and _to_roman(total) == text else None


def ref_roman(args, stdin):
    if not args:
        return '', 1
    arg = args[0]
    if re.match(r'^[0-9]+$', arg):
        n = int(arg)
        return (_to_roman(n) + '\n', 0) if 1 <= n <= 3999 else ('', 1)
    value = _from_roman(arg)
    return (f'{value}\n', 0) if value is not None else ('', 1)


def ref_lcs(args, stdin):
    lines = stdin.splitlines()
    if len(lines) < 2:
        return '0\n', 0
    a, b = lines[0], lines[1]
    dp = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            dp[i][j] = dp[i - 1][j - 1] + 1 if a[i - 1] == b[j - 1] else max(dp[i - 1][j], dp[i][j - 1])
    return f'{dp[-1][-1]}\n', 0


def ref_knap(args, stdin):
    lines = [x for x in stdin.splitlines() if x.strip()]
    capacity = int(lines[0])
    best = [0] * (capacity + 1)
    for line in lines[1:]:
        weight, value = (int(x) for x in line.split())
        for w in range(capacity, weight - 1, -1):
            best[w] = max(best[w], best[w - weight] + value)
    return f'{best[capacity]}\n', 0


def ref_lru(args, stdin):
    lines = stdin.splitlines()
    capacity = int(lines[0])
    cache, out = OrderedDict(), []
    for line in lines[1:]:
        parts = line.split()
        if parts[0] == 'put':
            cache[parts[1]] = int(parts[2])
            cache.move_to_end(parts[1])
            if len(cache) > capacity:
                cache.popitem(last=False)
        elif parts[0] == 'get':
            if parts[1] in cache:
                cache.move_to_end(parts[1])
                out.append(str(cache[parts[1]]))
            else:
                out.append('-1')
    return ''.join(x + '\n' for x in out), 0


def ref_maze(args, stdin):
    lines = stdin.splitlines()
    rows, cols = (int(x) for x in lines[0].split())
    grid = lines[1:1 + rows]
    start = end = None
    for r in range(rows):
        for c in range(cols):
            if grid[r][c] == 'S':
                start = (r, c)
            elif grid[r][c] == 'E':
                end = (r, c)
    seen, queue = {start: 0}, deque([start])
    while queue:
        r, c = queue.popleft()
        if (r, c) == end:
            return f'{seen[end]}\n', 0
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and grid[nr][nc] != '#' and (nr, nc) not in seen:
                seen[(nr, nc)] = seen[(r, c)] + 1
                queue.append((nr, nc))
    return '-1\n', 0


class _CalcError(Exception):
    pass


def _calc(text):
    tokens = re.findall(r'\s*(\d+|[-+*/()]|\S)', text)
    pos = [0]

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def take():
        pos[0] += 1
        return tokens[pos[0] - 1]

    def expr():
        value = term()
        while peek() in ('+', '-'):
            op = take()
            right = term()
            value = value + right if op == '+' else value - right
        return value

    def term():
        value = factor()
        while peek() in ('*', '/'):
            op = take()
            right = factor()
            if op == '*':
                value *= right
            else:
                if right == 0:
                    raise _CalcError()
                quotient = abs(value) // abs(right)
                value = quotient if (value >= 0) == (right > 0) else -quotient
        return value

    def factor():
        token = peek()
        if token is None:
            raise _CalcError()
        if token == '-':
            take()
            return -factor()
        if token == '(':
            take()
            value = expr()
            if peek() != ')':
                raise _CalcError()
            take()
            return value
        if token.isdigit():
            take()
            return int(token)
        raise _CalcError()

    value = expr()
    if peek() is not None:
        raise _CalcError()
    return value


def ref_calc(args, stdin):
    out = []
    for line in stdin.splitlines():
        if not line.strip():
            continue
        try:
            out.append(str(_calc(line)))
        except _CalcError:
            out.append('error')
    return ''.join(x + '\n' for x in out), 0


def ref_topo(args, stdin):
    edges = [line.split() for line in stdin.splitlines() if line.strip()]
    nodes, after, indegree = set(), {}, {}
    for a, b in edges:
        nodes.update((a, b))
        after.setdefault(a, set()).add(b)
    for n in nodes:
        indegree[n] = 0
    for a, targets in after.items():
        for b in targets:
            indegree[b] += 1
    heap = [n for n in nodes if indegree[n] == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        n = heapq.heappop(heap)
        order.append(n)
        for b in after.get(n, ()):
            indegree[b] -= 1
            if indegree[b] == 0:
                heapq.heappush(heap, b)
    if len(order) != len(nodes):
        return 'CYCLE\n', 0
    return ''.join(n + '\n' for n in order), 0


def ref_kvtx(args, stdin):
    base, stack, out = {}, [], []

    def view():
        merged = dict(base)
        for layer in stack:
            for k, v in layer.items():
                if v is None:
                    merged.pop(k, None)
                else:
                    merged[k] = v
        return merged

    for line in stdin.splitlines():
        parts = line.split()
        if not parts:
            continue
        cmd = parts[0]
        if cmd == 'SET':
            (stack[-1] if stack else base)[parts[1]] = parts[2]
        elif cmd == 'GET':
            out.append(view().get(parts[1], 'NULL'))
        elif cmd == 'DELETE':
            if stack:
                stack[-1][parts[1]] = None
            else:
                base.pop(parts[1], None)
        elif cmd == 'COUNT':
            out.append(str(sum(1 for v in view().values() if v == parts[1])))
        elif cmd == 'BEGIN':
            stack.append({})
        elif cmd == 'ROLLBACK':
            if not stack:
                out.append('NO TRANSACTION')
            else:
                stack.pop()
        elif cmd == 'COMMIT':
            if not stack:
                out.append('NO TRANSACTION')
            else:
                merged = view()
                base.clear()
                base.update(merged)
                stack.clear()
    return ''.join(x + '\n' for x in out), 0


def ref_regex(args, stdin):
    out = []
    for line in stdin.splitlines():
        if not line:
            continue
        pattern, _, text = line.partition('\t')
        out.append('match' if re.fullmatch(pattern, text) else 'no match')
    return ''.join(x + '\n' for x in out), 0


# ───────────────────────── 任务 / tasks ─────────────────────────
TASKS = (
    Task('hello', 0, '打招呼', '写一个 CLI：向标准输出打印一行 hello, masa（以换行结束），退出码 0。',
         (([], ''),), ref_hello, ('pipeline',)),
    Task('upper', 1, '转大写', '写一个 CLI：读取标准输入，把每一行转换为大写后输出，行数与输入相同；输入为空则不输出任何内容。',
         (([], 'abc\nHello World\n'), ([], ''), ([], 'x')), ref_upper, ('pipeline', 'io')),
    Task('join', 1, '参数拼接', '写一个 CLI：把所有命令行参数用单个空格连接后输出一行（以换行结束）；没有参数时输出一个空行。',
         ((['a', 'b c'], ''), ([], '')), ref_join, ('pipeline', 'args')),
    Task('sum', 2, '整数求和', '写一个 CLI：读取标准输入中以空白分隔的整数并输出它们的和（一行）。'
         '只要出现一个不是十进制整数的词，就向标准错误输出 error，标准输出不输出任何内容，退出码为 1。没有任何数字时输出 0。',
         (([], '1 2 3\n4\n'), ([], '-5 5 7'), ([], '1 x 2'), ([], '')), ref_sum, ('errors', 'imports')),
    Task('fizz', 2, 'FizzBuzz', '写一个 CLI：输出 1 到 N 的 FizzBuzz，每行一项（3 的倍数输出 Fizz，5 的倍数输出 Buzz，同时是 15 的倍数输出 FizzBuzz，其余输出数字本身）。'
         'N 是第一个命令行参数，缺省为 15。N 不是正整数（包括 0、负数、非数字）时，向标准错误输出 error，标准输出为空，退出码为 2。',
         (([], ''), (['5'], ''), (['0'], ''), (['abc'], ''), (['-3'], '')), ref_fizz, ('errors', 'args', 'imports')),
    Task('wc', 3, '文本统计', '写一个 CLI：读取标准输入，输出一行“行数 单词数 字节数”，三个数字以单个空格分隔。'
         '行数是换行符 \\n 的个数；单词是由空白字符（空格、制表符、换行）分隔的非空片段；字节数是输入的总字节数（UTF-8，一个汉字 3 字节）。',
         (([], 'hello world\n'), ([], 'a b\nc'), ([], ''), ([], '  \n\n'), ([], '你好\n')), ref_wc, ('edge_semantics', 'expectation')),
    Task('wordfreq', 3, '词频', '写一个 CLI：读取标准输入，按空白分词并全部转成小写，输出出现次数最多的前 3 个词，每行格式“词 次数”。'
         '次数降序；次数相同按词的字典序升序；不足 3 个不同的词时有几个输出几个；输入为空则不输出。',
         (([], 'the cat The dog the cat\n'), ([], 'b a b a c\n'), ([], ''), ([], 'Go go GO')), ref_wordfreq, ('sorting', 'tie_break')),
    Task('csv_sum', 4, 'CSV 费用汇总', '写一个 CLI：读取标准输入的 CSV，第一行是表头 category,amount（跳过），按 category 对 amount（可含小数）求和，'
         '按总和降序输出，每行格式“category: 总和”，总和保留两位小数；总和相同按 category 字典序升序。amount 不是数字的行跳过，并向标准错误输出一行警告，退出码仍为 0。只有表头时不输出。',
         (([], 'category,amount\nfood,10.5\nrent,100\nfood,4.5\n'), ([], 'category,amount\na,1\nb,1\nc,2\n'),
          ([], 'category,amount\nfood,abc\nfood,2\n'), ([], 'category,amount\n')), ref_csv_sum, ('sorting', 'tie_break', 'errors', 'expectation')),
    Task('roman', 4, '罗马数字', '写一个 CLI：第一个命令行参数如果是 1 到 3999 的十进制整数，输出它的罗马数字；如果是合法的标准写法罗马数字（大写，如 IV，而不是 IIII），输出对应的整数。'
         '其它任何情况（缺省、超出范围、非法写法）向标准错误输出 error，标准输出为空，退出码为 1。',
         ((['4'], ''), (['1994'], ''), (['MMXXIV'], ''), (['IIII'], ''), (['0'], ''), (['4000'], ''), (['abc'], ''), ([], '')), ref_roman, ('errors', 'edge_semantics')),
    Task('lcs', 5, '最长公共子序列', '写一个 CLI：读取标准输入的前两行字符串（可含空格），输出它们最长公共子序列的长度（一个整数）；少于两行时输出 0。',
         (([], 'ABCBDAB\nBDCABA\n'), ([], 'abc\nabc'), ([], 'abc\n'), ([], 'a b\nb a\n'), ([], '\n\n')), ref_lcs, ('dp',)),
    Task('knap', 5, '0/1 背包', '写一个 CLI：0/1 背包。标准输入第一行是整数 W（背包容量），之后每行两个整数“重量 价值”表示一件物品，每件物品至多选一次。输出重量总和不超过 W 时的最大总价值；没有物品输出 0。',
         (([], '10\n5 10\n4 40\n6 30\n3 50\n'), ([], '0\n1 5\n'), ([], '5\n'), ([], '7\n9 100\n2 3\n5 6\n')), ref_knap, ('dp',)),
    Task('lru', 6, 'LRU 缓存', '写一个 CLI：实现 LRU 缓存。标准输入第一行是整数 capacity（至少为 1），之后每行一条命令：“put 键 整数值”或“get 键”（键不含空格）。'
         'get 命中时输出值并把该键标为最近使用，未命中输出 -1；put 新键或更新旧键都视为最近使用，超出容量时淘汰最久未使用的键。只有 get 产生输出，每次一行。',
         (([], '2\nput a 1\nput b 2\nget a\nput c 3\nget b\nget a\nget c\n'), ([], '1\nput a 1\nput a 2\nget a\nput b 3\nget a\n'), ([], '3\nget x\n')), ref_lru, ('state', 'expectation')),
    Task('maze', 6, '迷宫最短路', '写一个 CLI：标准输入第一行是“R C”，之后 R 行每行 C 个字符：# 是墙，. 是空地，S 是起点，E 是终点（各有一个）。'
         '只能上下左右移动、不能穿墙。输出从 S 到 E 的最少步数；不可达输出 -1。',
         (([], '3 3\nS..\n.#.\n..E\n'), ([], '3 3\nS#.\n###\n..E\n'), ([], '1 5\nS...E\n'), ([], '2 2\nSE\n..\n')), ref_maze, ('dp', 'graph')),
    Task('calc', 7, '表达式求值', '写一个 CLI：表达式求值器。标准输入每行一个算术表达式，支持非负整数、+ - * /、圆括号、一元负号（如 -3、-(2+1)、2*-3）和空格；'
         '乘除优先于加减，同级左结合；/ 是向零取整的整数除法（-7/2 得 -3）；除数为 0 输出 error。每行输出一个整数结果或 error；空行跳过不输出；'
         '语法错误（括号不匹配、缺少操作数、非法字符、两个数字之间没有运算符）也输出 error。',
         (([], '1+2*3\n(1+2)*3\n-7/2\n7/-2\n2*-3\n10/(5-5)\n'), ([], '1 +\n(1\n1 2\n\n3 $ 4\n--3\n'), ([], '100/10/5\n2-3-4\n-(2+1)*2\n')), ref_calc, ('parser', 'edge_semantics')),
    Task('topo', 7, '拓扑排序', '写一个 CLI：拓扑排序。标准输入每行“a b”表示 a 必须排在 b 之前（a、b 是不含空格的字符串）。'
         '输出包含所有出现过的节点的一个拓扑序，每个节点一行；有多个可选节点时总是选字典序最小的。存在环（包括 a a）时只输出一行 CYCLE，不输出任何节点。没有输入则不输出。',
         (([], 'b c\na b\na d\n'), ([], 'a b\nb a\n'), ([], ''), ([], 'x x\n'), ([], 'm n\nk n\nz k\n')), ref_topo, ('graph', 'tie_break')),
    Task('kvtx', 8, '事务键值存储', '写一个 CLI：带事务的内存键值存储。标准输入每行一条命令：SET 键 值、GET 键、DELETE 键、COUNT 值、BEGIN、ROLLBACK、COMMIT（键和值都不含空格）。'
         'GET 输出值，不存在输出 NULL；COUNT 输出当前值等于该值的键的个数；BEGIN 开启一个事务，事务可以嵌套；ROLLBACK 只撤销最近一个事务；COMMIT 提交所有已开启的事务；'
         '没有事务时 ROLLBACK 和 COMMIT 输出 NO TRANSACTION。只有 GET、COUNT 和上面这条提示会产生输出，每次一行。',
         (([], 'SET a 10\nGET a\nCOUNT 10\nBEGIN\nSET a 20\nBEGIN\nDELETE a\nGET a\nROLLBACK\nGET a\nCOMMIT\nGET a\nROLLBACK\n'),
          ([], 'SET a 1\nSET b 1\nBEGIN\nSET b 2\nCOUNT 1\nCOMMIT\nCOUNT 1\nCOUNT 2\nBEGIN\nDELETE b\nROLLBACK\nGET b\n')), ref_kvtx, ('state', 'expectation')),
    Task('regex', 9, '正则匹配', '写一个 CLI：不使用 regexp 包，自己实现正则表达式匹配。标准输入每行是“模式<TAB>文本”（用制表符分隔），空行跳过。'
         '模式支持：普通字符、. （任意单个字符）、* + ?（作用于前一个字符或分组）、字符集 [abc] [a-z] [^0-9]、| 、圆括号分组。'
         '模式必须匹配整个文本；匹配输出 match，否则输出 no match，每行一个结果。',
         (([], 'a*b\taaab\na*b\tb\na.c\tabc\na.c\tac\n(ab)+\tababab\n(ab)+\taba\ncolou?r\tcolor\ncolou?r\tcolour\n[a-c]+\tabcabc\n[^0-9]+\tab1\n'
               'cat|dog\tdog\ncat|dog\tcow\n(a|b)*c\tababc\n(a|b)*c\tabab\n.*\t\n'),
          ([], 'x?y?z?\t\nx?y?z?\txz\n(a(b|c)d)+\tabdacd\n(a(b|c)d)+\tabdac\n[a-z]+[0-9]?\tabc7\n[a-z]+[0-9]?\tabc77\n')), ref_regex, ('parser', 'recursion')),
)

BY_ID = {t.id: t for t in TASKS}

# 套餐：从“几分钟的金丝雀”到“跑很久的完整评测”。caps 是默认上限，界面里都可以改。
# Presets from a few-minute canary to the full suite. Caps are defaults and all editable in the UI.
SUITES = {
    'canary': {'title': '金丝雀（约 5 分钟）', 'desc': '3 个最简单的任务，一眼判断系统是不是还活着、有没有明显退步。', 'tasks': ['hello', 'upper', 'sum'], 'repeats': 1,
               'total_cloud_tokens': 120_000, 'total_minutes': 15},
    'quick': {'title': '快速（约 20 分钟）', 'desc': '覆盖 L0–L5 的代表任务，各 1 次，能看出能力大致落在哪一级。', 'tasks': ['hello', 'upper', 'sum', 'wc', 'csv_sum', 'lcs'], 'repeats': 1,
              'total_cloud_tokens': 400_000, 'total_minutes': 45},
    'standard': {'title': '标准（约 1.5 小时）', 'desc': 'L0–L6 的 13 个任务，各 2 次，用来比较 runtime 改动前后的通过率。', 'tasks': [t.id for t in TASKS if t.level <= 6], 'repeats': 2,
                 'total_cloud_tokens': 1_500_000, 'total_minutes': 180},
    'full': {'title': '完整（数小时）', 'desc': '全部 10 个等级的 17 个任务，各 3 次；高等级任务当前的模型多半做不出来，用来观察上限。', 'tasks': [t.id for t in TASKS], 'repeats': 3,
             'total_cloud_tokens': 4_000_000, 'total_minutes': 480},
}


def describe():
    """给界面用：任务与套餐。 Tasks and suites for the UI."""
    return {
        'tasks': [{'id': t.id, 'level': t.level, 'title': t.title, 'goal': t.goal, 'cases': len(t.cases), 'probes': list(t.probes),
                   'max_seconds': t.max_seconds, 'max_cloud_tokens': t.max_cloud_tokens} for t in TASKS],
        'suites': SUITES,
        'levels': {str(level): LEVEL_TEXT[level] for level in range(10)},
    }


LEVEL_TEXT = {
    0: '冒烟：只验证流水线能跑通', 1: '单一输入输出变换', 2: '输入校验与错误退出码', 3: '边界语义（结尾换行、并列排序、字节数）',
    4: '多规则组合（解析 + 聚合 + 排序 + 容错）', 5: '动态规划', 6: '有状态/图搜索', 7: '解析器与拓扑结构（语法错误、优先级、环）',
    8: '事务语义（嵌套、回滚、提交）', 9: '前沿：自己实现正则引擎（目前的模型多半做不出来）',
}
