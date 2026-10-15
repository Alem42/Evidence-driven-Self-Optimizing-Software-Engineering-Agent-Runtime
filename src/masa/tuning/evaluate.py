"""真实评估器：用评测运行器在“覆盖配置”下跑一个套餐，返回汇总。覆盖项只作用于这次评测，不写入用户设置。
The real evaluator: run one benchmark suite under overridden configuration with the benchmark runner and return its aggregate. Overrides never touch the user's settings.
"""
from masa.bench.runner import BenchRunner, resolve_config


def bench_evaluator(main_root, runner_path, go_path, project, body, *, on_change=None):
    """body = 评测请求（套餐、任务、repeats、上限）。每个候选各跑一次评测，云端 token 上限取评测自己的上限与调优剩余预算的较小者。
    body is the benchmark request; every candidate runs it once, capped by the smaller of the suite cap and the tuning budget left."""
    def evaluate(config, cap_tokens):
        request = dict(body)
        request['total_cloud_tokens'] = max(1_000, min(resolve_config(request)['total_cloud_tokens'], cap_tokens))
        resolved = resolve_config(request)
        resolved['overrides'] = config
        resolved['discard_state'] = True
        runner = BenchRunner(main_root, runner_path, go_path, project, resolved, on_change=on_change)
        runner.run()
        snapshot = runner.snapshot()
        if snapshot['state'] == 'error':
            raise RuntimeError(snapshot.get('message') or 'benchmark failed')
        return {'aggregate': snapshot['aggregate'], 'result_id': runner.id}
    return evaluate
