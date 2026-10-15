"""把所有真实实验的结果汇成一份 JSON，给前端“实验结果”页画表和图用（也是文档里数字的单一来源）。只读数据，不调用模型。
Gather every real experiment into one JSON for the frontend "Experiments" page (and as the single source of the numbers in the docs). Read-only, no model call.

用法 / usage:  python scripts/eval/export_experiments.py
"""
import collections
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import expectation_accuracy as base  # noqa: E402

ROOT = base.ROOT
OUT = ROOT / 'frontend/src/data/experiments.json'
TITLES = {'p2eval': '指挥者（动态角色）', 'p2eval_bon': 'best-of-N', 'p2eval_audit': 'Tester 用例审计（第 1 轮）', 'p2eval_audit2': 'Tester 用例审计（第 2 轮，复测）', 'p2eval_audit3': 'Tester 用例审计（第 3 轮，全部 L2–L6）', 'p2eval_final': '默认流程（再验证）', 'p2eval_switch': '设置开关通路', 'p2eval_sw': '大项目 bank（修复前，只有基线跑完）', 'p2eval_sw3': '大项目 bank（修复后，各 1 次）'}
ARM_TEXT = {'off': '关（默认流程）', 'on': '开', 'bon3': 'N=3', 'audit': '开', 'contract': '开：包间契约', 'referee': '开：断言裁判'}


def ab():
    out = []
    for folder in sorted(ROOT.glob('.masa/p2eval*/summary.json')):
        name = folder.parent.name
        runs = json.loads(folder.read_text(encoding='utf-8')).get('runs', [])
        by = collections.defaultdict(list)
        for r in runs:
            by[r['arm']].append(r)
        arms = []
        for arm, rs in by.items():
            arms.append({'arm': arm, 'label': ARM_TEXT.get(arm, arm), 'runs': len(rs), 'passed': sum(r['status'] == 'pass' for r in rs),
                         'cost': round(sum(r['cost'] for r in rs) / len(rs), 3), 'tokens': round(sum(r['cloud_tokens'] for r in rs) / len(rs)),
                         'seconds': round(sum(r['seconds'] for r in rs) / len(rs))})
        out.append({'experiment': name, 'title': TITLES.get(name, name), 'arms': sorted(arms, key=lambda a: a['arm'] != 'off')})
    # 合计：同一实验的多轮（p2eval_audit、_audit2、_audit3）合并成一组，样本更大 / combine the rounds of one experiment into a bigger sample
    combined = collections.defaultdict(list)
    for folder in sorted(ROOT.glob('.masa/p2eval_audit*/summary.json')):
        for r in json.loads(folder.read_text(encoding='utf-8')).get('runs', []):
            combined[r['arm']].append(r)
    if combined:
        arms = []
        for arm, rs in combined.items():
            arms.append({'arm': arm, 'label': ARM_TEXT.get(arm, arm), 'runs': len(rs), 'passed': sum(r['status'] == 'pass' for r in rs),
                         'cost': round(sum(r['cost'] for r in rs) / len(rs), 3), 'tokens': round(sum(r['cloud_tokens'] for r in rs) / len(rs)),
                         'seconds': round(sum(r['seconds'] for r in rs) / len(rs))})
        out.append({'experiment': 'p2eval_audit_all', 'title': 'Tester 用例审计（三轮合计）', 'arms': sorted(arms, key=lambda a: a['arm'] != 'off')})
    return out


def accuracy_rows():
    raw = json.loads(base.RAW.read_text(encoding='utf-8'))
    by = collections.defaultdict(list)
    for r in raw:
        by[(r['task'], r['model'])].append(r)
    flash, pro = 'deepseek-flash', 'deepseek-v4-pro'
    methods = (('Flash 单次', flash, None), ('Pro 单次', pro, None), ('Flash 表决 3', flash, 3), ('Flash 表决 5', flash, 5), ('Pro 表决 3', pro, 3))
    rows, cost = [], {}
    for label, model, vote in methods:
        values = collections.defaultdict(list)
        for task in base.TASKS:
            samples = sorted(by[(task.id, model)], key=lambda r: r['sample'])
            if not samples:
                continue
            value, _ = base.accuracy(samples, task, vote)
            if value is not None:
                values['all'].append(value)
                values['low' if task.level <= 3 else 'mid' if task.level <= 6 else 'high'].append(value)
        mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else None  # noqa: E731
        rows.append({'method': label, 'all': mean(values['all']), 'low': mean(values['low']), 'mid': mean(values['mid']), 'high': mean(values['high'])})
    return rows


def audit_rows():
    doc = base.REPORT.read_text(encoding='utf-8')
    if '## 审计实验' not in doc:
        return []
    section = doc.split('## 审计实验', 1)[1].split('\n## ', 1)[0]
    rows = []
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip('|').split('|')]
        if len(cells) == 4 and re.match(r'^[0-9—]', cells[1]):
            pct = lambda c: None if c.startswith('—') else int(re.match(r'(\d+)%', c).group(1)) / 100  # noqa: E731
            rows.append({'method': cells[0], 'recall': pct(cells[1]), 'false_alarm': pct(cells[2]), 'fixed': pct(cells[3])})
    return rows


def main():
    def load(path):
        return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else None
    real = load(ROOT / '.masa/test_reliability/case_audit.json')
    summary = None
    if real:
        total = collections.Counter()
        for r in real['rows']:
            for k in ('cases', 'suspect_original', 'corrupted', 'caught', 'abstain_corrupted'):
                total[k] += r[k]
        summary = {'cases': total['cases'], 'suspect_original': total['suspect_original'], 'corrupted': total['corrupted'], 'caught': total['caught'], 'cost': real['cost']}
    task_rows = []
    pro = collections.defaultdict(list)
    for r in json.loads(base.RAW.read_text(encoding='utf-8')):
        if r['model'].endswith('pro'):
            pro[r['task']].append(r)
    off = collections.defaultdict(list)
    for path in ROOT.glob('.masa/p2eval*/summary.json'):
        for r in json.loads(path.read_text(encoding='utf-8')).get('runs', []):
            if r['arm'] == 'off':
                off[r['task']].append(r)
    for task in base.TASKS:
        acc = base.accuracy(sorted(pro[task.id], key=lambda r: r['sample']), task)[0] if pro.get(task.id) else None
        rs = off.get(task.id, [])
        task_rows.append({'task': task.id, 'level': task.level, 'accuracy': None if acc is None else round(acc, 3), 'runs': len(rs), 'passed': sum(r['status'] == 'pass' for r in rs),
                          'cost': round(sum(r['cost'] for r in rs) / len(rs), 3) if rs else None, 'rounds': round(sum(r['rounds'] for r in rs) / len(rs), 1) if rs else None})
    data = {'ab': ab(), 'accuracy': accuracy_rows(), 'audit': audit_rows(), 'case_audit': summary, 'tasks': task_rows,
            'context_profile': load(ROOT / 'frontend/src/data/context_profile.json'), 'context_shadow': load(ROOT / 'frontend/src/data/context_shadow.json')}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    print('written', OUT, len(json.dumps(data)))


if __name__ == '__main__':
    main()
