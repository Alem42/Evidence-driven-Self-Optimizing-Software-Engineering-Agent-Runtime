"""审计实验：审计者能否发现“被故意改错的期望”？对每个任务的固定输入，把参考实现给出的正确期望一半原样、一半故意改错（两种分配各做一遍，每个用例都被测过“对”和“错”两种声明），
让审计者判断“声明的期望对不对”。比较 Flash 单次、Flash×3 多数表决（并行）、Pro 单次的召回、误报与修正正确率。
Audit experiment: can the auditor catch deliberately wrong expectations? For each task's fixed inputs, half of the correct expectations are kept and half corrupted (both assignments are run, so every case is tested as
both right and wrong). Compare Flash x1, Flash x3 majority (parallel) and Pro x1 on recall, false alarms and the accuracy of the correction.

用法 / usage:
    python scripts/eval/expectation_audit.py --run --yuan 1.5
    python scripts/eval/expectation_audit.py --report          # 追加到 docs/reference/TEST_RELIABILITY.md
"""
import argparse
import collections
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import expectation_accuracy as base  # noqa: E402

TASKS, PRICES, ROOT, key = base.TASKS, base.PRICES, base.ROOT, base.key
RAW = ROOT / '.masa/test_reliability/audit.json'
PLAN = (('deepseek-flash', 3), ('deepseek-v4-pro', 1))
SECTION = '## 审计实验'


def corrupt(stdout, code):
    """把正确期望改成确定不同的错误期望：数字加一、交换两行、改大小写、或改退出码。 Make a definitely different expectation: a number off by one, two lines swapped, case changed, or the exit code flipped."""
    m = re.search(r'[0-9]+', stdout)
    if m:
        n = int(m.group())
        return stdout[:m.start()] + str(n + 1 if n < 9 else n - 1) + stdout[m.end():], code
    lines = stdout.split(chr(10))
    full = [i for i, line in enumerate(lines) if line]
    if len(full) > 1 and lines[full[0]] != lines[full[1]]:
        lines[full[0]], lines[full[1]] = lines[full[1]], lines[full[0]]
        return chr(10).join(lines), code
    if stdout.strip() and stdout != stdout.swapcase():
        return stdout.swapcase(), code
    return stdout, (1 - code if code in (0, 1) else 0)


def context_of(task, parity):
    cases, truth = [], []
    for i, (args, stdin) in enumerate(task.cases):
        out, code = task.ref(args, stdin)
        wrong = i % 2 == parity
        stated = corrupt(out, code) if wrong else (out, code)
        if wrong and key(*stated) == key(out, code):
            wrong = False
        cases.append({'index': i, 'args': list(args), 'stdin': stdin, 'stated_stdout': stated[0], 'stated_exit_code': stated[1]})
        truth.append((wrong, key(out, code), key(*stated)))
    return {'purpose': 'expectation_auditor', 'goal': task.goal, 'cases': cases}, truth


def run(args):
    from masa.infrastructure.settings import Settings
    settings = Settings(ROOT / '.masa' / args.settings)
    ids = {p['model']: i for i, p in settings.profiles.items() if p['model'] in PRICES and p['enabled']}
    RAW.parent.mkdir(parents=True, exist_ok=True)
    raw = json.loads(RAW.read_text(encoding='utf-8')) if RAW.exists() else []
    done = {(r['task'], r['model'], r['parity'], r['sample']) for r in raw}
    jobs = [(t, m, par, n) for t in TASKS for m, c in PLAN for par in (0, 1) for n in range(c) if (t.id, m, par, n) not in done]
    lock, spent = threading.Lock(), [round(sum(r['cost'] for r in raw), 4)]

    def work(job):
        task, model, parity, n = job
        with lock:
            if spent[0] >= args.yuan:
                return None
        provider = settings.provider(ids[model])
        began = time.time()
        record = {'task': task.id, 'level': task.level, 'model': model, 'parity': parity, 'sample': n}
        try:
            record['answers'] = {a['index']: [a['stdout'], a['exit_code'], a['verdict']] for a in provider.respond(context_of(task, parity)[0])['answers']}
        except Exception as exc:
            record['error'] = type(exc).__name__ + ': ' + str(exc)[:120]
        usage = provider.usage or {}
        record.update(seconds=round(time.time() - began, 2), prompt_tokens=usage.get('prompt_tokens') or 0, completion_tokens=usage.get('completion_tokens') or 0)
        record['cost'] = round((record['prompt_tokens'] * PRICES[model][0] + record['completion_tokens'] * PRICES[model][1]) / 1e6, 5)
        with lock:
            raw.append(record)
            spent[0] = round(spent[0] + record['cost'], 4)
            RAW.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf-8')
        return record
    print(f'{len(jobs)} audit calls, spent {spent[0]}, cap {args.yuan}', flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, jobs))
    print('audit done, spent', spent[0])


