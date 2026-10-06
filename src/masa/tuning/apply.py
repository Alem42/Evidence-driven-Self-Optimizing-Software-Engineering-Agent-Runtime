"""把调优出的最优策略写回用户的路由设置（只含策略部分；去掉的边属于“预设”，只能经评测覆盖使用）。
Write the tuned policy into the user's routing settings (the policy part only; dropped edges belong to the preset and are used through benchmark overrides).
"""
import json


def apply_policy(settings, best_path):
    """校验后保存；返回新的策略。不合法就抛 MasaError，不写文件。 Validate, then save; returns the new policy; an illegal one raises and writes nothing."""
    best = json.loads(open(best_path, encoding='utf-8').read())
    delta = best['best']['overrides']['policy']
    saved = settings.routing()
    policy = {**saved['policy'], **{k: v for k, v in delta.items() if not isinstance(v, dict)}}
    for nested in ('attempts_per_level', 'start_level_by_role'):
        policy[nested] = {**saved['policy'].get(nested, {}), **delta.get(nested, {})}
    return settings.save_routing({'policy': policy, 'budget': saved['budget']})['policy']
