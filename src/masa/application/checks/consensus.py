"""期望值的“盲推导 + 多数表决 + 机械比较”：审计测试期望是否算错的确定性骨架（模型调用由调用方注入，这里没有任何 I/O）。
Blind derivation + majority vote + mechanical comparison: the deterministic skeleton for auditing whether test expectations are wrong (model calls are injected; nothing here does I/O).

为什么这样设计（真实测量，见 docs/reference/TEST_RELIABILITY.md）/ Why this design (measured):
  · 直接问模型“这个期望对不对”几乎没用：召回只有 26–33%，误报 12–22%——它被声明的期望**锚定**了。
    Asking a model "is this expectation right?" barely works: recall 26-33%, false alarms 12-22%; it is ANCHORED by the stated value.
  · 不给它看声明的期望，让它**独立推导**，再机械地比较：召回 91%，误报 8%（Flash×3 多数表决，每次约 ¥0.0008、约 1 秒，可并行）。
    Derive blindly, compare mechanically: recall 91%, false alarms 8% (Flash x3 majority, about CNY 0.0008 and 1 s per call, parallel).
  · 单次的 Pro 推导（76%）并不比 Flash（78%）准；便宜的多次表决（92%）胜过贵的单次。
    One Pro derivation (76%) is no better than one Flash (78%); a cheap vote (92%) beats one expensive call.
"""
import collections
from concurrent.futures import ThreadPoolExecutor


def canonical(stdout, exit_code):
    """逐行去掉行尾空白并去掉末尾空行（与独立判官相同的宽容度）。 Strip trailing whitespace per line and trailing blank lines (the oracle's tolerance)."""
    lines = [line.rstrip() for line in str(stdout).replace('\r\n', '\n').split('\n')]
    while lines and not lines[-1]:
        lines.pop()
    return ('\n'.join(lines), int(exit_code))


def majority(values, total):
    """严格多数（超过一半）的值；没有则返回 None（弃权）。total = 参与表决的样本数（含失败的样本，失败的样本不投票）。
    The strict-majority value (more than half of `total`), else None (abstain). Failed samples count in `total` but do not vote."""
    votes = collections.Counter(v for v in values if v is not None)
    if not votes:
        return None
    (top, count), = votes.most_common(1)
    return top if count * 2 > total and sum(1 for c in votes.values() if c == count) == 1 else None


def derive_all(derive, cases, samples=3, workers=6):
    """并行取 samples 份独立推导。derive(cases) -> [{'index', 'stdout', 'exit_code'}]；每个线程应使用自己的 provider。失败的样本记为 None。
    Take `samples` independent derivations in parallel; derive(cases) returns answers; each thread should use its own provider. A failed sample is None."""
    def one(_):
        try:
            return {a['index']: canonical(a['stdout'], a['exit_code']) for a in derive(cases)}
        except Exception:  # 一个样本失败只是少一票 / a failed sample is one vote fewer
            return None
    with ThreadPoolExecutor(max_workers=max(1, min(workers, samples))) as pool:
        return list(pool.map(one, range(samples)))


def audit(stated, derivations):
    """把声明的期望与盲推导的多数机械比较。stated = {index: (stdout, exit_code)}；返回 {index: {'status', 'derived', 'agree'}}。
    status: 'agree'（多数与声明一致）/ 'disagree'（多数与声明不同，建议替换或丢弃）/ 'abstain'（没有严格多数，不下结论）。
    Compare each stated expectation with the majority of the blind derivations. status: agree / disagree (replace or drop) / abstain (no strict majority, no verdict)."""
    result = {}
    for index, value in stated.items():
        want = canonical(*value)
        winner = majority([d.get(index) if d else None for d in derivations], len(derivations))
        agree = sum(1 for d in derivations if d and d.get(index) == want)
        result[index] = {'status': 'abstain' if winner is None else 'agree' if winner == want else 'disagree', 'derived': winner, 'agree': agree}
    return result
