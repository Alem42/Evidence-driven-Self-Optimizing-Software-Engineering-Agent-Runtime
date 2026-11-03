"""R3：加固阶段的探针放大。问题是：通过 Gate 之后，没看过实现的角色能不能可靠地找出 Gate 漏掉的缺陷？
R3: the hardening-stage probe, scaled up. The question: after the Gate, can roles that never saw the implementation reliably find the defects the Gate missed?

和上一版探针（adversary_probe.py，召回 22–36%、误报 63–74%）的区别 / what changed since the first probe:
1. 事实标准是干净的：用 R1 的库存程序（bench/workload_stock.py）。功能请求把格式写死，参考实现对**任何**输入都给出唯一正确答案，没有“规格没说”的灰区。
   A clean ground truth: R1's stock program. The requests pin the formats and the reference gives THE right answer for any input; no "the requirement is silent" grey zone.
2. “真实缺陷”是 Gate 漏掉的缺陷：对通过的版本做单点变异，只留下**能通过它自己全部测试**（单元 + 验收）的变异体；它们和参考实现在某个输入上不同，就是一个假通过样本。
   "Real defects" are defects the Gate missed: single-point mutants of a passed version that still pass ALL of its own tests (unit + acceptance); one that differs from the reference on some input is a false-pass sample.
3. 期望是结构化的（blind_oracle：精确的标准输出 + 退出码），比较是确定性的——不再需要另一个模型来判断“两段描述是不是同一个意思”。
   Expectations are structured (blind_oracle: exact stdout + exit code), so the comparison is deterministic - no model has to judge whether two descriptions mean the same.
4. 比较多种检测器与它们的“一致”组合：同族盲推导（1 次 / 3 次多数）、同族暴力参考实现、异族（本地模型）盲推导与参考实现；“两个独立来源给出同一个不同于实现的答案”才标记。
   Several detectors and their AGREEMENT combinations: same-family blind derivation (1 sample / majority of 3), a same-family brute-force reference, other-family (local model) blind derivation and reference; flag only when two
   independent sources give the same answer that differs from the implementation.

接入主流程的门槛（ROUTE_REVIEW §5.1）：召回 ≥ 60% 且误报 ≤ 15%；召回 < 30% 就放弃“自动找场景”。
The bar for the main flow (ROUTE_REVIEW §5.1): recall >= 60% and false positives <= 15%; recall < 30% means giving up "finding scenarios automatically".

用法 / usage:
  python scripts/eval/hardening_probe.py --from .masa/r1_flash --levels 3,6,9,12 --mutants 40 --max-yuan 1.0 [--local gemma4:12b]
"""
import argparse
import json
import random
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts' / 'eval'))
from adversary_probe import Counting, run_program, run_script  # noqa: E402
from masa.bench import workload_stock as workload  # noqa: E402
from masa.bench.oracle import normalize  # noqa: E402
from masa.infrastructure.runner import Runner  # noqa: E402
from masa.infrastructure.settings import Settings  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402
from masa.languages.python import mutants  # noqa: E402
from masa.languages.registry import registry  # noqa: E402

PACK = registry.get('python')
RUNNER = Runner(ROOT / '.tools/bin/masa-runner.exe')
BATCH = 10


def requirement(level):
    """独立检查者拿到的需求：最初的目标 + 依次做过的修改（就是用户实际说过的话）。 What an independent checker gets: the original goal plus the changes in order (exactly what the user said)."""
    text = workload.BASE_GOAL
    if level:
        text += '\n\n之后依次做了这些修改（后面的修改优先）/ then these changes, in order (later ones win):\n' + '\n'.join(f'{i}. {c}' for i, c in enumerate(workload.CHANGES[:level], 1))
    return text


def version_at(folder, level):
    data = json.loads((folder / 'r1.json').read_text(encoding='utf-8'))
    step = next((s for s in data['steps'] if s['step'] == level and s['outcome'] == 'succeeded'), None)
    if step is None:
        return None, None
    store = Store(folder / 'state')
    try:
        bundle = store.read(store.run(step['latest'])['data']['project_bundle']['approval_ref'])
        spec = store.read(bundle['spec_approval_ref'])['spec']
    finally:
        store.close()
    return bundle['files'], spec


