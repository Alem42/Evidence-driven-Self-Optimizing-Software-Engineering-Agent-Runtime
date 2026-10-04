"""确定性的 import 修复：补上缺失的标准库 import、删掉没用到的 import。不调用任何模型。
Deterministic import fixing: add missing standard-library imports and drop unused ones. No model call.

为什么：弱模型最常见的编译错误就是 `undefined: io` / `"bufio" imported and not used`（真实任务里占了约一半）。
Go 官方的 goimports 做的正是这件事；主流 agent（Aider 的 lint --fix、各种“生成后自动 goimports”）都把这类机器能判定的错误交给工具，而不是再问一次模型。
Why: the most common weak-model compile errors are `undefined: io` and `"bufio" imported and not used` (about half of the real errors we saw).
Go's own goimports does exactly this; mainstream agents (Aider's lint --fix, "run goimports after generation") hand such machine-decidable errors to a tool instead of asking the model again.

保守原则：宁可不改也不改错。有同名局部变量的包不自动添加；带别名/点/下划线的 import 不动；非标准库不动。改动之后编译仍会独立验证。
Conservative: when unsure, do nothing. A package whose name is also used as a bare identifier (a local variable) is never added; aliased/dot/blank imports and non-stdlib imports are left alone. Compilation still verifies independently.
"""
import re

# 常见标准库：包名 → import 路径。rand 有 math/rand 与 crypto/rand 两种，不自动添加。
# Common stdlib: package name -> import path. `rand` is ambiguous (math/rand vs crypto/rand) and is never added automatically.
STD = {
    'fmt': 'fmt', 'os': 'os', 'io': 'io', 'bufio': 'bufio', 'bytes': 'bytes', 'strings': 'strings', 'strconv': 'strconv',
    'sort': 'sort', 'errors': 'errors', 'flag': 'flag', 'math': 'math', 'time': 'time', 'unicode': 'unicode', 'utf8': 'unicode/utf8',
    'regexp': 'regexp', 'csv': 'encoding/csv', 'json': 'encoding/json', 'filepath': 'path/filepath', 'path': 'path', 'sync': 'sync',
    'context': 'context', 'tabwriter': 'text/tabwriter', 'slices': 'slices', 'maps': 'maps', 'log': 'log', 'runtime': 'runtime',
    'reflect': 'reflect', 'testing': 'testing', 'list': 'container/list', 'heap': 'container/heap', 'hex': 'encoding/hex',
}

_IMPORT_LINE = re.compile(r'^\s*(?:(?P<alias>[A-Za-z_.][\w]*)\s+)?"(?P<path>[^"]+)"\s*(?://.*)?$')


def _mask(source):
    """把注释、字符串、字符字面量替换成空格（保留换行），只留下真正的代码，用来判断标识符是否被使用。
    Blank out comments, strings and rune literals (keeping newlines) so only real code remains for identifier checks."""
    out, i, n = [], 0, len(source)
    while i < n:
        c = source[i]
        if source.startswith('//', i):
            while i < n and source[i] != '\n':
                out.append(' ')
                i += 1
        elif source.startswith('/*', i):
            end = source.find('*/', i + 2)
            end = n if end < 0 else end + 2
            out.extend('\n' if ch == '\n' else ' ' for ch in source[i:end])
            i = end
        elif c in '"\'':
            out.append(' ')
            i += 1
            while i < n and source[i] != c and source[i] != '\n':
                if source[i] == '\\':
                    out.append(' ')
                    i += 1
                out.append(' ')
                i += 1
            out.append(' ')
            i += 1
        elif c == '`':
            end = source.find('`', i + 1)
            end = n if end < 0 else end + 1
            out.extend('\n' if ch == '\n' else ' ' for ch in source[i:end])
            i = end
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def _import_region(lines):
    """找到 import 声明所在的行范围：[(start, end, is_block)]。 Line ranges of import declarations."""
    spans, i = [], 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith('import ('):
            j = i + 1
            while j < len(lines) and lines[j].strip() != ')':
                j += 1
            spans.append((i, j, True))
            i = j
        elif re.match(r'import\s+(?:[A-Za-z_.]\w*\s+)?"', stripped):
            spans.append((i, i, False))
        i += 1
    return spans


