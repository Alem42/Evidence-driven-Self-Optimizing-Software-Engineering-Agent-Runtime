"""交付报告：任务结束时由运行时（不是模型）从账本生成——验收项→证据、假设清单、已知缺陷、花费与人工干预、怎么运行。
Delivery report: produced by the runtime (not a model) from the ledger when a task ends - acceptance items -> evidence, the assumption list, known defects, cost and human interventions, how to run it.

原则 / principles
  · 报告里的“已验证”必须能在账本里找到对应的成功检查（exit_code 0 且属于最终快照的验证）；找不到就是“未验证”。 "Verified" must map to a successful check of the final verification in the ledger, otherwise it is "unverified".
  · “不知道”是合法的答案，编造的通过不是：没有检查覆盖的验收项、没跑的启动检查、没有价格的费用，都如实写“未验证 / 未运行 / 未知”。 "Unknown" is a legal answer, an invented pass is not.
  · 测试是模型写的，所以“已验证”只表示“覆盖它的检查在最终快照上通过”，强度写在报告里。 The tests are model-written, so "verified" only means the check covering it passed on the final snapshot; the strength is stated in the report.
"""
import uuid
from pathlib import Path

from masa.application.projects import Projects
from masa.application.usage import task_report
from masa.infrastructure.workspaces import verify_snapshot
from masa.languages.registry import registry
from masa.runtime import user_input


def _final_run(store, report):
    verifications = [v for v in report['versions'] if v['kind'] == 'verification']
    if not verifications:
        return None
    verifications.sort(key=lambda v: v['created_at'] or 0)
    return store.run(verifications[-1]['run_id'])


def _evidence(report, run_id, operation):
    """最终验证里某个检查操作的真实结果。 The real result of a check operation in the final verification."""
    for tool in report['tools']:
        if tool['run_id'] == run_id and tool['kind'] == 'tool' and tool['operation'] == operation:
            return {'operation': operation, 'step_id': tool['step_id'], 'exit_code': tool['exit_code'], 'status': tool['status']}
    return None


def smoke(store, runner, run, pack, cancelled=lambda: False):
    """启动检查：用真实入口、不带参数和输入跑一次。它只证明“程序能启动”，不证明行为正确。 A start-up check: the real entry point without arguments or input. It proves the program starts, not that it behaves correctly."""
    operation = pack.op_id('run')
    data = run['data']
    if operation is None or runner is None:
        return {'executed': False, 'reason': 'this language pack has no run operation'}
    try:
        workspace = Path(data['workspace'])
        verify_snapshot(workspace, data['snapshot_id'])
        request = {'protocol_version': 1, 'request_id': uuid.uuid4().hex, 'snapshot_id': data['snapshot_id'], 'operation': operation, 'argv': [], 'stdin': '',
                   'timeout_ms': 20000, 'max_output_bytes': 65536}
        result = runner.execute(request, workspace, cancelled)
        return {'executed': True, 'exit_code': result.get('exit_code'), 'status': result.get('status'), 'stdout': str(result.get('stdout', ''))[:400], 'stderr': str(result.get('stderr', ''))[:400]}
    except Exception as exc:  # 启动检查只是证据：失败如实写下，不影响报告 / evidence only: a failure is recorded, the report still stands
        return {'executed': False, 'reason': f'{type(exc).__name__}: {str(exc)[:120]}'}


