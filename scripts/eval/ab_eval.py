"""V1 与 V2（以及 Planner 示例开/关）的同预算配对对照：同一批任务、同一组模型、同样的单次费用上限，独立判官判定。
Same-budget paired comparison of V1 vs V2 (and Planner examples on/off): the same tasks, the same models, the same per-run cost cap, judged by the independent oracle.

臂 / arms:  v1 = 默认流程；v2 = 包工人循环；v2r = V1 生成 + 工人修复；v2rm = v2r + 按窗口自动选择的记忆策略；v2s = v2 + 旧的字母序提示排布。
模型 / models: `--models flash` 只用云端 Flash；`--models flash+glm` 本地 glm-4.7-flash 起步、Flash 兜底并担任 Planner/Tester/Diagnoser（Pro 一律不用）。

用法 / usage:  python scripts/eval/ab_eval.py --name ab1 --language python --only upper,fizz,sum --arms v1,v2,v1x,v2x --models flash+glm --yuan 2.0 --per-run 0.3 --round 1
每个臂的结果追加到 .masa/<name>/summary.json（可续跑；已完成的 任务×臂×轮 不重复）。 Results are appended to .masa/<name>/summary.json (resumable).
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from masa.bench.runner import BenchRunner, resolve_config  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402

PRICES = {'deepseek-flash': (1.0, 4.0)}  # 元/百万 token（输入, 输出）/ CNY per million tokens
ARMS = {'v1': {'runtime': 'v1', 'policy': {}}, 'v2': {'runtime': 'v2', 'policy': {}}, 'v2r': {'runtime': 'v2repair', 'policy': {'start_level_by_role': {'project_repair': 2}}},  # 修复的工人直接用 Flash（本地 glm 当不了工人）/ the repair workers start at Flash (the local glm cannot be a worker)
        'v2rm': {'runtime': 'v2repair', 'policy': {'start_level_by_role': {'project_repair': 2}, 'memory_strategy': 'auto'}},  # 同 v2r，但包工人用按窗口自动选择的记忆策略 / like v2r, but the workers use the window-adaptive memory strategy
        'v2s': {'runtime': 'v2', 'policy': {}, 'layout': 'sorted'},  # 与 v2 相同，只是提示用旧的字母序排布（缓存命中率对照）/ same as v2 but with the old alphabetical prompt layout (cache hit-rate comparison)
}


def prepare(source, folder, with_local):
    """复制设置：只启用 Flash（以及可选的 glm-4.7-flash），写入价格；Pro 与其它本地模型一律关闭。 Copy the settings: Flash (and optionally glm-4.7-flash) only, with prices; Pro and every other local model off."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in ('provider.json', 'provider-keys.local.json', 'routing.json', 'token-calibration.json'):
        if (source / name).exists():
            shutil.copy(source / name, folder / name)
    raw = json.loads((folder / 'provider.json').read_text(encoding='utf-8'))
    ids = []
    for profile in raw['profiles']:
        model = profile['model']
        enabled = model == 'deepseek-flash' or (with_local and model.startswith('glm-4.7-flash'))
        profile['enabled'] = enabled
        if model == 'deepseek-flash':
            profile['input_price_per_million'], profile['output_price_per_million'] = PRICES[model]
            profile['level'] = 2
            profile['context_limit'] = max(profile['context_limit'], 262144)
        if enabled:
            ids.append(profile['id'])
    if len(ids) != (2 if with_local else 1):
        raise SystemExit('需要 deepseek-flash（以及 glm-4.7-flash）各一个配置 / the required profiles are missing')
    (folder / 'provider.json').write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding='utf-8')
    return ids


def events_of(state_dir, bench_id):
    """这次评测里所有 run 的账本事件：{类型: [载荷]}，以及各 run 的验证次数。 Every run's ledger events of this evaluation."""
    store = Store(state_dir / 'bench' / 'state' / bench_id)
    try:
        out, verifications = {}, 0
        for run in store.all_runs():
            if store.tools(run['id']):
                verifications += 1
            for event in store.events(run['id']):
                out.setdefault(event['type'], []).append(event['payload'])
        return out, verifications
    finally:
        store.close()


