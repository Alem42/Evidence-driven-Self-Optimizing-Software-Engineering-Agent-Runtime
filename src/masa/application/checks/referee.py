"""断言裁判（独立证据）：测试失败时，“测试写的期望”与“实现给的结果”哪个对，不让任何一方先入为主——两个值打乱成 A/B，便宜的模型各独立选一次，多数决定。
Assertion referee (independent evidence): when a test fails, which is right, the value the test expects or the value the implementation gave? Neither is presented as "the answer": the two values are shuffled into A/B,
a cheap model picks one independently several times, and the majority decides.

为什么是二选一而不是“这个期望对吗”（真实测量，docs/reference/TEST_RELIABILITY.md）：直接问“这个期望对不对”召回只有 26–33%，被声明的值锚定；
让模型自己面对两个并列的候选，没有哪个带着“这是测试写的/这是实现给的”的标签，锚定被打散。标签的顺序由断言文本的哈希决定，所以同一个失败每次重放都得到同样的顺序。
Why a forced choice instead of "is this expectation right?" (measured): asking that recalls only 26-33%, the model is anchored by the stated value. Facing two side-by-side candidates with no label saying which one
the test wrote and which one the implementation gave, the anchoring is broken. The label order comes from a hash of the assertion text, so replays see the same order.

纯逻辑：模型调用由调用方注入。 Pure logic: model calls are injected.
"""
import collections
import hashlib
import re

# Go 测试里最常见的失败行：“<表达式> = <实际>, want <期望>”、“got X, want Y”。 The common Go failure lines.
_CALL = re.compile(r'^(?P<call>.+?) = (?P<got>.+?),? want (?P<want>.+)$')
_PLAIN = re.compile(r'^(?:\w+:\s*)?got:?\s+(?P<got>.+?),?\s+want:?\s+(?P<want>.+)$', re.I)
_SUBSTANCE = re.compile(r'[\w"`]|' + "'")  # 至少有一个字母数字或引号，排除逗号之类的残片 / at least one alphanumeric or quote, to exclude fragments such as a lone comma


def parse_assertion(message):
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


def case_for(index, item, files):
    """一个失败断言 → 发给裁判的一条用例。item 是 ownership 的条目。 One failing assertion -> one referee case; item is an ownership entry."""
    parsed = parse_assertion(item['message'])
    if not parsed:
        return None
    swap = int(hashlib.sha1(item['message'].encode('utf-8')).hexdigest(), 16) % 2 == 1
    first, second = (parsed['want'], parsed['got']) if swap else (parsed['got'], parsed['want'])
    return {'index': index, 'call': parsed['call'], 'test_source': source_of_test_function(files, item.get('path'), item.get('line')),
            'options': {'A': first, 'B': second}, '_a_is': 'want' if swap else 'got'}


def public(case):
    return {k: v for k, v in case.items() if not k.startswith('_')}


def tally(cases, samples):
    """samples：每份是 {index: 'A'|'B'|'neither'}（失败的样本是 None）。返回 {index: {'verdict', 'votes'}}。
    verdict：'test_wrong'（多数选了实现给的值）/ 'implementation_wrong'（多数选了测试期望的值）/ 'unclear'（没有严格多数或多数选 neither）。
    samples: one {index: 'A'|'B'|'neither'} per sample (None for a failed one). verdict: test_wrong (the majority picked the implementation's value) /
    implementation_wrong (the majority picked the test's value) / unclear (no strict majority, or the majority said neither)."""
    out = {}
    for case in cases:
        votes = collections.Counter()
        for sample in samples:
            choice = (sample or {}).get(case['index'])
            if choice in ('A', 'B'):
                votes['want' if (choice == 'A') == (case['_a_is'] == 'want') else 'got'] += 1
            elif choice == 'neither':
                votes['neither'] += 1
        total = len(samples)
        top = votes.most_common(1)
        winner = top[0][0] if top and top[0][1] * 2 > total and sum(1 for c in votes.values() if c == top[0][1]) == 1 else None
        out[case['index']] = {'verdict': {'got': 'test_wrong', 'want': 'implementation_wrong'}.get(winner, 'unclear'), 'votes': dict(votes)}
    return out


def evidence_text(cases, result):
    """给 Diagnoser 和修复指令的一段话；全部 unclear 时返回空串（不增加 token）。 A paragraph for the Diagnoser and the fix instructions; empty when everything is unclear (no tokens added)."""
    lines = []
    for case in cases:
        verdict = result[case['index']]['verdict']
        if verdict == 'test_wrong':
            lines.append(f"- {case['call']}: independent votes say the value the implementation produced ({_value(case, 'got')}) is correct and the test's expected value ({_value(case, 'want')}) is WRONG")
        elif verdict == 'implementation_wrong':
            lines.append(f"- {case['call']}: independent votes say the test's expected value ({_value(case, 'want')}) is correct and the implementation's result ({_value(case, 'got')}) is WRONG")
    return ('Independent evidence (a blind forced choice between the two values, majority of several samples; weigh it, it is not a proof):\n' + '\n'.join(lines)) if lines else ''


def _value(case, which):
    return case['options']['A'] if case['_a_is'] == which else case['options']['B']
