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
from masa.languages.base import AMBIGUOUS, ASSERTION, COMPILE, FORMAT, IMPLEMENTATION, SETUP, TEST
from masa.languages.registry import DEFAULT_LANGUAGE, registry


def _pack(pack=None):
    return pack or registry.get(DEFAULT_LANGUAGE)


def analyse(checks, pack=None) -> dict:
    """checks = [(operation, result)]。返回逐条诊断 + 汇总。无模型、无 I/O；原始输出怎么解析由语言包决定。
    Per-item diagnostics plus a summary; pure. How the raw output is parsed is the language pack's business."""
    items = _pack(pack).parse_failures(checks)
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


def hint(analysis: dict, owner: str, pack=None) -> str:
    """给修复/测试修订调用的聚焦说明：只列属于它的问题，并明确另一侧由别的步骤处理。
    A focused instruction for the fix call: list only the problems it owns and say the other side is handled elsewhere."""
    mine = [i for i in analysis['items'] if i['owner'] == owner and i['kind'] in (COMPILE, SETUP, FORMAT)]
    other = TEST if owner == IMPLEMENTATION else IMPLEMENTATION
    if owner == IMPLEMENTATION:
        text = '【聚焦】只修下列实现文件里的错误（file:line: message）；测试文件是冻结的，位于 ' + _pack(pack).layout['test_suffixes'][0] + ' 的错误由别的步骤处理，请忽略：' + chr(10) + _fmt(mine)
    else:
        text = '【聚焦】只修订下列测试文件里的错误（file:line: message）；实现文件的错误由别的步骤处理，不要改实现：\n' + _fmt(mine)
        for marker, advice in _pack(pack).test_advice():
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
