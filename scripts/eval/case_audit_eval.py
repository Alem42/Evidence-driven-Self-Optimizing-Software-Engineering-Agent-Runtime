"""Tester 用例审计的真实可靠性：用评测账本里**真实的 Tester 用例**（自由文本期望），测审计（Flash 盲推导×3 + 等价判断）能抓到多少被故意改错的期望、对原样用例有多少“可疑”判定。
Real reliability of the Tester case audit on REAL Tester cases (free-text expectations) from the benchmark ledgers: how many deliberately corrupted expectations does it catch (Flash blind derivation x3 + equivalence judge), and how many original cases does it call suspect.

注意 / caveat: 原样用例里的“可疑”不一定是误报——Tester 自己写错的期望也会被抓到（这正是目的）；所以它是误报的**上界**。故意改错 = 把期望文本里的第一个数字加一（只对含数字的用例做）。
A "suspect" original case is not necessarily a false alarm (the Tester's own wrong expectations are caught on purpose), so it is an UPPER bound on false alarms. Corruption = the first number in the expectation text plus one (cases with a number only).

用法 / usage:  python scripts/eval/case_audit_eval.py --run --yuan 0.5
"""
import argparse
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from masa.application.checks.case_audit import audit_cases  # noqa: E402
from masa.infrastructure.settings import Settings  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402

OUT = ROOT / '.masa/test_reliability/case_audit.json'
REPORT = ROOT / 'docs/reference/TEST_RELIABILITY.md'
SECTION = '## Tester 用例审计的真实可靠性（真实 Tester 用例）'
PRICES = {'deepseek-flash': (1.0, 4.0)}


def corrupt(text):
    m = re.search(r'[0-9]+', text)
    if not m:
        return None
    n = int(m.group())
    return text[:m.start()] + str(n + 1 if n < 9 else n - 1) + text[m.end():]


def real_plans():
    seen = {}
    for summary in ROOT.glob('.masa/p2eval*/summary.json'):
        for row in json.loads(summary.read_text(encoding='utf-8')).get('runs', []):
            path = summary.parent / 'bench/state' / row.get('bench_id', '')
            if row['task'] in seen or not (path / 'runtime.sqlite3').exists():
                continue
            store = Store(path)
            try:
                for run in sorted(store.all_runs(), key=lambda r: r['created'] if 'created' in r.keys() else 0):
                    plan = run['data'].get('project_plan') or {}
                    if plan.get('checks_ref') and plan.get('spec_ref'):
                        test = next((c for c in store.read(plan['checks_ref']) if c['operation'] == 'go_test'), None)
                        if test and test.get('cases'):
                            seen[row['task']] = (run['data']['goal'], store.read(plan['spec_ref'])['acceptance'], test['cases'])
                            break
            finally:
                store.close()
    return seen


def run(args):
    settings = Settings(ROOT / '.masa' / args.settings)
    flash = next(i for i, p in settings.profiles.items() if p['model'] == 'deepseek-flash' and p['enabled'])
    cost = [0.0]

    def ask(purpose, values):
        provider = settings.provider(flash)
        answer = provider.respond({'purpose': purpose, **values})
        usage = provider.usage or {}
        cost[0] += ((usage.get('prompt_tokens') or 0) * PRICES['deepseek-flash'][0] + (usage.get('completion_tokens') or 0) * PRICES['deepseek-flash'][1]) / 1e6
        return answer['answers']
    rows = []
    for task, (goal, acceptance, cases) in sorted(real_plans().items()):
        if cost[0] >= args.yuan:
            break

        def derive(views):
            return {a['index']: a['result'] for a in ask('case_deriver', {'goal': goal, 'acceptance': acceptance, 'cases': views})}

        def judge(pairs):
            return {a['index']: a['same'] for a in ask('case_judge', {'pairs': pairs})}
        original = audit_cases(cases, derive, judge)
        wrong_cases, positions = [], []
        for i, c in enumerate(cases):
            bad = corrupt(c.get('expected', ''))
            if bad is not None:
                wrong_cases.append(dict(c, expected=bad))
                positions.append(i)
        flagged = audit_cases(wrong_cases, derive, judge) if wrong_cases else {}
        rows.append({'task': task, 'cases': len(cases), 'suspect_original': sum(s == 'disagree' for s in original.values()), 'abstain_original': sum(s == 'abstain' for s in original.values()),
                     'corrupted': len(wrong_cases), 'caught': sum(s == 'disagree' for s in flagged.values()), 'abstain_corrupted': sum(s == 'abstain' for s in flagged.values())})
        print(rows[-1], flush=True)
    OUT.write_text(json.dumps({'rows': rows, 'cost': round(cost[0], 4)}, ensure_ascii=False, indent=1), encoding='utf-8')
    print('cost', round(cost[0], 4))


def report():
    data = json.loads(OUT.read_text(encoding='utf-8'))
    rows = data['rows']
    total = collections.Counter()
    for r in rows:
        for k in ('cases', 'suspect_original', 'abstain_original', 'corrupted', 'caught', 'abstain_corrupted'):
            total[k] += r[k]
    pct = lambda a, b: '—' if not b else str(round(100 * a / b)) + '%'  # noqa: E731
    lines = ['', SECTION, '', '方法：账本里每个任务第一份真实 Tester 计划的 go_test 用例（自由文本期望）。审计 = Flash 盲推导×3（只看输入）+ 等价判断，多数认为不一致即“可疑”。'
             '“故意改错”= 把期望文本里的第一个数字加一（只对含数字的用例做）。原样用例里的“可疑”包含 Tester 自己写错的期望，所以是误报的上界。花费约 ¥' + format(data['cost'], '.3f') + '。', '',
             '| 任务 | 用例数 | 原样：判可疑 | 原样：弃权 | 改错的用例数 | 抓到 | 改错：弃权 |', '|---|---|---|---|---|---|---|']
    for r in rows:
        lines.append('| ' + r['task'] + ' | ' + str(r['cases']) + ' | ' + str(r['suspect_original']) + ' | ' + str(r['abstain_original']) + ' | ' + str(r['corrupted']) + ' | ' + str(r['caught']) + ' | ' + str(r['abstain_corrupted']) + ' |')
    lines += ['', '**合计**：被故意改错的期望抓到 ' + pct(total['caught'], total['corrupted']) + '（' + str(total['caught']) + '/' + str(total['corrupted']) + '），弃权 ' + pct(total['abstain_corrupted'], total['corrupted'])
              + '；原样用例判可疑 ' + pct(total['suspect_original'], total['cases']) + '（' + str(total['suspect_original']) + '/' + str(total['cases']) + '，含 Tester 自己的真实错误，是误报上界），弃权 ' + pct(total['abstain_original'], total['cases']) + '。']
    doc = REPORT.read_text(encoding='utf-8')
    REPORT.write_text(doc.split(chr(10) + SECTION)[0].rstrip(chr(10)) + chr(10) + chr(10).join(lines) + chr(10), encoding='utf-8')
    print(chr(10).join(lines[-6:]))


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--yuan', type=float, default=0.5)
    ap.add_argument('--settings', default='p2eval_final')
    a = ap.parse_args()
    if a.run:
        run(a)
    if a.report:
        report()
