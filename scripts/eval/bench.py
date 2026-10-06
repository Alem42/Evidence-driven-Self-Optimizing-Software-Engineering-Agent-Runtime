"""命令行评测：与设置页的“评测”是同一个运行器。会调用真实模型（本地免费，云端计费），所以有三层上限。
Command-line benchmark: the same runner as the Settings "Benchmark" page. It calls real models (local is free, cloud is billed), hence three layers of limits.

例 / examples:
    python scripts/eval/bench.py --suite canary                       # 约 5 分钟的金丝雀 / the ~5 minute canary
    python scripts/eval/bench.py --suite quick --total-tokens 200000  # 限制整个评测的云端 token / cap cloud tokens for the whole run
    python scripts/eval/bench.py --tasks hello,wc --repeats 3         # 自选任务 / pick tasks
    python scripts/eval/bench.py --list                               # 看有哪些任务和套餐 / list tasks and suites
    python scripts/eval/bench.py --compare <旧结果id> <新结果id>       # 比较两次评测 / compare two saved results
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from masa.bench.report import aggregate, compare, format_table  # noqa: E402
from masa.bench.runner import BenchRunner, load_result, resolve_config  # noqa: E402
from masa.bench.tasks import SUITES, TASKS  # noqa: E402


def main():
    # Windows 终端默认 GBK，打不出 ✓ ≠ 等符号：统一用 UTF-8 输出。 Windows consoles default to GBK and cannot print the result glyphs.
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('--suite', default='canary', choices=sorted(SUITES) + ['custom'])
    ap.add_argument('--tasks', help='逗号分隔的任务 id（覆盖套餐的任务） / comma separated task ids')
    ap.add_argument('--repeats', type=int)
    ap.add_argument('--total-tokens', type=int, help='整个评测的云端 token 上限 / cloud token cap for the whole run')
    ap.add_argument('--total-minutes', type=int, help='整个评测的时间上限（分钟） / wall-clock cap in minutes')
    ap.add_argument('--scale', type=int, help='单次运行上限的百分比，100 = 默认 / per-run limit scale in percent')
    ap.add_argument('--models', help='只用这些 profile id（逗号分隔） / restrict to these profile ids')
    ap.add_argument('--state', type=Path, default=ROOT / '.masa', help='主状态目录 / main state directory')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--compare', nargs=2, metavar=('BASE', 'NEW'))
    args = ap.parse_args()
    if args.list:
        for task in TASKS:
            print(f'L{task.level}  {task.id:<9} {task.title:<10} {len(task.cases)} 用例  ≤{task.max_seconds}s ≤{task.max_cloud_tokens} tok')
        for name, suite in SUITES.items():
            print(f"\n{name}: {suite['title']} · {len(suite['tasks'])} 个任务 × {suite['repeats']} 次 · 上限 {suite['total_cloud_tokens']} tok / {suite['total_minutes']} 分钟")
        return 0
    if args.compare:
        a, b = (load_result(args.state, x) for x in args.compare)
        print(json.dumps(compare(aggregate(a['records']), aggregate(b['records'])), ensure_ascii=False, indent=1))
        return 0
    body = {'suite': args.suite}
    for key, value in (('repeats', args.repeats), ('total_cloud_tokens', args.total_tokens), ('total_minutes', args.total_minutes), ('per_run_scale', args.scale)):
        if value is not None:
            body[key] = value
    if args.tasks:
        body['task_ids'] = args.tasks.split(',')
    if args.models:
        body['model_ids'] = args.models.split(',')
    config = resolve_config(body)
    seen = [0]

    def progress(state):
        current = state.get('current')
        if current and seen[0] != (current['task'], current['repeat']):
            seen[0] = (current['task'], current['repeat'])
            print(f"  [{current['index']}/{state['total']}] L{current['level']} {current['task']} #{current['repeat']} …", flush=True)

    runner = BenchRunner(args.state, ROOT / '.tools/bin/masa-runner.exe', ROOT / '.tools/go/bin/go.exe', ROOT, config, on_change=progress)
    print(f"评测 {runner.id}：{len(config['task_ids'])} 个任务 × {config['repeats']} 次；上限 云端 {config['total_cloud_tokens']} tok / {config['total_minutes']} 分钟", flush=True)
    try:
        runner.run()
    except KeyboardInterrupt:
        runner.stop()
    result = load_result(args.state, runner.id)
    print()
    print(format_table(result))
    print(f"\n结果已保存：{args.state / 'bench' / 'results' / (runner.id + '.json')}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
