"""调优的目标函数：加权通过率 − λ·每次通过的云端 token − μ·假通过率。纯函数。
The tuning objective: weighted pass score - lambda * cloud tokens per pass - mu * false-pass rate. Pure.

单位 / units: 加权通过率是 0–100 分；每 1 万云端 token 扣 λ 分；每 1% 假通过扣 μ 分。
Weighted score is 0-100; every 10k cloud tokens per pass costs lambda points; every 1% of false passes costs mu points.
没有任何通过时，成本按“花掉的全部云端 token”算（花了钱却没有产出）。 With no pass at all, the cost is ALL cloud tokens spent (money spent without output).
"""


def objective(aggregate, *, lam=1.0, mu=1.0):
    o = aggregate['overall']
    if not o['runs']:
        return None
    cost = (o['cloud_tokens_per_pass'] if o['cloud_tokens_per_pass'] is not None else o['cloud_tokens']) / 10_000
    false_pass = 100 * o['false_pass'] / o['runs']
    return round((o['weighted_score'] or 0.0) - lam * cost - mu * false_pass, 2)


def summarize(aggregate):
    """给提议者和报告看的结果摘要（短）。 A short result summary for the proposer and the report."""
    o = aggregate['overall']
    return {'runs': o['runs'], 'passed': o['passed'], 'pass_rate': o['rate'], 'weighted_score': o['weighted_score'], 'false_pass': o['false_pass'],
            'cloud_tokens_per_pass': o['cloud_tokens_per_pass'], 'cloud_tokens': o['cloud_tokens'],
            'rate_by_level': {lv: row['rate'] for lv, row in sorted(aggregate['by_level'].items(), key=lambda item: int(item[0]))},
            'mechanisms': {k: v for k, v in o['mechanisms'].items() if v}}