def fix_imports(source):
    """返回 (新源码, 改动列表)。改动形如 ('add', 'io') / ('remove', 'bufio')。源码没有 package 行时原样返回。
    Return (new source, changes) such as ('add', 'io') / ('remove', 'bufio'); sources without a package clause are returned untouched."""
    if not re.search(r'^\s*package\s+\w+', source, re.M):
        return source, []
    lines = source.split('\n')
    code = _mask(source)
    spans = _import_region(lines)
    import_rows = {k for a, b, _ in spans for k in range(a, b + 1)}
    body = '\n'.join(('' if k in import_rows else line) for k, line in enumerate(code.split('\n')))
    qualified = set(re.findall(r'(?<![\w.])([A-Za-z_]\w*)\s*\.\s*[A-Za-z_]', body))
    bare = set(re.findall(r'(?<![\w.])([A-Za-z_]\w*)(?!\s*\.\s*[A-Za-z_])\b', body))

    changes, imported = [], {}
    remove_rows = set()
    for a, b, block in spans:
        for k in range(a, b + 1):
            text = lines[k]
            if block and k in (a, b):
                continue
            match = _IMPORT_LINE.match(text.replace('import', '', 1).strip() if not block else text)
            if not match:
                continue
            path, alias = match.group('path'), match.group('alias')
            if alias or '.' in path.split('/')[0]:
                continue  # 别名/点/下划线与非标准库：不动 / aliased, dot, blank or non-stdlib: leave alone
            name = path.rsplit('/', 1)[-1]
            imported[name] = path
            if name not in qualified:
                remove_rows.add(k)
                changes.append(('remove', path))

    missing = []
    for name in sorted(qualified):
        path = STD.get(name)
        if path and name not in imported and path not in imported.values() and name not in bare:
            missing.append(path)
            changes.append(('add', path))
    if not changes:
        return source, []

    # 删除没用的 import 行；块里删空了就整块去掉。 Drop unused rows; remove a block that became empty.
    kept = [line for k, line in enumerate(lines) if k not in remove_rows]
    text = '\n'.join(kept)
    text = re.sub(r'import \(\s*\)\n?', '', text)
    if missing:
        block = re.search(r'import \(\n', text)
        if block:
            insert = ''.join(f'\t"{p}"\n' for p in missing)
            text = text[:block.end()] + insert + text[block.end():]
        else:
            single = re.search(r'^import\s+(?:[A-Za-z_.]\w*\s+)?"[^"]+"[ \t]*$', text, re.M)
            if single:
                # 单行 import 换成块 / turn a single-line import into a block
                old = single.group(0).strip()[len('import'):].strip()
                insert = ''.join(f'\t"{p}"\n' for p in missing)
                text = text[:single.start()] + 'import (\n\t' + old + '\n' + insert + ')' + text[single.end():]
            else:
                package = re.search(r'^\s*package\s+\w+[^\n]*\n', text, re.M)
                insert = '\nimport (\n' + ''.join(f'\t"{p}"\n' for p in missing) + ')\n'
                text = text[:package.end()] + insert + text[package.end():]
    return text, changes


def fix_module_imports(source, module, dirs):
    """把写错 module 前缀的“本项目内”import 改回批准的 module 路径，例如
    `github.com/example.com/expense/internal/expense` → `example.com/task/internal/expense`（真实任务里弱模型这样编过）。
    只在 import 路径以某个已知包目录结尾、且恰好匹配一个目录时才改；已经是正确前缀的、标准库的都不动。
    Rewrite a wrongly prefixed import of a package of THIS project back to the approved module path (a weak model invented
    `github.com/example.com/expense/internal/expense` in a real task). Only when the path ends with exactly one known package directory."""
    if not module or not dirs:
        return source, []
    lines = source.split(chr(10))
    changes = []
    for start, end, block in _import_region(lines):
        for k in range(start, end + 1):
            if block and k in (start, end):
                continue
            text = lines[k]
            match = re.search(r'"([^"]+)"', text)
            if not match:
                continue
            path = match.group(1)
            if path == module or path.startswith(module + '/') or '.' not in path.split('/')[0]:
                continue
            hits = [d for d in dirs if path == d or path.endswith('/' + d)]
            if len(hits) == 1:
                lines[k] = text.replace('"' + path + '"', '"' + module + '/' + hits[0] + '"', 1)
                changes.append(('module', path + ' -> ' + module + '/' + hits[0]))
    return (chr(10).join(lines), changes) if changes else (source, [])
