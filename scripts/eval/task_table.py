"""任务难度与成本表：把真实运行（默认流程，指挥者/best-of-N 关闭）的通过率、轮数、耗时、token、费用，与“模型当测试作者的期望准确率”并排，写进 docs/reference/TEST_RELIABILITY.md。
Task difficulty and cost table: pass rate, rounds, time, tokens and cost of real runs (default loop, conductor and best-of-N off) next to the "model as test author" expectation accuracy.

用法 / usage:  python scripts/eval/task_table.py
"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import expectation_accuracy as base  # noqa: E402

ROOT, TASKS = base.ROOT, base.TASKS
SECTION = '## 任务难度与成本（真实运行，默认流程）'


def main():
    runs = []
    for path in ROOT.glob('.masa/p2eval*/summary.json'):
        runs += [r for r in json.loads(path.read_text(encoding='utf-8')).get('runs', []) if r['arm'] == 'off']
    by = collections.defaultdict(list)
    for r in runs:
        by[r['task']].append(r)
    raw = json.loads(base.RAW.read_text(encoding='utf-8'))
    flash = collections.defaultdict(list)
    pro = collections.defaultdict(list)
    for r in raw:
        (flash if r['model'].endswith('flash') else pro)[r['task']].append(r)
    task_by_id = {t.id: t for t in TASKS}
    pct = lambda x: '—' if x is None else str(round(100 * x)) + '%'  # noqa: E731
    mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
    lines = ['', SECTION, '',
             '来源：`.masa/p2eval*/summary.json` 里“关”组（默认流程）的 ' + str(len(runs)) + ' 次真实运行（Pro 做规划/测试/诊断，Flash 做生成/修复；费用按保守价格估算）。样本很小（每任务 0–6 次），只作参考，不是统计结论。'
             ' “期望准确率”来自上一节：Pro 单次推导该任务固定输入的期望输出与参考实现一致的比例，越低说明测试越难写对。', '',
             '| 任务 | 等级 | 运行数 | 通过 | 平均验证轮数 | 平均耗时 | 平均云端 token | 平均费用 | Pro 期望准确率 | 难度（1−准确率） |', '|---|---|---|---|---|---|---|---|---|---|']
    for task in TASKS:
        rs = by.get(task.id, [])
        acc = base.accuracy(sorted(pro[task.id], key=lambda r: r['sample']), task)[0] if pro.get(task.id) else None
        if not rs:
            lines.append('| ' + task.id + ' | L' + str(task.level) + ' | 0 | — | — | — | — | — | ' + pct(acc) + ' | ' + ('—' if acc is None else format(1 - acc, '.2f')) + ' |')
            continue
        passed = sum(r['status'] == 'pass' for r in rs)
        lines.append('| ' + task.id + ' | L' + str(task.level) + ' | ' + str(len(rs)) + ' | ' + str(passed) + '/' + str(len(rs)) + ' | ' + format(mean([r['rounds'] for r in rs]), '.1f') + ' | '
                     + format(mean([r['seconds'] for r in rs]), '.0f') + 's | ' + format(mean([r['cloud_tokens'] for r in rs]), '.0f') + ' | ¥' + format(mean([r['cost'] for r in rs]), '.3f') + ' | '
                     + pct(acc) + ' | ' + ('—' if acc is None else format(1 - acc, '.2f')) + ' |')
    text = chr(10).join(lines) + chr(10)
    doc = base.REPORT.read_text(encoding='utf-8')
    head = doc.split(chr(10) + SECTION)[0]
    tail = ''
    if chr(10) + '## 审计实验' in doc:
        tail = chr(10) + '## 审计实验' + doc.split(chr(10) + '## 审计实验', 1)[1].split(chr(10) + SECTION)[0]
        head = head.split(chr(10) + '## 审计实验')[0]
    base.REPORT.write_text(head.rstrip(chr(10)) + chr(10) + text + tail, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
