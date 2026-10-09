"""上下文画像：从评测账本里量化每个角色的输入由什么组成、有多大、随任务难度怎么变。只读数据，不调用模型，不花 token。
Context profile: quantify, from the benchmark ledgers, what each role's input is made of, how large it is and how it scales with task difficulty. Read-only, no model call, no tokens.

输出 / output: .masa/context_profile.json 与一张 markdown 表（写进 docs/reference/CONTEXT_PROFILE.md）。
用法 / usage:  python scripts/eval/context_profile.py
"""
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from masa.domain.tokens import estimate_tokens  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402
from masa.bench.tasks import BY_ID  # noqa: E402

OUT = ROOT / 'docs/reference/CONTEXT_PROFILE.md'
DATA = ROOT / 'frontend/src/data/context_profile.json'


def size(value):
    return estimate_tokens(json.dumps(value, ensure_ascii=False)) if not isinstance(value, str) else estimate_tokens(value)


def main():
    per_role = collections.defaultdict(lambda: {'calls': 0, 'total': 0, 'keys': collections.defaultdict(int), 'max': 0})
    per_level = collections.defaultdict(lambda: collections.defaultdict(list))
    seen = 0
    for summary in ROOT.glob('.masa/p2eval*/summary.json'):
        folder = summary.parent / 'bench/state'
        for row in json.loads(summary.read_text(encoding='utf-8')).get('runs', []):
            path = folder / row.get('bench_id', '')
            if row['arm'] != 'off' or not (path / 'runtime.sqlite3').exists():
                continue
            store = Store(path)
            try:
                seen += 1
                level = BY_ID[row['task']].level
                try:
                    calls = store.db.execute('select purpose,input_ref from role_invocations').fetchall()
                except Exception:
                    continue  # 这份账本没有角色调用表 / this ledger has no role-call table
                for call in calls:
                    context = store.read(call['input_ref'])
                    total = size(context)
                    role = per_role[call['purpose']]
                    role['calls'] += 1
                    role['total'] += total
                    role['max'] = max(role['max'], total)
                    for key, value in context.items():
                        role['keys'][key] += size(value)
                    per_level[level][call['purpose']].append(total)
            finally:
                store.close()
    rows = []
    for purpose, role in sorted(per_role.items(), key=lambda item: -item[1]['total']):
        n = role['calls']
        top = sorted(role['keys'].items(), key=lambda item: -item[1])[:4]
        rows.append({'role': purpose, 'calls': n, 'mean_tokens': round(role['total'] / n), 'max_tokens': role['max'],
                     'composition': [{'key': k, 'share': round(v / role['total'], 3)} for k, v in top]})
    levels = []
    for level in sorted(per_level):
        entry = {'level': level}
        for purpose in ('project_planner', 'project_tester', 'project_developer', 'project_repair', 'project_test_revision', 'project_diagnoser'):
            values = per_level[level].get(purpose)
            entry[purpose] = round(sum(values) / len(values)) if values else None
        levels.append(entry)
    data = {'runs': seen, 'roles': rows, 'by_level': levels}
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    lines = ['# 上下文画像（来自 ' + str(seen) + ' 次真实运行的账本）', '',
             '方法：读每次角色调用的输入（账本里的 input_ref），按顶层字段估算 token（项目自己的估算器，中文偏保守），不调用模型。只含默认流程（指挥者/best-of-N 关闭）的运行。', '',
             '## 每个角色的输入有多大、由什么组成', '', '| 角色 | 调用数 | 平均输入 token | 最大 | 主要组成（占比） |', '|---|---|---|---|---|']
    for r in rows:
        comp = '、'.join(f"{c['key']} {round(100 * c['share'])}%" for c in r['composition'])
        lines.append(f"| {r['role']} | {r['calls']} | {r['mean_tokens']} | {r['max_tokens']} | {comp} |")
    lines += ['', '## 输入大小随任务等级的变化（平均 token）', '', '| 等级 | Planner | Tester | Developer | Repair | TestRevision | Diagnoser |', '|---|---|---|---|---|---|---|']
    for e in levels:
        cell = lambda k: '—' if e[k] is None else str(e[k])  # noqa: E731
        lines.append(f"| L{e['level']} | {cell('project_planner')} | {cell('project_tester')} | {cell('project_developer')} | {cell('project_repair')} | {cell('project_test_revision')} | {cell('project_diagnoser')} |")
    OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines))


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
