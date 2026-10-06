"""模型路由与任务级预算：纯函数，无网络、无存储、无副作用。
Model routing and task-level budgets: pure functions with no network, storage or side effects.

策略（本地优先，有界升级）/ Policy (local first, bounded escalation):
  1. 每个失败链从满足角色/上下文/预算的最低等级开始。 Each failure chain starts at the lowest eligible level.
  2. 非最高等级的模型只允许 attempts_per_level 次尝试，失败后升到严格更高的等级。 A non-top level gets a bounded number of attempts.
  3. 最高等级不限次数（由调用方的总轮次上限约束）。 The top level is bounded only by the caller's round cap.
  4. 预算耗尽、无更高等级、升级次数到顶时返回 stop，而不是抛异常。 Exhausted budgets yield a stop decision, never an exception.
"""
from __future__ import annotations

from dataclasses import dataclass

from masa.roles import registry
from masa.domain.tokens import estimate_tokens  # noqa: F401  (re-exported; coefficients are fitted on real calls)

# 上下文准入只为“预期输出”预留窗口：整个 max_output 往往是上限而不是常态（真实修复输出约 1–3k token）。预算预留仍按完整 max_output。
# Context admission reserves room for the EXPECTED output; max_output is usually a ceiling. Budget reservations still use the full max_output.
CONTEXT_OUTPUT_RESERVE = 4096

DEFAULT_POLICY = {
    'attempts_per_level': {'planning': 2, 'generation': 2, 'fix': 1},  # 初次 + 自修；fix 链的“初次”已是前面的生成 / initial + self-repair
    'max_escalations': 2,
    'planner_retries': 2,
    'start_level_by_role': {},  # 例如 {'project_planner': 2}：Planner 从 L2 起步 / e.g. start the Planner at L2
    # 免费本地调用：崩溃遗留或没收到响应时，等服务恢复后作为新尝试重试；付费云调用永远不自动重放。
    # Free local calls: re-attempted after a crash or when no response arrived. Paid cloud calls are never replayed.
    'retry_unknown_local': True,
    'diagnose': True,  # 修复前允许 Diagnoser 诊断 / allow the Diagnoser before fixing
    'diagnose_max': 2,  # 每个任务最多诊断几次 / diagnoses per task
    # 规格与测试决定后面一切（测试写坏了，再多轮“修实现”也无效），且 token 很少：用最高等级；代码实现由低等级起步、逐级上推。
    # Spec and tests decide everything downstream (broken tests make any number of implementation repairs useless) and cost few tokens: use the top level; implementation starts low and escalates.
    'prefer_highest_roles': list(registry.current().top_ids()),
    'stuck_after': 4,  # 同一失败签名连续出现几次（且已用过最高等级）就停下交给人 / stop after this many identical failure signatures
    'triage_model': True,  # 规划前让本地模型复核可行性（只告警，不拦截）/ local feasibility review before planning (warn only)
    # 指挥者（默认关闭）：规则有歧义或补丁停滞时，让模型在候选步骤里选一个；非法提议回退到规则。cascade = 先用低等级，提议非法再升级。
    # Conductor (off by default): on ambiguity or a stall the model picks one candidate step; an illegal proposal falls back to the rules. cascade = start low, escalate on an illegal proposal.
    'conductor': False,
    'conductor_max_calls': 6,
    'conductor_cascade': False,
    'transport_retries': 6,
    'transport_wait_seconds': 900,
}
FIXED_POLICY = {'attempts_per_level': {'planning': 1, 'generation': 1, 'fix': 1}, 'max_escalations': 0, 'planner_retries': 1,
                'retry_unknown_local': False, 'triage_model': False, 'stuck_after': 99, 'diagnose': False, 'diagnose_max': 0, 'prefer_highest_roles': [], 'transport_retries': 0, 'transport_wait_seconds': 0}
DEFAULT_BUDGET = {'max_model_calls': 40, 'max_cloud_tokens': 200_000, 'max_active_seconds': 3600, 'max_cost': None}
BUDGET_KEYS = ('max_model_calls', 'max_cloud_tokens', 'max_active_seconds', 'max_cost')


