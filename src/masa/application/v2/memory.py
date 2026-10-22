"""包工人的记忆策略：离开最近几步窗口的结果不再静默丢失——变成可回查的摘要行和钉住项；策略按当前模型的上下文窗口选择。
Memory strategies of the package worker: results that leave the recent-steps window are no longer lost silently - they become recallable digest lines and pinned items, and the strategy is chosen from the current model's context window.

设计见 docs/design/AUTO_COMPACTION.md。要点 / the essentials
  · 账本无损、窗口有损：完整结果在账本里，上下文里放的是确定性（0 token）的压缩视图。 The ledger is lossless, the window is lossy; the compressed view is deterministic and costs no tokens.
  · 只追加：摘要区只在末尾增长，保护服务端的前缀缓存。 Append-only so the server's prefix cache survives.
  · `window` 就是原来的行为（最近 3 步原文），字节不变；其它策略在步数不超过窗口时与它完全相同，所以小任务不受影响。 `window` is the legacy behaviour byte for byte; the other strategies equal it while the steps still fit the window, so small tasks are unaffected.
"""
import re
from dataclasses import dataclass

from masa.domain.models import ContextOverflow
from masa.domain.tokens import estimate_tokens, estimate_tokens_lower

RECALL_TOOL = 'recall_step'
RECALL_NOTE = ('Older tool results are summarised one line each in progress_digest (s<N> is the step number); progress_pins holds what is still true now (the failing checks, referee verdicts). '
               'To read an older result in full reply {"tool":"recall_step","step":N}: it costs one step, so use it only when a digest line is not enough.')


@dataclass(frozen=True)
class Strategy:
    name: str
    keep: int  # 原文保留的最近工具往返数 / recent tool round trips kept verbatim
    result_chars: int  # 每条原文结果的上限 / cap of one verbatim result
    digest_chars: int  # 摘要块的字符预算（0 = 没有摘要）/ character budget of the digest block (0 = no digest)
    line_chars: int  # 一行摘要的宽度 / width of one digest line
    pin_lines: int  # 钉住的失败行数 / failing lines pinned
    recall: bool  # 是否提供 recall_step 工具 / whether recall_step is offered
    title: str = ''


STRATEGIES = {
    'window': Strategy('window', 3, 2800, 0, 0, 0, False, '最近 3 步窗口（原有行为）'),
    'digest_wide': Strategy('digest_wide', 5, 2800, 4000, 200, 12, True, '大窗口：摘要行 + 钉住项 + 回查，原文保留 5 步'),
    'digest': Strategy('digest', 3, 2800, 2400, 160, 10, True, '摘要行 + 钉住项 + 回查，原文保留 3 步'),
    'digest_tight': Strategy('digest_tight', 1, 1200, 1200, 120, 5, True, '小窗口：原文只留 1 步，摘要更短更早折叠'),
}
LOOSE_TO_TIGHT = ('digest_wide', 'digest', 'digest_tight')
CHOICES = ('auto', *STRATEGIES)
WIDE_BUDGET, NORMAL_BUDGET = 60_000, 14_000  # 单步输入预算（token）的分档：一个包工人的典型单步上下文约 5–10k，大项目可到 15k+ / input-budget tiers in tokens: a worker step is typically 5-10k, 15k+ on a big project


def input_budget(window, output_reserve, instruction_tokens, margin=0.1):
    """窗口 − 输出预留 − 系统指令，再留一点余量；窗口未知返回 None。 Window minus the output reserve and the instruction, with a margin; None when the window is unknown."""
    if not window:
        return None
    return max(0, int((window - output_reserve - instruction_tokens) * (1 - margin)))


def start_strategy(name, budget):
    """从哪一档开始试。显式指定的策略原样使用；auto 按预算分档，未知窗口用普通档。 Where to start; an explicit strategy is used as is, auto picks a tier from the budget (unknown window: the normal tier)."""
    if name != 'auto':
        return STRATEGIES[name]
    if budget is None:
        return STRATEGIES['digest']
    return STRATEGIES['digest_wide' if budget >= WIDE_BUDGET else 'digest' if budget >= NORMAL_BUDGET else 'digest_tight']


def tighter(strategy):
    """比当前更紧的一档；已是最紧或不在梯子上返回 None。 The next tighter tier, or None."""
    if strategy.name not in LOOSE_TO_TIGHT:
        return None
    index = LOOSE_TO_TIGHT.index(strategy.name)
    return STRATEGIES[LOOSE_TO_TIGHT[index + 1]] if index + 1 < len(LOOSE_TO_TIGHT) else None


