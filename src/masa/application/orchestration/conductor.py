"""指挥者（Conductor）的确定性部分：候选集合、简报、提议校验。LLM 只在歧义或停滞时被问一次；这里的一切都是纯函数。
The deterministic part of the Conductor: candidate set, briefing and validation of the proposal. The LLM is asked only on ambiguity or a stall; everything here is pure.

原则 / Principles (DESIGN_DYNAMIC_ROLES §0):
  · 动态的是“选择”，不是代码：指挥者只能在已注册的节点里选一个，或者 halt。 It picks one registered node; it never writes code.
  · 规则优先：只有一个明显候选时不问模型（零 token）。 Rules first: a single obvious candidate costs zero tokens.
  · 简报由代码从账本生成，不让 LLM 总结，也不给它原始代码或日志。 The briefing is built from the ledger by code; the conductor never sees raw code or logs.
  · 任何不合法的提议都被拒绝、留痕并回退到确定性规则。 Illegal proposals are rejected, recorded, and the deterministic rules take over.
"""
from masa.roles import registry

# 修复子图里的节点 → 对应的角色（注册表里取一句话描述给指挥者看）。 Node of the fix graph -> its role (the registry supplies the one-line description).
NODE_ROLE = {'repair': 'project_repair', 'revise': 'project_test_revision', 'diagnose': 'project_diagnoser', 'skeptic': 'test_skeptic'}
# 没有独立角色的节点用固定说明。 Nodes without a role of their own use fixed text.
NODE_TEXT = {
    'rewrite': '整体重写实现：丢弃现有结构，由最高等级模型对着冻结的测试重新写（只在最强模型已试过补丁后可选）',
    'halt': '停止并交给人（保留在图里供规则使用；指挥者不能选）',
}
MAX_BRIEF = 300
MAX_REASON = 200


def candidate_nodes(flags: dict) -> list[str]:
    """由确定性事实算出“此刻允许选择”的节点。flags 全部来自协调器，不含任何模型输出。
    The nodes that may be chosen right now, from deterministic facts only (no model output)."""
    out = ['repair', 'revise']
    if flags.get('can_diagnose') and not flags.get('diagnosed'):
        out.append('diagnose')
    if flags.get('rewrite_possible'):
        out.append('rewrite')
    if flags.get('skeptic_possible'):
        out.append('skeptic')
    # halt 不再是候选：真实评测里指挥者在第 3 轮选了 halt，任务被它自己终止（wc：规则路径历史上 3/4 通过）。停止由确定性规则（stuck_after、轮数上限、预算）负责，指挥者只能“选路”，不能“放弃”。
    # halt is no longer a candidate: in the real evaluation the conductor halted a task in round 3 (wc passes 3/4 on the rules path). Stopping belongs to the deterministic rules (stuck_after, round cap, budget); the conductor routes, it does not give up.
    return out


def is_ambiguous(flags: dict) -> bool:
    """规则已经试过而没有进展：补丁停滞或同一失败签名重复。否则走快路径（不问模型）。
    The rules were tried without progress: stalled patches or a repeating failure signature. Otherwise the fast path (no model call).
    真实评测（round 1/2）：归属不明（只剩断言失败）几乎每个任务的第一轮都会出现，指挥者在那里的选择和规则一样（diagnose），白花一次 pro 调用，并让任务更早触到单任务 token 上限。
    In the real evaluation, "ownership unclear" shows up in the first round of almost every task, where the conductor picked what the rules pick (diagnose): a wasted pro call that also hit the per-task token cap sooner."""
    return int(flags.get('stall', 0)) >= 1 or int(flags.get('repeats', 0)) >= 2


def due(policy: dict, flags: dict, calls: int, conducted: bool, nodes: list[str]) -> bool:
    """这一次要不要问指挥者：策略开启、不是纯格式问题、没问过、没超次数、确实有多个候选且存在歧义。
    Whether to ask the conductor now: enabled, not a pure format problem, not asked yet, within the call cap, several candidates and real ambiguity."""
    return bool(policy.get('conductor') and not conducted and not flags.get('format_only')
                and calls < int(policy.get('conductor_max_calls', 0)) and len(nodes) >= 2 and is_ambiguous(flags))


def describe_nodes(nodes: list[str]) -> list[dict]:
    out = []
    for node in nodes:
        spec = registry.get(NODE_ROLE.get(node))
        out.append({'id': node, 'description': NODE_TEXT.get(node) or (spec.description if spec else node)})
    return out


def briefing(*, goal, acceptance, ownership_lines, attempt_summary, flags, nodes, spend, budget, decisions, round_no) -> dict:
    """给指挥者的简报：约 1.5–3k token，全部由代码从账本截取。 The briefing: about 1.5-3k tokens, all cut from the ledger by code."""
    left = {}
    if budget.get('max_model_calls') is not None:
        left['model_calls'] = max(0, budget['max_model_calls'] - int(spend.get('calls', 0)))
    if budget.get('max_cloud_tokens') is not None:
        left['cloud_tokens'] = max(0, budget['max_cloud_tokens'] - int(spend.get('cloud_tokens', 0)))
    return {
        'goal': str(goal)[:500],
        'acceptance': [str(a)[:160] for a in list(acceptance)[:8]],
        'failure': [str(line)[:240] for line in list(ownership_lines)[:8]],
        'earlier_rounds': str(attempt_summary or '')[:1200],
        'state': {'round': round_no, 'primary_owner': flags.get('primary'), 'unresolved': flags.get('unresolved'), 'stalled_rounds': int(flags.get('stall', 0)),
                  'same_failure_repeats': int(flags.get('repeats', 0)), 'strongest_model_tried': bool(flags.get('strongest_tried'))},
        'budget_left': left,
        'candidates': describe_nodes(nodes),
        'earlier_decisions': [{'next': d.get('next'), 'reason': str(d.get('reason', ''))[:120]} for d in decisions[-3:]],
    }


def check_choice(raw, nodes: list[str]):
    """校验指挥者的提议。返回 (choice, None) 或 (None, 拒绝原因)。不修改、不“尽量理解”：不合法就拒绝。
    Validate the proposal: (choice, None) or (None, reason). No repair, no guessing: illegal means rejected."""
    if not isinstance(raw, dict) or set(raw) != {'next', 'reason', 'brief'}:
        return None, 'malformed'
    if not all(isinstance(raw[k], str) for k in raw):
        return None, 'malformed'
    if raw['next'] not in nodes:
        return None, f"not_a_candidate:{raw['next'][:40]}"
    # 真实评测里模型写的 reason 超过 200 字被整个拒绝，白白浪费一次调用：选择本身是合法的，只是啰嗦。所以只截断，不拒绝（schema 里 1000 字是防滥用的硬上限）。
    # In the real evaluation a reason over 200 characters made the whole proposal fail although the choice was legal: truncate, do not reject (the schema keeps a hard 1000-character abuse cap).
    return {'next': raw['next'], 'reason': raw['reason'].strip()[:MAX_REASON], 'brief': raw['brief'].strip()[:MAX_BRIEF]}, None


def check_optional_roles():
    """带 when 条件的可选角色，其 guard 必须已注册：模块导入时检查，而不是运行时才发现。
    The guard named by an optional role's `when` must be registered: checked at import, not discovered at run time."""
    from masa.application.orchestration.flow import GUARDS
    for spec in registry.current().all():
        if spec.when and spec.when['guard'] not in GUARDS:
            raise ValueError(f"role {spec.id}: unknown guard {spec.when['guard']!r} in when")