def passes_own_tests(files):
    with tempfile.TemporaryDirectory(prefix='hard-', ignore_cleanup_errors=True) as directory:
        for path, text in files.items():
            target = Path(directory) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding='utf-8', newline='\n')
        request = {'protocol_version': 1, 'request_id': uuid.uuid4().hex, 'snapshot_id': 'hardening-probe', 'operation': PACK.op_id('test'), 'timeout_ms': 60000, 'max_output_bytes': 65536}
        result = RUNNER.execute(request, Path(directory), lambda: False)
        return result.get('status') == 'completed' and result.get('exit_code') == 0


def survivors(files, wanted, seed, workers):
    """Gate 漏掉的变异体：能通过自己全部测试的单点变异。 Mutants the Gate misses: single-point mutations that pass all of the program's own tests."""
    implementation = [p for p in files if PACK.is_source(p) and not p.endswith(('__init__.py', '__main__.py'))]
    plan = []
    for path in implementation:
        plan += [(path, d, m) for d, m in mutants.generate(files[path], max(4, wanted // max(1, len(implementation)) * 3), seed)]
    random.Random(seed).shuffle(plan)
    plan = plan[:wanted * 3]

    def one(item):
        path, description, mutated = item
        return (path, description, mutated) if passes_own_tests({**files, path: mutated}) else None
    with ThreadPoolExecutor(max_workers=workers) as pool:
        kept = [r for r in pool.map(one, plan) if r]
    return kept[:wanted], len(plan)


def behaviour(out):
    return normalize(out[0]), out[2]


def derive(provider, level, spec, inputs, order_seed):
    """blind_oracle 分批推导每个输入的精确输出；order_seed 打乱顺序（同一个模型多次采样时让样本之间少一点相关）。 Derive the exact output per input in batches; order_seed shuffles the order to decorrelate repeated samples."""
    order = list(range(len(inputs)))
    if order_seed:
        random.Random(order_seed).shuffle(order)
    out = {}
    for start in range(0, len(order), BATCH):
        chunk = order[start:start + BATCH]
        views = [{'index': k, 'args': inputs[i]['args'], 'stdin': inputs[i]['stdin']} for k, i in enumerate(chunk)]
        try:
            answer = provider.respond({'purpose': 'blind_oracle', 'goal': requirement(level), 'acceptance': spec['acceptance'], 'inputs': views, 'language': 'python'})
        except Exception as exc:  # 一批失败只让这批弃权 / a failed batch only abstains
            print(f'      blind_oracle batch failed: {str(exc)[:100]}', flush=True)
            continue
        for a in answer.get('answers', []):
            if isinstance(a, dict) and isinstance(a.get('index'), int) and 0 <= a['index'] < len(chunk):
                out[chunk[a['index']]] = (normalize(str(a.get('stdout', '')).replace('\\n', '\n')), int(a.get('exit_code', 0)))
    return out


def reference(provider, level, spec):
    try:
        lines = provider.respond({'purpose': 'reference_oracle', 'goal': requirement(level), 'acceptance': spec['acceptance'], 'language': 'python'})['lines']
        return chr(10).join(str(line) for line in lines)
    except Exception as exc:
        print(f'      reference_oracle failed: {str(exc)[:100]}', flush=True)
        return None


def adversarial_inputs(provider, level, spec, rounds):
    inputs, known = [], [c['stdin'] for c in (spec.get('cases') or [])][:16]
    for _ in range(rounds):
        try:
            got = provider.respond({'purpose': 'qa_adversary', 'goal': requirement(level), 'acceptance': spec['acceptance'], 'known_inputs': known[-24:], 'language': 'python'})['inputs']
        except Exception as exc:
            print(f'      qa_adversary failed: {str(exc)[:100]}', flush=True)
            continue
        for item in got:
            if isinstance(item, dict) and isinstance(item.get('stdin'), str) and isinstance(item.get('args'), list) and len(item['stdin']) <= 800 \
                    and item['stdin'] not in [i['stdin'] for i in inputs if i['args'] == item['args']]:
                inputs.append({'name': str(item.get('name', ''))[:80], 'args': [str(a) for a in item['args']][:6], 'stdin': item['stdin']})
                known.append(item['stdin'])
    return inputs


def majority(samples, index):
    votes = [s[index] for s in samples if index in s]
    for value in votes:
        if votes.count(value) >= 2:
            return value
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--from', dest='folder', type=Path, required=True)
    ap.add_argument('--levels', default='3,6,9,12')
    ap.add_argument('--mutants', type=int, default=40)
    ap.add_argument('--rounds', type=int, default=3, help='qa_adversary 调用次数（每次 6–10 个输入）/ qa_adversary calls (6-10 inputs each)')
    ap.add_argument('--samples', type=int, default=3)
    ap.add_argument('--local', default=None, help='异族检测器用的本地模型（Ollama 名）/ the local model of the other-family detectors')
    ap.add_argument('--source', type=Path, default=ROOT / '.masa/p2eval_final')
    ap.add_argument('--max-yuan', type=float, default=1.0)
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--out', type=Path, default=None)
    args = ap.parse_args()
    settings = Settings(args.source)
    flash = Counting(settings.provider(next(i for i, p in settings.profiles.items() if p['model'] == 'deepseek-flash')))
    local = None
    if args.local:
        local_id = next(i for i, p in settings.profiles.items() if p['model'] == args.local)
        settings.profiles[local_id]['enabled'] = True
        local = settings.provider(local_id)
    rows = []
    for level in [int(x) for x in args.levels.split(',')]:
        if flash.yuan >= args.max_yuan:
            print(f'预算到了 / budget reached ¥{flash.yuan:.3f}')
            break
        files, spec = version_at(args.folder, level)
        if files is None:
            print(f'level {level}: no passed version in {args.folder}', flush=True)
            continue
        print(f'== level {level}: {len(files)} files', flush=True)
        kept, tried = survivors(files, args.mutants, args.seed + level, args.workers)
        print(f'   Gate-surviving mutants: {len(kept)} of {tried} tried', flush=True)
        inputs = adversarial_inputs(flash, level, spec, args.rounds)
        print(f'   adversarial inputs: {len(inputs)}', flush=True)
        truth = [(normalize(workload.run(level, i['args'], i['stdin'])[0]), workload.run(level, i['args'], i['stdin'])[1]) for i in inputs]
        sources = {}
        flash_samples = [derive(flash, level, spec, inputs, seed) for seed in range(args.samples)]
        sources['F1'] = flash_samples[0]
        sources['F3maj'] = {i: v for i in range(len(inputs)) if (v := majority(flash_samples, i)) is not None}
        ref_source = reference(flash, level, spec)
        if ref_source:
            sources['FR'] = {i: behaviour(run_script(ref_source, x['args'], x['stdin'])) for i, x in enumerate(inputs)}
        if local is not None:
            sources['L1'] = derive(local, level, spec, inputs, 0)
            local_ref = reference(local, level, spec)
            if local_ref:
                sources['LR'] = {i: behaviour(run_script(local_ref, x['args'], x['stdin'])) for i, x in enumerate(inputs)}
            try:
                local.unload()
            except Exception:
                pass

        def agree(*names):
            out = {}
            for i in range(len(inputs)):
                values = [sources[n].get(i) for n in names if n in sources]
                if len(values) == len(names) and None not in values and all(v == values[0] for v in values):
                    out[i] = values[0]
            return out
        for combo in (('F1', 'FR'), ('F3maj', 'FR'), ('F1', 'L1'), ('FR', 'LR'), ('F1', 'LR'), ('F3maj', 'LR')):
            if all(c in sources for c in combo):
                sources['+'.join(combo)] = agree(*combo)
        accuracy = {name: (sum(1 for i, v in src.items() if v == truth[i]), len(src)) for name, src in sources.items()}
        variants = [('original', None, files)] + [(f'{p}:{d}', p, {**files, p: m}) for p, d, m in kept]

        def outputs_of(variant):
            name, _, program = variant
            return [behaviour(run_program(program, spec['module'], x['args'], x['stdin'])) for x in inputs]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            outputs = list(pool.map(outputs_of, variants))
        # 每个检测器：在每个 (程序, 输入) 上，期望 != 程序输出 就标记 / per detector: flag on (program, input) when the expectation differs from the program's output
        stats = {}
        for name, src in sources.items():
            tp = fp = fn = actionable = 0
            detected, defects, alarms, finds = 0, 0, 0, 0
            for v, outs in enumerate(outputs):
                wrong_here = [i for i in range(len(inputs)) if outs[i] != truth[i]]
                flags = [i for i, value in src.items() if value != outs[i]]
                tp += len(set(flags) & set(wrong_here))
                fp += len(set(flags) - set(wrong_here))
                fn += len(set(wrong_here) - set(flags))
                actionable += sum(1 for i in set(flags) & set(wrong_here) if src[i] == truth[i])
                if v == 0:
                    alarms = len(set(flags) - set(wrong_here))  # 原程序在这些输入上是对的却被标记 / flagged although the original is right there
                    finds = len(set(flags) & set(wrong_here))  # 原程序真的错了并被标记：一个真实的假通过被发现 / the original really is wrong there: a real false pass found
                elif wrong_here:
                    defects += 1
                    detected += bool(set(flags) & set(wrong_here))
            stats[name] = {'tp': tp, 'fp': fp, 'fn': fn, 'actionable': actionable, 'precision': round(tp / (tp + fp), 3) if tp + fp else None,
                           'recall': round(tp / (tp + fn), 3) if tp + fn else None, 'defects': defects, 'detected': detected,
                           'false_alarms_on_original': alarms, 'finds_on_original': finds, 'covered': len(src), 'correct_expectations': accuracy[name][0]}
        exposed = sum(1 for outs in outputs[1:] if any(outs[i] != truth[i] for i in range(len(inputs))))
        row = {'level': level, 'inputs': len(inputs), 'mutants_tried': tried, 'survivors': len(kept), 'survivors_exposed_by_inputs': exposed,
               'original_wrong': sum(1 for i in range(len(inputs)) if outputs[0][i] != truth[i]), 'detectors': stats, 'yuan_so_far': round(flash.yuan, 4),
               'inputs_detail': [{'name': x['name'], 'args': x['args'], 'stdin': x['stdin'][:200], 'truth': truth[i][0][:200], 'truth_code': truth[i][1],
                                  'expectations': {n: (s[i][0][:200], s[i][1]) if i in s else None for n, s in sources.items() if '+' not in n}} for i, x in enumerate(inputs)]}
        rows.append(row)
        print(f"   original program wrong on {row['original_wrong']} inputs; survivors exposed by the inputs: {exposed}/{len(kept)}", flush=True)
        for name, s in stats.items():
            print(f"   {name:10} expectations right {s['correct_expectations']}/{s['covered']}  precision {s['precision']}  recall {s['recall']}  defects detected {s['detected']}/{s['defects']}"
                  f"  false alarms on the original {s['false_alarms_on_original']}/{len(inputs)}", flush=True)
        if args.out:
            args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    print()
    print(f'== 汇总 / total (¥{flash.yuan:.3f}) ==')
    names = sorted({n for r in rows for n in r['detectors']}, key=lambda n: (n.count('+'), n))
    for name in names:
        tp = sum(r['detectors'][name]['tp'] for r in rows if name in r['detectors'])
        fp = sum(r['detectors'][name]['fp'] for r in rows if name in r['detectors'])
        fn = sum(r['detectors'][name]['fn'] for r in rows if name in r['detectors'])
        det = sum(r['detectors'][name]['detected'] for r in rows if name in r['detectors'])
        dfc = sum(r['detectors'][name]['defects'] for r in rows if name in r['detectors'])
        alarms = sum(r['detectors'][name]['false_alarms_on_original'] for r in rows if name in r['detectors'])
        n_inputs = sum(r['inputs'] for r in rows if name in r['detectors'])
        right = sum(r['detectors'][name]['correct_expectations'] for r in rows if name in r['detectors'])
        cov = sum(r['detectors'][name]['covered'] for r in rows if name in r['detectors'])
        print(f"{name:12} expectations right {right}/{cov}  precision {tp / (tp + fp) if tp + fp else float('nan'):.2f}  recall {tp / (tp + fn) if tp + fn else float('nan'):.2f}"
              f"  defects detected {det}/{dfc}  false alarms on correct programs {alarms}/{n_inputs}")
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
