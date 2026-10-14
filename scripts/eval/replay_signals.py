"""零 token 回放：在已有账本上检验“实际值稳定”信号（S1）——修复工人改了代码，失败断言的**实际值**却一点没变——什么时候触发、会不会误触发。
Zero-token replay: on the existing ledgers, check the "stable actual value" signal (S1) - the repair worker changed code yet the failing assertion's ACTUAL value did not move at all - when it fires and whether it fires wrongly.

不调用任何模型。输入是账本里已经记下的角色输入（每一步工人看到的上一步结果）。标签是弱标签：同一个任务在账本里出现过“修订测试”的版本，说明测试当时有问题。
No model is called. The input is what the ledger already holds (the previous step's result that every worker step saw). The label is weak: a test revision of the same task appears in the ledger, which means the tests had a problem.

规则（S1）/ the rule: 同一条失败断言的实际值在**连续 3 次观察**里完全一样，并且这 3 次观察之间代码被改过**至少 2 次**。Same failing assertion, same actual value in 3 consecutive observations, with at least 2 code changes in between.
（只看 1 次改动就触发会误报：csv_sum 的修复第一次改动先修了别的失败，那条断言的实际值要到第二次改动才变。 Firing after one change is a false alarm: in the csv_sum repair the first change fixed other failures and that assertion only moved with the second.）

用法 / usage:  python scripts/eval/replay_signals.py [状态目录 ...]   默认：.masa 与 .masa/ab2/bench/state 下的每个目录
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from masa.infrastructure.store import Store  # noqa: E402
from masa.languages.registry import registry  # noqa: E402

LINE = re.compile(r'^(?P<path>[^\s:]+):(?P<line>\d+): (?P<message>.+)$')


def observations(text, pack):
    """一次检查结果里每条失败断言的 {不含行号的键: 实际值}。 {key without line number: actual value} for every failing assertion in one check result."""
    found = {}
    for raw in str(text).splitlines():
        match = LINE.match(raw.strip())
        if not match:
            continue
        parsed = pack.assertion_of(match.group('message'))
        if parsed:
            found[(match.group('path'), re.sub(r'\d+', 'N', parsed['call'])[:80], parsed['want'])] = parsed['got']
    return found


def scan_job(store, runs, pack_for):
    """一个任务的全部修复运行（按时间顺序）。返回触发时累计的（工人调用数, 观察数），或 None。 All repair runs of one task in time order; returns (worker calls so far, observations so far) when it fires, or None."""
    history, writes, calls, checks = {}, 0, 0, 0
    for run in runs:
        pack = pack_for(run)
        rows = store.db.execute("select input_ref from role_invocations where run_id=? and purpose='package_worker' order by started", (run['id'],)).fetchall()
        for row in rows:
            calls += 1
            context = store.read(row['input_ref'])
            for result in context.get('last_results', [])[-1:]:
                if result['tool'] in ('write_file', 'edit_file') and result['ok']:
                    writes += 1
                text = result['result']
                if 'problem(s) in your files' not in text and 'automatic check' not in text:
                    continue
                checks += 1
                for key, got in observations(text, pack).items():
                    trail = history.setdefault(key, [])
                    trail.append((got, writes))
                    if len(trail) >= 3 and len({g for g, _ in trail[-3:]}) == 1 and trail[-1][1] - trail[-3][1] >= 2:
                        return calls, checks
    return None


def main(argv):
    roots = [Path(a) for a in argv] or [ROOT / '.masa'] + sorted((ROOT / '.masa/ab2/bench/state').glob('*'))
    rows = []
    for root in roots:
        if not (root / 'runtime.sqlite3').exists():
            continue
        store = Store(root)
        try:
            runs = sorted(store.all_runs(), key=lambda r: r['data'].get('created_at', 0))
            by_goal = {}
            for run in runs:
                by_goal.setdefault(run['data'].get('goal'), []).append(run)
            for goal, group in by_goal.items():
                repairs = [r for r in group if (r['data'].get('project_plan') or {}).get('repair_of') and (r['data'].get('project_plan') or {}).get('revision_scope') != 'tests']
                total_calls = sum(store.db.execute("select count(*) from role_invocations where run_id=? and purpose='package_worker'", (r['id'],)).fetchone()[0] for r in repairs)
                if not total_calls:
                    continue
                fired = scan_job(store, repairs, lambda r: registry.get(r['data'].get('language') or 'go'))
                revised = any((r['data'].get('project_plan') or {}).get('revision_scope') == 'tests' for r in group)
                rows.append({'ledger': root.name, 'goal': (goal or '')[:30], 'calls': total_calls, 'fires_after': fired[0] if fired else None, 'revised': revised,
                             'succeeded': any(r['status'] == 'succeeded' and r['data'].get('project_bundle') for r in group)})
        finally:
            store.close()
    for r in rows:
        print(f"{r['ledger'][-9:]:<10} {r['goal']:<31} repair-worker calls {r['calls']:>3}  fires after {str(r['fires_after']):>4}  tests-revised {str(r['revised']):<5} succeeded {r['succeeded']}")
    fired = [r for r in rows if r['fires_after']]
    print()
    print(f"{len(rows)} 个有工人修复的任务；信号触发 {len(fired)} 个；触发的里面之后修订了测试 {sum(r['revised'] for r in fired)} 个；没触发的里面修订了测试 {sum(1 for r in rows if not r['fires_after'] and r['revised'])} 个。")
    if fired:
        left = sum(r['calls'] - r['fires_after'] for r in fired)
        total = sum(r['calls'] for r in fired)
        print(f"触发的任务里，触发之后还花了 {left} 次工人调用（共 {total} 次）：这个信号最多能省 {round(100 * left / total)}%。")


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main(sys.argv[1:])