def fit(strategy, budget, build, model='', limit=0):
    """让单步上下文放进预算：先用 strategy，估计（保守）超出就换更紧的；最紧的一档连下界估计都超出才抛 ContextOverflow（交给路由器换候选）。
    build(strategy) 返回这一档下的完整上下文。返回 (strategy, context)。
    Fit one step's context into the budget: try the strategy, step to a tighter one while the (pessimistic) estimate is over; raise ContextOverflow (the router then picks another candidate) only when even the tightest tier is over by the lower-bound estimate."""
    current = strategy
    while True:
        context = build(current)
        if budget is None or estimate_tokens(context) <= budget:
            return current, context
        nxt = tighter(current)
        if nxt is None:
            if estimate_tokens_lower(context) > budget:
                raise ContextOverflow(estimate_tokens_lower(context), limit or budget, model)
            return current, context
        current = nxt


# ───────────── 摘要行 / digest lines ─────────────
_PROBLEMS = re.compile(r'found (\d+) problem')


def _key_of(tool, ok, text, width):
    """一条结果里值得留下的一句话。 The one phrase of a result worth keeping."""
    lines = [line.strip() for line in str(text).split('\n') if line.strip()]
    if not lines:
        return ''
    match = _PROBLEMS.search(lines[0])
    if match:  # 失败的检查：问题数 + 第一条问题 / a failing check: how many problems + the first one
        first = lines[1] if len(lines) > 1 else ''
        return f'{match.group(1)} problem(s): {first}'[:width]
    if 'check passed' in lines[0] or 'package sealed' in lines[0]:
        return lines[0][:width]
    if tool in ('write_file', 'edit_file'):
        return 'local check problems' if any('Quick local check found problems' in line for line in lines) else ''
    return lines[0][:width]


def digest_entry(record, width):
    """(步号, 工具+路径, 状态, 关键句)：连续相同的会被合并。 (step, tool+path, status, key): consecutive equal ones are merged."""
    tool = record.get('tool') or 'reply'
    label = tool + (' ' + str(record['path']) if record.get('path') else '')
    return (record.get('step'), label, 'ok' if record.get('ok') else 'FAIL', _key_of(record.get('tool'), record.get('ok'), record.get('result', ''), width))


def _render(entries, width):
    out = []
    for first, last, label, status, key, count in entries:
        span = f's{first}' if first == last else f's{first}-{last}'
        text = f'{span} {label} {status}' + (f' | {key}' if key else '') + (f' x{count}' if count > 1 else '')
        out.append(text[:width + 24])
    return out


def digest_lines(records, strategy):
    """摘要块：先把连续相同的结果合并，超出预算再把最老的几行折叠成一行计数。 The digest block: merge consecutive equal results, then fold the oldest lines into one counting line while over budget."""
    merged = []
    for step, label, status, key in (digest_entry(r, strategy.line_chars) for r in records):
        if merged and merged[-1][2:5] == [label, status, key]:
            merged[-1][1], merged[-1][5] = step, merged[-1][5] + 1
        else:
            merged.append([step, step, label, status, key, 1])
    lines = _render(merged, strategy.line_chars)
    folded = None
    while sum(len(line) + 1 for line in lines) > strategy.digest_chars and len(merged) > 2:
        group, merged = merged[:3], merged[3:]
        folded = _fold(folded, group)
        lines = [_fold_line(folded)] + _render(merged, strategy.line_chars)
    return lines


def _fold(previous, group):
    first = previous['first'] if previous else group[0][0]
    counts = dict(previous['counts']) if previous else {}
    for _, _, label, status, _, count in group:
        name = label.split(' ')[0] + ('' if status == 'ok' else ' FAIL')
        counts[name] = counts.get(name, 0) + count
    return {'first': first, 'last': group[-1][1], 'counts': counts}


def _fold_line(folded):
    return f"s{folded['first']}-{folded['last']} (folded): " + ', '.join(f'{name} x{count}' for name, count in sorted(folded['counts'].items()))


# ───────────── 钉住项 / pinned items ─────────────
AUTO_CHECK = 'automatic check after your change:'


def _check_text(record):
    """这条结果里检查的那一部分（run_check / finish 的结果，或修复模式里改文件后自动接上的检查）；不是检查返回 None。
    The check part of a result (run_check / finish, or the check automatically appended after an edit in repair mode); None when it is not a check."""
    text = str(record.get('result', ''))
    if AUTO_CHECK in text:
        return text.split(AUTO_CHECK, 1)[1].strip()
    return text if record.get('tool') in ('run_check', 'finish') else None


_KIND = re.compile(r'(?<![A-Za-z])(test|static) check(?![A-Za-z])')
PASSED = ('test check passed', 'static check passed', 'package sealed')
STALE_AFTER_EDITS = 2  # 之后代码又改了这么多次，裁判结论就可能过时 / after this many edits since, a referee verdict may be outdated


def _check_kind(record):
    """这条检查结果属于哪一种检查（test / static）；认不出时当作 test（修复模式里改文件后自动接的就是 test）。 Which kind of check a result belongs to; test when it cannot be told."""
    found = _KIND.search(_check_text(record) or '')
    return found.group(1) if found else 'test'


