"""Go 的失败解析：把 go test / go vet / gofmt 的原始输出变成语言无关的诊断。 Go failure parsing: raw go test / go vet / gofmt output into language-neutral diagnostics.

原来在 application/checks/ownership.py 与 check_policy.py 里；归属分析的“中立部分”（谁先修、给模型的聚焦说明）仍在核心，只吃这里解析出的诊断。
These lived in application/checks/ownership.py and check_policy.py; the neutral part of ownership analysis (who fixes first, the focused instruction) stays in the core and consumes the diagnostics parsed here.

带列号的是编译器/vet 诊断；只带行号的是测试运行时的断言输出。 With a column = compiler/vet; line only = a runtime assertion.
"""
import json
import re

from masa.languages.base import AMBIGUOUS, ASSERTION, COMPILE, FORMAT, IMPLEMENTATION, SETUP, TEST

_LOCATED = re.compile(r'^\s*(?:\./)?([\w./\\:-]*?[\w-]+\.go):(\d+)(?::(\d+))?:?\s*(.*)$')
_SETUP_MARKERS = ('import cycle not allowed in test', 'found packages', 'no Go files in', 'cannot find package', 'is a program, not an importable package',
                  'use of internal package', 'no required module provides package')

# 常见测试侧问题的具体做法：本地小模型往往看不懂诊断，直接告诉它怎么改。 Concrete advice for common test-side problems: small local models often cannot read the diagnostic.
TEST_ADVICE = (
    ('import cycle', '测试文件与被测代码在同一个包里时，不能 import 这个包本身。删掉对自己包的 import，直接调用同包的函数；'
                     '如果必须从外部测试，把测试文件的包名改成 <包名>_test 并 import 该包。'),
    ('found packages', '同一目录里的所有 Go 文件必须使用同一个包名（外部测试包 <包名>_test 除外）。把测试文件的 package 改成与实现一致。'),
    ('is a program, not an importable package', '不能 import package main。把逻辑放进 internal/<name> 包，main 只做参数解析并调用它。'),
    ('no Go files in', '测试里构建命令时，目标目录必须包含 Go 文件：从测试所在目录构建 "."，不要构建空目录。'),
)


def lines(text):
    """输出的逻辑行：go test -json 的帧取 Output 字段。 Logical output lines: `go test -json` frames contribute their Output field."""
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


def parse_failures(checks, format_operation='go_fmt_check'):
    """checks = [(operation, result)] → 诊断列表（最多 40 条）。无模型、无 I/O。 A diagnostics list (at most 40); pure."""
    items, seen = [], set()

    def add(owner, kind, path, line, message):
        key = (owner, kind, path, message[:80])
        if key not in seen and len(items) < 40:
            seen.add(key)
            items.append({'owner': owner, 'kind': kind, 'path': path, 'line': line, 'message': message[:240]})

    for operation, result in checks:
        if result.get('exit_code') == 0 and result.get('status') == 'completed':
            continue
        if operation == format_operation:
            for line in lines(result.get('stdout')):
                path = line.strip().replace('\\', '/')
                if path.endswith('.go'):
                    add(_owner(path), FORMAT, path, None, 'gofmt 格式不合格')
            continue
        for line in lines(str(result.get('stdout', '')) + '\n' + str(result.get('stderr', ''))):
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
    return items


def needs_test_revision(evidence, checks=(), format_operation='go_fmt_check'):
    """失败证据显示问题在测试源码或测试准备里？按诊断行定位，避免把实现报错误配给另一行测试。 Do the diagnostics point at test source or test setup? Matched line by line, never across unrelated output."""
    out = []
    for line in evidence.splitlines():
        try:
            frame = json.loads(line)
            output = frame.get('Output', '') if isinstance(frame, dict) else line
        except (ValueError, TypeError):
            output = line
        out.extend(str(output).splitlines())
    if 'import cycle not allowed in test' in evidence:
        return True
    markers = ('imported and not used', 'declared and not used', 'undefined:',
               'syntax error', 'missing return', 'overflows int', 'executable file not found in %PATH%',
               'use of internal package', 'no required module provides package', 'is not in std',
               'cannot run executable found relative to current directory')
    if any(re.search(r'_test\.go:\d+:', line) and
           (any(marker in line for marker in markers) or re.search(r'_test\.go:\d+:\d+: expected ', line))
           for line in out):
        return True
    # 编译器类型错误带行和列；普通断言仅带行，不能看到 expected 就修测试。
    # Compiler type diagnostics carry line and column; ordinary assertion text must not trigger test repair.
    type_errors = ('cannot use', 'invalid operation:', 'assignment mismatch:',
                   'too many arguments', 'not enough arguments', 'multiple-value', 'cannot convert')
    if any(re.search(r'_test\.go:\d+:\d+:', line) and any(marker in line for marker in type_errors)
           for line in out):
        return True
    # 测试内构建选错目录属于测试准备错误；不能让 Developer 修改冻结测试。
    # Building an empty package from a test is a test-setup failure, not an implementation repair.
    if any('no Go files in ' in line for line in out) and any(re.search(r'_test\.go:\d+:', line) for line in out):
        return True
    for operation, result in checks:
        if operation != format_operation or result.get('exit_code') == 0:
            continue
        paths = [p.strip().replace('\\', '/') for p in result.get('stdout', '').splitlines() if p.strip()]
        if paths and all(p.endswith('_test.go') for p in paths):
            return True
    return False


def assertion_signature(checks, test_operation='go_test'):
    """失败测试的断言指纹，避免同一矛盾反复消耗修复调用。 Fingerprint failing assertions to stop repeated ineffective repairs."""
    for operation, result in checks:
        if operation != test_operation or result.get('exit_code') == 0:
            continue
        assertions = []
        for line in result.get('stdout', '').splitlines():
            try:
                frame = json.loads(line)
                output = frame.get('Output', '') if isinstance(frame, dict) else line
            except (ValueError, TypeError):
                output = line
            for item in str(output).splitlines():
                if re.search(r'_test\.go:\d+:', item) and re.search(r'\b(?:want|expected|got)\b', item, re.I):
                    assertions.append(re.sub(r'_test\.go:\d+:', '_test.go:', item.strip()))
        if assertions:
            return tuple(sorted(set(assertions)))
    return ()


def failure_signature(checks):
    """失败签名：所有失败检查的诊断去掉文件名、行列号和数字后取集合。同一问题在多轮修复后仍出现，签名就不变。
    A normalized failure signature: diagnostics stripped of paths, positions and numbers; unchanged after several repair rounds means no progress."""
    found = set()
    for operation, result in checks:
        if result.get('exit_code') == 0:
            continue
        text = str(result.get('stdout', '')) + '\n' + str(result.get('stderr', ''))
        for raw in text.splitlines():
            try:
                frame = json.loads(raw)
                raw = frame.get('Output', '') if isinstance(frame, dict) else raw
            except (ValueError, TypeError):
                pass
            for line in str(raw).splitlines():
                match = re.search(r'\.go:\d+(?::\d+)?:\s*(.+)$', line.strip())
                if match:
                    found.add(re.sub(r'\d+', 'N', match.group(1).strip())[:160])
                elif re.match(r'(?:--- FAIL|FAIL\b|panic:)', line.strip()):
                    found.add(re.sub(r'\(?[\d.]+s\)?|\d+', 'N', line.strip())[:120])
    return tuple(sorted(found))[:12]
