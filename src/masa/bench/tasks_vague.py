"""VagueBench（模糊任务的尺子，v0）：七个 Python 命令行任务，题面像用户平时会写的 1–3 句话——只给一个输入样例，把格式、排序、边界、报错方式都留白。
VagueBench (the ruler for vague tasks, v0): seven Python CLI tasks whose statements read like what a user types in 1-3 sentences - one input example, and the formats, ordering, edge cases and error behaviour are left open.

为什么需要它 / Why: 现有 23 个评测任务把输入输出写得逐字节精确，量的是“按规格实现”的能力；目标是“复杂且模糊的任务”，没有尺子就无法判断任何路线选择（ROADMAP G10）。
The existing 23 tasks pin I/O byte for byte and measure "implement the spec"; the goal is complex AND vague tasks, and without a ruler no route decision can be judged (ROADMAP G10).

判官 / The oracle: 只验证**无歧义的核心行为**，对留白处采用“任何合理解释都接受”的宽容判定（解析输出里的数字/词，不比对逐字节）：
  · 输出格式、排序、小数位数、报错用 stdout 还是 stderr —— 不检查；
  · 只检查：每个类别的合计、每个级别的计数、表达式的值、拓扑顺序是否合法、HTML 里是否有对应标签、库存数字、版本排序。
It checks only the unambiguous core and accepts any reasonable reading of what the statement left open (it parses numbers and words out of the output). Every task ships a reference
and the self-tests (tests/test_bench_vague.py) require that the verdict accepts the reference and rejects wrong outputs, so the ruler itself is checked.
"""
import re

from masa.bench.tasks import Task

_NUMBER = re.compile(r'-?\d+(?:\.\d+)?')
_NO_TRACEBACK = 'Traceback (most recent call last)'


def numbers(text):
    return [float(m) for m in _NUMBER.findall(text)]


def _close(a, b, tolerance=0.006):
    return abs(a - b) <= tolerance


def lines_of(text):
    return [line for line in text.replace('\r\n', '\n').split('\n') if line.strip()]


def _crashed(stderr):
    return _NO_TRACEBACK in (stderr or '')


def has_value(text, word, value):
    """有一行同时含有 word 与数值 value。 Some line contains the word and the value."""
    return any(word.casefold() in line.casefold() and any(_close(n, value) for n in numbers(line)) for line in lines_of(text))


def last_value(text, word, value):
    """含有 word 的最后一行里有数值 value（报告通常打印在最后）。 The LAST line containing the word holds the value (a report is usually printed last)."""
    hits = [line for line in lines_of(text) if word.casefold() in line.casefold()]
    return bool(hits) and any(_close(n, value) for n in numbers(hits[-1]))


def _ok():
    return True, ''


# ───────────────────────── expenses ─────────────────────────
EXPENSES = '2024-03-01 餐饮 35.5\n2024-03-01 交通 12\n2024-03-02 餐饮 20.25\n2024-03-03 娱乐 100\n2024-03-03 交通 -12\n'
EXPENSES_B = '2024-01-01 a 0.1\n2024-01-02 a 0.2\n2024-01-03 b 1.5\n2024-01-04 c 7\n'


def _totals(stdin):
    totals = {}
    for line in stdin.splitlines():
        parts = line.split()
        if len(parts) == 3:
            totals[parts[1]] = totals.get(parts[1], 0.0) + float(parts[2])
    return totals


def ref_expenses(args, stdin):
    totals = _totals(stdin)
    return ''.join(f'{k}: {v:.2f}\n' for k, v in sorted(totals.items())) + f'总计: {sum(totals.values()):.2f}\n', 0


def verify_expenses(args, stdin, out, err, code):
    if _crashed(err):
        return False, 'traceback'
    totals = _totals(stdin)
    if not totals:
        return _ok()  # 空输入怎么处理是留白：只要不崩溃 / empty input is left open: no crash is enough
    for name, value in totals.items():
        if not has_value(out, name, value):
            return False, f'no line with {name!r} and {value:g}'
    grand = sum(totals.values())
    named = [line for line in lines_of(out) if re.search('总|合计|共|total|sum', line, re.I)]
    if not any(_close(n, grand) for line in named for n in numbers(line)) and not any(_close(n, grand) for n in numbers(out)):
        return False, f'no grand total {grand:g}'
    return _ok()


# ───────────────────────── loglevels ─────────────────────────
LOG_A = ('2024-03-05 12:00:01 INFO 服务启动\n2024-03-05 12:00:02 INFO 收到请求\n2024-03-05 12:00:03 WARN 响应较慢\n2024-03-05 12:00:04 ERROR 支付超时\n'
         '2024-03-05 12:00:05 ERROR 库存不足\n2024-03-05 12:00:06 INFO 收到请求\n2024-03-05 12:00:07 ERROR 支付超时\n2024-03-05 12:00:08 ERROR 支付超时\n2024-03-05 12:00:09 ERROR 库存不足\n')