def _check_failed(record):
    text = _check_text(record) or ''
    if record.get('tool') in ('run_check', 'finish'):
        return not record.get('ok')
    return not text.lstrip().startswith(PASSED)  # 修复模式里改文件后自动接上的检查 / the check appended after an edit in repair mode


def failing_checks(records):
    """每一种检查（test / static）各取最近一次结果，只留下失败的。以前只看“最近一次检查”：一次后到的静态检查通过，会把更早的测试失败从钉住项里挤掉。
    The latest result of EACH kind of check (test / static), keeping only the failing ones. Before, only the very latest check counted: a later passing static check pushed an earlier failing test out of the pins."""
    latest = {}
    for record in reversed(records):
        if _check_text(record) is None:
            continue
        latest.setdefault(_check_kind(record), record)
    return {kind: record for kind, record in latest.items() if _check_failed(record)}


def _edits_after(records, step):
    """这一步之后成功写入了几次文件（检查结果对应的代码已经变了几次）。 How many successful writes happened after this step (how often the code behind a check result changed since)."""
    return sum(1 for r in records if (r.get('step') or 0) > (step or 0) and r.get('tool') in ('write_file', 'edit_file') and r.get('ok'))


def pins_of(workspace, records, strategy):
    """此刻仍然有效、不该随时间淡出的事实：每一种检查最近一次失败的问题清单（带“其后代码又改了几次”）、裁判结论（带年龄）、相同失败的重复次数、修复里最好状态的问题数。
    What is still true now and must not fade with time: the problems of the latest failing result of each kind of check (with how often the code changed since), referee verdicts (with their age), how often the same failure repeated, the best state's problem count in a repair."""
    pins = {}
    failing = failing_checks(records)
    ordered = sorted(failing.items(), key=lambda item: item[1].get('step') or 0, reverse=True)  # 最近的在前 / the most recent first

    def entry(kind, check):
        text = _check_text(check)
        lines = [line.strip()[:strategy.line_chars * 2] for line in text.split(chr(10)) if line.strip()]
        problems = lines[1:1 + strategy.pin_lines] if _PROBLEMS.search(lines[0] if lines else '') else lines[:strategy.pin_lines]
        out = {'step': check.get('step'), 'problems': problems}
        since = _edits_after(records, check.get('step'))
        if since:
            out['code_changed_since'] = since  # 这份清单是改动之前的 / the list predates these edits
        if len(failing) > 1:
            out['check'] = kind
        return out

    if ordered:
        pins['failing_check'] = entry(*ordered[0])
        if len(ordered) > 1:
            pins['also_failing'] = [entry(kind, check) for kind, check in ordered[1:]]
    verdicts = list(getattr(workspace, 'verdicts', []))[-2:]
    ages = list(getattr(workspace, 'verdict_edits', []))[-2:]
    edits_now = getattr(workspace, 'edits', 0)
    shown = []
    for index, verdict in enumerate(verdicts):
        if not verdict:
            continue
        passed_since = edits_now - ages[index] if index < len(ages) else 0
        prefix = f'[given {passed_since} edit(s) ago - may be outdated] ' if passed_since >= STALE_AFTER_EDITS else ''
        shown.append(prefix + str(verdict)[:400])
    if shown:
        pins['referee_verdicts'] = shown
    signatures = list(getattr(workspace, 'signatures', []))
    repeats = 0
    while signatures and signatures[-1] and repeats < len(signatures) and signatures[-1 - repeats] == signatures[-1]:
        repeats += 1
    if repeats >= 2:
        pins['same_failure_repeats'] = repeats
    best = getattr(workspace, 'best', None)
    if best is not None:
        pins['best_problems_so_far'] = best[0]
        if (getattr(workspace, 'best_rank', None) or (False,))[0]:
            pins['best_state_has_blocking_errors'] = True  # 最好的状态也起不来（导入/编译错误）/ even the best state cannot start (import / compile errors)
    return pins


def view(strategy, records, workspace):
    """这一步要放进上下文的记忆字段：{last_results, 以及（有旧结果时）progress_digest / progress_pins / memory_notes / memory}。
    `window` 只返回 last_results，与原来逐字节相同。 The memory fields for this step; `window` returns last_results only, byte for byte as before."""
    recent = records[-strategy.keep:]
    if strategy.name == 'window':
        return {'last_results': recent}
    capped = [dict(r, result=str(r.get('result', ''))[:strategy.result_chars]) if len(str(r.get('result', ''))) > strategy.result_chars else r for r in recent]
    out = {'last_results': capped}
    older = records[:-strategy.keep] if len(records) > strategy.keep else []
    if not older:
        return out  # 还没有旧结果：与 window 完全相同 / nothing has left the window yet: identical to `window`
    out['progress_digest'] = digest_lines(older, strategy)
    out['memory_notes'] = RECALL_NOTE
    pins = pins_of(workspace, records, strategy)
    if pins:
        out['progress_pins'] = pins
    out['memory'] = {'strategy': strategy.name, 'older_steps': len(older), 'recall': strategy.recall}
    return out
