"""尝试摘要：把前几轮修复“试过什么、结果如何”变成几行事实，喂给下一轮修复。
Attempt summary: turn "what earlier repair rounds tried and what happened" into a few lines of facts for the next round.

做法来源：Reflexion（Shinn 等，2023）把失败的语言化反思放进下一次尝试的上下文；这里的区别是事实全部来自账本
（改了哪些文件、未解决条目从多少变成多少、还剩什么），而不是模型的自述，所以弱模型也不会“反思错”。
Source of the idea: Reflexion (Shinn et al., 2023) keeps verbal reflections on past failures in the next attempt's context; here every fact comes from the ledger
(files changed, unresolved count before/after, what remains) instead of the model's self-report, so a weak model cannot reflect wrongly.
"""


def close_attempt(job, analysis):
    """新一轮验证出来后，补全上一次修复的结果：未解决条目 before→after，以及还剩的前三条。
    After the next verification, complete the previous fix entry: unresolved before -> after and the first remaining items."""
    items = analysis['items']
    log = list(job.get('fix_log', []))
    if log and 'after' not in log[-1]:
        log[-1]['after'] = len(items)
        # 连续没有改善的轮数：补丁修不动时的信号（用于“整体重写”）。 Consecutive rounds without improvement: the signal that patching has stalled (drives the whole rewrite).
        before = log[-1].get('before')
        stalled = before is not None and len(items) >= before
        job['stall'] = int(job.get('stall', 0)) + 1 if stalled else 0
        log[-1]['remaining'] = [str(item.get('message') or '')[:120] for item in items[:3]]
        job['fix_log'] = log
    job['unresolved_now'] = len(items)


def summary(job, keep=4):
    """最近几轮的摘要；没有已完成的尝试就返回空串。 Summary of the latest rounds; empty when no attempt has completed."""
    rows = []
    for number, entry in enumerate(job.get('fix_log', []), 1):
        if 'after' not in entry:
            continue
        who = entry.get('model') or f"level {entry.get('level')}"
        changed = ', '.join(entry.get('changed') or []) or 'nothing'
        before = entry.get('before')
        trend = 'no improvement' if before is not None and entry['after'] >= before else 'improved'
        row = f"- round {number} ({entry.get('stage')}, by {who}) changed {changed}: unresolved {'?' if before is None else before} -> {entry['after']} ({trend})"
        if entry.get('remaining'):
            row += '; still failing: ' + ' | '.join(entry['remaining'])
        rows.append(row)
    if not rows:
        return ''
    return 'Previous repair rounds in this task (facts from the ledger; do not repeat an approach that did not help):\n' + '\n'.join(rows[-keep:])