def build(store, rid, runner=None, run_smoke=False):
    report = task_report(store, rid)
    final = _final_run(store, report)
    delivery = {'task': report['title'], 'project_id': report['project_id'], 'outcome': report['outcome'], 'final_run_id': final['id'] if final else None}
    if final is None or not final['data'].get('project_bundle'):
        return {**delivery, 'complete': False, 'reason': 'no verification run exists yet: there is nothing to deliver', 'acceptance': [], 'assumptions': [], 'open_items': [], 'cost': {}}
    bundle = store.read(final['data']['project_bundle']['approval_ref'])
    approved = store.read(bundle['spec_approval_ref'])
    spec, checks = approved['spec'], approved.get('checks') or []
    pack = registry.for_run(final['data'])
    gate_passed = final['status'] == 'succeeded'

    # 验收项 → 覆盖它的检查 → 最终验证里的真实结果 / acceptance -> the checks covering it -> their real results in the final verification
    acceptance = []
    for index, text in enumerate(spec.get('acceptance') or []):
        covering = [c for c in checks if index in (c.get('acceptance_indices') or [])]
        evidence = [e for e in (_evidence(report, final['id'], c.get('operation')) for c in covering) if e]
        if not covering or not evidence:
            status = 'unverified'
        elif all(e['exit_code'] == 0 for e in evidence) and gate_passed:
            status = 'verified'
        else:
            status = 'failed'
        black_box = sum(1 for c in (spec.get('cases') or []) if index in (c.get('accepts') or []))  # 程序生成并执行的黑盒用例数 / black-box cases generated and executed by the harness
        strength = None
        if status == 'verified':
            strength = (f'{black_box} black-box acceptance case(s) executed by the harness; their expectations are model-written' if black_box
                        else 'covered by model-written checks; not independently verified')
        acceptance.append({'index': index, 'text': text, 'status': status, 'evidence': evidence, 'cases': sum(len(c.get('cases') or []) for c in covering),
                           'black_box_cases': black_box, 'strength': strength})

    # 假设：用户没说、但代码里做了的事，以及这次运行里不是用户写的东西 / assumptions: what the user did not say but the code does, and what was not written by the user
    plan = store.run(report['project_id'])['data'].get('project_plan') or {}
    revisions = next((s['calls'] for s in report['by_step'] if s['step_id'] == 'project_test_revision'), 0)
    notes = user_input.notes_for(store, final['id'])
    assumptions = [{'source': 'architect', 'text': f'Acceptance criteria were written by the Architect model and approved automatically ({len(spec.get("acceptance") or [])} items); they are the only definition of "done" used here.'}]
    assumptions += [{'source': 'architect', 'text': str(item)[:300]} for item in (plan.get('assumptions') or [])]  # 目标没说、Architect 选了约定的地方 / what the goal left open and the convention the Architect chose
    questions = (plan.get('clarification') or {}).get('questions') or []
    for question in questions:
        assumptions.append({'source': 'clarification', 'text': f'Asked: {question.get("text") or question.get("question") or question}'})
    for note in notes:
        assumptions.append({'source': 'user', 'text': f'You added during the run: {str(note)[:200]}'})
    if revisions:
        bases = [e['payload'].get('basis') for member in {v['run_id'] for v in report['versions']} for e in store.events(member) if e['type'] == 'test_revision_basis']
        weak = [b for b in bases if b == 'legacy_shortcut_no_adjudicator']
        text = f'The tests were revised {revisions} time(s) by the harness after failed verifications: those expectations were not written by you.'
        if bases:
            text += ' Basis: ' + ', '.join(f'{b} x{bases.count(b)}' for b in sorted(set(bases))) + '.'
        if weak:
            text += ' WARNING: at least one revision rested only on "the implementation could not be fixed" (no adjudicator was available), which is not independent evidence that the test was wrong.'
        assumptions.append({'source': 'harness', 'text': text})
    assumptions.append({'source': 'default', 'text': f'Language: {pack.title}; project layout and entry point follow the language pack ({pack.entrypoint(spec)}).'})

    independent = [p for p in report['process'] if p['kind'] in ('case_audit', 'assertion_referee')]
    open_items = []
    if not gate_passed:
        open_items.append({'kind': 'gate_not_passed', 'text': f"The Gate did not pass ({final['reason'] or final['status']}); the code is a draft, not a delivery."})
    for tool in report['tools']:
        if tool['run_id'] == final['id'] and tool['kind'] == 'tool' and tool['exit_code'] not in (0, None):
            open_items.append({'kind': 'failing_check', 'text': f"Check {tool['operation']} exited with {tool['exit_code']} in the final verification."})
    open_items += [{'kind': 'unverified_acceptance', 'text': f"Acceptance item {a['index'] + 1} has no check that covers it: {a['text']}"} for a in acceptance if a['status'] == 'unverified']
    open_items += [{'kind': 'stopped', 'text': str(p['data'])[:200]} for p in report['process'] if p['kind'] == 'task_stopped']

    totals = report['totals']
    interventions = 0
    for member in {v['run_id'] for v in report['versions']}:
        interventions += sum(1 for e in store.events(member) if e['type'] in ('user_input', 'uncertain_call_resolved'))
    run_hint = f"python -m {spec.get('module')}" if pack.id == 'python' else 'go run ./cmd/app' if pack.id == 'go' else pack.entrypoint(spec)
    return {**delivery, 'complete': gate_passed and not [a for a in acceptance if a['status'] != 'verified'], 'gate_passed': gate_passed, 'language': pack.id,
            'files': sorted(bundle.get('files') or {}),
            'acceptance': acceptance, 'assumptions': assumptions, 'independent_evidence': [{'kind': p['kind'], 'data': p['data']} for p in independent], 'open_items': open_items,
            'how_to_run': run_hint, 'smoke': smoke(store, runner, final, pack) if run_smoke else {'executed': False, 'reason': 'not requested'},
            'cost': {'calls': totals['calls'], 'cloud_tokens': totals['cloud_tokens'], 'local_tokens': totals['local_tokens'], 'cache_hit_rate': totals['cache_hit_rate'],
                     'wall_seconds': report['wall_seconds'], 'failed_calls': totals['failed_calls'], 'human_interventions': interventions}}