@dataclass(frozen=True)
class Candidate:
    id: str
    level: int
    priority: int
    model_type: str  # 'local' | 'cloud'
    model: str
    roles: tuple = ()  # 空 = 允许全部角色 / empty = every role
    context_limit: int = 8192
    max_output: int = 4096
    price_in: float | None = None  # 每百万 token / per million tokens
    price_out: float | None = None
    digest: str | None = None


@dataclass(frozen=True)
class Decision:
    action: str  # 'use' | 'stop'
    candidate: str | None = None
    level: int | None = None
    reason: str = ''
    detail: str = ''
    escalated: bool = False


def validate_budget(budget) -> dict:
    """规范化用户预算；缺省取默认，None 表示该项不限。 Normalize a user budget; None disables that limit."""
    from masa.domain.models import MasaError
    out = dict(DEFAULT_BUDGET)
    for key, value in (budget or {}).items():
        if key not in BUDGET_KEYS:
            raise MasaError(f'unknown budget field: {key}')
        if value is None:
            out[key] = None
        elif key == 'max_cost':
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= 100000:
                raise MasaError('max_cost must be a positive number')
            out[key] = float(value)
        elif type(value) is not int or not 0 < value <= 10_000_000:
            raise MasaError(f'{key} must be a positive integer')
        else:
            out[key] = value
    return out


def unlimited_budget() -> dict:
    return {k: None for k in BUDGET_KEYS}


def spend_from_report(report: dict, prices: dict | None = None) -> dict:
    """从任务报告计算已花费与保守预留。未知用量的云调用按其上下文上限预留，不当作 0。
    Compute settled spend plus conservative reservations; unknown-usage cloud calls are charged at their context cap, never zero."""
    prices = prices or {}
    cloud_tokens = 0
    cost = 0.0
    cost_known = True
    reserved_calls = 0
    for call in report.get('calls', []):
        if call.get('kind') != 'cloud':
            continue
        p_in, p_out = prices.get(call.get('model'), (None, None))
        if call.get('total_tokens') is not None:
            cloud_tokens += call['total_tokens']
            if p_in is not None and p_out is not None:
                cost += ((call.get('prompt_tokens') or 0) * p_in + (call.get('completion_tokens') or 0) * p_out) / 1e6
            else:
                cost_known = False
        else:
            # 请求已发出但没有用量：结果未知，仍占预留。 Request sent without usage: still reserved.
            reserve = call.get('reserved_tokens') or 0
            cloud_tokens += reserve
            reserved_calls += 1
            if p_in is not None and p_out is not None:
                cost += reserve * max(p_in, p_out) / 1e6
            else:
                cost_known = False
    totals = report.get('totals', {})
    tool_ms = sum(t.get('duration_ms') or 0 for t in report.get('tools', []) if t.get('kind') == 'tool')
    return {
        'calls': totals.get('calls', 0),
        'cloud_tokens': cloud_tokens,
        'active_seconds': round((totals.get('model_ms', 0) + tool_ms) / 1000, 1),
        'cost': round(cost, 6),
        'cost_known': cost_known,
        'reserved_calls': reserved_calls,
    }


def _blocked(c: Candidate, need_tokens: int, spend: dict, budget: dict) -> str | None:
    """候选被上下文或预算挡住的原因；None 表示可用。 Why a candidate cannot be used now (None = usable)."""
    if need_tokens + min(c.max_output, CONTEXT_OUTPUT_RESERVE) > c.context_limit:
        return 'context'
    limit = budget.get('max_model_calls')
    if limit is not None and spend.get('calls', 0) + 1 > limit:
        return 'budget_calls'
    limit = budget.get('max_active_seconds')
    if limit is not None and spend.get('active_seconds', 0) >= limit:
        return 'budget_time'
    if c.model_type == 'cloud':
        reserve = need_tokens + c.max_output
        limit = budget.get('max_cloud_tokens')
        if limit is not None and spend.get('cloud_tokens', 0) + reserve > limit:
            return 'budget_cloud_tokens'
        limit = budget.get('max_cost')
        if limit is not None:
            if c.price_in is None or c.price_out is None:
                return 'price_unknown'  # 缺价格不能宣称费用上限有保证 / no price, no cost guarantee
            if spend.get('cost', 0) + reserve * max(c.price_in, c.price_out) / 1e6 > limit:
                return 'budget_cost'
    return None