def report():
    raw = json.loads(RAW.read_text(encoding='utf-8'))
    by = collections.defaultdict(list)
    for r in raw:
        by[(r['task'], r['model'], r['parity'])].append(r)
    stats = collections.defaultdict(collections.Counter)
    for (task_id, model, parity), samples in by.items():
        task = next(t for t in TASKS if t.id == task_id)
        _, truth = context_of(task, parity)
        samples = sorted(samples, key=lambda r: r['sample'])
        flash = model.endswith('flash')
        for i, (wrong, true_key, stated_key) in enumerate(truth):
            answers = [(s.get('answers') or {}).get(str(i)) for s in samples]
            for method, pool in (('Flash x1', answers[:1] if flash else None), ('Flash x3 表决', answers[:3] if flash else None), ('Pro x1', answers[:1] if not flash else None),
                                 ('Flash x1 推导对比', answers[:1] if flash else None), ('Flash x3 推导表决后对比', answers[:3] if flash else None), ('Pro x1 推导对比', answers[:1] if not flash else None)):
                if pool is None:
                    continue
                if '对比' in method:
                    # 不看模型自己的“对/错”判断，只把它推导出的值与声明的值机械地比较；多个样本时先对推导值做严格多数。
                    # Ignore the model's own verdict; mechanically compare its DERIVED value with the stated one; with several samples take a strict majority of the derived values first.
                    derived_votes = collections.Counter(key(a[0], a[1]) for a in pool if a is not None)
                    top = derived_votes.most_common(1)
                    flagged = bool(top) and top[0][1] * 2 > len(pool) and top[0][0] != stated_key
                else:
                    votes = sum(1 for a in pool if a is not None and a[2] == 'wrong')
                    flagged = votes * 2 > len(pool)
                st = stats[method]
                if wrong:
                    st['wrong'] += 1
                    st['caught'] += flagged
                    derived = collections.Counter(key(a[0], a[1]) for a in pool if a is not None)
                    st['fixed'] += bool(flagged and derived and derived.most_common(1)[0][0] == true_key)
                else:
                    st['right'] += 1
                    st['false_alarm'] += flagged
    # 盲推导对比：审计者根本不看声明的期望（用第一个实验里“只给输入”的推导样本），推导出来之后再与声明的期望机械比较。避免被声明的期望锚定。
    # Blind derive-and-compare: the auditor never sees the stated expectation (the samples of the first experiment, inputs only); the derived value is compared with the stated one afterwards. No anchoring.
    blind_raw = json.loads(base.RAW.read_text(encoding='utf-8')) if base.RAW.exists() else []
    blind_by = collections.defaultdict(list)
    for r in blind_raw:
        blind_by[(r['task'], r['model'])].append(r)
    for label, model, k in (('盲推导 Flash x1', 'deepseek-flash', 1), ('盲推导 Flash x3 表决', 'deepseek-flash', 3), ('盲推导 Flash x5 表决', 'deepseek-flash', 5),
                            ('盲推导 Pro x1', 'deepseek-v4-pro', 1), ('盲推导 Pro x3 表决', 'deepseek-v4-pro', 3)):
        st = stats[label]
        for task in TASKS:
            samples = sorted(blind_by.get((task.id, model), []), key=lambda r: r['sample'])[:k]
            if not samples:
                continue
            for parity in (0, 1):
                _, truth = context_of(task, parity)
                for i, (wrong, true_key, stated_key) in enumerate(truth):
                    answers = [(s_.get('answers') or {}).get(str(i)) for s_ in samples]
                    votes = collections.Counter(key(a[0], a[1]) for a in answers if a is not None)
                    top = votes.most_common(1)
                    flagged = bool(top) and top[0][1] * 2 > len(samples) and top[0][0] != stated_key
                    if wrong:
                        st['wrong'] += 1
                        st['caught'] += flagged
                        st['fixed'] += bool(flagged and top[0][0] == true_key)
                    else:
                        st['right'] += 1
                        st['false_alarm'] += flagged
    pct = lambda a, b: '—' if not b else str(round(100 * a / b)) + '%'  # noqa: E731
    cost = sum(r['cost'] for r in raw)
    lines = ['', SECTION + '：能否发现“被故意改错的期望”', '',
             '方法：对每个任务的固定输入，把参考实现给出的正确期望**一半原样、一半故意改错**（数字加一、交换两行、改大小写或改退出码；两种分配各做一遍，每个用例都被测过“对”和“错”两种声明），'
             '让审计者判断“声明的期望对不对”并给出自己推导的值。数据：`.masa/test_reliability/audit.json`（' + str(len(raw)) + ' 次调用，约 ¥' + format(cost, '.2f') + '）。', '',
             '| 方法 | 抓到错误的期望（召回） | 把正确的期望误报为错（误报率） | 抓到且改对（修正正确率） |', '|---|---|---|---|']
    for method in ('Flash x1', 'Flash x3 表决', 'Pro x1', 'Flash x1 推导对比', 'Flash x3 推导表决后对比', 'Pro x1 推导对比', '盲推导 Flash x1', '盲推导 Flash x3 表决', '盲推导 Flash x5 表决', '盲推导 Pro x1', '盲推导 Pro x3 表决'):
        st = stats[method]
        lines.append('| ' + method + ' | ' + pct(st['caught'], st['wrong']) + ' (' + str(st['caught']) + '/' + str(st['wrong']) + ') | ' + pct(st['false_alarm'], st['right']) + ' (' + str(st['false_alarm']) + '/' + str(st['right']) + ') | ' + pct(st['fixed'], st['wrong']) + ' |')
    text = chr(10).join(lines) + chr(10)
    doc = base.REPORT.read_text(encoding='utf-8')
    base.REPORT.write_text(doc.split(chr(10) + SECTION)[0].rstrip(chr(10)) + chr(10) + text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--yuan', type=float, default=1.5)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--settings', default='p2eval_final')
    a = ap.parse_args()
    if a.run:
        run(a)
    if a.report:
        report()
