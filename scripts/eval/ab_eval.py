"""V1 与 V2（以及 Planner 示例开/关）的同预算配对对照：同一批任务、同一组模型、同样的单次费用上限，独立判官判定。
Same-budget paired comparison of V1 vs V2 (and Planner examples on/off): the same tasks, the same models, the same per-run cost cap, judged by the independent oracle.

臂 / arms:  v1 = 默认流程；v2 = 包工人循环；v2r = V1 生成 + 工人修复；v2rm = v2r + 按窗口自动选择的记忆策略；v2m = v2 + 同样的记忆策略；v2s = v2 + 旧的字母序提示排布。
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
        'v2ra': {'runtime': 'v2repair', 'policy': {'start_level_by_role': {'project_repair': 2}, 'acceptance_cases': True}},  # 同 v2r，但 Architect 写结构化的可执行验收用例 / like v2r, but the Architect writes structured executable acceptance cases
        'v2rL': {'runtime': 'v2repair', 'policy': {'acceptance_cases': False}},  # 全本地能力档案（R2）：同 v2r，但不要求结构化验收用例（小模型常把它写错位置，规划阶段就死，量不到能力）/ the all-local profile (R2): like v2r without structured acceptance cases (small models misplace them and die at planning, which measures nothing)
        'v2rH': {'runtime': 'v2repair', 'policy': {}},  # 混合（--models hybrid:<本地>）：默认 v2repair 策略（验收用例开），云端只做定义与裁决 / hybrid: the default v2repair policy (acceptance cases on), the cloud only defines and judges
        'v2m': {'runtime': 'v2', 'policy': {'memory_strategy': 'auto'}},  # 全程 V2 + 按窗口自动选择的记忆策略（长循环才看得出差别）/ full V2 + the window-adaptive memory strategy (only long loops show a difference)
        'v2s': {'runtime': 'v2', 'policy': {}, 'layout': 'sorted'},  # 与 v2 相同，只是提示用旧的字母序排布（缓存命中率对照）/ same as v2 but with the old alphabetical prompt layout (cache hit-rate comparison)
}


def prepare_local(source, folder, model, context):
    """全本地（R2 的能力档案）：只启用这一个本地模型，所有角色（Architect、Developer、工人、Diagnoser）都由它承担；云端一律关闭，费用恒为 0。
    All-local (the R2 capability profile): only this one local model is enabled and plays every role (Architect, Developer, workers, Diagnoser); every cloud model is off, so the cost is always 0."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in ('provider.json', 'routing.json', 'token-calibration.json'):
        if (source / name).exists():
            shutil.copy(source / name, folder / name)
    raw = json.loads((folder / 'provider.json').read_text(encoding='utf-8'))
    ids = []
    for profile in raw['profiles']:
        profile['enabled'] = profile['model'] == model and profile.get('model_type') == 'local'
        if profile['enabled']:
            profile.update(level=1, context_limit=context, timeout_seconds=600)  # 本地允许的最长单次时限 / the longest per-request limit allowed for a local model
            ids.append(profile['id'])
    if len(ids) != 1:
        raise SystemExit(f'没有恰好一个本地配置是 {model} / need exactly one local profile for {model}')
    (folder / 'provider.json').write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding='utf-8')
    return ids


JUDGING_ROLES = ['project_architect', 'project_diagnoser', 'project_test_revision', 'assertion_referee', 'case_deriver', 'case_judge']


def prepare_hybrid(source, folder, model, context):
    """混合（R2 的对照）：云端 Flash 只承担“定义和裁决”的角色（Architect、Diagnoser、测试修订、裁判、用例审计），本地模型承担全部“干活”的角色（生成、修复工人）。
    全本地时小模型自己写测试也自己写实现，Gate 放行的一半是假通过；这里检验把测试与裁决交给另一个模型家族能不能把假通过压下去，而云端只花很少的钱。
    Hybrid (the R2 control): cloud Flash plays only the defining and judging roles (Architect, Diagnoser, test revision, referee, case audit); the local model plays every working role (generation, repair workers).
    All-local, the small model writes the tests AND the code and half of its Gate passes are false; this checks whether giving the tests and the verdicts to another model family pushes false passes down while the cloud costs little."""
    ids = prepare_local(source, folder, model, context)
    for name in ('provider-keys.local.json',):
        if (source / name).exists():
            shutil.copy(source / name, folder / name)
    raw = json.loads((folder / 'provider.json').read_text(encoding='utf-8'))
    for profile in raw['profiles']:
        if profile['model'] == 'deepseek-flash':
            profile.update(enabled=True, level=2, roles=list(JUDGING_ROLES), context_limit=max(profile['context_limit'], 262144))
            profile['input_price_per_million'], profile['output_price_per_million'] = PRICES['deepseek-flash']
            ids.append(profile['id'])
    if len(ids) != 2:
        raise SystemExit('需要 deepseek-flash 配置 / the deepseek-flash profile is missing')
    (folder / 'provider.json').write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding='utf-8')
    return ids


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
    ap.add_argument('--models', default='flash+glm', help="flash | flash+glm | local:<Ollama 模型名>（全本地）| hybrid:<Ollama 模型名>（云端只做定义与裁决）/ local:<model> (all-local) | hybrid:<model> (the cloud only defines and judges)")
    ap.add_argument('--local-ctx', type=int, default=16384, help='全本地时的上下文窗口（请求里的 num_ctx）/ the context window (num_ctx) in all-local mode')
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
    if args.models.startswith('local:'):
        ids = prepare_local(args.source, folder, args.models[len('local:'):], args.local_ctx)
    elif args.models.startswith('hybrid:'):
        ids = prepare_hybrid(args.source, folder, args.models[len('hybrid:'):], args.local_ctx)
    elif args.models in ('flash', 'flash+glm'):
        ids = prepare(args.source, folder, args.models == 'flash+glm')
    else:
        raise SystemExit('--models: flash | flash+glm | local:<model>')
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
