"""Python 的失败解析：把 unittest / pytest / 静态检查的原始输出变成语言无关的诊断。 Python failure parsing: raw unittest / pytest / static-check output into language-neutral diagnostics.

和 Go 包的 failures.py 一一对应：编译/vet ↔ SyntaxError 与静态检查；测试运行时断言 ↔ AssertionError；测试准备错误 ↔ 测试模块导入失败。
Counterpart of the Go pack's failures.py: compile/vet <-> SyntaxError and the static check; runtime assertions <-> AssertionError; test-setup errors <-> a failing test-module import.

归属规则（与 Go 一致）：实现文件里的语法/运行时错误归实现；测试文件里的语法/导入/名字错误归测试；断言失败归属不明，交给 Diagnoser。
Ownership (same as Go): syntax/runtime errors in implementation files belong to the implementation; syntax/import/name errors in test files to the tests; assertion failures are ambiguous and go to the Diagnoser.
"""
import re

from masa.languages.base import AMBIGUOUS, ASSERTION, COMPILE, IMPLEMENTATION, SETUP, TEST
from masa.languages.python.checks import is_test_path

_FRAME = re.compile(r'^\s*File "(?P<path>.+?)", line (?P<line>\d+)(?:, in (?P<func>.+))?\s*$')
_EXCEPTION = re.compile(r'^(?P<type>[A-Za-z_][\w.]*(?:Error|Exception|Exit|Warning|Failure))\b:?\s*(?P<message>.*)$')
_LINT = re.compile(r'^(?P<path>[^\s:][^:]*\.py):(?P<line>\d+):(?P<col>\d+): (?P<message>.+)$')
_PYTEST_FRAME = re.compile(r'^(?P<path>\S+\.py):(?P<line>\d+): in (?P<func>\S+)\s*$')
_PYTEST_LAST = re.compile(r'^(?P<path>\S+\.py):(?P<line>\d+): (?P<type>\w+(?:Error|Exception))\b:?\s*(?P<message>.*)$')
_WORKSPACE = re.compile(r'workspaces[\\/][0-9a-f]{32}[\\/](?P<rel>.+)$')
_SYNTAX_TYPES = ('SyntaxError', 'IndentationError', 'TabError')
_SETUP_TYPES = ('ImportError', 'ModuleNotFoundError', 'NameError')


def relative(path):
    """把回溯里的绝对路径还原成项目内的相对路径；标准库与第三方（site-packages、lib/python）返回 None。 Make a traceback path project-relative; stdlib and third-party paths give None."""
    text = str(path).replace('\\', '/')
    match = _WORKSPACE.search(text)
    if match:
        return match.group('rel')
    low = text.lower()
    if 'site-packages' in low or '/lib/python' in low or '/lib64/python' in low or '<frozen' in low or '/unittest/' in low or '/_pytest/' in low or '/pluggy/' in low:
        return None
    if re.match(r'^[A-Za-z]:|^/', text):
        # 其它绝对路径：从 tests/ 或第一个包目录之后开始 / other absolute paths: keep from tests/ onwards, else the last two segments
        index = text.find('/tests/')
        if index >= 0:
            return text[index + 1:]
        return '/'.join(text.split('/')[-2:])
    return text[2:] if text.startswith('./') else text


def _owner(path):
    return TEST if is_test_path(path) else IMPLEMENTATION


def _blocks(text):
    """unittest 输出里的每个失败块：(标题, 行列表)。 Each failure block of unittest output: (title, lines)."""
    blocks, current = [], None
    for line in text.splitlines():
        if re.match(r'^={20,}$', line.strip()):
            current = []
            blocks.append(current)
            continue
        if current is not None:
            current.append(line.rstrip())
    return blocks


