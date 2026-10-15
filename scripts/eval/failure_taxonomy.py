"""失败分类：从评测账本里统计“每一次验证失败”属于哪一类，用来决定该做什么工具。只读数据，不调用任何模型，不花 token。
Failure taxonomy: classify EVERY failed verification found in the benchmark ledgers, to decide which tools are worth building. Read-only, no model call, no tokens.

类别 / classes
  expectation   期望算错：失败的断言里，测试期望本身是错的（Diagnoser 判 owner=test 且逐条核对出不一致，或测试修订之后问题消失）
  stdlib_api    标准库/语言用法错：编译错误里 pkg.X 不存在、参数个数/类型不对、无字段或方法等（imports 的“未使用/未导入”已由确定性修复处理，单独计）
  imports       import 缺失/未使用（已有 goimports 确定性修复）
  logic         逻辑错：断言失败且实现修复之后才消失（或 panic）
  unresolved    断言失败，后面也没有解决，没有证据说明是哪一类
  test_setup    测试结构错：import cycle、包名不一致、构建了空目录等
  syntax        语法/其它编译错误
  format        gofmt
  generation    生成阶段就被确定性检查拒绝（评测记录里的 note）

用法 / usage:  python scripts/eval/failure_taxonomy.py [状态目录 ...]   （默认扫描 .masa 下所有评测账本 / defaults to every benchmark ledger under .masa）
"""
import collections
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from masa.application.checks import ownership  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402

API = re.compile(r'undefined: \w+\.\w+|has no field or method|cannot use .* as .* (?:value|type)|(?:not enough|too many) (?:arguments|return values)|assignment mismatch|'
                 r'cannot (?:range over|convert|call)|invalid operation|multiple-value|does not implement|undefined \(type|missing return')
IMPORTS = re.compile(r'imported and not used|undefined: [a-z]+$|"[\w/.]+" imported and not used|missing import')
MODULE = re.compile(r'is not in std|cannot find module providing package|no required module|import lookup disabled|cannot find package|use of internal package')
STD = re.compile(r'\b(strings|strconv|sort|bufio|os|io|fmt|bytes|math|time|errors|unicode|utf8|regexp|list|heap|flag|filepath|path|sync|big|rand|csv|json|bits|slices|maps|atomic|ioutil|exec)\.')
CLASSES = ('expectation', 'logic', 'stdlib_api', 'internal_api', 'module_path', 'imports', 'unresolved', 'test_setup', 'syntax', 'format', 'generation')


def default_states():
    return sorted({db.parent for db in ROOT.glob('.masa/**/bench/state/*/runtime.sqlite3')})


def checks_of(store, run_id):
    return [(store.read(t['request_ref'])['operation'], store.read(t['result_ref'])) for t in store.tools(run_id) if t['result_ref']]


