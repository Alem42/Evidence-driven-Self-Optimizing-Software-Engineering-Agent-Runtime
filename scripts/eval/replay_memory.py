"""记忆策略的零 token 回放：在真实账本里的包工人循环上重建“每一步的结果序列”，看各策略会给模型什么、多花多少 token、有没有信息离开窗口后没留痕迹。
Zero-token replay of the memory strategies: rebuild each package-worker loop's result sequence from real ledgers and measure what every strategy would show the model, what it costs and whether anything that left the window left no trace.

方法 / method: 第 s 步的上下文里 last_results 是第 s-3..s-1 步的结果原文；把同一循环里所有调用的 last_results 按步号合并，就得到（几乎）完整的结果序列。
在每个 s≥2 的步骤上用 application/v2/memory.py 计算各策略的视图。只读，不调用任何模型。
Used on step s, last_results holds the verbatim results of steps s-3..s-1, so merging them per loop gives the (almost) complete result sequence; every strategy's view is then computed on each step. Read-only, no model is called.

用法 / usage:  python scripts/eval/replay_memory.py [--glob '.masa/**/runtime.sqlite3'] [--json out.json]
"""
import argparse
import json
import re
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from masa.application.v2 import memory  # noqa: E402
from masa.domain.tokens import estimate_tokens  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402

SKIP = ('old', 'legacy-backup')
SPAN = re.compile(r's(\d+)(?:-(\d+))?')


class Stub:
    verdicts, best = [], None

    def __init__(self, signatures):
        self.signatures = signatures


def loops_of(store):
    """[(run_id, package, [按步号排序的结果记录], {步: 该步的完整上下文})]。 Loops of the ledger with their records and contexts."""
    rows = store.db.execute("select run_id,invocation_id,input_ref,started from role_invocations where purpose='package_worker' order by run_id,started").fetchall()
    current = None
    out = []
    for row in rows:
        try:
            context = store.read(row['input_ref'])
        except Exception:
            continue
        if not isinstance(context, dict) or not isinstance(context.get('step'), int):
            continue
        key = (row['run_id'], context.get('package'))
        if current is None or current['key'] != key or context['step'] == 1:
            current = {'key': key, 'records': {}, 'contexts': {}}
            out.append(current)
        for record in context.get('last_results') or []:
            if isinstance(record, dict) and isinstance(record.get('step'), int):
                current['records'][record['step']] = record
        current['contexts'][context['step']] = context
    return [(c['key'][0], c['key'][1], [c['records'][k] for k in sorted(c['records'])], c['contexts']) for c in out]


def covered(lines, steps):
    """摘要行（含折叠行）覆盖了多少个步号。 How many step numbers the digest lines cover."""
    seen = set()
    for line in lines:
        match = SPAN.match(line)
        if match:
            first = int(match.group(1))
            last = int(match.group(2) or first)
            seen.update(range(first, last + 1))
    return sum(1 for s in steps if s in seen)


def signatures_up_to(records):
    return [re.sub(r'\d+', 'N', str(r.get('result', ''))[:120]) for r in records if str(r.get('result', '')).find('found') >= 0 and not r.get('ok')]


def analyse(paths):
    stats = {name: {'extra': [], 'extra_pct': [], 'covered': 0, 'older': 0} for name in memory.STRATEGIES if name != 'window'}
    gone, gone_pinned, steps_seen, steps_old, loops = 0, 0, 0, 0, 0
    for path in paths:
        try:
            store = Store(path.parent)
        except Exception:
            continue
        try:
            for run_id, package, records, contexts in loops_of(store):
                loops += 1
                by_step = {r['step']: r for r in records}
                for step, context in sorted(contexts.items()):
                    if step < 2:
                        continue
                    history = [by_step[s] for s in range(1, step) if s in by_step]
                    if len(history) <= 3:
                        continue
                    steps_seen += 1
                    base = {k: v for k, v in context.items() if k != 'last_results'}
                    legacy = estimate_tokens(history[-3:])
                    total = estimate_tokens(base) + legacy
                    stub = Stub(signatures_up_to(history))
                    last = memory._last_check(history)
                    if last is not None and not last.get('ok') and last not in history[-3:]:
                        gone += 1
                    for name, strategy in memory.STRATEGIES.items():
                        if name == 'window':
                            continue
                        view = memory.view(strategy, history, stub)
                        extra = estimate_tokens(view) - legacy
                        stats[name]['extra'].append(extra)
                        stats[name]['extra_pct'].append(100.0 * extra / max(total, 1))
                        older = [r['step'] for r in history[:-strategy.keep]]
                        stats[name]['older'] += len(older)
                        stats[name]['covered'] += covered(view.get('progress_digest', []), older)
                        if name == 'digest' and last is not None and not last.get('ok') and last not in history[-3:]:
                            gone_pinned += int('failing_check' in view.get('progress_pins', {}))
                    steps_old += 1
        except sqlite3.OperationalError:
            continue  # 不是账本或旧格式 / not a ledger or an old format
        finally:
            store.close()
    return stats, {'loops': loops, 'steps_with_results_gone': steps_seen, 'latest_failing_check_outside_window': gone, 'of_which_pinned_by_digest': gone_pinned}


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(q * len(values)))] if values else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--glob', default='.masa/**/runtime.sqlite3')
    ap.add_argument('--json')
    args = ap.parse_args()
    paths = [p for p in sorted(ROOT.glob(args.glob)) if not any(part in SKIP for part in p.parts)]
    stats, summary = analyse(paths)
    report = {'ledgers': len(paths), **summary, 'strategies': {}}
    print(f"ledgers {len(paths)} · loops {summary['loops']} · steps where results had left the window {summary['steps_with_results_gone']}")
    print(f"the latest FAILING check had already left the verbatim window on {summary['latest_failing_check_outside_window']} of those steps; "
          f"pinned by `digest` on {summary['of_which_pinned_by_digest']}")
    print(f"{'strategy':<14}{'older results':>14}{'covered':>9}{'extra tokens median':>21}{'p90':>7}{'extra % of prompt median':>26}")
    for name, s in stats.items():
        row = {'older': s['older'], 'covered': s['covered'], 'extra_median': statistics.median(s['extra']) if s['extra'] else 0, 'extra_p90': percentile(s['extra'], 0.9),
               'extra_pct_median': round(statistics.median(s['extra_pct']), 1) if s['extra_pct'] else 0}
        report['strategies'][name] = row
        print(f"{name:<14}{row['older']:>14}{row['covered']:>9}{row['extra_median']:>21}{row['extra_p90']:>7}{row['extra_pct_median']:>25}%")
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
