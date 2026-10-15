"""测试可靠性的量化：让模型当“测试作者”，对评测题库里**固定的输入**推导期望输出，再与独立的 Python 参考实现逐用例比对。
Quantify test reliability: a model acts as the test author and derives the expected output for the benchmark's FIXED inputs; the answers are compared case by case with the independent Python reference.

测的是：单个模型的期望准确率（Flash / Pro）、多数表决后的准确率（Flash×3、Flash×5、Pro×3）、没有多数时的弃权率、每次调用的耗时与 token。采样并行（每个线程一个独立的 provider）。
It measures: single-model expectation accuracy (Flash / Pro), accuracy after a majority vote (Flash x3, x5, Pro x3), the abstention rate when there is no majority, and time and tokens per call. Samples run in parallel (one provider per thread).

用法 / usage:
    python scripts/eval/expectation_accuracy.py --run --yuan 3.5     # 调用 API，结果增量写入 .masa/test_reliability/raw.json，可中断后继续
    python scripts/eval/expectation_accuracy.py --report            # 由 raw.json 生成 docs/reference/TEST_RELIABILITY.md
"""
import argparse
import collections
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from masa.bench import oracle  # noqa: E402
from masa.bench.tasks import TASKS  # noqa: E402

PRICES = {'deepseek-v4-pro': (9.0, 27.0), 'deepseek-flash': (1.0, 4.0)}  # 元/百万 token，保守估算（Pro 取高峰价）/ yuan per million tokens, conservative
PLAN = (('deepseek-flash', 5), ('deepseek-v4-pro', 3))  # 每个任务每个模型的采样数 / samples per task per model
RAW = ROOT / '.masa/test_reliability/raw.json'
REPORT = ROOT / 'docs/reference/TEST_RELIABILITY.md'


def key(stdout, code):
    return (oracle.normalize(stdout), int(code))


def context_of(task):
    return {'purpose': 'expectation_deriver', 'goal': task.goal,
            'cases': [{'index': i, 'args': list(args), 'stdin': stdin} for i, (args, stdin) in enumerate(task.cases)]}


