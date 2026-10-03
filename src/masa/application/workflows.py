"""内置工作流定义与它们的 guard。Built-in workflow definitions and their guards.

fix-v1：一次验证失败之后“下一步做什么”。它取代了协调器里写死的 if/elif 链。
fix-v1: what to do after a failed verification. It replaces the hard-coded if/elif chain in the coordinator.

            ┌───────────── 仅 gofmt 失败 ──────────────► format ──┐
            │                                                      │
  classify ─┼─ 需要诊断 ─► diagnose ─┬ 实现 ─► repair ──────────────┤
            │                        ├ 测试 ─► revise ───────────────┤
            │                        ├ 规格/不明 ─► halt（交给人）    ├─► drafted（有新草稿，去验证）
            ├─ 实现有编译/语法错 ───► repair ───────────────────────┤
            ├─ 测试有编译/准备错 ───► revise ────────────────────────┤
            ├─ 同一断言反复失败 ────► arbitrate ─────────────────────┤
            └─ 其它 ───────────────► repair ─────────────────────────┘
  任一修复节点“没有任何改动且已无更强模型” → halt
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
        'drafted': {'end': 'drafted', 'label': '得到新草稿 → 去验证', 'kind': 'end'},
        'halt': {'end': 'halt', 'label': '停止并交给人', 'kind': 'end'},
    },
    'edges': [
        {'from': 'classify', 'to': 'format', 'when': 'format_only'},
        {'from': 'classify', 'to': 'diagnose', 'when': 'needs_diagnosis'},
        {'from': 'classify', 'to': 'repair', 'when': 'primary_is', 'params': {'owner': 'implementation'}},
        {'from': 'classify', 'to': 'revise', 'when': 'primary_is', 'params': {'owner': 'test'}},
        {'from': 'classify', 'to': 'arbitrate', 'when': 'arbitrate_due'},
        {'from': 'classify', 'to': 'repair'},
        {'from': 'diagnose', 'to': 'halt', 'when': 'diagnosis_is', 'params': {'owners': ['spec', 'unclear']}},
        {'from': 'diagnose', 'to': 'revise', 'when': 'diagnosis_is', 'params': {'owners': ['test']}},
        {'from': 'diagnose', 'to': 'repair'},
        {'from': 'format', 'to': 'drafted'},
        # 修复节点之后：没有改动 → 先让 Diagnoser 看看（若可用）→ 再试（路由器会换更强的模型）→ 仍无改动就停。
        # After a fix node: nothing changed → let the Diagnoser look (if available) → try again (the router escalates) → halt if still nothing.
        *[edge for node in ('repair', 'revise', 'arbitrate') for edge in (
            {'from': node, 'to': 'halt', 'when': 'halted'},
            {'from': node, 'to': 'diagnose', 'when': 'noop_diagnose'},
            {'from': node, 'to': node, 'when': 'noop_retry'},
            {'from': node, 'to': 'halt', 'when': 'noop'},
            {'from': node, 'to': 'drafted'})],
    ],
}


@guard('format_only')
def _format_only(facts, params):
    return bool(facts.get('format_only'))


@guard('primary_is')
def _primary_is(facts, params):
    # 诊断过后不再按规则的 primary 走，改由诊断结论决定。 After a diagnosis the diagnosis decides, not the rule-based primary.
    return not facts.get('diagnosed') and facts.get('primary') == params['owner']


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


@guard('halted')
def _halted(facts, params):
    return bool(facts.get('halted'))


# 注意：guard 注册之后才能校验定义；导入本模块即完成校验。 Validate on import, after the guards are registered.
validate(FIX_V1)
