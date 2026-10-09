"""配对评测的逐对明细与合计：同一任务、同一轮里“关”和某个“开”的结果并排，并给出合计与符号检验（赢/输/平）。只读数据。
Pair-by-pair detail and totals: for the same task and round, the "off" result next to the "on" result, with totals and a sign count (wins/losses/ties). Read-only.

用法 / usage:  python scripts/eval/pair_table.py p2eval_audit audit        # 目录前缀 + 开启的那一组 / directory prefix + the arm under test
"""
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(prefix):
    rows = []
    for path in sorted(ROOT.glob('.masa/' + prefix + '*/summary.json')):
        for r in json.loads(path.read_text(encoding='utf-8')).get('runs', []):
            rows.append((path.parent.name, r))
    return rows


def main(prefix, arm):
    rows = load(prefix)
    by = collections.defaultdict(dict)
    for folder, r in rows:
        by[(folder, r['task'])][r['arm']] = r
    rank = {'pass': 2, 'false_pass': 1}  # 通过 > 假通过 > 其它 / pass > false pass > everything else
    wins = losses = ties = 0
    lines = ['| 轮 | 任务 | 关：结果 / ¥ | 开：结果 / ¥ | 谁更好 |', '|---|---|---|---|---|']
    for (folder, task), arms in sorted(by.items()):
        if 'off' not in arms or arm not in arms:
            continue
        o, u = arms['off'], arms[arm]
        # 被单次费用上限截断的运行（budget_*）不算有效配对，单独标出 / runs cut by the per-run cost cap are not valid pairs; marked separately
        cut = any((x.get('stop_reason') or '').startswith('budget') for x in (o, u))
        ro, ru = rank.get(o['status'], 0), rank.get(u['status'], 0)
        verdict = '（费用上限截断，不计）' if cut else ('开更好' if ru > ro else '关更好' if ro > ru else ('平，开更便宜' if u['cost'] < o['cost'] else '平，关更便宜'))
        if not cut:
            wins += ru > ro
            losses += ro > ru
            ties += ru == ro
        lines.append(f"| {folder[-1] if folder[-1].isdigit() else '1'} | {task} | {o['status']} / {o['cost']:.2f} | {u['status']} / {u['cost']:.2f} | {verdict} |")
    total = collections.defaultdict(collections.Counter)
    for _, r in rows:
        if r['arm'] in ('off', arm):
            total[r['arm']]['n'] += 1
            total[r['arm']]['pass'] += r['status'] == 'pass'
            total[r['arm']]['cost'] += r['cost']
            total[r['arm']]['tokens'] += r['cloud_tokens']
    lines += ['', f'有效配对：{arm} 更好 {wins}，关更好 {losses}，平 {ties}。']
    for name in ('off', arm):
        t = total[name]
        if t['n']:
            lines.append(f"- {name}：{t['pass']}/{t['n']} 通过，平均 ¥{t['cost'] / t['n']:.3f}/次，平均 {t['tokens'] // t['n']} 云端 token")
    print('\n'.join(lines))


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main(sys.argv[1], sys.argv[2])
