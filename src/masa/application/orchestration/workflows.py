"""内置工作流定义与它们的 guard。Built-in workflow definitions and their guards.

fix：一次验证失败之后“下一步做什么”。它取代了协调器里写死的 if/elif 链。
fix: what to do after a failed verification. It replaces the hard-coded if/elif chain in the coordinator.

            ┌───────────── 仅 gofmt 失败 ──────────────► format ──┐
            │  补丁连续没有改善且最强模型试过 ─► rewrite（整体重写）┤
            │                                                      │
  classify ─┼─ 需要诊断 ─► diagnose ─┬ 实现 ─► repair ──────────────┤
            │      （唯一的裁决入口） ├ 测试/两者 ─► revise ──────────┤
            │                        ├ 规格/不明 ─► halt（交给人）    ├─► drafted（有新草稿，去验证）
            ├─ 实现有编译/语法错 ───► repair ───────────────────────┤
            ├─ 测试有编译/准备错 ───► revise ────────────────────────┤
            └─ 其它 ───────────────► repair ─────────────────────────┘
  修复节点之后：
    · 裁判与“实际值不变”给出独立证据（expectation_suspected）→ revise
    · 修不动（没有任何改动 / 想改冻结的测试）→ 先让 Diagnoser 裁决一次（stalled_unadjudicated）；已裁决过或没有裁决者 → halt（交给人）
    · 只有**没有任何裁决者**（固定单模型、诊断被关）时，才保留旧的“修不动 ⇒ 疑似测试错”捷径（legacy_flip）

设计要点：**“修不动实现”不能证明“测试错”**。修订测试只有三种依据——确定性分类（测试自己编译/准备出错）、Diagnoser 的逐条核对、
裁判多数 + 实际值不变。以前的“仲裁”节点（arbitrate）在默认配置下永远被诊断抢先，已删除：Diagnoser 本来就逐条对照规格。
Design: "the implementation cannot be fixed" does not prove "the test is wrong". A test revision needs evidence: a deterministic classification
(the test itself fails to compile or set up), the Diagnoser's per-case check, or referee majority plus a stable actual value. The old arbitrate node
was always pre-empted by the diagnosis in the default configuration and is gone: the Diagnoser already compares each assertion with the spec.
"""
from masa.application.orchestration.flow import guard, validate

FIX_V2 = {
    'id': 'fix',
    'version': 2,
    'title': '验证失败后的修复子图',
    'entry': 'classify',
    'nodes': {
        'classify': {'action': 'classify', 'label': '失败分类（归属：实现/测试）', 'kind': 'router'},
        'diagnose': {'action': 'diagnose', 'label': 'Diagnoser 裁决（最高等级）', 'kind': 'role'},
        'format': {'action': 'format_files', 'label': '格式化（无模型）', 'kind': 'tool'},
        'repair': {'action': 'fix_implementation', 'label': '修复实现（测试冻结）', 'kind': 'role'},
        'revise': {'action': 'revise_tests', 'label': '修订测试（实现冻结）', 'kind': 'role'},
        'rewrite': {'action': 'rewrite_implementation', 'label': '整体重写实现（最高等级，对着冻结的测试）', 'kind': 'role'},
        'drafted': {'end': 'drafted', 'label': '得到新草稿 → 去验证', 'kind': 'end'},
        'halt': {'end': 'halt', 'label': '停止并交给人', 'kind': 'end'},
    },
    'edges': [
        {'from': 'classify', 'to': 'format', 'when': 'format_only'},
        {'from': 'classify', 'to': 'diagnose', 'when': 'needs_diagnosis'},
        # 补丁连续没有改善、最强模型也试过：与其继续补一个结构错误的实现，不如整体重写一次。
        # Patching has stalled and the strongest model already tried: rewrite once instead of patching a wrongly structured implementation.
        {'from': 'classify', 'to': 'rewrite', 'when': 'rewrite_due'},
        {'from': 'classify', 'to': 'repair', 'when': 'primary_is', 'params': {'owner': 'implementation'}},
        {'from': 'classify', 'to': 'revise', 'when': 'primary_is', 'params': {'owner': 'test'}},
        {'from': 'classify', 'to': 'repair'},
        {'from': 'diagnose', 'to': 'halt', 'when': 'diagnosis_is', 'params': {'owners': ['spec', 'unclear']}},
        # 测试期望有错时先改测试（即使实现也有问题）：否则工人会为迎合错的测试去污染实现（EVIDENCE E4：73% 的 token 花在修一个正确的实现上）。
        # A wrong expectation is corrected first (even when the implementation has defects too): otherwise a worker bends the implementation to satisfy it (E4: 73% of the tokens went into repairing a correct implementation).
        {'from': 'diagnose', 'to': 'revise', 'when': 'diagnosis_is', 'params': {'owners': ['test', 'both']}},
        {'from': 'diagnose', 'to': 'rewrite', 'when': 'rewrite_due'},
        {'from': 'diagnose', 'to': 'repair'},
        {'from': 'format', 'to': 'drafted'},
        # 修复节点之后（见模块说明）/ after a fix node (see the module note).
        *[edge for node in ('repair', 'revise', 'rewrite') for edge in (
            *([{'from': node, 'to': 'revise', 'when': 'expectation_suspected'}] if node != 'revise' else []),
            *([{'from': node, 'to': 'revise', 'when': 'legacy_flip'}] if node == 'repair' else []),
            {'from': node, 'to': 'diagnose', 'when': 'stalled_unadjudicated'},
            {'from': node, 'to': 'halt', 'when': 'halted'},
            {'from': node, 'to': 'diagnose', 'when': 'noop_diagnose'},
            {'from': node, 'to': node, 'when': 'noop_retry'},
            {'from': node, 'to': 'halt', 'when': 'noop'},
            {'from': node, 'to': 'drafted'})],
    ],
}
FIX_V1 = FIX_V2  # 旧名字（接口与测试仍用它）/ the old name (the HTTP route and tests still use it)