LOG_B = '2024-03-06 08:00:00 ERROR 磁盘已满\n2024-03-06 08:00:01 ERROR 超时重试\n2024-03-06 08:00:02 ERROR 磁盘已满\n2024-03-06 08:00:03 DEBUG 心跳\n'


def _log_counts(stdin):
    levels, errors = {}, {}
    for line in stdin.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4:
            levels[parts[2]] = levels.get(parts[2], 0) + 1
            if parts[2] == 'ERROR':
                errors[parts[3]] = errors.get(parts[3], 0) + 1
    return levels, errors


def ref_loglevels(args, stdin):
    levels, errors = _log_counts(stdin)
    top = max(errors.items(), key=lambda kv: (kv[1], kv[0])) if errors else None
    return ''.join(f'{k} {v}\n' for k, v in sorted(levels.items())) + (f'最多的错误: {top[0]} ({top[1]}次)\n' if top else ''), 0


def verify_loglevels(args, stdin, out, err, code):
    if _crashed(err):
        return False, 'traceback'
    levels, errors = _log_counts(stdin)
    for level, count in levels.items():
        if not has_value(out, level, count):
            return False, f'no line with {level} and {count}'
    best = max(errors.values())
    winners = [m for m, c in errors.items() if c == best]
    if not any(m in out for m in winners):
        return False, f'the most frequent error {winners} is not reported'
    for message, count in errors.items():
        if count < best:  # 若它出现在输出里并带着次数，次数必须对；它不能被当成“最多”的 / if a less frequent message is listed with a count, the count must be right
            for line in lines_of(out):
                if message in line and numbers(line) and not any(_close(n, count) for n in numbers(line)) and any(_close(n, best) for n in numbers(line)):
                    return False, f'{message!r} is listed with the top count'
    return _ok()


# ───────────────────────── calc ─────────────────────────
CALC_CASES = (
    ('x = 3 + 4 * 2\n(x + 1) / 2\nx * x\n', [6.0, 121.0]),
    ('a = 10\nb = a / 4\nb\na - b * 2\n', [2.5, 5.0]),
    ('2 * (3 + 4) - 5 / 5\n', [13.0]),
    ('-3 + 5\nx = -2\nx * x\n', [2.0, 4.0]),
)
CALC_ERRORS = ('1 / 0\n2 + 3\n', 'y + 1\n')


def ref_calc(args, stdin):
    env, out = {}, []
    try:
        for line in stdin.splitlines():
            if not line.strip():
                continue
            if '=' in line:
                name, expr = line.split('=', 1)
                env[name.strip()] = float(eval(expr, {'__builtins__': {}}, env))  # noqa: S307 - the benchmark's own reference on its own inputs
            else:
                value = eval(line, {'__builtins__': {}}, env)  # noqa: S307
                out.append(f'{value:g}\n')
    except (ZeroDivisionError, NameError):
        return ''.join(out) + 'error\n', 1
    return ''.join(out), 0


def verify_calc(args, stdin, out, err, code):
    if _crashed(err):
        return False, 'traceback'
    expected = next((values for text, values in CALC_CASES if text == stdin), None)
    if expected is not None:
        got = numbers(out)
        if len(got) != len(expected) or not all(_close(a, b, 1e-6) for a, b in zip(got, expected)):
            return False, f'values {got}, want {expected}'
        return _ok()
    # 除零 / 未定义变量：要报告出来——非零退出码，或输出/错误里有出错的说法；不能把 inf/nan 当成一个值打印 / must be reported: a non-zero exit or an error wording; inf/nan must not be printed as a value
    said = re.search('error|错误|除零|除以|zero|undefined|未定义|invalid|无效|not defined', out + (err or ''), re.I)
    if code == 0 and not said:
        return False, 'an error case ended silently with exit code 0'
    if re.search(r'\b(inf|nan)\b', out, re.I):
        return False, 'printed inf/nan as a value'
    return _ok()


# ───────────────────────── deporder ─────────────────────────
DEP_CASES = (
    'app: lib util\nlib: core\nutil: core\ncore:\n',
    'deploy: test\ntest: build\nbuild:\ndocs:\nlint: build\n',
    'a: b\nb: a\n',
)


def _deps(stdin):
    graph = {}
    for line in stdin.splitlines():
        if ':' in line:
            name, rest = line.split(':', 1)
            graph[name.strip()] = rest.split()
    return graph


def _has_cycle(graph):
    state = {}

    def visit(node):
        if state.get(node) == 1:
            return True
        if state.get(node) == 2:
            return False
        state[node] = 1
        if any(visit(d) for d in graph.get(node, ()) if d in graph):
            return True
        state[node] = 2
        return False
    return any(visit(n) for n in graph)