def route(role: str, chain: str, candidates: list[Candidate], history: list[dict], spend: dict, budget: dict,
          policy: dict, need_tokens: int = 0, unavailable: frozenset = frozenset()) -> Decision:
    """选择下一次尝试使用的候选。history = 本失败链此前失败的尝试 [{'candidate','level'}]。
    Pick the candidate for the next attempt; history lists the failed attempts of this chain."""
    allowed = sorted((c for c in candidates if (not c.roles or role in c.roles) and c.id not in unavailable),
                     key=lambda c: (c.level, c.priority, c.id))
    if not allowed:
        return Decision('stop', reason='no_candidate', detail=f'没有可承担 {role} 的可用模型')
    per_level = int(policy.get('attempts_per_level', {}).get(chain.split(':')[0], 1))  # fix:test 与 fix:implementation 共用 fix 的次数 / siblings share the base chain's budget
    top = max(c.level for c in allowed)
    levels = sorted({c.level for c in allowed})
    used_levels = [h['level'] for h in history]
    escalations = len(set(used_levels)) - 1 if used_levels else 0
    current = used_levels[-1] if used_levels else None

    skipped: list[str] = []

    def pick(level_ok, turn: int = 0) -> tuple[Candidate | None, str | None]:
        """在满足 level_ok 的候选里挑第 turn 个可用的（同级轮换）；被挡住的记入 skipped。
        Pick the turn-th usable candidate (same-level siblings take turns); blocked ones are noted."""
        first_block = None
        usable = []
        for c in allowed:
            if not level_ok(c.level):
                continue
            why = _blocked(c, need_tokens, spend, budget)
            if why is None:
                usable.append(c)
                continue
            skipped.append(f'{c.model}（{SKIP_TEXT.get(why, why)}）')
            first_block = first_block or why
        if usable:
            return usable[turn % len(usable)], None
        return None, first_block

    def note() -> str:
        # 被跳过的候选要写进决策，否则看起来像“凭空升级”。 Skipped candidates must be visible in the decision.
        return ('跳过：' + '；'.join(skipped)) if skipped else ''

    if current is None:
        # 某些角色可以从更高等级起步（例如 Planner/Tester 的输出短但影响大）；没有可用的就退回到任意等级。
        # Some roles may start higher (short but decisive outputs); fall back to any level when none is usable.
        start = policy.get('start_level_by_role', {}).get(role)
        if role in policy.get('prefer_highest_roles', ()) and not (role == 'project_conductor' and policy.get('conductor_cascade')):
            start = top  # 诊断者：判断一次，值得用最强的模型 / the diagnoser judges once, so it is worth the strongest model
        c, why = (pick(lambda lv: lv >= start) if start is not None else (None, None))
        if not c:
            skipped.clear() if start is not None else None
            c, why = pick(lambda lv: True)
        if c:
            return Decision('use', c.id, c.level, 'start_lowest_eligible', detail=note())
        return Decision('stop', reason=why or 'no_candidate', detail='没有满足上下文/预算的候选')

    failed_here = sum(1 for lv in used_levels if lv == current)
    # 当前等级还有尝试额度，或已是最高等级：继续使用本级。 Stay on this level while attempts remain, or at the top.
    if current == top or failed_here < per_level:
        # 失败次数作为轮换序号：同级有多个候选时交替使用，换一个模型比重复同一个更可能出现不同结果。
        # The failure count is the rotation turn: siblings alternate, since a different model is likelier to differ.
        c, why = pick(lambda lv: lv == current, turn=failed_here)
        if c:
            return Decision('use', c.id, c.level, 'top_level' if current == top else 'retry_same_level', detail=note())
        if current == top:
            return Decision('stop', reason=why or 'no_candidate', detail='最高等级无法继续')
        # 本级被上下文/预算挡住：直接尝试升级，而不是原地空转。 Blocked on this level: escalate instead of spinning.
    if escalations >= int(policy.get('max_escalations', 0)):
        return Decision('stop', reason='escalation_limit', detail=f'升级次数已达上限 {policy.get("max_escalations", 0)}')
    higher = [lv for lv in levels if lv > current]
    if not higher:
        return Decision('stop', reason='no_higher_level', detail='没有更高等级的模型可用')
    c, why = pick(lambda lv: lv > current)
    if c:
        return Decision('use', c.id, c.level, 'escalate', detail=note(), escalated=True)
    return Decision('stop', reason=why or 'no_higher_level', detail='更高等级的模型被上下文或预算挡住')


