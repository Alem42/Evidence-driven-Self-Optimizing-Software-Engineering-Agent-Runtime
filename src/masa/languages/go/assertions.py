"""Go 的断言消息解析（给断言裁判）。 Parsing Go assertion messages (for the assertion referee)."""
import re

# Go 测试里最常见的失败行：“<表达式> = <实际>, want <期望>”、“got X, want Y”。 The common Go failure lines.
_CALL = re.compile(r'^(?P<call>.+?) = (?P<got>.+?),? want (?P<want>.+)$')
_PLAIN = re.compile(r'^(?:\w+:\s*)?got:?\s+(?P<got>.+?),?\s+want:?\s+(?P<want>.+)$', re.I)
_SUBSTANCE = re.compile(r'[\w"`]|' + "'")  # 至少有一个字母数字或引号，排除逗号之类的残片 / at least one alphanumeric or quote, to exclude fragments such as a lone comma


def assertion_of(message):
    """{'call', 'got', 'want'} 或 None（解析不了就不裁判，回退到原来的流程）。 or None when it cannot be parsed (no refereeing then, the old flow goes on)."""
    text = str(message).strip()
    m = _CALL.match(text) or _PLAIN.match(text)
    if not m:
        return None
    parts = m.groupdict()
    got, want = parts['got'].strip(), parts['want'].strip()
    if not _SUBSTANCE.search(got) or not _SUBSTANCE.search(want) or got == want or len(got) > 300 or len(want) > 300:
        return None
    return {'call': (parts.get('call') or 'the checked value').strip()[:300], 'got': got, 'want': want}


def source_of_test_function(files, path, line, limit=1500):
    """失败行所在的测试函数源码（给模型看测试的前置数据）；找不到返回空串。 The source of the test function holding the failing line (the setup the model needs); empty when not found."""
    for name, text in files.items():
        if name == path or name.endswith('/' + str(path)):
            lines = text.splitlines()
            if not 1 <= int(line or 0) <= len(lines):
                return ''
            start = int(line) - 1
            while start > 0 and not lines[start].startswith('func '):
                start -= 1
            end = int(line)
            while end < len(lines) and not lines[end].startswith('func '):
                end += 1
            return '\n'.join(lines[start:end])[:limit]
    return ''
