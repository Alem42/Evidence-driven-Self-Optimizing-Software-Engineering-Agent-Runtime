"""Tester 用例审计：对每个用例，让便宜的模型**只看输入、不看期望**独立推导结果（多个样本），再让一个“等价判断”把推导结果与 Tester 声明的期望比较；多数认为不一致的用例被判为“期望可疑”。
Tester case audit: for every case a cheap model derives the result from the INPUT ONLY (several samples), a judge compares each derivation with the Tester's stated expectation, and a case whose majority says "different" is deemed suspect.

为什么盲推导（真实测量，docs/reference/TEST_RELIABILITY.md）：直接问“这个期望对不对”召回只有 26–33%（被声明的期望锚定）；盲推导再比较召回 91%、误报 8%。
Why blind (measured): asking "is this expectation right?" recalls only 26-33% (anchored by the stated value); blind derivation then comparison recalls 91% with 8% false alarms.

纯逻辑：模型调用由调用方注入（derive / judge），所以没有 I/O，可用假函数测试。
Pure logic: model calls are injected (derive / judge), so there is no I/O and fakes can test it.
"""


def case_views(cases):
    return [{'index': i, 'name': c.get('name', ''), 'input': c.get('input', '')} for i, c in enumerate(cases)]


def audit_cases(cases, derive, judge, samples=3):
    """derive(views) -> {index: 推导出的结果文本}；judge(pairs) -> {index: 是否等价}。任一调用抛异常只算少一票。返回 {index: 'agree'|'disagree'|'abstain'}。
    derive(views) returns {index: derived text}; judge(pairs) returns {index: same?}. A raising call is one vote fewer."""
    views = case_views(cases)
    votes = {i: [] for i in range(len(cases))}  # True = 等价 / same
    for _ in range(samples):
        try:
            derived = derive(views)
            pairs = [{'index': i, 'input': cases[i].get('input', ''), 'stated': cases[i].get('expected', ''), 'derived': derived[i]} for i in range(len(cases)) if i in derived]
            same = judge(pairs) if pairs else {}
        except Exception:
            continue
        for i, flag in same.items():
            votes.setdefault(i, []).append(bool(flag))
    status = {}
    for i, flags in votes.items():
        different = flags.count(False)
        equal = flags.count(True)
        status[i] = 'disagree' if different * 2 > samples else 'agree' if equal * 2 > samples else 'abstain'
    return status


def apply(cases, status, minimum=3):
    """丢弃“多数认为期望可疑”的用例；剩下的不足 minimum 个就一个都不丢（宁可保留，也不让测试变得太薄）。返回 (保留的用例, 被丢弃的下标)。
    Drop the cases a majority finds suspect; if fewer than `minimum` would remain, drop none (a thin test suite is worse)."""
    dropped = [i for i, s in sorted(status.items()) if s == 'disagree']
    kept = [c for i, c in enumerate(cases) if i not in dropped]
    if not dropped or len(kept) < minimum:
        return list(cases), []
    return kept, dropped