def details(checks_results, test_op, limit=1400):
    """前几个失败块里“异常行之后”的部分：unittest 的 assertEqual 失败会在异常行之后逐行列出 - 期望 / + 实际 的差异，而异常行本身常把长字符串缩成 `'erro[21 chars]...'`，看不出差在哪里。
    For the first failure blocks: everything from the exception line on. unittest lists the `- expected / + actual` line diff after the exception line, while the exception line itself often abbreviates long strings to `'erro[21 chars]...'`, which hides where they differ."""
    out = []
    for operation, result in checks_results:
        if operation != test_op:
            continue
        text = str(result.get('stderr', '')) + '\n' + str(result.get('stdout', ''))
        for block in _blocks(text):
            lines = [line for line in block if line.strip()]
            if not lines or not re.match(r'^(FAIL|ERROR): ', lines[0]):
                continue
            start = next((n for n, line in enumerate(lines) if _EXCEPTION.match(line.strip())), None)
            if start is None:
                continue
            tail = [line for line in lines[start + 1:] if not re.match(r'^-{20,}$', line.strip()) and not re.match(r'^(Ran \d+ test|FAILED \()', line.strip())]
            out.append(lines[0] + '\n' + lines[start][:200] + '\n' + '\n'.join(tail)[:600])
            if len(out) >= 2:
                break
    return '\n'.join(out)[:limit]


def assertion_diffs(checks_results, test_op, limit=3):
    """失败断言里“实际 vs 期望”真正不同的那几行（unittest 的逐行差异：`- ` 是第一个参数=实际值，`+ ` 是第二个参数=期望值），每个差异块一对 {got, want}。
    多行输出的断言消息会被缩写成 'ok\\n[13 chars]...'，断言裁判看不懂；差异块只留下不同的几行，短而完整。单行的值没有差异块，用断言消息本身。
    The lines that really differ in a failing assertion (unittest's line diff: `- ` is the first argument = the actual value, `+ ` the second = the expected one), one {got, want} pair per hunk. A multi-line comparison is
    abbreviated to 'ok\\n[13 chars]...' in the message which an assertion referee cannot read; the hunks keep only the differing lines, short and complete. A single-line value has no hunk, so the message itself is used."""
    out = []
    for operation, result in checks_results:
        if operation != test_op:
            continue
        text = str(result.get('stderr', '')) + '\n' + str(result.get('stdout', ''))
        for block in _blocks(text):
            if not block or not re.match(r'^FAIL: ', block[0]):
                continue
            lines = block[1:]
            frames, exc_index = [], None
            for index, line in enumerate(lines):
                frame = _FRAME.match(line)
                if frame:
                    rel = relative(frame.group('path'))
                    if rel is not None and is_test_path(rel):
                        frames.append((rel, int(frame.group('line'))))
                elif exc_index is None and _EXCEPTION.match(line) and not line.startswith(' '):
                    exc_index = index
            if exc_index is None or not frames or not lines[exc_index].startswith('AssertionError'):
                continue
            hunks, minus, plus = [], [], []

            def flush():
                if minus or plus:
                    hunks.append(('\n'.join(minus) or '(no line)', '\n'.join(plus) or '(no line)'))
                minus.clear()
                plus.clear()
            for line in lines[exc_index + 1:]:
                if line.startswith('- '):
                    minus.append(line[2:])
                elif line.startswith('+ '):
                    plus.append(line[2:])
                elif line.startswith('? '):
                    continue
                else:
                    flush()
            flush()
            path, line = frames[-1]
            if hunks:
                for got, want in hunks[:2]:
                    out.append({'path': path, 'line': line, 'got': got[:300], 'want': want[:300]})
            else:
                parsed = assertion_of(lines[exc_index])
                if parsed:
                    out.append({'path': path, 'line': line, 'got': parsed['got'], 'want': parsed['want']})
            if len(out) >= limit:
                return out[:limit]
    return out


