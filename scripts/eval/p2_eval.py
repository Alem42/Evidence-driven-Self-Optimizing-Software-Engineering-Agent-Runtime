"""动态角色（指挥者）的真实对照评测：随机抽低等级任务，同一任务各跑一次“关”和“开”，用独立判官复核，费用有硬上限。
Real A/B evaluation of the dynamic roles (conductor): draw low-level tasks at random, run each task once with the conductor OFF and once ON, re-check with the independent oracle, under a hard cost cap.

模型分工 / model split: 最上位（规划、测试、诊断、指挥者、怀疑者、审阅者）用 deepseek-v4-pro；耗 token 的生成与修复由 deepseek-flash 完成（路由器从最低等级起步）。
Top roles use deepseek-v4-pro; the token-heavy generation and repair run on deepseek-flash (the router starts at the lowest level).
不动用户的设置：在 .masa/p2eval/ 里复制一份配置，禁用本地模型，写入价格用于费用上限。 The user's settings are untouched: a copy lives in .masa/p2eval/ with local models off and prices set for the cost cap.

用法 / usage:  python scripts/eval/p2_eval.py --yuan 9 --tasks 4 --seed 7
"""
import argparse
import json
import random
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from masa.bench.runner import BenchRunner, resolve_config  # noqa: E402
from masa.bench.tasks import BY_ID, TASKS  # noqa: E402

# 保守的价格（元 / 百万 token）：Pro 取高峰价（官方：未命中缓存输入 9、输出 27），Flash 取折合人民币的上沿。价格只用于费用上限的估算。
# Conservative prices (yuan per million tokens): Pro at the peak rate, Flash at the upper end. They only drive the cost cap.
PRICES = {'deepseek-v4-pro': (9.0, 27.0), 'deepseek-flash': (1.0, 4.0)}
ARMS = {'off': {'policy': {}, 'drop_edges': []}, 'on': {'policy': {'conductor': True}, 'drop_edges': []}}


def prepare(source, folder):
    """复制设置并改成评测用：只留 Flash 与 Pro 两个云端配置，写入价格。 Copy the settings for the evaluation: only Flash and Pro, with prices."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in ('provider.json', 'provider-keys.local.json', 'routing.json', 'token-calibration.json'):
        if (source / name).exists():
            shutil.copy(source / name, folder / name)
    raw = json.loads((folder / 'provider.json').read_text(encoding='utf-8'))
    ids = []
    for profile in raw['profiles']:
        cloud = profile['model'] in PRICES
        profile['enabled'] = cloud
        if cloud:
            profile['input_price_per_million'], profile['output_price_per_million'] = PRICES[profile['model']]
            profile['level'] = 3 if profile['model'] == 'deepseek-v4-pro' else 2
            profile['context_limit'] = max(profile['context_limit'], 262144)
            ids.append(profile['id'])
    if len(ids) != 2:
        raise SystemExit('需要 deepseek-flash 与 deepseek-v4-pro 两个云端配置 / both cloud profiles are required')
    (folder / 'provider.json').write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding='utf-8')
    return ids


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('--yuan', type=float, default=9.0, help='整个对照评测的费用硬上限（元） / hard cost cap in yuan')
    ap.add_argument('--per-run', type=float, default=1.2, help='单次运行的费用上限（元） / per-run cap')
    ap.add_argument('--tasks', type=int, default=4)
    ap.add_argument('--min-level', type=int, default=2)
    ap.add_argument('--max-level', type=int, default=5)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--only', help='指定任务（逗号分隔），不随机 / explicit tasks, no sampling')
    ap.add_argument('--state', type=Path, default=ROOT / '.masa')
    ap.add_argument('--name', default='p2eval')
    ap.add_argument('--arms', default='off,on', help='只跑哪些组（逗号分隔） / which arms to run')
    ap.add_argument('--saved-conductor', action='store_true', help='不用覆盖项，而是把 conductor 写进（副本的）路由设置，验证“设置页开关 → 实际流程”这条路 / set the conductor in the (copied) saved routing settings instead of an override')
    ap.add_argument('--round', type=int, default=1, help='第几轮重复（同一任务多次，看噪声） / repetition round, to see noise')
    args = ap.parse_args()
    folder = args.state / args.name
    ids = prepare(args.state, folder)
    if args.saved_conductor:
        from masa.infrastructure.settings import Settings
        Settings(folder).save_routing({'policy': {'conductor': True}})
    pool = [t.id for t in TASKS if args.min_level <= t.level <= args.max_level]
    tasks = args.only.split(',') if args.only else random.Random(args.seed).sample(pool, min(args.tasks, len(pool)))
    print(f'抽到的任务 / tasks: {tasks}（池 {pool}，seed {args.seed}）；费用上限 {args.yuan} 元，单次 {args.per_run} 元', flush=True)
    summary_path = folder / 'summary.json'
    summary = json.loads(summary_path.read_text(encoding='utf-8')) if summary_path.exists() else {'runs': []}
    spent = round(sum(r['cost'] for r in summary['runs'] + summary.get('invalid', [])), 4)  # 作废的运行也花了钱 / invalidated runs still cost money
    done = {(r['task'], r['arm'], r.get('round', 1)) for r in summary['runs']}
    for index, task in enumerate(tasks):
        order = ['off', 'on'] if index % 2 == 0 else ['on', 'off']  # 交替先后，避免时段偏差 / alternate the order to avoid time-of-day bias
        for arm in [a for a in order if a in args.arms.split(',')]:
            if (task, arm, args.round) in done:
                continue
            left = args.yuan - spent
            if left < 0.3:
                print(f'费用已接近上限（已花 {spent} 元），停止 / budget nearly exhausted, stopping', flush=True)
                summary['stopped'] = f'budget {spent}/{args.yuan}'
                summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
                return 0
            config = resolve_config({'suite': 'custom', 'task_ids': [task], 'repeats': 1, 'model_ids': ids, 'total_cost': left, 'per_run_cost': min(args.per_run, left),
                                     'total_minutes': 30, 'total_cloud_tokens': 2_000_000})
            config['overrides'] = ARMS[arm]
            config['keep_state'] = True
            runner = BenchRunner(args.state / args.name, ROOT / '.tools/bin/masa-runner.exe', ROOT / '.tools/go/bin/go.exe', ROOT, config)
            print(f'[{task} / {arm}] 开始 …', flush=True)
            started = time.time()
            runner.run()
            record = runner.snapshot()['records'][0]
            spent = round(spent + (record.get('cost') or 0), 4)
            row = {'task': task, 'round': args.round, 'level': record['level'], 'arm': arm, 'status': record['status'], 'cost': record.get('cost') or 0, 'cloud_tokens': record['cloud_tokens'],
                   'seconds': record['seconds'], 'rounds': record['rounds'], 'calls': record['calls'], 'escalations': record['escalations'],
                   'mechanisms': record['mechanisms'], 'oracle_reason': record.get('oracle_reason'), 'note': record.get('note'), 'run_id': record.get('run_id'),
                   'bench_id': runner.id, 'stop_reason': record.get('stop_reason')}
            summary['runs'].append(row)
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
            print(f"[{task} / {arm}] {record['status']}  {row['cost']} 元  {row['cloud_tokens']} tok  {row['seconds']} 秒  轮数 {row['rounds']}  机制 { {k: v for k, v in row['mechanisms'].items() if v} }  累计 {spent} 元", flush=True)
    print(f'完成，累计 {spent} 元')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