def ref_deporder(args, stdin):
    graph = _deps(stdin)
    if _has_cycle(graph):
        return '循环依赖\n', 1
    done, order = set(), []

    def visit(node):
        if node in done:
            return
        done.add(node)
        for d in graph.get(node, ()):
            if d in graph:
                visit(d)
        order.append(node)
    for node in graph:
        visit(node)
    return ''.join(n + '\n' for n in order), 0


def verify_deporder(args, stdin, out, err, code):
    if _crashed(err):
        return False, 'traceback'
    graph = _deps(stdin)
    if _has_cycle(graph):
        if code == 0 and not re.search('循环|cycle|circular|环|loop', out + (err or ''), re.I):
            return False, 'a dependency cycle was not reported'
        return _ok()
    position = {}
    for name in re.findall(r'[A-Za-z_][\w-]*', out):
        if name in graph and name not in position:
            position[name] = len(position)
    if set(position) != set(graph):
        return False, f'tasks missing from the order: {sorted(set(graph) - set(position))}'
    for node, deps in graph.items():
        for dep in deps:
            if dep in position and position[dep] > position[node]:
                return False, f'{dep} must come before {node}'
    return _ok()


# ───────────────────────── markdown ─────────────────────────
MD_A = '# 标题\n\n你好 **世界**\n\n- 苹果\n- 香蕉\n'
MD_B = '## 二级\n### 三级\n\n第二段 **加粗** 结束\n'


