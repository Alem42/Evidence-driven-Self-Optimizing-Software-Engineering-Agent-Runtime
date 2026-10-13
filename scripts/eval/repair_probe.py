"""V2 修复探针：拿一次已经通过的运行，在某个包的实现里埋一个 bug，让真实验证失败，再让真实模型走“继续自动修复”（V2 工人只重开出问题的包），看它能不能修好。
V2 repair probe: take a run that passed, plant a bug in one package's implementation so the real verification fails, then let a real model run the "continue automatic repair" flow (V2 workers reopen only the failing package).

不重新生成项目，所以便宜（通常 ¥0.05–0.2）：它只检验“失败之后”这一半流程。 It does not regenerate the project, so it is cheap: it exercises only the "after a failure" half of the flow.

用法 / usage:  python scripts/eval/repair_probe.py --bench <评测账本目录名> --task bank --pick 0 --yuan 0.5
  --bench: .masa/ab2/bench/state 下某次**通过**的运行的目录名 / the directory name of a PASSED run under .masa/ab2/bench/state
  --pick:  埋 bug 的候选文件序号（候选 = 非入口、非测试的实现文件）/ which candidate file gets the bug (candidates = implementation files other than the entry and tests)
"""
import argparse
import ast
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from masa.application.console import Console  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402
from masa.domain.models import Budget  # noqa: E402
from masa.languages.registry import registry  # noqa: E402
from masa.runtime.engine import Runtime  # noqa: E402


def plant(text):
    """把第一个“有返回值”的 return 的值换成 None：函数的调用方立刻出错，测试失败，但文件仍然是合法的 Python。 Replace the value of the first valued return by None: callers break and tests fail, yet the file stays valid Python."""
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and len(node.body) >= 2:
            for child in ast.walk(node):
                if isinstance(child, ast.Return) and child.value is not None and not (isinstance(child.value, ast.Constant) and child.value.value is None):
                    start = sum(len(line) for line in lines[:child.value.lineno - 1]) + child.value.col_offset
                    end = sum(len(line) for line in lines[:child.value.end_lineno - 1]) + child.value.end_col_offset
                    return text[:start] + 'None' + text[end:], node.name, child.lineno
    return None, None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--state', type=Path, default=ROOT / '.masa/ab2')
    ap.add_argument('--bench', required=True)
    ap.add_argument('--task', default='bank')
    ap.add_argument('--pick', type=int, default=0)
    ap.add_argument('--yuan', type=float, default=0.5)
    ap.add_argument('--start-level', type=int, default=2)
    ap.add_argument('--keep', default='probe')
    args = ap.parse_args()
    source = args.state / 'bench/state' / args.bench
    work = ROOT / '.masa' / args.keep / f'{args.task}-{int(time.time())}'
    shutil.copytree(source, work)
    store = Store(work)
    try:
        passed = [r for r in store.all_runs() if r['status'] == 'succeeded' and (r['data'].get('project_bundle') or {}).get('approval_ref')]
        base_run = passed[-1]
        bundle = store.read(base_run['data']['project_bundle']['approval_ref'])
        files, spec_ref = dict(bundle['files']), bundle['spec_approval_ref']
        spec = store.read(spec_ref)['spec']
        pack = registry.get('python')
        entry = pack.entrypoint(spec)
        candidates = sorted(p for p in files if pack.is_source(p) and p not in pack.seed_files(spec) and p != entry and files[p].strip())
        broken, name, line = None, None, None
        for path in candidates[args.pick:] + candidates[:args.pick]:
            broken, name, line = plant(files[path])
            if broken:
                target = path
                break
        if not broken:
            raise SystemExit('nothing to plant a bug in')
        files[target] = broken
        print(f'planted: {target} function {name} line {line} (package {pack.package_of(target, spec)})', flush=True)
        console = Console(work, ROOT / '.tools/bin/masa-runner.exe', ROOT / '.tools/go/bin/go.exe', ROOT)
        runner = console._runner()
        approval = store.put({'files': files, 'spec_approval_ref': spec_ref})
        approved = store.read(spec_ref)
        import tempfile
        with tempfile.TemporaryDirectory(prefix='probe-', dir=store.root) as temp:
            for rel, content in files.items():
                path = Path(temp) / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding='utf-8', newline='\n')
            failed = Runtime(store, runner).create(Path(temp), base_run['data']['goal'], Budget(model_calls=12, tool_calls=3, deadline_seconds=1800),
                                                  graph=Runtime.compile_project_checks(approved['spec'], approved['checks'], 'python'),
                                                  parent_run_id=base_run['data'].get('parent_run_id') or base_run['id'], project_bundle={'approval_ref': approval})
        result = Runtime(store, runner).execute(failed)
        print('verification of the planted bug:', result['status'], flush=True)
        if result['status'] != 'failed':
            raise SystemExit('the planted bug did not make the verification fail')
        from masa.application.usage import task_report
        spent = (task_report(store, base_run['id']).get('routing') or {}).get('spend') or {}  # 复制来的账本里已有的花费也算在预算里 / the spend already in the copied ledger counts against the budget too
        print('already spent in the copied ledger:', spent.get('cost'), 'yuan,', spent.get('calls'), 'calls', flush=True)
        body = {'routing': 'ladder', 'runtime': 'v2', 'language': 'python', 'api_profile_id': next(iter(console.settings.profiles), None),
                'budget': {'max_cloud_tokens': int(spent.get('cloud_tokens') or 0) + 400000, 'max_active_seconds': int(spent.get('active_seconds') or 0) + 1500, 'max_cost': round(float(spent.get('cost') or 0) + args.yuan, 4), 'max_model_calls': int(spent.get('calls') or 0) + 150},
                'policy': {'start_level_by_role': {'project_developer': args.start_level, 'project_repair': args.start_level}}}
        started = time.time()
        job_id = console.auto_fix(failed, body)['job_id']
        while console.project_job(job_id)['status'] == 'running':
            time.sleep(5)
        job = console.project_job(job_id)
        print('job:', job['status'], str(job.get('error') or '')[:200], round(time.time() - started), 's', flush=True)
        console._release_local_models()
        out = Store(work)
        try:
            from masa.application.usage import task_report
            rid = (job.get('result') or {}).get('id') or job.get('run_id')
            report = task_report(out, rid)
            print('outcome:', report['outcome'], 'cost', (report.get('routing') or {}).get('spend'), flush=True)
            for run in sorted(out.all_runs(), key=lambda r: r['data'].get('created_at', 0)):
                for event in out.events(run['id']):
                    if event['type'] in ('repair_scope', 'repair_worker_done', 'assertion_referee', 'workflow_node'):
                        print('  ', run['id'][:6], event['type'], json.dumps(event['payload'], ensure_ascii=False)[:300])
        finally:
            out.close()
    finally:
        store.close()


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