def cache_text(row):
    hit, miss = row.get('cache_hit_tokens'), row.get('cache_miss_tokens')
    return f'{hit / (hit + miss):.0%}（{hit}/{hit + miss}）' if hit is not None and miss is not None and hit + miss else '未报告'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True)
    ap.add_argument('--language', default='python')
    ap.add_argument('--only', required=True)
    ap.add_argument('--arms', default='v1,v2')
    ap.add_argument('--models', default='flash+glm', choices=['flash', 'flash+glm'])
    ap.add_argument('--yuan', type=float, default=2.0)
    ap.add_argument('--per-run', type=float, default=0.3)
    ap.add_argument('--round', type=int, default=1)
    ap.add_argument('--scale', type=int, default=100, help='单次运行的时间/token 上限倍率（%%）；大任务要 300 左右 / percentage of the per-run limits; big tasks need about 300')
    ap.add_argument('--max-calls', type=int, default=40, help='单次运行的模型调用上限 / model-call limit per run')
    ap.add_argument('--minutes', type=int, default=30)
    ap.add_argument('--start-level', type=int, default=0, help='生成阶段从这个等级起步（2 = 直接用 Flash，不先试本地模型）/ start the generation stage at this level (2 = Flash at once, no local model first)')
    ap.add_argument('--source', type=Path, default=ROOT / '.masa/p2eval_final')
    ap.add_argument('--state', type=Path, default=ROOT / '.masa')
    args = ap.parse_args()
    folder = args.state / args.name
    ids = prepare(args.source, folder, args.models == 'flash+glm')
    path = folder / 'summary.json'
    summary = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'runs': []}
    spent = round(sum(r['cost'] for r in summary['runs']), 4)
    done = {(r['task'], r['arm'], r.get('round', 1)) for r in summary['runs']}
    arms = args.arms.split(',')
    for index, task in enumerate(args.only.split(',')):
        order = arms if index % 2 == 0 else arms[::-1]  # 交替先后，避免时段/缓存偏差 / alternate the order to avoid time-of-day and cache bias
        for arm in order:
            if (task, arm, args.round) in done:
                continue
            left = args.yuan - spent
            if left < 0.12:
                print(f'费用已接近上限（已花 {spent} 元），停止', flush=True)
                summary['stopped'] = f'budget {spent}/{args.yuan}'
                path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
                return 0
            spec = ARMS[arm]
            os.environ['MASA_PROMPT_LAYOUT'] = spec.get('layout', 'stable_first')  # 同一进程里按臂切换提示排布 / switch the prompt layout per arm within one process
            config = resolve_config({'suite': 'custom', 'task_ids': [task], 'repeats': 1, 'model_ids': ids, 'total_cost': left, 'per_run_cost': min(args.per_run, left),
                                     'total_minutes': args.minutes, 'total_cloud_tokens': 5_000_000, 'language': args.language, 'runtime': spec['runtime'],
                                     'per_run_scale': args.scale, 'max_model_calls': args.max_calls})
            policy = {**spec['policy'], **({'start_level_by_role': {**spec['policy'].get('start_level_by_role', {}), 'project_developer': args.start_level}} if args.start_level else {})}
            config['overrides'] = {'policy': policy}
            config['keep_state'] = True
            runner = BenchRunner(folder, ROOT / '.tools/bin/masa-runner.exe', ROOT / '.tools/go/bin/go.exe', ROOT, config)
            print(f'[{task} / {arm}] 开始 …', flush=True)
            runner.run()
            record = runner.snapshot()['records'][0]
            spent = round(spent + (record.get('cost') or 0), 4)
            events, verifications = events_of(folder, runner.id)
            checked = (events.get('examples_checked') or [{}])[-1]
            row = {'task': task, 'arm': arm, 'runtime': spec['runtime'], 'examples': bool(spec['policy']), 'round': args.round, 'language': args.language, 'models': args.models,
                   'level': record['level'], 'status': record['status'], 'cost': record.get('cost') or 0, 'cloud_tokens': record['cloud_tokens'], 'local_tokens': record.get('local_tokens', 0),
                   'seconds': record['seconds'], 'rounds': record['rounds'], 'verifications': verifications, 'calls': record['calls'], 'failed_calls': record.get('failed_calls', 0),
                   'escalations': record['escalations'], 'cache_hit_tokens': record.get('cache_hit_tokens'), 'cache_miss_tokens': record.get('cache_miss_tokens'), 'stuck': len(events.get('worker_stuck', [])), 'deferred': len(events.get('package_deferred', [])),
                   'worker_steps': len(events.get('worker_step', [])), 'recalls': sum(1 for e in events.get('worker_step', []) if e.get('tool') == 'recall_step'), 'memory_strategy': sorted({e.get('strategy') for e in events.get('memory_strategy', [])}), 'examples_total': checked.get('total'), 'examples_passed': checked.get('passed'),
                   'mechanisms': record['mechanisms'], 'oracle_reason': record.get('oracle_reason'), 'note': record.get('note'), 'stop_reason': record.get('stop_reason'),
                   'run_id': record.get('run_id'), 'bench_id': runner.id}
            summary['runs'].append(row)
            path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
            print(f"[{task} / {arm}] {row['status']}  {row['cost']} 元  云 {row['cloud_tokens']} / 本地 {row['local_tokens']} tok  {row['seconds']} 秒  验证 {verifications} 次  调用 {row['calls']}"
                  f"  缓存命中 {cache_text(row)}  示例 {row['examples_passed']}/{row['examples_total']}  判官: {row['oracle_reason'] or '通过'}  累计 {spent} 元", flush=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'完成，累计 {spent} 元')
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
