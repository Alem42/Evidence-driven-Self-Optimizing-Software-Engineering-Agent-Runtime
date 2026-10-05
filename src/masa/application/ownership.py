"""失败归属分析：一次验证失败里，哪些问题是“实现”的，哪些是“测试”的，各自该怎么处理。
Failure ownership: in one failed verification, which problems belong to the implementation and which to the tests.

为什么需要（真实案例 c903b2b7）/ Why (real case c903b2b7):
  测试文件有 import cycle（测试 import 了自己的包），实现里有语法错误 `err != nil not nil`。旧分类器只要看到任何测试侧信号就走
  “修订测试”，于是自动流程连续 4 轮修订测试，**实现里的语法错误一轮都没修**。归属分析把两类问题分开，让每一类都能轮到。
  The test imported its own package AND the implementation had a syntax error. The old classifier saw any test-side signal and chose
  "revise tests" four rounds in a row, so the implementation error was never touched. Splitting ownership lets every class take a turn.

规则 / Rules:
  · 实现有编译/语法错误 → 先修实现（它们无歧义、会挡住所有检查）。 Implementation compile/syntax errors are fixed first.
  · 否则测试有编译/准备错误 → 修订测试。 Otherwise test compile/setup errors → revise the tests.
  · 否则（只有断言失败）→ 归属不明，交给原有的“修实现 → 仲裁”流程或 Diagnoser。 Assertion-only failures stay ambiguous.
"""
import json
import re

IMPLEMENTATION, TEST, AMBIGUOUS = 'implementation', 'test', 'ambiguous'
COMPILE, SETUP, ASSERTION, FORMAT = 'compile', 'setup', 'assertion', 'format'

# 带列号的是编译器/vet 诊断；只带行号的是测试运行时的断言输出。 With a column = compiler/vet; line only = a runtime assertion.
_LOCATED = re.compile(r'^\s*(?:\./)?([\w./\\:-]*?[\w-]+\.go):(\d+)(?::(\d+))?:?\s*(.*)$')
_SETUP_MARKERS = ('import cycle not allowed in test', 'found packages', 'no Go files in', 'cannot find package', 'is a program, not an importable package',
                  'use of internal package', 'no required module provides package')


def _lines(text):
    for raw in str(text or '').splitlines():
        try:
            frame = json.loads(raw)
            raw = frame.get('Output', '') if isinstance(frame, dict) else raw
        except (ValueError, TypeError):
            pass
        for line in str(raw).splitlines():
            if line.strip():
                yield line.rstrip()


def _owner(path):
    return TEST if path.replace('\\', '/').endswith('_test.go') else IMPLEMENTATION


def analyse(checks) -> dict:
    """checks = [(operation, result)]。返回逐条诊断 + 汇总。无模型、无 I/O。 Per-item diagnostics plus a summary; pure."""
    items, seen = [], set()

    def add(owner, kind, path, line, message):
        key = (owner, kind, path, message[:80])
        if key not in seen and len(items) < 40:
            seen.add(key)
            items.append({'owner': owner, 'kind': kind, 'path': path, 'line': line, 'message': message[:240]})

    for operation, result in checks:
        if result.get('exit_code') == 0 and result.get('status') == 'completed':
            continue
        if operation == 'go_fmt_check':
            for line in _lines(result.get('stdout')):
                path = line.strip().replace('\\', '/')
                if path.endswith('.go'):
                    add(_owner(path), FORMAT, path, None, 'gofmt 格式不合格')
            continue
        for line in _lines(str(result.get('stdout', '')) + '\n' + str(result.get('stderr', ''))):
            located = _LOCATED.match(line)
            if located:
                path = located.group(1).replace('\\', '/')
                col, message = located.group(3), located.group(4).strip()
                if message.startswith('vet.exe:') or message.startswith('# '):
                    message = message.split(':', 1)[-1].strip()
                if any(m in message for m in _SETUP_MARKERS):
                    add(_owner(path), SETUP, path, int(located.group(2)), message)
                elif col is not None:
                    add(_owner(path), COMPILE, path, int(located.group(2)), message)
                elif _owner(path) == TEST:
                    # 测试运行时的断言：可能是测试期望写错，也可能是实现行为错——归属不明。
                    # A runtime assertion: the expectation may be wrong or the behaviour may be: ambiguous.
                    add(AMBIGUOUS, ASSERTION, path, int(located.group(2)), message)
                continue
            if any(m in line for m in _SETUP_MARKERS):
                # 测试包准备错误（如 import cycle）发生在测试一侧。 Test-package setup problems belong to the test side.
                add(TEST, SETUP, '', None, line.strip())
    return {'items': items, **_summary(items)}


