"""跨语言评测：同一批任务、同一个云端模型（只用 Flash，不用 Pro），按语言包运行并由该语言的独立判官核对。
Cross-language evaluation: the same tasks and the same cloud model (Flash only, never Pro), run per language pack and checked by that language's independent oracle.

用法 / usage:  python scripts/eval/lang_eval.py --name langeval --language python --only sum,wc --yuan 0.6 --per-run 0.3
设置只在 .masa/<name>/ 里复制一份（本地模型一律关闭）。 The settings are copied into .masa/<name>/ (local models always off).
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from masa.bench.runner import BenchRunner, resolve_config  # noqa: E402

PRICES = {'deepseek-flash': (1.0, 4.0)}  # 元/百万 token（输入, 输出）/ CNY per million tokens (input, output)


def prepare(source, folder):
    """复制设置：只启用 Flash，写入价格。 Copy the settings: only Flash enabled, prices written."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in ('provider.json', 'provider-keys.local.json', 'routing.json', 'token-calibration.json'):
        if (source / name).exists():
            shutil.copy(source / name, folder / name)
    raw = json.loads((folder / 'provider.json').read_text(encoding='utf-8'))
    ids = []
    for profile in raw['profiles']:
        flash = profile['model'] == 'deepseek-flash'
        profile['enabled'] = flash
        if flash:
            profile['input_price_per_million'], profile['output_price_per_million'] = PRICES['deepseek-flash']
            profile['level'] = 2
            profile['context_limit'] = max(profile['context_limit'], 262144)
            ids.append(profile['id'])
    if len(ids) != 1:
        raise SystemExit('需要恰好一个 deepseek-flash 配置 / exactly one deepseek-flash profile is required')
    (folder / 'provider.json').write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding='utf-8')
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True)
    ap.add_argument('--language', default='go')
    ap.add_argument('--only', required=True, help='逗号分隔的任务 id / comma separated task ids')
    ap.add_argument('--yuan', type=float, default=0.6, help='总费用上限 / total cost cap')
    ap.add_argument('--per-run', type=float, default=0.3, help='单次运行费用上限 / per-run cost cap')
    ap.add_argument('--source', type=Path, default=ROOT / '.masa/p2eval_final', help='复制设置的来源 / where the settings are copied from')
    ap.add_argument('--state', type=Path, default=ROOT / '.masa')
    ap.add_argument('--round', type=int, default=1)
    args = ap.parse_args()
    folder = args.state / args.name
    ids = prepare(args.source, folder)
    path = folder / 'summary.json'
    summary = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'runs': []}
    spent = round(sum(r['cost'] for r in summary['runs']), 4)
    done = {(r['task'], r['language'], r.get('round', 1)) for r in summary['runs']}
    for task in args.only.split(','):
        if (task, args.language, args.round) in done:
            continue
        left = args.yuan - spent
        if left < 0.12:
            print(f'费用已接近上限（已花 {spent} 元），停止', flush=True)
            summary['stopped'] = f'budget {spent}/{args.yuan}'
            break
        config = resolve_config({'suite': 'custom', 'task_ids': [task], 'repeats': 1, 'model_ids': ids, 'total_cost': left, 'per_run_cost': min(args.per_run, left),
                                 'total_minutes': 30, 'total_cloud_tokens': 2_000_000, 'language': args.language})
        config['keep_state'] = True
        runner = BenchRunner(folder, ROOT / '.tools/bin/masa-runner.exe', ROOT / '.tools/go/bin/go.exe', ROOT, config)
        print(f'[{args.language} / {task}] 开始 …', flush=True)
        runner.run()
        record = runner.snapshot()['records'][0]
        spent = round(spent + (record.get('cost') or 0), 4)
        row = {'task': task, 'language': args.language, 'round': args.round, 'level': record['level'], 'status': record['status'], 'cost': record.get('cost') or 0,
               'cloud_tokens': record['cloud_tokens'], 'seconds': record['seconds'], 'rounds': record['rounds'], 'calls': record['calls'],
               'escalations': record['escalations'], 'mechanisms': record['mechanisms'], 'oracle_reason': record.get('oracle_reason'), 'note': record.get('note'),
               'run_id': record.get('run_id'), 'bench_id': runner.id, 'stop_reason': record.get('stop_reason')}
        summary['runs'].append(row)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f"[{args.language} / {task}] {record['status']}  {row['cost']} 元  {row['cloud_tokens']} tok  {row['seconds']} 秒  轮数 {row['rounds']}  "
              f"机制 { {k: v for k, v in row['mechanisms'].items() if v} }  判官: {row['oracle_reason'] or '通过'}  累计 {spent} 元", flush=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'完成，累计 {spent} 元')
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