def ref_markdown(args, stdin):
    out = []
    for block in stdin.strip().split('\n\n'):
        lines = block.splitlines()
        if all(line.startswith('- ') for line in lines):
            out.append('<ul>' + ''.join(f'<li>{line[2:]}</li>' for line in lines) + '</ul>')
        elif block.startswith('#'):
            for line in lines:
                level = len(line) - len(line.lstrip('#'))
                out.append(f'<h{level}>{line[level:].strip()}</h{level}>')
        else:
            out.append('<p>' + re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', block) + '</p>')
    return '\n'.join(out) + '\n', 0


def _tag(out, names, text):
    return any(re.search(rf'<{name}[^>]*>\s*{re.escape(text)}\s*</{name}>', out) for name in names)


def verify_markdown(args, stdin, out, err, code):
    if _crashed(err):
        return False, 'traceback'
    if stdin == MD_A:
        wanted = [(('h1',), '标题'), (('li',), '苹果'), (('li',), '香蕉')]
        if not re.search(r'<(strong|b)>\s*世界\s*</(strong|b)>', out) or not re.search(r'<p[^>]*>[^<]*你好', out) or not re.search(r'<ul[^>]*>', out):
            return False, 'bold / paragraph / list markup is missing'
    else:
        wanted = [(('h2',), '二级'), (('h3',), '三级')]
        if not re.search(r'<(strong|b)>\s*加粗\s*</(strong|b)>', out):
            return False, 'bold markup is missing'
    for names, text in wanted:
        if not _tag(out, names, text):
            return False, f'no <{names[0]}> with {text!r}'
    if '**' in out or re.search(r'^\s*#', out, re.M):
        return False, 'raw Markdown markers are left in the output'
    return _ok()


# ───────────────────────── shop ─────────────────────────
SHOP_CASES = (
    ('stock apple 10\nstock pear 5\norder o1 apple 3\norder o2 pear 6\nreport\n', {'apple': 7, 'pear': 5}),
    ('stock apple 8\norder o1 apple 5\nreport\ncancel o1\nreport\n', {'apple': 8}),
    ('stock apple 4\nstock apple -5\nstock pear 9\norder o9 pear 2\norder o9 pear 3\nreport\n', {'apple': 4}),
    ('stock cola 12\norder o1 cola 4\norder o2 cola 4\norder o3 cola 4\norder o4 cola 1\ncancel o2\nreport\n', {'cola': 4}),
)


def ref_shop(args, stdin):
    stock, orders, out = {}, {}, []
    for line in stdin.splitlines():
        parts = line.split()
        if not parts:
            continue
        if parts[0] == 'stock' and len(parts) == 3 and parts[2].isdigit():
            stock[parts[1]] = stock.get(parts[1], 0) + int(parts[2])
            out.append('ok')
        elif parts[0] == 'order' and len(parts) == 4 and parts[3].isdigit():
            if parts[1] in orders or stock.get(parts[2], 0) < int(parts[3]):
                out.append('rejected')
            else:
                stock[parts[2]] -= int(parts[3])
                orders[parts[1]] = (parts[2], int(parts[3]))
                out.append('ok')
        elif parts[0] == 'cancel' and len(parts) == 2 and parts[1] in orders:
            product, count = orders.pop(parts[1])
            stock[product] += count
            out.append('ok')
        elif parts[0] == 'report':
            out += [f'{k}: {v}' for k, v in sorted(stock.items())]
        else:
            out.append('error')
    return '\n'.join(out) + '\n', 0


def verify_shop(args, stdin, out, err, code):
    if _crashed(err):
        return False, 'traceback'
    expected = next(values for text, values in SHOP_CASES if text == stdin)
    for product, count in expected.items():
        if not last_value(out, product, count):
            return False, f'the last line about {product!r} does not show {count}'
    return _ok()


# ───────────────────────── semver ─────────────────────────
SEMVER_A = '1.10.0\n1.2.0\n1.2.10\n1.2.9\n0.9.9\n'
SEMVER_B = '1.0.0\n1.0.0-beta\n1.0.0-alpha\n0.9.0\n2.0.0-rc\n1.0.1\n'


def _semver_key(text):
    core, _, pre = text.partition('-')
    parts = tuple(int(p) for p in core.split('.'))
    return parts, (1, ()) if not pre else (0, tuple((0, int(x), '') if x.isdigit() else (1, 0, x) for x in pre.split('.')))


def ref_semver(args, stdin):
    return ''.join(v + '\n' for v in sorted(stdin.split(), key=_semver_key)), 0


def verify_semver(args, stdin, out, err, code):
    if _crashed(err):
        return False, 'traceback'
    want = sorted(stdin.split(), key=_semver_key)
    got = [line.strip() for line in out.replace('\r\n', '\n').split('\n') if line.strip()]
    return (True, '') if got == want else (False, f'order {got}, want {want}')


def _cases(*stdins):
    return tuple(([], text) for text in stdins)


VAGUE_TASKS = (
    Task('v_expenses', 4, '记账汇总', '写一个命令行小工具：从标准输入读取记账流水，每行一笔，形如 `2024-03-05 餐饮 35.5`（日期、类别、金额，空格分隔）。按类别汇总支出，并给出总支出。金额可能是负数（表示退款）。',
         _cases(EXPENSES, EXPENSES_B, ''), ref_expenses, ('vague', 'aggregation'), verify_expenses),
    Task('v_loglevels', 4, '日志统计', '写一个命令行小工具：从标准输入读取应用日志，每行形如 `2024-03-05 12:01:33 ERROR 支付超时`（日期 时间 级别 消息）。统计每种级别出现了多少次，并找出出现次数最多的 ERROR 消息。',
         _cases(LOG_A, LOG_B), ref_loglevels, ('vague', 'aggregation'), verify_loglevels),
    Task('v_calc', 5, '小计算器', '写一个命令行小计算器：从标准输入逐行读取，每行要么是赋值 `x = 3 + 4 * 2`，要么是一个表达式 `(x + 1) / 2`。支持 + - * / 、括号和变量。对每个表达式行输出它的值，赋值行不输出。除零或者用了没定义的变量要报错。',
         _cases(*[text for text, _ in CALC_CASES], *CALC_ERRORS), ref_calc, ('vague', 'parser', 'errors'), verify_calc),
    Task('v_deporder', 5, '构建顺序', '写一个命令行小工具：标准输入每行描述一个构建任务和它依赖的任务，形如 `app: lib util`（冒号前是任务名，冒号后是它依赖的任务，可以没有依赖）。输出一个可行的执行顺序，保证每个任务排在它的依赖后面。如果有循环依赖要报出来。',
         _cases(*DEP_CASES), ref_deporder, ('vague', 'graph', 'errors'), verify_deporder),
    Task('v_markdown', 5, 'Markdown 转 HTML', '写一个命令行小工具：把标准输入里的一小段 Markdown 转成 HTML 打印出来。至少支持：一到三级标题（# ## ###）、以 - 开头的无序列表、**粗体**、用空行分隔的普通段落。',
         _cases(MD_A, MD_B), ref_markdown, ('vague', 'parser'), verify_markdown),
    Task('v_shop', 6, '库存与订单', '写一个命令行小库存系统：从标准输入读取指令脚本，每行一条。能进货（`stock 商品 数量`）、下单（`order 订单号 商品 数量`，库存不够就拒绝）、取消订单（`cancel 订单号`，库存退回）、查看库存（`report`）。',
         _cases(*[text for text, _ in SHOP_CASES]), ref_shop, ('vague', 'state', 'errors'), verify_shop),
    Task('v_semver', 4, '版本排序', '写一个命令行小工具：标准输入每行一个版本号（如 `1.2.3`，也可能带预发布标记，如 `1.0.0-beta`），按版本从低到高排序后每行输出一个。',
         _cases(SEMVER_A, SEMVER_B), ref_semver, ('vague', 'sorting'), verify_semver),
)
