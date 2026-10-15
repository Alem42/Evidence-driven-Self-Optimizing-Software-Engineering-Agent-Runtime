"""新任务页的“开跑前估计”：任务大概多难、云端 API 大概要多少 token、大概多少钱。纯函数，没有 I/O（本地模型的调用由 console 注入）。
The pre-run estimate on the New Task page: how hard the task is, how many cloud API tokens it needs and roughly what it costs. Pure functions, no I/O (the local-model call is injected by the console).

这是**建议**，不是承诺 / This is advice, not a promise:
- 难度等级来自本地模型的一句话判断；本地模型不可用时退回“按目标长度的经验公式”（26 个评测任务上目标字数与等级的相关系数 0.67：字数 ≈ -33 + 40×等级）。
  The level comes from the local model's one-line judgement; without it a rule of thumb on the goal's length is used (on the 26 benchmark tasks the length correlates 0.67 with the level: chars ≈ -33 + 40 x level).
- 等级 → token 沿用评测的单任务上限公式 40 000 + 15 000 × 等级（评测里通过的运行都在这个上限以内），所以“标准”档就是“评测认为够用的量”。
  Level -> tokens reuses the benchmark's per-task limit 40,000 + 15,000 x level (the passing benchmark runs stayed within it), so "standard" is "the amount the benchmark considers enough".
- 三档：经济 0.6 倍（一次通过时够用，出问题就可能不够）、标准 1 倍、充足 2.5 倍（给修复轮次留足余量）。
  Three tiers: economy 0.6x (enough when it works first time), standard 1x, rich 2.5x (headroom for repair rounds).
- 价格 = 档位 token × 各云端模型的单价，取最便宜到最贵的区间；输入输出按 85:15 折算（真实账本里约 89:11），不含缓存折扣；没设价格的模型不参与并如实说明。
  Price = tier tokens x each cloud model's unit price, shown as the cheapest-to-dearest range; input and output are blended 85:15 (about 89:11 in real ledgers), cache discounts are ignored, and models without a price are left out and said so.
"""
TIERS = (('economy', '经济', 0.6), ('standard', '标准', 1.0), ('rich', '充足', 2.5))
INPUT_SHARE = 0.85


def tokens_for_level(level):
    return 40_000 + 15_000 * max(1, min(10, int(level)))


def rules_level(goal):
    """没有本地模型时的经验估计：按目标的字数。 The fallback estimate without the local model: by the goal's length."""
    size = len(str(goal or '').strip())
    if size > 450:
        return 10
    return max(1, min(9, round((size + 33) / 40)))


def tier_tokens(level):
    base = tokens_for_level(level)
    return {key: int(round(base * factor / 1000.0)) * 1000 for key, _label, factor in TIERS}


def price_range(tokens, profiles):
    """{'low','high','models','unpriced'}：每个有价格的云端模型的 token × 混合单价；没有价格的数个数。 One entry per priced cloud model, plus how many have no price."""
    rows, unpriced = [], 0
    for profile in profiles:
        pin, pout = profile.get('input_price_per_million'), profile.get('output_price_per_million')
        if not isinstance(pin, (int, float)) or not isinstance(pout, (int, float)):
            unpriced += 1
            continue
        blended = INPUT_SHARE * pin + (1 - INPUT_SHARE) * pout
        rows.append({'model': profile.get('name') or profile.get('model'), 'per_million': round(blended, 3), 'cost': round(tokens * blended / 1_000_000, 4)})
    if not rows:
        return {'low': None, 'high': None, 'models': [], 'unpriced': unpriced}
    costs = [r['cost'] for r in rows]
    return {'low': min(costs), 'high': max(costs), 'models': rows, 'unpriced': unpriced}


def build(goal, rules, model, cloud_profiles, ready=True, source='rules'):
    """合成给页面的结果。rules = 确定性预检（assess 的结果）；model = 本地模型的回答或 None。
    The result for the page. `rules` is the deterministic pre-check (assess's result); `model` is the local model's answer or None."""
    level = int(model['level']) if model else rules_level(goal)
    can, reasons = 'yes', []
    if rules.get('verdict') == 'infeasible':
        can = 'no'
        reasons += [f['reason'] for f in rules.get('findings', []) if f.get('level') == 'infeasible'][:2]
    elif rules.get('verdict') == 'risky':
        can = 'risky'
        reasons += [f['reason'] for f in rules.get('findings', []) if f.get('level') == 'risky'][:2]
    if model:
        if model['fits'] == 'no':
            can = 'no'
        elif model['fits'] == 'risky' and can == 'yes':
            can = 'risky'
        if model.get('reason'):
            reasons.append(model['reason'])
    if not ready:
        can = 'no'
        reasons.insert(0, '所选语言的工具链还没有就绪（设置 → 语言）')
    if not cloud_profiles:
        reasons.append('没有可用的云端模型：只能靠本地模型，成功率会低')
        can = 'risky' if can == 'yes' else can
    tiers = tier_tokens(level)
    return {'source': source, 'level': level, 'files': int(model['files']) if model else None, 'can_handle': can, 'reasons': reasons[:4],
            'tiers': [{'key': key, 'label': label, 'tokens': tiers[key], 'price': price_range(tiers[key], cloud_profiles)} for key, label, _factor in TIERS]}
