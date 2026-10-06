"""内置工作流定义与它们的 guard。Built-in workflow definitions and their guards.

fix-v1：一次验证失败之后“下一步做什么”。它取代了协调器里写死的 if/elif 链。
fix-v1: what to do after a failed verification. It replaces the hard-coded if/elif chain in the coordinator.

            ┌───────────── 仅 gofmt 失败 ──────────────► format ──┐
            │  补丁连续没有改善且最强模型试过 ─► rewrite（整体重写）┤
            │                                                      │
  classify ─┼─ 需要诊断 ─► diagnose ─┬ 实现 ─► repair ──────────────┤
            │                        ├ 测试 ─► revise ───────────────┤
            │                        ├ 规格/不明 ─► halt（交给人）    ├─► drafted（有新草稿，去验证）
            ├─ 实现有编译/语法错 ───► repair ───────────────────────┤
            ├─ 测试有编译/准备错 ───► revise ────────────────────────┤
            ├─ 同一断言反复失败 ────► arbitrate ─────────────────────┤
            └─ 其它 ───────────────► repair ─────────────────────────┘
  （可选，策略 conductor 开启）规则有歧义时 classify → conduct：指挥者在候选里选一个（含 skeptic = 测试怀疑者）；不合法则回到规则
  任一修复节点“没有任何改动且已无更强模型” → halt（修复实现例外：先转去修订测试一次）
"""
from masa.application.flow import guard, validate

FIX_V1 = {
    'id': 'fix',
    'version': 1,
    'title': '验证失败后的修复子图',
    'entry': 'classify',
    'nodes': {
        'classify': {'action': 'classify', 'label': '失败分类（归属：实现/测试）', 'kind': 'router'},
        'diagnose': {'action': 'diagnose', 'label': 'Diagnoser 诊断（最高等级）', 'kind': 'role'},
        'format': {'action': 'format_files', 'label': 'gofmt 格式化（无模型）', 'kind': 'tool'},
        'repair': {'action': 'fix_implementation', 'label': '修复实现（测试冻结）', 'kind': 'role'},
        'revise': {'action': 'revise_tests', 'label': '修订测试（实现冻结）', 'kind': 'role'},
        'arbitrate': {'action': 'arbitrate_tests', 'label': '测试↔规格仲裁', 'kind': 'role'},
        'rewrite': {'action': 'rewrite_implementation', 'label': '整体重写实现（最高等级，对着冻结的测试）', 'kind': 'role'},
        # 指挥者（默认关闭，由策略 conductor 开启）：规则有歧义/停滞时，让模型在已声明的候选里选一个；不合法就回到确定性规则。
        # Conductor (off by default, policy `conductor`): on ambiguity or a stall the model picks one DECLARED candidate; an illegal pick falls back to the deterministic rules.
        'conduct': {'action': 'conduct', 'label': '指挥者选择下一步（规则有歧义时）', 'kind': 'llm_choice',
                    'candidates': ['repair', 'revise', 'diagnose', 'rewrite', 'skeptic', 'halt']},
        'skeptic': {'action': 'test_skeptic', 'label': '测试怀疑者核对期望（只读）', 'kind': 'role'},
        'drafted': {'end': 'drafted', 'label': '得到新草稿 → 去验证', 'kind': 'end'},
        'halt': {'end': 'halt', 'label': '停止并交给人', 'kind': 'end'},
    },
    'edges': [
        {'from': 'classify', 'to': 'conduct', 'when': 'conductor_due'},
        {'from': 'classify', 'to': 'format', 'when': 'format_only'},
        {'from': 'classify', 'to': 'diagnose', 'when': 'needs_diagnosis'},
        # 补丁连续没有改善、最强模型也试过：与其继续补一个结构错误的实现，不如整体重写一次。
        # Patching has stalled and the strongest model already tried: rewrite once instead of patching a wrongly structured implementation.
        {'from': 'classify', 'to': 'rewrite', 'when': 'rewrite_due'},
        {'from': 'classify', 'to': 'repair', 'when': 'primary_is', 'params': {'owner': 'implementation'}},
        {'from': 'classify', 'to': 'revise', 'when': 'primary_is', 'params': {'owner': 'test'}},
        {'from': 'classify', 'to': 'arbitrate', 'when': 'arbitrate_due'},
        {'from': 'classify', 'to': 'repair'},
        {'from': 'diagnose', 'to': 'halt', 'when': 'diagnosis_is', 'params': {'owners': ['spec', 'unclear']}},
        {'from': 'diagnose', 'to': 'revise', 'when': 'diagnosis_is', 'params': {'owners': ['test']}},
        {'from': 'diagnose', 'to': 'rewrite', 'when': 'rewrite_due'},
        {'from': 'diagnose', 'to': 'repair'},
        {'from': 'format', 'to': 'drafted'},
        *[{'from': 'conduct', 'to': node, 'when': 'chosen', 'params': {'node': node}} for node in ('repair', 'revise', 'diagnose', 'rewrite', 'skeptic', 'halt')],
        {'from': 'conduct', 'to': 'classify'},  # 指挥者不可用或提议非法：回到确定性规则（conducted 已置位，不会再问）/ unavailable or illegal: back to the rules (conducted is set, so it is not asked again)
        {'from': 'skeptic', 'to': 'revise', 'when': 'skeptic_says_wrong'},
        {'from': 'skeptic', 'to': 'repair'},
        # 修复节点之后：没有改动 → 先让 Diagnoser 看看（若可用）→ 再试（路由器会换更强的模型）→ 仍无改动就停。
        # After a fix node: nothing changed → let the Diagnoser look (if available) → try again (the router escalates) → halt if still nothing.
        # 最强模型反复“不改实现”＝它认为实现没错：错的很可能是测试（例如手算的期望值错了）。转去修订测试一次，而不是直接停下。
        # The strongest model repeatedly leaves the implementation unchanged = it believes the implementation is right: suspect the tests (e.g. a miscalculated expectation) once before halting.
        {'from': 'repair', 'to': 'revise', 'when': 'flip_to_tests'},
        *[edge for node in ('repair', 'revise', 'arbitrate', 'rewrite') for edge in (
            {'from': node, 'to': 'halt', 'when': 'halted'},
            {'from': node, 'to': 'diagnose', 'when': 'noop_diagnose'},
            {'from': node, 'to': node, 'when': 'noop_retry'},
            {'from': node, 'to': 'halt', 'when': 'noop'},
            {'from': node, 'to': 'drafted'})],
    ],
}


