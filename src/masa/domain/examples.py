"""Planner 的具体示例：输入 → 输出，是整个项目里唯一的“精确期望”来源。 The Planner's concrete examples: input -> output, the single source of exact expected values in a project.

为什么（见 docs/EVIDENCE.md 的 E15）：一次真实运行里，Planner 的 summary 写“原样保留行尾换行符”，验收条款写“最后一行没有换行符时仍作为一行输出”，Tester 与实现者各读到一句，
结果一个期望 `HELLO\\n`、一个输出 `HELLO`。规格里的规则散在三处（summary、验收、模型脑中的约定），没有一个“具体到字节”的地方。示例就是那个地方：每个示例写明参数、标准输入、**逐字节**的标准输出和退出码。
Why: in a real run the Planner's summary said "keep line endings as they are" while an acceptance item said "a last line without a newline is still output as a line"; the Tester and the implementer each read one sentence and one expected `HELLO\\n`
while the other produced `HELLO`. The rules were spread over three places and none was byte-exact. An example is that place: arguments, stdin, the byte-exact stdout and the exit code.

纯数据逻辑，不依赖语言：示例描述的是“程序的外部行为”。 Pure data logic, language-independent: an example describes the program's external behaviour.
"""
from masa.domain.models import MasaError

MIN_EXAMPLES, MAX_EXAMPLES = 3, 8
KEYS = {'name', 'args', 'stdin', 'stdout', 'exit_code'}


def _text(value, limit, what):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value:
        raise MasaError(f'example {what} must be text of at most {limit} characters')
    return value


def validate_examples(raw):
    """校验并返回示例列表；不合法抛 MasaError。 Validate and return the examples; raise MasaError when illegal."""
    if not isinstance(raw, list) or not MIN_EXAMPLES <= len(raw) <= MAX_EXAMPLES:
        raise MasaError(f'provide {MIN_EXAMPLES}..{MAX_EXAMPLES} examples')
    seen, out = set(), []
    for item in raw:
        if not isinstance(item, dict) or set(item) != KEYS:
            raise MasaError('each example needs exactly name, args, stdin, stdout and exit_code')
        name = _text(item['name'], 60, 'name').strip()
        if not name or name.lower() in seen:
            raise MasaError('example names must be non-empty and unique')
        seen.add(name.lower())
        args = item['args']
        if not isinstance(args, list) or len(args) > 6 or any(not isinstance(a, str) or len(a) > 200 or '\x00' in a for a in args):
            raise MasaError('example args must be a list of at most 6 short strings')
        code = item['exit_code']
        if type(code) is not int or not 0 <= code <= 255:
            raise MasaError('example exit_code must be an integer 0..255')
        out.append({'name': name, 'args': list(args), 'stdin': _text(item['stdin'], 400, 'stdin'), 'stdout': _text(item['stdout'], 400, 'stdout'), 'exit_code': code})
    return out


def describe(example):
    """给人和模型看的一行描述。 A one-line description for people and models."""
    args = ' '.join(example['args']) if example['args'] else '(no arguments)'
    return f"args: {args} | stdin: {example['stdin']!r}"


def expected_text(example):
    return f"stdout {example['stdout']!r}; exit code {example['exit_code']}"


def cases_from_examples(examples):
    """示例变成 Tester 用例（固定格式、确定性，不让模型转抄）。 Examples become Tester cases (fixed format, deterministic: no model re-types them)."""
    return [{'name': 'example: ' + e['name'], 'input': describe(e)[:1000], 'expected': expected_text(e)[:1000], 'level': 'cli'} for e in examples]


def merge_into_checks(checks, spec, test_operation):
    """把示例用例放到测试检查的最前面（总数不超过 16，Tester 自己的用例让位）。 Put the example cases first in the test check (16 at most; the Tester's own cases give way)."""
    examples = spec.get('examples') or []
    if not examples:
        return checks
    fixed = cases_from_examples(examples)
    merged = []
    for check in checks:
        if check.get('operation') != test_operation:
            merged.append(check)
            continue
        own = [c for c in check.get('cases', []) if not str(c.get('name', '')).startswith('example: ')]
        merged.append({**check, 'cases': (fixed + own)[:16]})
    return merged


def normalize(text):
    """逐行去掉行尾空白、去掉末尾空行：宽容格式噪声，不宽容内容（与评测判官同一规则）。 Strip trailing whitespace per line and trailing blank lines: tolerant of format noise, not of content (the benchmark oracle's rule)."""
    lines = [line.rstrip() for line in str(text).replace('\r\n', '\n').split('\n')]
    while lines and not lines[-1]:
        lines.pop()
    return '\n'.join(lines)


def compare(example, result):
    """一个示例的真实运行结果是否符合：{'ok', 'reason'}。 Does the real run of one example match: {'ok', 'reason'}."""
    if result.get('status') != 'completed':
        return {'ok': False, 'reason': f"did not complete ({result.get('status')}{': ' + str(result.get('error'))[:80] if result.get('error') else ''})"}
    code = result.get('exit_code')
    if code != example['exit_code']:
        tail = str(result.get('stderr') or '').strip()[-120:]
        return {'ok': False, 'reason': f"exit code {code}, want {example['exit_code']}" + (f'; stderr: {tail}' if tail else '')}
    got, want = normalize(result.get('stdout', '')), normalize(example['stdout'])
    if got != want:
        return {'ok': False, 'reason': f'stdout {got[:100]!r}, want {want[:100]!r}'}
    return {'ok': True, 'reason': ''}