def _summary(items):
    impl = [i for i in items if i['owner'] == IMPLEMENTATION and i['kind'] in (COMPILE, SETUP)]
    test = [i for i in items if i['owner'] == TEST and i['kind'] in (COMPILE, SETUP)]
    assertions = [i for i in items if i['kind'] == ASSERTION]
    if impl:
        primary, why = IMPLEMENTATION, '实现里有编译/语法错误：它们无歧义，并且会挡住所有检查，先修实现。'
    elif test:
        primary, why = TEST, '测试里有编译/准备错误（例如测试 import 了自己的包）：修订测试。'
    elif assertions:
        primary, why = AMBIGUOUS, '只有断言失败：可能是测试期望写错，也可能是实现行为不对，需要对照规格判断。'
    else:
        primary, why = AMBIGUOUS, '没有识别到明确的归属。'
    return {'primary': primary, 'why': why, 'implementation_blocking': bool(impl), 'test_blocking': bool(test),
            'assertion_count': len(assertions)}


def _fmt(items, limit=8):
    return '\n'.join(f"- {i['path'] or '(测试包)'}{':' + str(i['line']) if i['line'] else ''}: {i['message']}" for i in items[:limit])


# 常见测试侧问题的具体做法：本地小模型往往看不懂诊断，直接告诉它怎么改。
# Concrete advice for common test-side problems: small local models often cannot read the diagnostic.
_TEST_ADVICE = (
    ('import cycle', '测试文件与被测代码在同一个包里时，不能 import 这个包本身。删掉对自己包的 import，直接调用同包的函数；'
                     '如果必须从外部测试，把测试文件的包名改成 <包名>_test 并 import 该包。'),
    ('found packages', '同一目录里的所有 Go 文件必须使用同一个包名（外部测试包 <包名>_test 除外）。把测试文件的 package 改成与实现一致。'),
    ('is a program, not an importable package', '不能 import package main。把逻辑放进 internal/<name> 包，main 只做参数解析并调用它。'),
    ('no Go files in', '测试里构建命令时，目标目录必须包含 Go 文件：从测试所在目录构建 "."，不要构建空目录。'),
)


def hint(analysis: dict, owner: str) -> str:
    """给修复/测试修订调用的聚焦说明：只列属于它的问题，并明确另一侧由别的步骤处理。
    A focused instruction for the fix call: list only the problems it owns and say the other side is handled elsewhere."""
    mine = [i for i in analysis['items'] if i['owner'] == owner and i['kind'] in (COMPILE, SETUP, FORMAT)]
    other = TEST if owner == IMPLEMENTATION else IMPLEMENTATION
    if owner == IMPLEMENTATION:
        text = '【聚焦】只修下列实现文件里的错误（file:line: message）；测试文件是冻结的，位于 _test.go 的错误由别的步骤处理，请忽略：\n' + _fmt(mine)
    else:
        text = '【聚焦】只修订下列测试文件里的错误（file:line: message）；实现文件的错误由别的步骤处理，不要改实现：\n' + _fmt(mine)
        for marker, advice in _TEST_ADVICE:
            if any(marker in i['message'] for i in analysis['items']):
                text += '\n【做法】' + advice
    if not mine:
        text = ''
    if any(i['owner'] == other and i['kind'] in (COMPILE, SETUP) for i in analysis['items']):
        text += f"\n（另一侧也有问题，会在之后的步骤处理。）" if text else ''
    return text


def describe(analysis: dict) -> list[str]:
    """给人看的归属摘要（一行一条）。 Human-readable ownership summary."""
    names = {IMPLEMENTATION: '实现', TEST: '测试', AMBIGUOUS: '待判断'}
    kinds = {COMPILE: '编译/语法错误', SETUP: '测试准备错误', ASSERTION: '断言失败', FORMAT: '格式'}
    out = [f"{names[i['owner']]} · {kinds[i['kind']]} · {i['path'] or '(测试包)'}{':' + str(i['line']) if i['line'] else ''} · {i['message']}" for i in analysis['items'][:10]]
    return out