@guard('format_only')
def _format_only(facts, params):
    return bool(facts.get('format_only'))


@guard('primary_is')
def _primary_is(facts, params):
    # 诊断过后不再按规则的 primary 走，改由诊断结论决定。 After a diagnosis the diagnosis decides, not the rule-based primary.
    return not facts.get('diagnosed') and facts.get('primary') == params['owner']


@guard('rewrite_due')
def _rewrite_due(facts, params):
    return bool(facts.get('rewrite_due')) and not facts.get('rewritten')


@guard('needs_diagnosis')
def _needs_diagnosis(facts, params):
    return bool(facts.get('needs_diagnosis')) and not facts.get('diagnosed')


@guard('diagnosis_is')
def _diagnosis_is(facts, params):
    return (facts.get('diagnosis') or {}).get('owner') in params['owners']


@guard('noop')
def _noop(facts, params):
    return bool(facts.get('noop'))


@guard('noop_diagnose')
def _noop_diagnose(facts, params):
    return bool(facts.get('noop')) and bool(facts.get('can_diagnose')) and not facts.get('diagnosed')


@guard('noop_retry')
def _noop_retry(facts, params):
    return bool(facts.get('noop')) and int(facts.get('noop_count', 0)) < int(facts.get('noop_limit', 3))


@guard('expectation_suspected')
def _expectation_suspected(facts, params):
    """独立证据（裁判多数 + 实际值不变）说测试期望错了：直接修订测试，并带着裁判的证据。 Independent evidence says the expectation is wrong: revise the tests with that evidence."""
    return bool(facts.get('halted')) and facts.get('halt_kind') == 'expectation_suspected' and not facts.get('flipped')


@guard('stalled_unadjudicated')
def _stalled_unadjudicated(facts, params):
    """修不动（想改冻结的测试 / 每次都原样返回）：先让 Diagnoser 裁决，而不是推断“测试错”。 Stuck: ask the Diagnoser to adjudicate instead of inferring "the test is wrong"."""
    return bool(facts.get('halted')) and facts.get('halt_kind') in ('noop', 'wants_tests') and bool(facts.get('can_diagnose')) and not facts.get('diagnosed')


@guard('legacy_flip')
def _legacy_flip(facts, params):
    """旧捷径：只在**没有任何裁决者**时（固定单模型、诊断被关）保留；有裁决者时“修不动”只能得到裁决或停下。 The old shortcut survives only where there is NO adjudicator at all."""
    if facts.get('adjudicator') or facts.get('flipped'):
        return False
    if facts.get('halted') and facts.get('halt_kind') in ('noop', 'wants_tests'):
        return True
    return bool(facts.get('noop')) and int(facts.get('noop_count', 0)) >= int(facts.get('noop_limit', 3))


@guard('halted')
def _halted(facts, params):
    return bool(facts.get('halted'))


# 注意：guard 注册之后才能校验定义；导入本模块即完成校验。 Validate on import, after the guards are registered.
validate(FIX_V2)