def run(args):
    from masa.infrastructure.settings import Settings
    settings = Settings(ROOT / '.masa' / args.settings)
    ids = {p['model']: i for i, p in settings.profiles.items() if p['model'] in PRICES and p['enabled']}
    if len(ids) != 2:
        raise SystemExit('both cloud profiles are required in .masa/' + args.settings)
    RAW.parent.mkdir(parents=True, exist_ok=True)
    raw = json.loads(RAW.read_text(encoding='utf-8')) if RAW.exists() else []
    done = collections.Counter((r['task'], r['model']) for r in raw)
    jobs = [(task, model, n) for task in TASKS for model, count in PLAN for n in range(count) if n >= done[(task.id, model)]]
    lock = threading.Lock()
    spent = [round(sum(r['cost'] for r in raw), 4)]
    started = time.time()

    def work(job):
        task, model, n = job
        with lock:
            if spent[0] >= args.yuan:
                return None
        provider = settings.provider(ids[model])
        began = time.time()
        record = {'task': task.id, 'level': task.level, 'model': model, 'sample': n, 'cases': len(task.cases)}
        try:
            answer = provider.respond(context_of(task))
            record['answers'] = {a['index']: [a['stdout'], a['exit_code']] for a in answer['answers']}
        except Exception as exc:  # 记录失败也算一次样本（准确率按失败计）/ a failure is a sample too (counted as wrong)
            record['error'] = f'{type(exc).__name__}: {str(exc)[:120]}'
        usage = provider.usage or {}
        p_in, p_out = PRICES[model]
        record.update(seconds=round(time.time() - began, 2), prompt_tokens=usage.get('prompt_tokens') or 0, completion_tokens=usage.get('completion_tokens') or 0)
        record['cost'] = round((record['prompt_tokens'] * p_in + record['completion_tokens'] * p_out) / 1e6, 5)
        with lock:
            raw.append(record)
            spent[0] = round(spent[0] + record['cost'], 4)
            RAW.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
        return record

    print(f'{len(jobs)} 次调用待做，已花 {spent[0]} 元，上限 {args.yuan} 元，并行 {args.workers}', flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for record in pool.map(work, jobs):
            if record:
                print(f"{record['task']:<9}{record['model'][-5:]:>6}#{record['sample']} {record['seconds']:>5}s {record['cost']:.4f}元 {'ERR ' + record['error'] if 'error' in record else ''}", flush=True)
    print(f'完成：墙钟 {time.time() - started:.0f} 秒，累计 {spent[0]} 元')


def accuracy(samples, task, vote=None):
    """一组样本在某任务上的准确率。vote=None：每个样本各算一次再平均；vote=k：取前 k 个样本多数表决，返回 (准确率, 覆盖率)。
    Accuracy of a set of samples on a task. vote=None: mean over single samples; vote=k: majority of the first k samples, returning (accuracy, coverage)."""
    truth = [key(*task.ref(args, stdin)) for args, stdin in task.cases]

    def answer(sample, i):
        a = (sample.get('answers') or {}).get(str(i)) or (sample.get('answers') or {}).get(i)
        return key(a[0], a[1]) if a else None
    if vote is None:
        right = [sum(answer(s, i) == truth[i] for i in range(len(truth))) / len(truth) for s in samples]
        return sum(right) / len(right), 1.0
    pool = samples[:vote]
    covered = correct = 0
    for i in range(len(truth)):
        votes = collections.Counter(a for a in (answer(s, i) for s in pool) if a is not None)
        if not votes:
            continue
        (top, count), = votes.most_common(1)
        tied = [k for k, c in votes.items() if c == count]
        if len(tied) > 1 or count * 2 <= len(pool):  # 没有严格多数：弃权 / no strict majority: abstain
            continue
        covered += 1
        correct += top == truth[i]
    return (correct / covered if covered else None), covered / len(truth)


def report():
    raw = json.loads(RAW.read_text(encoding='utf-8'))
    by = collections.defaultdict(list)
    for r in raw:
        by[(r['task'], r['model'])].append(r)
    flash, pro = 'deepseek-flash', 'deepseek-v4-pro'
    rows, totals = [], collections.defaultdict(list)
    for task in TASKS:
        f, p = sorted(by[(task.id, flash)], key=lambda r: r['sample']), sorted(by[(task.id, pro)], key=lambda r: r['sample'])
        if not f or not p:
            continue
        f1, _ = accuracy(f, task)
        p1, _ = accuracy(p, task)
        f3, c3 = accuracy(f, task, 3)
        f5, c5 = accuracy(f, task, 5)
        p3, pc3 = accuracy(p, task, 3)
        secs = lambda rs: sum(r['seconds'] for r in rs) / len(rs)  # noqa: E731
        toks = lambda rs: sum(r['prompt_tokens'] + r['completion_tokens'] for r in rs) / len(rs)  # noqa: E731
        cost = lambda rs: sum(r['cost'] for r in rs) / len(rs)  # noqa: E731
        rows.append((task, len(task.cases), f1, f3, c3, f5, c5, p1, p3, pc3, secs(f), secs(p), toks(f), toks(p), cost(f), cost(p)))
        for name, value in (('f1', f1), ('f3', f3), ('f5', f5), ('p1', p1), ('p3', p3)):
            if value is not None:
                totals[name].append((task.level, value))
    pct = lambda x: '—' if x is None else f'{100 * x:.0f}%'  # noqa: E731
    mean = lambda xs: sum(xs) / len(xs) if xs else None  # noqa: E731
    lines = ['# 测试可靠性：模型当“测试作者”推导期望值的准确率', '',
             f'生成于 {time.strftime("%Y-%m-%d")}。方法：对评测题库每个任务的**固定输入**（`tasks.py` 的 cases），让模型根据需求推导期望的 stdout 与退出码，与独立的 Python 参考实现逐用例比对（行尾空白与末尾空行宽容，内容不宽容）。'
             f'每个任务采样 Flash×5、Pro×3，采样并行。数据：`.masa/test_reliability/raw.json`（{len(raw)} 次调用，花费约 ¥{sum(r["cost"] for r in raw):.2f}，价格为保守估算）。', '',
             '**读法**：准确率 = 推导对的用例占比。“表决 k”= 取前 k 个样本，严格多数的答案算数；没有严格多数的用例**弃权**（生产里等于丢弃这个用例），所以同时给“覆盖率”。表决后的准确率只统计未弃权的用例。', '',
             '| 任务 | 等级 | 用例 | Flash 单次 | Flash 表决3 (覆盖) | Flash 表决5 (覆盖) | Pro 单次 | Pro 表决3 (覆盖) | 耗时 Flash/Pro | token Flash/Pro | ¥/次 Flash/Pro |', '|---|---|---|---|---|---|---|---|---|---|---|']
    for t, n, f1, f3, c3, f5, c5, p1, p3, pc3, sf, sp, tf, tp, cf, cp in rows:
        lines.append(f'| {t.id} | L{t.level} | {n} | {pct(f1)} | {pct(f3)} ({pct(c3)}) | {pct(f5)} ({pct(c5)}) | {pct(p1)} | {pct(p3)} ({pct(pc3)}) | {sf:.1f}s / {sp:.1f}s | {tf:.0f} / {tp:.0f} | {cf:.4f} / {cp:.4f} |')
    lines += ['', '## 汇总（按任务平均）', '', '| 方法 | 全部任务 | L0–L3 | L4–L6 | L7–L9 |', '|---|---|---|---|---|']
    names = {'f1': 'Flash 单次', 'f3': 'Flash 表决 3', 'f5': 'Flash 表决 5', 'p1': 'Pro 单次', 'p3': 'Pro 表决 3'}
    for k, label in names.items():
        sel = lambda lo, hi: mean([v for lv, v in totals[k] if lo <= lv <= hi])  # noqa: E731
        lines.append(f'| {label} | {pct(sel(0, 9))} | {pct(sel(0, 3))} | {pct(sel(4, 6))} | {pct(sel(7, 9))} |')
    ok = [r for r in raw if 'error' not in r]
    lines += ['', f'失败的调用（计为全错）：{len(raw) - len(ok)} / {len(raw)}。',
              '', '## 怎么用这些数字', '- **测试难度**：Pro 单次准确率越低、样本之间越不一致的任务，期望越难算对；这些任务里“测试写错”的概率高，应当给测试更多的核对（表决、独立推导）。',
              '- **便宜的多数表决能否替代贵的单次**：比较“Flash 表决 3/5”与“Pro 单次”的准确率与费用（Flash 单价约为 Pro 的 1/9）。',
              '- **弃权的代价**：覆盖率低说明很多用例没有多数，生产里要么丢弃这些用例，要么交给更强的模型裁决。']
    REPORT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines[-22:]))


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--yuan', type=float, default=3.5)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--settings', default='p2eval_final', help='.masa 下的设置副本（要有 Flash 与 Pro）/ the settings copy under .masa (needs Flash and Pro)')
    a = ap.parse_args()
    if a.run:
        run(a)
    if a.report:
        report()
