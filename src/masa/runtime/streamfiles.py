"""从“还没写完的 JSON”里增量取出模型正在写的文件。
Incrementally read the files a model is writing out of a JSON answer that is still being generated.

为什么需要：模型的回答是一整段 JSON（`{"files":{"a.txt":"package a\\n..."}}`，或者 V2 工人的 `{"tool":"write_file","path":"a.txt","content":"..."}`）。
前端想让代码在编辑器里**逐字出现**，就得在 JSON 还没闭合时把已经写出的那部分字符串解码出来——转义（`\\n`、`\\"`、`\\uXXXX`）可能正好写到一半。
Why: the answer is one big JSON value. To make code appear character by character in the editor we must decode the part of a string that has been written
while the JSON is still open, with an escape sequence possibly cut in half.

只处理两种形状，其余一律忽略（宁可不显示，也不显示错的）：
Only two shapes are handled; anything else is ignored (better to show nothing than something wrong):
1. `{"files": {"<path>": "<content>", ...}}`  — V1 整包/逐文件/修复/测试修订 / V1 whole bundle, per file, repair, test revision
2. `{"tool": "write_file" | "edit_file", "path": "...", "content" | "new": "..."}` — V2 包工人 / V2 package workers
"""
import re

MAX_TEXT = 262144  # 一次调用最多保留这么多字符（256 KB） / characters kept for one call

_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f', '"': '"', '\\': '\\', '/': '/'}
_HEX = re.compile(r'[0-9a-fA-F]{4}')


def _ws(t, i):
    while i < len(t) and t[i] in ' \t\r\n':
        i += 1
    return i


def _string(t, i):
    """t[i] 是开引号。返回 (已解码的内容, 下一个下标, 是否已闭合)。写到一半的转义序列被丢弃（不会解码成乱码）。
    t[i] is the opening quote. Returns (decoded so far, next index, closed). A half-written escape is dropped, never decoded into garbage."""
    out, i = [], i + 1
    while i < len(t):
        c = t[i]
        if c == '"':
            return _join(out), i + 1, True
        if c != '\\':
            out.append(c)
            i += 1
            continue
        if i + 1 >= len(t):  # 反斜杠是最后一个字符 / the backslash is the last character
            break
        n = t[i + 1]
        if n == 'u':
            digits = t[i + 2:i + 6]
            if len(digits) < 4 or not _HEX.fullmatch(digits):
                break  # \uXXXX 还没写完 / not complete yet
            out.append(chr(int(digits, 16)))
            i += 6
        else:
            out.append(_ESCAPES.get(n, n))
            i += 2
    return _join(out), len(t), False


def _join(parts):
    text = ''.join(parts)
    try:  # 代理对（😀）合成一个字符 / join surrogate pairs
        return text.encode('utf-16', 'surrogatepass').decode('utf-16')
    except UnicodeError:
        return text


def _value(t, i):
    """读一个值：('str', 文本, 是否闭合) / ('obj', 键值对, 是否闭合) / ('raw', None, 是否读完)；返回 (值, 下一个下标)。
    Read one value: ('str', text, closed) / ('obj', pairs, closed) / ('raw', None, done); returns (value, next index)."""
    i = _ws(t, i)
    if i >= len(t):
        return ('raw', None, False), i
    c = t[i]
    if c == '"':
        text, j, closed = _string(t, i)
        return ('str', text, closed), j
    if c == '{':
        pairs, j, closed = _object(t, i)
        return ('obj', pairs, closed), j
    if c == '[':  # 数组整体跳过（带字符串感知的括号配对） / skip arrays with string-aware bracket matching
        depth, j, in_string = 0, i, False
        while j < len(t):
            ch = t[j]
            if in_string:
                if ch == chr(92):  # 反斜杠：跳过被转义的字符 / a backslash: skip the escaped character
                    j += 2
                    continue
                if ch == '"':
                    in_string = False
                j += 1
                continue
            if ch == '"':
                in_string = True
            elif ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    return ('raw', None, True), j + 1
            j += 1
        return ('raw', None, False), len(t)
    j = i
    while j < len(t) and t[j] not in ',}]':
        j += 1
    return ('raw', None, j < len(t)), j


def _object(t, i):
    """t[i] 是 '{'。返回 (键值对列表, 下一个下标, 是否闭合)。 t[i] is '{'. Returns (pairs, next index, closed)."""
    pairs, i = [], i + 1
    while True:
        i = _ws(t, i)
        if i >= len(t):
            return pairs, i, False
        if t[i] == '}':
            return pairs, i + 1, True
        if t[i] == ',':
            i += 1
            continue
        if t[i] != '"':
            return pairs, len(t), False  # 不是合法 JSON：停在这里 / not valid JSON: stop here
        key, i, closed = _string(t, i)
        if not closed:
            return pairs, i, False
        i = _ws(t, i)
        if i >= len(t) or t[i] != ':':
            return pairs, len(t), False
        value, i = _value(t, i + 1)
        pairs.append((key, value))
        if value[0] != 'raw' and not value[2]:
            return pairs, i, False  # 这个值还没写完：后面没有别的了 / this value is still open: nothing follows yet


def extract_files(text):
    """返回 [{path, content, complete, kind}]；kind 是 'write'（整份内容）或 'edit'（只是新片段）。
    Returns [{path, content, complete, kind}]; kind is 'write' (the whole file) or 'edit' (just the new fragment)."""
    start = text.find('{')
    if start < 0:
        return []
    pairs, _, _ = _object(text, start)
    top = {k: v for k, v in pairs}
    out = []
    files = top.get('files')
    if files and files[0] == 'obj':
        for path, value in files[1]:
            if value[0] == 'str' and path:
                out.append({'path': path, 'content': value[1], 'complete': value[2], 'kind': 'write'})
        return out
    tool, path = top.get('tool'), top.get('path')
    if tool and tool[0] == 'str' and path and path[0] == 'str' and path[2] and path[1]:
        if tool[1] == 'write_file' and top.get('content', ('', '', False))[0] == 'str':
            out.append({'path': path[1], 'content': top['content'][1], 'complete': top['content'][2], 'kind': 'write'})
        elif tool[1] == 'edit_file' and top.get('new', ('', '', False))[0] == 'str':
            out.append({'path': path[1], 'content': top['new'][1], 'complete': top['new'][2], 'kind': 'edit'})
    return out
