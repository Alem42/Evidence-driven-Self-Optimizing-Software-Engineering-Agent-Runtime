"""用**当前**的判官重新判一遍已有评测运行的最终代码：评测过程中改进了判官（例如把分数 5/2 当成值 2.5），旧结果要用同一把尺子重新量，臂与臂之间才可比。零 token。
Re-judge the final code of finished evaluation runs with the CURRENT oracle: the oracle improves while evaluations run (e.g. reading the fraction 5/2 as 2.5) and old results must be measured with the same ruler to stay comparable. Zero tokens.

用法 / usage:  python scripts/eval/rejudge.py .masa/vb2_new .masa/vb2_old [...]      （每个参数是 ab_eval 的 --name 目录 / each argument is an ab_eval --name folder）
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from masa.bench.oracle import judge  # noqa: E402
from masa.bench.runner import BY_ID  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402


def final_files(state, bench_id):
    store = Store(state / 'bench' / 'state' / bench_id)
    try:
        runs = [r for r in store.all_runs() if r['data'].get('project_bundle')]
        if not runs:
            return None
        return store.read(runs[-1]['data']['project_bundle']['approval_ref'])['files']
    finally:
        store.close()


def main(folders):
    rows = []
    for folder in folders:
        state = Path(folder)
        summary = json.loads((state / 'summary.json').read_text(encoding='utf-8'))
        for run in summary['runs']:
            files = final_files(state, run['bench_id'])
            task = BY_ID[run['task']]
            if files is None:
                verdict = 'no code'
            else:
                result = judge(files, task, None, run.get('language') or 'python')
                verdict = 'pass' if result['passed'] else 'fail' if result.get('built') else 'no build'
            rows.append((state.name, run['task'], run['arm'], run.get('round', 1), run['status'], verdict, run['cost']))
    print(f"{'folder':14} {'task':12} {'arm':5} {'rd':>2}  {'then':10} {'now':8} {'cost':>7}")
    for folder, task, arm, rnd, then, now, cost in rows:
        flag = '  <-- changed' if (then == 'pass') != (now == 'pass') and then in ('pass', 'false_pass') else ''
        print(f'{folder:14} {task:12} {arm:5} {rnd:>2}  {then:10} {now:8} {cost:7.4f}{flag}')
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main(sys.argv[1:]))
