"""上下文预算的影子重放：用真实账本里的修复/修订测试输入，假设窗口变小，看分层预算会丢什么、省多少、必需内容是否都保住。不调用模型，不影响任何行为。
Shadow replay of the context budget: take the real repair / test-revision inputs from the ledgers, pretend the window is smaller, and see what the tiered plan would drop, how much it saves and whether the must-have content survives. No model call, no behaviour change.

另外按账本里测得的“每个文件平均多少 token”，外推项目规模变大时修复输入的大小（有预算 vs 没有预算）。
It also extrapolates, from the measured tokens per file, how the repair input grows with the project size (with and without the budget).

用法 / usage:  python scripts/eval/context_shadow.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from masa.application.orchestration.context_plan import item, plan  # noqa: E402
from masa.domain.tokens import estimate_tokens  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402

DATA = ROOT / 'frontend/src/data/context_shadow.json'
DOC = ROOT / 'docs/reference/CONTEXT_SHADOW.md'
WINDOWS = (4_000, 8_000, 12_000, 16_000, 32_000)


def calls():
    for summary in ROOT.glob('.masa/p2eval*/summary.json'):
        for row in json.loads(summary.read_text(encoding='utf-8')).get('runs', []):
            path = summary.parent / 'bench/state' / row.get('bench_id', '')
            if row['arm'] != 'off' or not (path / 'runtime.sqlite3').exists():
                continue
            store = Store(path)
            try:
                try:
                    rows = store.db.execute("select purpose,input_ref from role_invocations where purpose in ('project_repair','project_test_revision')").fetchall()
                except Exception:
                    continue
                for r in rows:
                    yield store.read(r['input_ref'])
            finally:
                store.close()


def items_of(context):
    """把一次修复/修订测试的输入拆成带层级的项：失败证据与“证据里出现过名字的文件”是必需，其余文件可丢，规格/测试清单/反馈重要。
    Split a repair input into tiered items: failure evidence and the files named in it are must-have, the other files droppable, spec / checks / feedback important."""
    evidence = json.dumps(context.get('failure_evidence', ''), ensure_ascii=False)
    out = [item('failure_evidence', 'must', evidence)]
    for path, text in (context.get('original_files') or {}).items():
        name = path.rsplit('/', 1)[-1]
        out.append(item('file:' + path, 'must' if name in evidence else 'droppable', text))
    for key in ('spec', 'checks', 'feedback', 'goal'):
        if key in context:
            out.append(item(key, 'important', json.dumps(context[key], ensure_ascii=False) if not isinstance(context[key], str) else context[key]))
    return out


def main():
    contexts = list(calls())
    rows = []
    for window in WINDOWS:
        large = dropped_tokens = total = must_lost = over = 0
        for context in contexts:
            items = items_of(context)
            result = plan(items, window)
            total += result['total']
            dropped_tokens += result['total'] - result['kept_tokens']
            large += result['scale'] == 'large'
            over += result['over_budget']
            must_lost += sum(1 for i in items if i['tier'] == 'must' and i['name'] not in result['keep'])
        rows.append({'window': window, 'calls': len(contexts), 'large_share': round(large / len(contexts), 3), 'mean_total': round(total / len(contexts)),
                     'mean_saved': round(dropped_tokens / len(contexts)), 'must_items_lost': must_lost, 'over_budget_calls': over})
    # 外推：每个文件平均多少 token、失败证据的上限 / extrapolation inputs
    file_tokens = [estimate_tokens(t) for c in contexts for t in (c.get('original_files') or {}).values()]
    per_file = round(sum(file_tokens) / max(1, len(file_tokens)))
    evidence = round(sum(estimate_tokens(json.dumps(c.get('failure_evidence', ''), ensure_ascii=False)) for c in contexts) / max(1, len(contexts)))
    fixed = 700  # 规格/测试清单/反馈等固定部分的粗略值（来自画像）/ rough fixed part (spec, checks, feedback) from the profile
    projection = []
    for n in (4, 10, 30, 100, 300):
        no_budget = fixed + evidence + n * per_file
        failing = min(n, 3)  # 假设一次失败涉及的文件不超过 3 个 / assume a failure touches at most 3 files
        with_budget = min(no_budget, max(int(32_000 * 0.5), fixed + evidence + failing * per_file))
        projection.append({'files': n, 'without_budget': no_budget, 'with_budget_for_32k_window': with_budget})
    data = {'calls': len(contexts), 'windows': rows, 'tokens_per_file': per_file, 'evidence_tokens': evidence, 'projection': projection}
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
    lines = ['# 上下文预算的影子重放', '', f'用 {len(contexts)} 次真实的修复/修订测试输入（默认流程的账本），假设模型窗口变小，看分层预算（`context_plan.py`）会怎么做。**影子模式：不改变任何现有行为。**', '',
             '| 假设的窗口 | 被判为“大任务”的调用占比 | 平均输入 token | 平均省下 | 丢掉的必需项 | 必需项就超预算的调用 |', '|---|---|---|---|---|---|']
    for r in rows:
        lines.append(f"| {r['window']} | {round(100 * r['large_share'])}% | {r['mean_total']} | {r['mean_saved']} | {r['must_items_lost']} | {r['over_budget_calls']} |")
    lines += ['', f'## 项目变大时修复输入的外推（每个文件平均 {per_file} token，失败证据约 {evidence} token，固定部分约 {fixed}）', '',
              '| 项目文件数 | 不做预算的输入 token | 做预算后（32k 窗口） |', '|---|---|---|']
    for p in projection:
        lines.append(f"| {p['files']} | {p['without_budget']} | {p['with_budget_for_32k_window']} |")
    DOC.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines))


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    main()
