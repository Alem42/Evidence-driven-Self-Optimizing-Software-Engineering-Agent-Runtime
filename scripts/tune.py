"""工作流调优（AFlow-lite）：在评测套餐上搜索更好的路由策略/修复子图配置。会调用真实模型，所以有整体预算（token、分钟、候选数）。
Workflow tuning (AFlow-lite): search for a better routing policy / repair-graph configuration on a benchmark suite. It calls real models, hence an overall budget (tokens, minutes, candidates).

例 / examples:
    python scripts/tune.py --dry-run                                  # 只打印计划与上限，不花钱 / print the plan and caps, spend nothing
    python scripts/tune.py --suite canary --generations 1 --width 2 --total-tokens 150000 --proposer random
    python scripts/tune.py --suite quick --generations 2 --width 4 --total-tokens 600000 --proposer llm
    python scripts/tune.py --apply <调优id>                           # 把最优策略写回路由设置（只含策略）/ write the best policy into the routing settings
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from masa.bench.tasks import SUITES  # noqa: E402
from masa.infrastructure.settings import Settings  # noqa: E402
from masa.tuning import space  # noqa: E402
from masa.tuning.apply import apply_policy  # noqa: E402
from masa.tuning.evaluate import bench_evaluator  # noqa: E402
from masa.tuning.proposer import LLMProposer, RandomProposer, provider_ask  # noqa: E402
from masa.tuning.search import search  # noqa: E402


def strongest_cloud(settings, only=None):
    """提议者用的模型：已就绪的云端配置里等级最高的。没有就返回 None（改用随机提议者）。 The proposer's model: the highest-level ready cloud profile, else None."""
    ready = [(p['level'], ident) for ident, p in settings.ready_profiles() if p['model_type'] == 'cloud' and (not only or ident in only)]
    return settings.provider(max(ready)[1]) if ready else None


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('--suite', default='canary', choices=sorted(SUITES))
    ap.add_argument('--tasks', help='逗号分隔的任务 id（覆盖套餐的任务） / comma separated task ids')
    ap.add_argument('--repeats', type=int)
    ap.add_argument('--models', help='评测只用这些 profile id / restrict the benchmark to these profile ids')
    ap.add_argument('--width', type=int, default=4, help='每代候选数 / candidates per generation')
    ap.add_argument('--generations', type=int, default=2)
    ap.add_argument('--max-candidates', type=int, default=8, help='整个调优最多评估几个候选（含基线）/ evaluations in total, baseline included')
    ap.add_argument('--total-tokens', type=int, default=400_000, help='整个调优的云端 token 上限 / cloud token cap for the whole tuning')
    ap.add_argument('--total-minutes', type=int, default=120)
    ap.add_argument('--min-gain', type=float, default=2.0, help='候选必须比最优高出这么多分才算改进 / points a candidate must beat the best by')
    ap.add_argument('--lam', type=float, default=1.0, help='每 1 万云端 token（每次通过）扣的分 / points per 10k cloud tokens per pass')
    ap.add_argument('--mu', type=float, default=1.0, help='每 1%% 假通过扣的分 / points per 1%% of false passes')
    ap.add_argument('--proposer', choices=['llm', 'random'], default='llm')
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--state', type=Path, default=ROOT / '.masa')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--apply', metavar='TUNING_ID')
    args = ap.parse_args()
    settings = Settings(args.state)
    if args.apply:
        policy = apply_policy(settings, args.state / 'tuning' / args.apply / 'best.json')
        print('已写入路由设置的策略 / saved policy:\n' + json.dumps(policy, ensure_ascii=False, indent=1))
        return 0
    body = {'suite': args.suite}
    if args.tasks:
        body['task_ids'] = args.tasks.split(',')
    if args.repeats:
        body['repeats'] = args.repeats
    if args.models:
        body['model_ids'] = args.models.split(',')
    from masa.bench.runner import resolve_config
    config = resolve_config(body)
    evaluations = min(args.max_candidates, 1 + args.width * args.generations)
    print(f"调优计划：套餐 {args.suite}（{len(config['task_ids'])} 个任务 × {config['repeats']} 次）；最多评估 {evaluations} 个候选（含基线）；"
          f"整体上限 云端 {args.total_tokens} tok / {args.total_minutes} 分钟；每个候选的上限 云端 {config['total_cloud_tokens']} tok / {config['total_minutes']} 分钟")
    print('可调空间 / space:\n' + json.dumps(space.catalog(), ensure_ascii=False, indent=1))
    if args.dry_run:
        print('（dry-run：没有调用任何模型）')
        return 0
    provider = strongest_cloud(settings) if args.proposer == 'llm' else None
    if args.proposer == 'llm' and provider is None:
        print('没有就绪的云端模型可做提议者，改用随机提议者 / no ready cloud model for the proposer, falling back to the random one')
    proposer = LLMProposer(provider_ask(provider)) if provider is not None else RandomProposer(args.seed)
    evaluate = bench_evaluator(args.state, ROOT / '.tools/bin/masa-runner.exe', ROOT / '.tools/go/bin/go.exe', ROOT, body)
    result = search(evaluate, proposer, args.state / 'tuning', width=args.width, generations=args.generations, min_gain=args.min_gain,
                    max_candidates=args.max_candidates, total_cloud_tokens=args.total_tokens, total_minutes=args.total_minutes, lam=args.lam, mu=args.mu,
                    log=lambda text: print(text, flush=True))
    folder = args.state / 'tuning' / result['id']
    print(f"\n结论：{result['verdict']}  基线 {result['baseline']['score']} → 最优 {result['best']['score']}（差 {result['gain']}）；花费 {result['spent']}")
    print(f'报告：{folder / "report.md"}\n采用最优策略：python scripts/tune.py --apply {result["id"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
