"""断言裁判的可靠性：对评测题库的每个固定用例，把参考实现给出的正确输出和一个故意改错的输出打乱成 A/B，让便宜模型（Flash）二选一；看单次和 3 次多数选对的比例。
Reliability of the assertion referee: for every fixed benchmark case, the reference output and a deliberately corrupted one are shuffled into A/B and a cheap model (Flash) must pick; single-sample and three-sample majority accuracy.

用法 / usage:  python scripts/eval/referee_eval.py --run --yuan 0.2 --report
"""
import argparse
import collections
import hashlib
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import expectation_accuracy as base  # noqa: E402
import expectation_audit as audit  # noqa: E402

ROOT, TASKS = base.ROOT, base.TASKS
RAW = ROOT / '.masa/test_reliability/referee.json'
SECTION = '## 断言裁判：二选一能选对吗'
SAMPLES = 3


def build(task):
    cases, truth = [], []
    for i, (args, stdin) in enumerate(task.cases):
        out, code = task.ref(args, stdin)
        wrong, _ = audit.corrupt(out, code)
        if base.key(wrong, code) == base.key(out, code):
            continue
        swap = int(hashlib.sha1(f'{task.id}{i}'.encode()).hexdigest(), 16) % 2 == 1
        a, b = (wrong, out) if swap else (out, wrong)
        cases.append({'index': len(cases), 'call': f'the stdout of the program for arguments {list(args)} and stdin {stdin!r}', 'test_source': '', 'options': {'A': a, 'B': b}})
        truth.append('A' if not swap else 'B')
    return cases, truth


def run(args):
    from masa.infrastructure.settings import Settings
    settings = Settings(ROOT / '.masa' / args.settings)
    flash = next(i for i, p in settings.profiles.items() if p['model'] == 'deepseek-flash' and p['enabled'])
    RAW.parent.mkdir(parents=True, exist_ok=True)
    raw = json.loads(RAW.read_text(encoding='utf-8')) if RAW.exists() else []
    done = {(r['task'], r['sample']) for r in raw}
    lock, spent = threading.Lock(), [round(sum(r['cost'] for r in raw), 4)]

    def work(job):
        task, n = job
        with lock:
            if spent[0] >= args.yuan:
                return
        cases, truth = build(task)
        provider = settings.provider(flash)
        record = {'task': task.id, 'sample': n, 'truth': truth}
        try:
            record['choices'] = {a['index']: a['choice'] for a in provider.respond({'purpose': 'assertion_referee', 'goal': task.goal, 'acceptance': [], 'cases': cases})['answers']}
        except Exception as exc:
            record['error'] = type(exc).__name__
        usage = provider.usage or {}
        record['cost'] = round(((usage.get('prompt_tokens') or 0) * 1.0 + (usage.get('completion_tokens') or 0) * 4.0) / 1e6, 5)
        with lock:
            raw.append(record)
            spent[0] = round(spent[0] + record['cost'], 4)
            RAW.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
    jobs = [(t, n) for t in TASKS for n in range(SAMPLES) if (t.id, n) not in done]
    print(len(jobs), 'calls; spent', spent[0], 'cap', args.yuan, flush=True)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(work, jobs))
    print('done; spent', spent[0])


def report():
    raw = json.loads(RAW.read_text(encoding='utf-8'))
    by = collections.defaultdict(list)
    for r in raw:
        by[r['task']].append(r)
    single = single_n = neither = maj = maj_n = abstain = 0
    for rs in by.values():
        truth = rs[0]['truth']
        for i, answer in enumerate(truth):
            picks = [(r.get('choices') or {}).get(str(i)) for r in rs]
            for p in picks:
                single_n += 1
                single += p == answer
                neither += p == 'neither'
            votes = collections.Counter(p for p in picks if p in ('A', 'B'))
            top = votes.most_common(1)
            if top and top[0][1] * 2 > len(rs) and len([c for c in votes.values() if c == top[0][1]]) == 1:
                maj_n += 1
                maj += top[0][0] == answer
            else:
                abstain += 1
    pct = lambda a, b: '—' if not b else str(round(100 * a / b)) + '%'  # noqa: E731
    cost = sum(r['cost'] for r in raw)
    lines = ['', SECTION, '', '方法：每个任务的固定用例里，把参考实现的正确输出和一个故意改错的输出（数字加一、换行序、改大小写、改退出码）**打乱成 A/B**，不标明哪个是“测试写的”哪个是“实现给的”，让 Flash 二选一，每批独立采样 3 次。数据：`.masa/test_reliability/referee.json`（'
             + str(len(raw)) + ' 次调用，约 ¥' + format(cost, '.2f') + '）。', '',
             '| 指标 | 数值 |', '|---|---|', f'| 单次选对 | {pct(single, single_n)} ({single}/{single_n}) |', f'| 单次回答“neither” | {pct(neither, single_n)} |',
             f'| 3 次严格多数选对（未弃权的） | {pct(maj, maj_n)} ({maj}/{maj_n}) |', f'| 3 次没有严格多数（弃权） | {pct(abstain, abstain + maj_n)} |', '',
             '对比：直接问“这个期望对不对”的召回只有 26–33%（被锚定）；盲推导再比较的召回 91%。']
    doc = base.REPORT.read_text(encoding='utf-8')
    base.REPORT.write_text(doc.split(chr(10) + SECTION)[0].rstrip(chr(10)) + chr(10) + chr(10).join(lines) + chr(10), encoding='utf-8')
    print(chr(10).join(lines[-8:]))


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--yuan', type=float, default=0.2)
    ap.add_argument('--settings', default='p2eval_final')
    a = ap.parse_args()
    if a.run:
        run(a)
    if a.report:
        report()
