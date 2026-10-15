"""失败证据里的源码片段。Source excerpts attached to failure evidence.

弱模型读不懂长日志，但能照着一小段带行号的源码改（SWE-agent 的 ACI 把“出错位置附近的代码”作为反馈的核心）。
Weak models cannot digest long logs but can edit a small numbered excerpt (SWE-agent's ACI makes "code around the error" the core of its feedback).
"""
import re


def source_snippets(diagnostics, files, radius=4, limit=8, pack=None):
    """为每条 `文件:行[:列]` 诊断附上出错行前后几行源码（带行号，出错行标 >>）。路径只在能唯一匹配到某个文件时才使用。
    For each `file:line[:col]` diagnostic attach numbered source lines around it (failing line marked >>); a path is used only when it matches exactly one file."""
    if not files:
        return []
    if pack is None:
        from masa.languages.registry import DEFAULT_LANGUAGE, registry
        pack = registry.get(DEFAULT_LANGUAGE)
    location = re.compile(pack.location_regex())  # 由语言包给出：`文件:行[:列]` 的写法因语言而异 / the file:line:col form is the language's business
    out, seen = [], set()
    for text in diagnostics:
        for match in location.finditer(text):
            name = pack.relative_path(match.group(1)).replace('\\', '/')
            while name.startswith('./'):
                name = name[2:]
            line = int(match.group(2))
            keys = [k for k in files if k == name or k.endswith('/' + name)]
            if len(keys) != 1 or (keys[0], line) in seen or not isinstance(files[keys[0]], str):
                continue
            seen.add((keys[0], line))
            lines = files[keys[0]].split('\n')
            if not 1 <= line <= len(lines):
                continue
            low, high = max(1, line - radius), min(len(lines), line + radius)
            out.append({'path': keys[0], 'line': line,
                        'source': '\n'.join(f"{'>>' if n == line else '  '} {n:4} | {lines[n - 1][:200]}" for n in range(low, high + 1))})
            if len(out) >= limit:
                return out
    return out
