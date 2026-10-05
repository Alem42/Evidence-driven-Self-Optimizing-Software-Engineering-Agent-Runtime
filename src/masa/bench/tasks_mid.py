"""补充任务：L6–L9 之间的中间难度（评测显示云端模型到 L8 都能做，需要更细的梯度）。
Extra tasks filling the gap between L6 and L9 (the first benchmark showed cloud models clear L8, so a finer gradient was needed).

和 tasks.py 一样：期望由参考实现算出，参考实现用手算答案钉住（tests/test_bench.py）。
Same rules as tasks.py: expectations come from reference implementations that are pinned by hand-computed answers in tests/test_bench.py.
"""
import json


def ref_coins(args, stdin):
    lines = [x for x in stdin.splitlines() if x.strip()]
    amount = int(lines[0])
    coins = [int(x) for x in lines[1].split()] if len(lines) > 1 else []
    inf = float('inf')
    least = [0] + [inf] * amount
    ways = [1] + [0] * amount
    for coin in coins:
        for total in range(coin, amount + 1):
            least[total] = min(least[total], least[total - coin] + 1)
            ways[total] += ways[total - coin]
    return f"{-1 if least[amount] == inf else least[amount]} {ways[amount]}\n", 0


def ref_intervals(args, stdin):
    spans = sorted(tuple(int(x) for x in line.split()) for line in stdin.splitlines() if line.strip())
    merged = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    total = sum(end - start + 1 for start, end in merged)
    return ''.join(f'{s} {e}\n' for s, e in merged) + f'total {total}\n', 0


def _flatten(value, path, out):
    if isinstance(value, dict):
        if not value and path:
            out.append(f'{path}=<empty>')
        for key, item in value.items():
            _flatten(item, f'{path}.{key}' if path else key, out)
    elif isinstance(value, list):
        if not value:
            out.append(f'{path}=<empty>')
        for index, item in enumerate(value):
            _flatten(item, f'{path}[{index}]', out)
    elif value is None:
        out.append(f'{path}=null')
    elif value is True:
        out.append(f'{path}=true')
    elif value is False:
        out.append(f'{path}=false')
    else:
        out.append(f'{path}={value}')


def ref_flat(args, stdin):
    try:
        data = json.loads(stdin)
    except ValueError:
        return '', 1
    if not isinstance(data, dict):
        return '', 1
    out = []
    _flatten(data, '', out)
    return ''.join(line + '\n' for line in sorted(out, key=lambda x: x.split('=', 1)[0])), 0


def ref_bigint(args, stdin):
    a, b = (int(x) for x in stdin.split())
    return f'{a + b}\n{a * b}\n', 0


def ref_sched(args, stdin):
    tasks = {}
    for line in stdin.splitlines():
        parts = line.split()
        if parts:
            tasks[parts[0]] = (int(parts[1]), parts[2:])
    if not tasks:
        return '0\n', 0
    if any(dep not in tasks for _, deps in tasks.values() for dep in deps):
        return 'error\n', 0
    finish, state = {}, {}

    def visit(name):
        if state.get(name) == 1:
            raise ValueError('cycle')
        if name in finish:
            return finish[name]
        state[name] = 1
        duration, deps = tasks[name]
        finish[name] = duration + max((visit(d) for d in deps), default=0)
        state[name] = 2
        return finish[name]

    try:
        for name in tasks:
            visit(name)
    except ValueError:
        return 'error\n', 0
    return f'{max(finish.values())}\n' + ''.join(f'{n} {finish[n]}\n' for n in sorted(finish)), 0


def _solve(grid):
    for row in range(9):
        for col in range(9):
            if grid[row][col] == 0:
                for digit in range(1, 10):
                    if _fits(grid, row, col, digit):
                        grid[row][col] = digit
                        if _solve(grid):
                            return True
                        grid[row][col] = 0
                return False
    return True


def _fits(grid, row, col, digit):
    if any(grid[row][c] == digit for c in range(9)) or any(grid[r][col] == digit for r in range(9)):
        return False
    r0, c0 = row - row % 3, col - col % 3
    return not any(grid[r][c] == digit for r in range(r0, r0 + 3) for c in range(c0, c0 + 3))


def ref_sudoku(args, stdin):
    rows = stdin.split()
    grid = [[0 if ch == '.' else int(ch) for ch in row] for row in rows]
    for r in range(9):
        for c in range(9):
            digit = grid[r][c]
            if digit:
                grid[r][c] = 0
                ok = _fits(grid, r, c, digit)
                grid[r][c] = digit
                if not ok:
                    return 'NO SOLUTION\n', 0
    if not _solve(grid):
        return 'NO SOLUTION\n', 0
    return ''.join(''.join(str(d) for d in row) + '\n' for row in grid), 0


PUZZLE = ('53..7....', '6..195...', '.98....6.', '8...6...3', '4..8.3..1', '7...2...6', '.6....28.', '...419..5', '....8..79')
CONFLICT = ('55..7....', '6..195...', '.98....6.', '8...6...3', '4..8.3..1', '7...2...6', '.6....28.', '...419..5', '....8..79')
# 行内没有重复、但整体无解：第一行已填 1–8，唯一能填的 9 被第一列的 9 挡住。
# No duplicate in any row, yet unsolvable: row 1 needs a 9 in its only blank, but column 1 already has one.
DEAD_END = ('12345678.', '........9', '.........', '.........', '.........', '.........', '.........', '.........', '.........')


def _grid(rows):
    return '\n'.join(rows) + '\n'