def render(d):
    """DELIVERY.md：未验证和未完成项放在最前面。 DELIVERY.md: what is unverified or open comes first."""
    out = [f"# 交付报告 · {d['task']}", '']
    if d.get('reason'):
        return '\n'.join(out + [d['reason'], ''])
    verified = sum(1 for a in d['acceptance'] if a['status'] == 'verified')
    if not d['complete']:
        verdict = '未完成 / 部分验证'
    elif d['independent_evidence']:
        verdict = 'Gate 通过，每个验收项都有通过的检查，并有独立证据（用例审计 / 断言裁判）支持测试期望'
    else:
        verdict = 'Gate 通过，每个验收项都有通过的检查——但检查是模型写的、没有独立证据支持它们的期望值，请抽查关键行为'
    out += [f"**结论：{verdict}** · 验收项 {verified}/{len(d['acceptance'])} 已验证 · Gate {'通过' if d['gate_passed'] else '未通过'} · 语言 {d['language']}", '']
    if d['open_items']:
        out += ['## 未完成与已知缺陷', *[f"- [{i['kind']}] {i['text']}" for i in d['open_items']], '']
    out += ['## 验收项 → 证据', '| # | 验收项 | 状态 | 证据（最终验证里的检查） | 强度 |', '|---|---|---|---|---|']
    for a in d['acceptance']:
        evidence = '; '.join(f"{e['operation']} exit {e['exit_code']}" for e in a['evidence']) or '—'
        out.append(f"| {a['index'] + 1} | {a['text'].replace('|', '/')} | {a['status']} | {evidence} | {a['strength'] or '—'} |")
    out += ['', '## 假设与决策（用户没说、但代码里做了的事）', *[f"- ({a['source']}) {a['text']}" for a in d['assumptions']], '']
    if d['independent_evidence']:
        out += ['## 独立证据', *[f"- {i['kind']}: {i['data']}" for i in d['independent_evidence']], '']
    out += ['## 怎么运行', f"`{d['how_to_run']}`", '']
    smoke_result = d['smoke']
    out += ['**启动检查（只证明程序能启动，不证明行为正确）**：' + (f"退出码 {smoke_result.get('exit_code')}，输出开头：`{(smoke_result.get('stdout') or '').strip()[:120]}`" if smoke_result.get('executed') else f"未运行（{smoke_result.get('reason')}）"), '']
    cost = d['cost']
    out += ['## 花费与过程', f"- 模型调用 {cost['calls']} 次（失败 {cost['failed_calls']}）· 云端 token {cost['cloud_tokens']} · 本地 token {cost['local_tokens']} · 缓存命中 {cost['cache_hit_rate'] if cost['cache_hit_rate'] is not None else '未报告'}",
           f"- 墙钟 {cost['wall_seconds']} 秒 · 人工干预 {cost['human_interventions']} 次", '', f"## 文件（{len(d['files'])}）", *[f'- {p}' for p in d['files']], '']
    return '\n'.join(out)


def check_consistency(store, d):
    """机器核对：报告里每一条“verified”都能在账本里找到成功的检查。返回问题列表，空 = 一致。 Machine check: every "verified" maps to a successful check in the ledger; returns problems, empty means consistent."""
    problems = []
    if not d.get('final_run_id'):
        return problems
    tools = {t['step_id']: t for t in store.tools(d['final_run_id'])}
    for a in d['acceptance']:
        if a['status'] != 'verified':
            continue
        if not a['evidence']:
            problems.append(f"acceptance {a['index']} is verified without evidence")
        for e in a['evidence']:
            tool = tools.get(e['step_id'])
            result = store.read(tool['result_ref']) if tool and tool.get('result_ref') else None
            if not result or result.get('exit_code') != 0:
                problems.append(f"acceptance {a['index']}: {e['operation']} has no successful tool result in the ledger")
    return problems