def _classify(add, frames, exc_type, message, source):
    """frames：项目内的 (相对路径, 行)，最深的在最后。 Project frames (relative path, line), the deepest last."""
    exc_type = exc_type or ''
    short = exc_type.rsplit('.', 1)[-1]
    if not frames:
        add(AMBIGUOUS, ASSERTION, '', None, f'{exc_type}: {message}'.strip(': '))
        return
    path, line = frames[-1]
    if short in _SYNTAX_TYPES:
        add(_owner(path), COMPILE, path, line, f'{short}: {message}')
    elif short == 'AssertionError':
        test_frames = [f for f in frames if is_test_path(f[0])]
        where = test_frames[-1] if test_frames else frames[-1]
        text = f'{source.strip()} -> AssertionError: {message}' if source else f'AssertionError: {message}'
        add(AMBIGUOUS, ASSERTION, where[0], where[1], text.strip())
    elif short in _SETUP_TYPES and is_test_path(path):
        add(TEST, SETUP, path, line, f'{short}: {message}')
    elif is_test_path(path) and short in ('TypeError', 'AttributeError', 'ValueError', 'KeyError', 'IndexError'):
        # 测试调用实现时出错：可能是测试用错了接口，也可能是实现缺了东西——归属不明 / the test called the implementation wrongly, or the implementation lacks something: ambiguous
        add(AMBIGUOUS, ASSERTION, path, line, f'{source.strip()} -> {short}: {message}'.strip() if source else f'{short}: {message}')
    else:
        add(_owner(path), COMPILE, path, line, f'{short}: {message}')


def parse_unittest(text, add):
    for block in _blocks(text):
        if not block or not re.match(r'^(FAIL|ERROR):', block[0]):
            continue
        frames, source, exc_type, message = [], '', None, ''
        lines = block[1:]
        for index, line in enumerate(lines):
            frame = _FRAME.match(line)
            if frame:
                rel = relative(frame.group('path'))
                if rel is not None:
                    frames.append((rel, int(frame.group('line'))))
                    nxt = lines[index + 1] if index + 1 < len(lines) else ''
                    source = nxt.strip() if nxt.startswith('    ') and not nxt.strip().startswith('^') else ''
                continue
            match = _EXCEPTION.match(line)
            if match and not line.startswith(' '):
                exc_type, message = match.group('type'), match.group('message')
        if exc_type is None:
            continue
        # 测试模块导入失败：unittest 把它包装成 ImportError: Failed to import test module，真正的原因在后面的回溯里 / unittest wraps a failing test import; the real cause is in the traceback after it
        _classify(add, frames, exc_type, message, source)


def parse_pytest(text, add):
    block, header = [], None
    sections = []
    for line in text.splitlines():
        if re.match(r'^_{3,} .+ _{3,}$', line.strip()):
            if header is not None:
                sections.append((header, block))
            header, block = line.strip(), []
        elif header is not None:
            block.append(line.rstrip())
    if header is not None:
        sections.append((header, block))
    for _, lines in sections:
        frames, source, errors, exc_type, message = [], '', [], None, ''
        for index, line in enumerate(lines):
            frame = _PYTEST_FRAME.match(line)
            if frame:
                rel = relative(frame.group('path'))
                if rel is not None:
                    frames.append((rel, int(frame.group('line'))))
                    nxt = lines[index + 1] if index + 1 < len(lines) else ''
                    source = nxt.strip() if nxt.startswith('    ') else ''
                continue
            last = _PYTEST_LAST.match(line)
            if last:
                rel = relative(last.group('path'))
                if rel is not None:
                    frames.append((rel, int(last.group('line'))))
                exc_type, message = last.group('type'), last.group('message')
                continue
            if line.startswith('E '):
                errors.append(line[1:].strip())
        if errors and exc_type is None:
            first = errors[0]
            match = _EXCEPTION.match(first)
            if match:
                exc_type, message = match.group('type'), match.group('message')
            elif first.startswith('assert'):
                exc_type, message = 'AssertionError', first
        if exc_type:
            _classify(add, frames, exc_type, message, source)


def parse_lint(text, add):
    for line in text.splitlines():
        match = _LINT.match(line.strip())
        if match:
            path = relative(match.group('path')) or match.group('path')
            add(_owner(path), COMPILE, path, int(match.group('line')), match.group('message'))


