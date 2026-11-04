"""冻结之前核对 Architect 的用例：R1 里 Architect 写的黑盒用例有 2.7–4.5% 期望是错的，而每一个错用例都让那一步多花几倍的钱或直接停下（R1 第 3、10 步）。
这里量：在用例冻结之前，让没看过实现的独立来源（暴力参考程序 FR、盲推导 F）在同样的输入上给出答案，分歧就标记——能抓住几个错用例、误报几个对的。
Check the Architect's cases BEFORE they are frozen: in R1, 2.7-4.5% of the Architect's black-box cases had a wrong expectation and each one made its step cost several times more or stop (R1 steps 3 and 10).
This measures: before freezing, independent sources that never saw any implementation (a brute-force reference program FR, blind derivation F) answer the same inputs; a disagreement flags the case -
how many wrong cases are caught and how many right ones are flagged.

事实标准是 R1 负载的参考实现（bench/workload_stock.py），对任何输入都唯一。 The ground truth is the R1 workload's reference (bench/workload_stock.py), unique for any input.
用法 / usage:  python scripts/eval/case_precheck_probe.py .masa/r1_flash .masa/r1_flash2 --max-yuan 0.5 --out .masa/case_precheck.json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts' / 'eval'))
from adversary_probe import Counting, run_script  # noqa: E402
from hardening_probe import derive, reference, requirement  # noqa: E402
from masa.bench import workload_stock as workload  # noqa: E402
from masa.bench.oracle import normalize  # noqa: E402
from masa.infrastructure.settings import Settings  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402


def plans(folder):
    """每个规划（基础与每个变更）这次新写的用例。 The cases each plan (the base and every change) newly wrote."""
    store = Store(folder / 'state')
    try:
        out = []
        for run in sorted(store.all_runs(), key=lambda r: r['data'].get('created_at', 0)):
            plan = run['data'].get('project_plan') or {}
            if plan.get('kind') == 'code' or not plan.get('spec_ref') or plan.get('status') not in ('approved', 'awaiting_review'):
                continue
            spec = store.read(plan['spec_ref'])
            level = plan.get('change_no', 0)
            cases = spec.get('cases') or []
            new = cases if level == 0 else (cases[-plan['new_cases']:] if plan.get('new_cases') else [])
            if new:
                out.append({'run': run['id'], 'level': level, 'spec': spec, 'cases': new})
        return out
    finally:
        store.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('folders', nargs='+', type=Path)
    ap.add_argument('--source', type=Path, default=ROOT / '.masa/p2eval_final')
    ap.add_argument('--max-yuan', type=float, default=0.5)
    ap.add_argument('--out', type=Path, default=None)
    args = ap.parse_args()
    settings = Settings(args.source)
    flash = Counting(settings.provider(next(i for i, p in settings.profiles.items() if p['model'] == 'deepseek-flash')))
    rows, totals = [], {'cases': 0, 'wrong': 0}
    detectors = ('FR', 'F1', 'FR+F1', 'FR|F1')
    for name in detectors:
        totals[name] = {'tp': 0, 'fp': 0, 'covered': 0}
    for folder in args.folders:
        for plan in plans(folder):
            if flash.yuan >= args.max_yuan:
                print(f'预算到了 / budget reached ¥{flash.yuan:.3f}')
                break
            level, spec, cases = plan['level'], plan['spec'], plan['cases']
            inputs = [{'args': c['args'], 'stdin': c['stdin']} for c in cases]
            truth = [(normalize(o), code) for o, code in (workload.run(level, c['args'], c['stdin']) for c in cases)]
            claimed = [(normalize(c['stdout']), c['exit_code']) for c in cases]
            wrong = {i for i in range(len(cases)) if claimed[i] != truth[i]}
            source = reference(flash, level, spec)
            fr = {i: (normalize(o), code) for i, (o, _, code) in enumerate(run_script(source, x['args'], x['stdin']) for x in inputs)} if source else {}
            f1 = derive(flash, level, spec, inputs, 0)
            flags = {'FR': {i for i, v in fr.items() if v != claimed[i]}, 'F1': {i for i, v in f1.items() if v != claimed[i]}}
            flags['FR+F1'] = {i for i in flags['FR'] & flags['F1'] if fr[i] == f1[i]}  # 两个来源给出同一个不同的答案 / both sources give the same different answer
            flags['FR|F1'] = flags['FR'] | flags['F1']
            totals['cases'] += len(cases)
            totals['wrong'] += len(wrong)
            row = {'folder': folder.name, 'level': level, 'cases': len(cases), 'wrong': sorted(wrong), 'flags': {k: sorted(v) for k, v in flags.items()},
                   'fr_ran': bool(source), 'fr_right': sum(1 for i, v in fr.items() if v == truth[i]), 'f1_right': sum(1 for i, v in f1.items() if v == truth[i]),
                   'wrong_detail': [{'name': cases[i]['name'], 'stdin': cases[i]['stdin'], 'claimed': claimed[i], 'truth': truth[i], 'fr': fr.get(i), 'f1': f1.get(i)} for i in sorted(wrong)]}
            for name in detectors:
                totals[name]['tp'] += len(flags[name] & wrong)
                totals[name]['fp'] += len(flags[name] - wrong)
                totals[name]['covered'] += len(fr if name == 'FR' else f1 if name == 'F1' else cases)
            rows.append(row)
            print(f"{folder.name:10} level {level:2}  cases {len(cases):2}  wrong {len(wrong)}  | FR right {row['fr_right']}/{len(fr)} flags {len(flags['FR'])} caught {len(flags['FR'] & wrong)}"
                  f"  | F1 right {row['f1_right']}/{len(f1)} flags {len(flags['F1'])} caught {len(flags['F1'] & wrong)}  | FR+F1 flags {len(flags['FR+F1'])} caught {len(flags['FR+F1'] & wrong)}  ¥{flash.yuan:.3f}", flush=True)
            if args.out:
                args.out.write_text(json.dumps({'rows': rows, 'totals': totals}, ensure_ascii=False, indent=1), encoding='utf-8')
    print()
    print(f"== 汇总 / total: {totals['cases']} cases, {totals['wrong']} wrong (¥{flash.yuan:.3f}) ==")
    for name in detectors:
        t = totals[name]
        print(f"{name:6} caught {t['tp']}/{totals['wrong']}  false flags {t['fp']}/{totals['cases'] - totals['wrong']}  (precision {t['tp'] / (t['tp'] + t['fp']) if t['tp'] + t['fp'] else float('nan'):.2f})")
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
