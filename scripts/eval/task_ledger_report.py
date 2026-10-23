"""任务账本逐调用报告：给一个目标里的关键词，列出这个任务的每一次模型调用（角色、模型、输入/输出/缓存命中 token、耗时、输出摘要）和按阶段的汇总。
Per-call ledger report of one task: give a keyword from the goal and get every model call (role, model, input/output/cache-hit tokens, duration, output excerpt) plus a per-phase summary.

用来回答“这次运行把 token 花在哪了”。价格取自状态目录里的 provider.json；没填价格的模型只给 token，不猜费用。
It answers "where did the tokens go". Prices come from provider.json in the state directory; a model without a price shows tokens only, never a guessed cost.

用法 / usage:  python scripts/eval/task_ledger_report.py 公共子序列 [--state .masa] [--days 3]
"""
import argparse
import json
import sys
import time
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from masa.infrastructure.store import Store  # noqa: E402


def prices(state):
    path = state / 'provider.json'
    if not path.exists():
        return {}
    out = {}
    for profile in json.loads(path.read_text(encoding='utf-8')).get('profiles', []):
        pin, pout = profile.get('input_price_per_million'), profile.get('output_price_per_million')
        if isinstance(pin, (int, float)) and isinstance(pout, (int, float)):
            out[profile['model']] = (pin, pout)
    return out


def phase_of(purpose, model, repair_of):
    if purpose == 'package_worker':
        return 'repair workers'
    return {'project_triage': 'triage', 'project_architect': 'planning', 'project_planner': 'planning', 'project_tester': 'planning', 'project_developer': 'first draft', 'project_diagnoser': 'diagnoser',
            'project_test_revision': 'test revision', 'project_repair': 'repair (patch)', 'assertion_referee': 'referee', 'case_deriver': 'audit', 'case_judge': 'audit'}.get(purpose, purpose)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('keyword')
    ap.add_argument('--state', type=Path, default=ROOT / '.masa')
    ap.add_argument('--days', type=float, default=3)
    args = ap.parse_args()
    price = prices(args.state)
    store = Store(args.state)
    try:
        runs = sorted((r for r in store.all_runs() if args.keyword in (r['data'].get('goal') or '') and time.time() - r['data'].get('created_at', 0) < args.days * 86400),
                      key=lambda r: r['data'].get('created_at', 0))
        if not runs:
            raise SystemExit('no run matches')
        print('goal:', runs[0]['data']['goal'][:120])
        phases = OrderedDict()
        total_calls = 0
        for run in runs:
            plan = run['data'].get('project_plan') or {}
            kind = 'repair' if plan.get('repair_of') else ('test-revision' if plan.get('revision_scope') == 'tests' else (plan.get('kind') or ('plan' if plan.get('spec_ref') else 'verification')))
            print(f"\n## {run['id'][:6]} {run['status']} {kind} {time.strftime('%H:%M:%S', time.localtime(run['data'].get('created_at', 0)))} | {str(plan.get('error') or run['reason'])[:120]}")
            usage = {}
            for event in store.events(run['id']):
                if event['type'] == 'model_completed':
                    usage[(event['payload']['step_id'], event['payload']['invocation_id'])] = event['payload'].get('usage') or {}
            rows = store.db.execute("select purpose,invocation_id,status,started,finished,route_ref,output_ref from role_invocations where run_id=? order by started", (run['id'],)).fetchall()
            for row in rows:
                model = store.read(row['route_ref']).get('model', '')
                u = usage.get((row['purpose'], row['invocation_id']), {})
                total_calls += 1
                p_in, p_out, hit = u.get('prompt_tokens') or 0, u.get('completion_tokens') or 0, u.get('prompt_cache_hit_tokens') or 0
                excerpt = ''
                if row['output_ref']:
                    try:
                        excerpt = json.dumps(store.read(row['output_ref']), ensure_ascii=False)[:100].replace('\n', ' ')
                    except Exception:
                        pass
                print(f"  {row['purpose'][8:] if row['purpose'].startswith('project_') else row['purpose']:<14} {row['invocation_id'][:14]:<14} {model[:16]:<16} {row['status'][:4]} in={p_in} out={p_out} hit={hit} {round((row['finished'] or time.time()) - row['started'], 1)}s {excerpt}")
                name = phase_of(row['purpose'], model, plan.get('repair_of')) + (' (' + ('local' if not model.startswith('deepseek') else model.replace('deepseek-', '')) + ')')
                acc = phases.setdefault(name, {'calls': 0, 'in': 0, 'out': 0, 'hit': 0, 'cost': 0.0, 'priced': True})
                acc['calls'] += 1
                acc['in'] += p_in
                acc['out'] += p_out
                acc['hit'] += hit
                if model in price:
                    acc['cost'] += (p_in * price[model][0] + p_out * price[model][1]) / 1e6
                elif model.startswith('deepseek'):
                    acc['priced'] = False
        total_in = sum(a['in'] for a in phases.values()) or 1
        print(f"\n{'phase':<34}{'calls':>6}{'input':>9}{'share':>8}{'output':>8}{'cache-hit':>10}  cost")
        for name, a in phases.items():
            cost = f"≈¥{a['cost']:.4f}" if a['priced'] else '(no price set)'
            print(f"{name:<34}{a['calls']:>6}{a['in']:>9}{a['in'] / total_in:>8.1%}{a['out']:>8}{a['hit']:>10}  {cost}")
        print(f"{'TOTAL':<34}{total_calls:>6}{total_in:>9}")
    finally:
        store.close()


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