def parse_failures(checks, test_operation, static_operation):
    """checks = [(operation, result)] → 诊断列表（最多 40 条）。 A diagnostics list (at most 40)."""
    items, seen = [], set()

    def add(owner, kind, path, line, message):
        key = (owner, kind, path, message[:80])
        if key not in seen and len(items) < 40:
            seen.add(key)
            items.append({'owner': owner, 'kind': kind, 'path': path, 'line': line, 'message': message[:240]})

    for operation, result in checks:
        if result.get('exit_code') == 0 and result.get('status') == 'completed':
            continue
        text = str(result.get('stdout', '')) + '\n' + str(result.get('stderr', ''))
        if operation == static_operation:
            parse_lint(text, add)
        else:
            parse_unittest(text, add)
            parse_pytest(text, add)
    return items


def needs_test_revision(evidence, checks=(), test_operation='py_test', static_operation='py_static'):
    """失败证据显示问题在测试源码或测试准备里？ Do the diagnostics point at test source or test setup?"""
    items = parse_failures(checks, test_operation, static_operation) if checks else []
    if not items:
        pseudo = [(test_operation, {'stdout': evidence, 'stderr': '', 'exit_code': 1, 'status': 'completed'})]
        items = parse_failures(pseudo, test_operation, static_operation)
    return any(i['owner'] == TEST and i['kind'] in (COMPILE, SETUP) for i in items)


def assertion_of(message):
    """{'call','got','want'} 或 None：unittest 的 `first != second`（约定 assertEqual(实际, 期望)）或 pytest 的 `assert a == b`。 {'call','got','want'} or None."""
    text = str(message).strip()
    substance = re.compile(r'[\w"\']')
    match = re.match(r'^(?P<call>.+?) -> AssertionError: (?P<got>.+?) != (?P<want>.+)$', text) or re.match(r'^(?P<call>.+?) -> AssertionError: assert (?P<got>.+?) == (?P<want>.+)$', text) \
        or re.match(r'^AssertionError: (?P<got>.+?) != (?P<want>.+)$', text)
    if not match:
        return None
    parts = match.groupdict()
    got, want = parts['got'].strip(), parts['want'].strip()
    if not substance.search(got) or not substance.search(want) or got == want or len(got) > 300 or len(want) > 300:
        return None
    return {'call': (parts.get('call') or 'the checked value').strip()[:300], 'got': got, 'want': want}


def assertion_signature(checks, test_operation='py_test', static_operation='py_static'):
    """失败断言的指纹（去掉行号），避免同一矛盾反复消耗修复调用。 Fingerprint of failing assertions (line numbers stripped)."""
    items = parse_failures(checks, test_operation, static_operation)
    found = {re.sub(r':\d+', ':N', f"{i['path']}: {i['message']}") for i in items if i['kind'] == ASSERTION}
    return tuple(sorted(found))


def failure_signature(checks, test_operation='py_test', static_operation='py_static'):
    """归一化的失败签名：去掉路径、行号与数字。 A normalised failure signature: paths, positions and numbers stripped."""
    items = parse_failures(checks, test_operation, static_operation)
    found = {re.sub(r'\d+', 'N', f"{i['owner']}/{i['kind']}: {i['message']}")[:160] for i in items}
    return tuple(sorted(found))[:12]


TEST_ADVICE = (
    ('Failed to import test module', '测试模块导入失败：检查测试文件开头的 import（模块名要和规格里的文件一致，不要 import 不存在的名字）。'),
    ('cannot import name', '测试 import 了实现里不存在的名字：只用实现文件里真正声明了的函数/类，名字拼写要一致。'),
    ('No module named', '导入了不存在的模块：只能 import 规格里列出的本项目模块和标准库。'),
    ('is not defined', '测试里用到了未定义的名字：补上 import 或定义，并检查拼写。'),
)