def classify_run(store, runs, run_id):
    """一次失败的验证 → 若干 (类别, 例子)。 One failed verification -> a list of (class, example)."""
    run = runs[run_id]
    checks = checks_of(store, run_id)
    failed = [(op, r) for op, r in checks if r.get('exit_code') != 0]
    if not failed:
        return []
    analysis = ownership.analyse(checks)
    items = analysis['items']
    children = [r for r in runs.values() if r['data'].get('parent_run_id') == run_id]
    drafts = [c for c in children if c['data'].get('project_plan', {}).get('repair_of') or c['data'].get('project_plan', {}).get('revision_scope') or c['data'].get('project_plan', {}).get('format_only')]
    remedy = None
    for draft in drafts:
        plan = draft['data'].get('project_plan', {})
        remedy = 'format' if plan.get('format_only') else 'tests' if plan.get('revision_scope') == 'tests' else 'implementation'
    next_ok = False
    for draft in drafts:
        for v2 in (r for r in runs.values() if r['data'].get('parent_run_id') == draft['id']):
            next_ok = next_ok or v2['status'] == 'succeeded'
    diag = [e['payload'] for e in store.events(run_id) if e['type'] == 'diagnosis']
    mismatch = any(d.get('mismatches') for d in diag)
    owner_test = any(d.get('owner') in ('test', 'both') for d in diag)
    # 后续验证里未解决条目变少了吗？ Did the unresolved count drop in the follow-up verification?
    improved = False
    for draft in drafts:
        for v2 in (r for r in runs.values() if r['data'].get('parent_run_id') == draft['id']):
            try:
                after = len(ownership.analyse(checks_of(store, v2['id']))['items'])
                improved = improved or after < len(items) or v2['status'] == 'succeeded'
            except Exception:
                pass
    out = []
    terminal = not drafts
    for item in items:
        kind, message = item['kind'], item['message']
        if kind == ownership.FORMAT:
            out.append(('format', message))
        elif kind == ownership.SETUP:
            out.append(('test_setup', message))
        elif kind == ownership.COMPILE:
            if MODULE.search(message):
                out.append(('module_path', message))
            elif IMPORTS.search(message):
                out.append(('imports', message))
            elif API.search(message):
                # 标准库的 API 用错，还是项目内部（测试与实现之间）的接口对不上？ A misused standard-library API, or a mismatch between the project's own test and implementation?
                out.append(('stdlib_api' if STD.search(message) else 'internal_api', message))
            else:
                out.append(('syntax', message))
        elif kind == ownership.ASSERTION:
            if mismatch or (owner_test and remedy == 'tests' and improved):
                out.append(('expectation', message))
            elif remedy == 'tests' and improved:
                out.append(('expectation', message))
            elif remedy == 'implementation' and improved:
                out.append(('logic', message))
            elif 'panic' in message:
                out.append(('logic', message))
            elif remedy is None and diag:
                # 最后一次验证，没有后续修复：只能用 Diagnoser 的判断。 The last verification has no follow-up fix: only the Diagnoser's verdict is left.
                out.append(('expectation' if owner_test else 'logic', message))
            else:
                out.append(('unresolved', message))
    if not items:
        out.append(('syntax', 'failed check without a parsed diagnostic: ' + ', '.join(op for op, _ in failed)))
    return [(c, m, improved, terminal) for c, m in out]


def main(argv):
    states = [Path(a) for a in argv] or default_states()
    counts, examples, runs_seen, tasks = collections.Counter(), collections.defaultdict(list), 0, 0
    fixed, tried, final = collections.Counter(), collections.Counter(), collections.defaultdict(collections.Counter)
    per_run = collections.Counter()
    for state in states:
        try:
            store = Store(state)
        except Exception:
            continue
        try:
            runs = {r['id']: r for r in store.all_runs()}
            tasks += 1
            for run_id, run in runs.items():
                if not run['data'].get('project_bundle') or run['status'] not in ('failed', 'succeeded'):
                    continue
                try:
                    found = classify_run(store, runs, run_id)
                except Exception:
                    continue
                if found:
                    runs_seen += 1
                    for cls in {c for c, *_ in found}:
                        per_run[cls] += 1
                    for cls, message, improved, terminal in found:
                        counts[cls] += 1
                        if terminal:
                            final[state.name][cls] += 1  # 这个任务最后一次验证里的失败 / failures of the task's last verification
                        else:
                            tried[cls] += 1
                            fixed[cls] += bool(improved)
                        if len(examples[cls]) < 4 and message not in examples[cls]:
                            examples[cls].append(message[:160])
        finally:
            store.close()
    # 生成阶段失败：评测记录里的 note。 Generation failures live in the benchmark records' notes.
    generation = 0
    for path in ROOT.glob('.masa/**/summary.json'):
        try:
            for row in json.loads(path.read_text(encoding='utf-8')).get('runs', []):
                if (row.get('note') or '').startswith('project generation failed') and 'byte limit' not in row['note']:
                    generation += 1
                    examples['generation'].append(row['note'][:160])
        except (OSError, ValueError):
            pass
    counts['generation'] += generation
    total = sum(counts.values())
    print(f'账本 {tasks} 个，失败的验证 {runs_seen} 次，失败条目 {total} 条（含 {generation} 次生成阶段拒绝）')
    print(f"{'类别':<13}{'条目':>5}{'占比':>6}{'验证次数':>9}{'下一轮缓解率':>14}{'任务终局里':>10}")
    ended = collections.Counter()
    for per in final.values():
        ended.update(per)
    for cls in CLASSES:
        rate = f'{100 * fixed[cls] / tried[cls]:.0f}% ({fixed[cls]}/{tried[cls]})' if tried[cls] else '-'
        print(f'{cls:<13}{counts[cls]:>5}{(100 * counts[cls] / total if total else 0):>5.0f}%{per_run[cls]:>9}{rate:>16}{ended[cls]:>8}')
        for sample in examples[cls][:3]:
            print('      · ' + sample)
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main(sys.argv[1:]))