@guard('conductor_due')
def _conductor_due(facts, params):
    return bool(facts.get('conductor_due')) and not facts.get('conducted')


@guard('chosen')
def _chosen(facts, params):
    return facts.get('conductor_choice') == params['node']


@guard('skeptic_says_wrong')
def _skeptic_says_wrong(facts, params):
    return (facts.get('skeptic') or {}).get('verdict') == 'tests_wrong'


@guard('only_assertions')
def _only_assertions(facts, params):
    return bool(facts.get('only_assertions'))


@guard('repaired_before_pass')
def _repaired_before_pass(facts, params):
    return bool(facts.get('repaired'))


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


@guard('arbitrate_due')
def _arbitrate_due(facts, params):
    return bool(facts.get('arbitrate_due'))


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
    return bool(facts.get('noop')) and int(facts.get('noop_count', 0)) < 3


@guard('flip_to_tests')
def _flip_to_tests(facts, params):
    if facts.get('flipped'):
        return False
    if facts.get('halted') and facts.get('halt_kind') == 'noop':
        return True
    # 重试次数用尽且 Diagnoser 已看过（或不可用）：同样视为“实现没错”。 Retries exhausted and the Diagnoser already looked (or is unavailable): same conclusion.
    return bool(facts.get('noop')) and int(facts.get('noop_count', 0)) >= 3 and (facts.get('diagnosed') or not facts.get('can_diagnose'))


@guard('halted')
def _halted(facts, params):
    return bool(facts.get('halted'))


# 注意：guard 注册之后才能校验定义；导入本模块即完成校验。 Validate on import, after the guards are registered.
validate(FIX_V1)