STOP_TEXT = {
    'no_candidate': '没有可用的模型候选',
    'no_higher_level': '已用尽所有等级，没有更高等级的模型',
    'escalation_limit': '升级次数已达上限',
    'budget_calls': '任务的模型调用次数预算已用完',
    'budget_time': '任务的运行时间预算已用完',
    'budget_cloud_tokens': 'API token 预算已用完（含对未知用量请求的预留）',
    'budget_cost': '费用预算已用完',
    'price_unknown': '设置了费用预算但候选模型缺少价格，无法保证上限',
    'context': '输入超过所有候选模型的上下文上限',
}

# 单个候选被跳过的原因（与 STOP_TEXT 不同：这里只是“这个模型不行”，不是“整个任务停止”）。
# Why ONE candidate was skipped (unlike STOP_TEXT, which explains why the whole task stops).
SKIP_TEXT = {
    'context': '输入放不进它的上下文窗口',
    'budget_calls': '调用次数预算用完',
    'budget_time': '运行时间预算用完',
    'budget_cloud_tokens': 'API token 预算不够',
    'budget_cost': '费用预算不够',
    'price_unknown': '缺少价格',
}


POLICY_BOUNDS = {'max_escalations': (0, 5), 'planner_retries': (1, 4), 'transport_retries': (0, 20), 'stuck_after': (3, 10), 'diagnose_max': (0, 6), 'conductor_max_calls': (1, 12), 'transport_wait_seconds': (10, 86400)}
CHAINS = ('planning', 'generation', 'fix')
# 路由器的角色表来自 RoleSpec 注册表（按 order）；这个常量只是导入时的快照，校验时用 registry.routable_ids() 取最新的。
# The router's role table comes from the RoleSpec registry (ordered); this constant is an import-time snapshot, validation asks the registry for the live set.
ROLES = registry.current().routable_ids()


def validate_policy(policy) -> dict:
    """规范化用户策略（缺省取默认）。 Normalize a user policy; missing fields take defaults."""
    from masa.domain.models import MasaError
    out = {**DEFAULT_POLICY, 'attempts_per_level': dict(DEFAULT_POLICY['attempts_per_level']), 'start_level_by_role': {}}
    for key, value in (policy or {}).items():
        if key == 'attempts_per_level':
            if not isinstance(value, dict):
                raise MasaError('attempts_per_level must be an object')
            for chain, n in value.items():
                if chain not in CHAINS or type(n) is not int or not 1 <= n <= 5:
                    raise MasaError('attempts_per_level needs planning/generation/fix integers in 1..5')
                out['attempts_per_level'][chain] = n
        elif key == 'start_level_by_role':
            if not isinstance(value, dict):
                raise MasaError('start_level_by_role must be an object')
            for role, level in value.items():
                if role not in registry.current().routable_ids() or type(level) is not int or not 1 <= level <= 100:
                    raise MasaError('start_level_by_role needs known roles and levels 1..100')
                out['start_level_by_role'][role] = level
        elif key == 'prefer_highest_roles':
            if not isinstance(value, list) or any(r not in registry.current().routable_ids() for r in value):
                raise MasaError('prefer_highest_roles needs known roles')
            out[key] = list(value)
        elif key in ('retry_unknown_local', 'triage_model', 'diagnose', 'conductor', 'conductor_cascade'):
            if type(value) is not bool:
                raise MasaError(f'{key} must be true or false')
            out[key] = value
        elif key in POLICY_BOUNDS:
            low, high = POLICY_BOUNDS[key]
            if type(value) is not int or not low <= value <= high:
                raise MasaError(f'{key} must be an integer in {low}..{high}')
            out[key] = value
        else:
            raise MasaError(f'unknown policy field: {key}')
    # 实验性的可选角色（指挥者、怀疑者、审阅者）只在 conductor 打开时才加入“直接用最高等级”的名单；默认策略与之前完全一致。
    # The optional experimental roles join the start-at-top list only when `conductor` is on; the default policy is exactly what it was before.
    if out['conductor']:
        out['prefer_highest_roles'] = list(dict.fromkeys([*out['prefer_highest_roles'], *registry.current().optional_top_ids()]))
    return out
