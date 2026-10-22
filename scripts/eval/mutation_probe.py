"""变异探针：对已完成评测运行的最终代码做单点变异，量出每一套测试“能抓住多少真正的错误”。零 token（不调用任何模型）。
Mutation probe: apply single-point mutations to the final code of finished evaluation runs and measure how many REAL bugs each suite catches. Zero tokens (no model call).

对每个变异体做三件事 / for every mutant:
  · 单元测试（模型写的 tests/test_*.py，不含验收文件）杀死它吗？ do the unit tests (model-written, without the acceptance file) kill it?
  · 验收测试（程序生成的 tests/test_acceptance.py，只有开了 acceptance_cases 的运行才有）杀死它吗？ does the acceptance file (harness-generated; only runs with acceptance_cases have one) kill it?
  · 独立判官（评测题库的参考实现）认为它错吗？——错且测试没杀死 = 一个真实的“假通过”样本。 does the independent oracle reject it? Rejected AND not killed = a real false-pass sample.

用法 / usage:  python scripts/eval/mutation_probe.py .masa/vb2_new .masa/hard_acc1 --per-bundle 14 --workers 4
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
from masa.bench.oracle import judge  # noqa: E402
from masa.bench.runner import BY_ID  # noqa: E402
from masa.infrastructure.runner import Runner  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402
from masa.languages.python import mutants  # noqa: E402
from masa.languages.registry import registry  # noqa: E402

PACK = registry.get('python')
RUNNER = Runner(ROOT / '.tools/bin/masa-runner.exe')
ACCEPTANCE = 'tests/test_acceptance.py'
NO_WINDOW = {'creationflags': 0x08000000} if os.name == 'nt' else {}
ENV = {k: v for k, v in os.environ.items() if k.upper() in ('SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'PATH')}


def write(directory, files):
    for path, text in files.items():
        target = directory / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8', newline='\n')


def passes(directory):
    """这一套测试在这个目录上全部通过吗：交给**项目自己的受控运行器**（超时、作业对象 kill-on-close、内存与进程上限）——变异体可能死循环，自己起子进程会留下孤儿进程。
    Does the suite pass on this directory: handed to the project's own controlled runner (timeout, Job Object with kill-on-close, memory and process limits) - a mutant may loop forever and spawning processes ourselves leaves orphans."""
    request = {'protocol_version': 1, 'request_id': uuid.uuid4().hex, 'snapshot_id': 'mutation-probe', 'operation': PACK.op_id('test'), 'timeout_ms': 40000, 'max_output_bytes': 65536}
    result = RUNNER.execute(request, Path(directory), lambda: False)
    return result.get('status') == 'completed' and result.get('exit_code') == 0


def bundle_of(state, bench_id):
    store = Store(state / 'bench' / 'state' / bench_id)
    try:
        runs = [r for r in store.all_runs() if r['data'].get('project_bundle')]
        return store.read(runs[-1]['data']['project_bundle']['approval_ref'])['files'] if runs else None
    finally:
        store.close()


def probe(files, task, per_bundle, seed, workers):
    unit_files = {p: t for p, t in files.items() if p != ACCEPTANCE}
    has_acceptance = ACCEPTANCE in files
    implementation = [p for p in files if PACK.is_source(p) and not p.endswith(('__init__.py', '__main__.py')) and p not in PACK.seed_files({'module': 'x', 'files': []})]
    plan = []
    per_file = max(2, -(-per_bundle // max(1, len(implementation))))
    for path in implementation:
        plan += [(path, d, m) for d, m in mutants.generate(files[path], per_file, seed)]
    plan = plan[:per_bundle] if len(plan) <= per_bundle else [plan[i * len(plan) // per_bundle] for i in range(per_bundle)]

    def one(item):
        path, description, mutated = item
        with tempfile.TemporaryDirectory(prefix='mut-unit-', ignore_cleanup_errors=True) as unit, tempfile.TemporaryDirectory(prefix='mut-acc-', ignore_cleanup_errors=True) as acc:
            write(Path(unit), {**unit_files, path: mutated})
            if has_acceptance:
                write(Path(acc), {'tests/test_acceptance.py': files[ACCEPTANCE], **{p: t for p, t in files.items() if not PACK.is_test(p)}, path: mutated})
            unit_killed = not passes(Path(unit))
            acceptance_killed = (not passes(Path(acc))) if has_acceptance else None
        oracle_bad = not judge({**files, path: mutated}, task, None, 'python')['passed']
        return {'file': path, 'mutant': description, 'unit_killed': unit_killed, 'acceptance_killed': acceptance_killed, 'oracle_bad': oracle_bad}

    with tempfile.TemporaryDirectory(prefix='mut-base-', ignore_cleanup_errors=True) as base:
        write(Path(base), unit_files)
        if not passes(Path(base)):
            return None  # 原始代码的单元测试本来就不全过：不是一个干净的样本 / the original does not pass its own unit tests: not a clean sample
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, plan))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('folders', nargs='+')
    ap.add_argument('--per-bundle', type=int, default=14)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--out', type=Path, default=None)
    args = ap.parse_args()
    rows = []
    for folder in args.folders:
        state = Path(folder)
        summary = json.loads((state / 'summary.json').read_text(encoding='utf-8'))
        for run in summary['runs']:
            files = bundle_of(state, run['bench_id'])
            if not files:
                continue
            results = probe(files, BY_ID[run['task']], args.per_bundle, args.seed, args.workers)
            if results is None:
                print(f"{state.name:10} {run['task']:12} {run['arm']:5} skipped (its own unit tests do not pass)", flush=True)
                continue
            row = {'folder': state.name, 'task': run['task'], 'arm': run['arm'], 'round': run.get('round', 1), 'has_acceptance': ACCEPTANCE in files, 'mutants': results}
            rows.append(row)
            bad = [m for m in results if m['oracle_bad']]
            print(f"{state.name:10} {run['task']:12} {run['arm']:5} mutants {len(results):2}  oracle-rejects {len(bad):2}  unit kills {sum(m['unit_killed'] for m in bad)}/{len(bad)}"
                  + (f"  acceptance kills {sum(bool(m['acceptance_killed']) for m in bad)}/{len(bad)}" if row['has_acceptance'] else ''), flush=True)
    if args.out:
        args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    summarise(rows)
    return 0


def summarise(rows):
    print('\n== 汇总（只算判官认为“真的错了”的变异体）/ summary (only mutants the oracle rejects) ==')
    for label, selector in (('arm v2r (no acceptance file)', lambda r: not r['has_acceptance']), ('arm v2ra (with acceptance file)', lambda r: r['has_acceptance'])):
        chosen = [r for r in rows if selector(r)]
        total = sum(len(r['mutants']) for r in chosen)
        bad = [m for r in chosen for m in r['mutants'] if m['oracle_bad']]
        if not chosen:
            continue
        line = f"{label}: bundles {len(chosen)}, mutants {total}, real bugs {len(bad)}; unit tests caught {sum(m['unit_killed'] for m in bad)}"
        if chosen[0]['has_acceptance']:
            line += (f"; acceptance file caught {sum(bool(m['acceptance_killed']) for m in bad)}; either caught {sum(bool(m['unit_killed'] or m['acceptance_killed']) for m in bad)}"
                     f"; missed by unit but caught by acceptance {sum(bool(m['acceptance_killed']) and not m['unit_killed'] for m in bad)}")
        print(line)
        survivors = sum(1 for m in bad if not m['unit_killed'] and not m['acceptance_killed'])
        print(f"   real bugs that NO model-written suite caught (false-pass samples): {survivors}")


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
