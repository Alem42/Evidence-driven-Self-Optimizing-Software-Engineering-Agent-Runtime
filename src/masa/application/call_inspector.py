"""调用检视：把账本里的“每一次模型调用”还原成人能看的东西——模型看到了什么、各部分占多少、哪些被省略、服务端缓存命中了多少。只读，不新增任何状态。
Call inspection: turn every model call in the ledger into something a person can read: what the model saw, how big each part is, what was left out, how much the server cached. Read-only, no new state.

数据来源 / Sources (all already in the ledger):
- `role_invocations` 表：每次调用的上下文（input_ref）、模型配置（route_ref）、响应（output_ref）；
- `model_requested / model_completed / model_failed` 事件：用量、耗时、缓存命中；
- `context_plan` 事件：分层上下文因窗口不够而降级/丢掉了哪些文件；
- 上下文自己带的 `omitted` 列表（智能上下文）。
Token 数是估算（见 domain/tokens.py），服务端报告的真实数字单独列出。
Token counts are estimates (see domain/tokens.py); the server-reported numbers are listed separately.
"""
import json

from masa.application.projects import Projects
from masa.application.usage import _model_calls
from masa.domain.models import MasaError, canonical
from masa.domain.prompt_layout import layout_name, ordered_keys, render
from masa.domain.tokens import estimate_tokens

PREVIEW_CHARS = 300
MAX_ENTRIES = 40  # 一个分段里最多列出的条目（文件）数 / entries listed per section
MAX_OMITTED = 40


# 上下文分类：每个顶层字段归入一个类别，类别用专业名称（与 Claude Code 的“上下文”面板同一种读法：各类占用 + 窗口占用 + 剩余）。
# Context categories: every top-level field belongs to one category with a professional name (read like Claude Code's context panel: usage per category, window usage, free space).
CATEGORIES = (
    ('system', '系统提示词', 'System prompt'),
    ('task', '任务与验收', 'Task & acceptance'),
    ('tests', '测试规格', 'Test specification'),
    ('code', '代码上下文', 'Code context'),
    ('memory', '工作记忆', 'Working memory'),
    ('evidence', '失败证据与反馈', 'Failure evidence & feedback'),
    ('conventions', '语言约定', 'Language conventions'),
    ('human', '人工输入', 'Human input'),
    ('control', '调度元数据', 'Control metadata'),
    ('other', '其它', 'Other'),
)
FIELD_CATEGORY = {
    'goal': 'task', 'summary': 'task', 'acceptance': 'task', 'spec': 'task', 'contract': 'task', 'module': 'task', 'package': 'task', 'own_files': 'task',
    'examples': 'tests', 'spec_examples': 'tests', 'checks': 'tests', 'cases': 'tests', 'pairs': 'tests',
    'files': 'code', 'interfaces': 'code', 'original_files': 'code', 'all_files': 'code', 'previous_files': 'code', 'other_files_outline_readonly': 'code', 'context_selection': 'code',
    'last_results': 'memory', 'notes': 'memory', 'history': 'memory', 'progress_summary': 'memory', 'progress_digest': 'memory', 'progress_pins': 'memory', 'memory_notes': 'memory', 'fix_log': 'memory', 'noop_streak': 'memory',
    'repair': 'evidence', 'failure_evidence': 'evidence', 'feedback': 'evidence', 'ownership': 'evidence', 'independent_evidence': 'evidence', 'previous_attempt_error': 'evidence',
    'language_notes': 'conventions', 'language': 'conventions', 'reason_language': 'conventions',
    'user_notes': 'human',
    'purpose': 'control', 'step': 'control', 'max_steps': 'control', 'allowed_tools': 'control', 'generation_mode': 'control', 'target_path': 'control', 'clarification_allowed': 'control',
    'examples_required': 'control', 'omitted': 'control', 'memory': 'control',
}


def category_of(field):
    return FIELD_CATEGORY.get(field, 'other')


def categories_of(instruction_tokens, sections):
    """按类别汇总：[{id, label, english, tokens, share, sections:[字段名]}]；没有内容的类别也列出（tokens=0），读的人才知道“没有”。 Per-category totals; empty categories are listed too (tokens 0) so the reader sees "none"."""
    buckets = {cid: {'id': cid, 'label': label, 'english': english, 'tokens': 0, 'sections': []} for cid, label, english in CATEGORIES}
    buckets['system']['tokens'] = instruction_tokens
    for section in sections:
        bucket = buckets[category_of(section['name'])]
        bucket['tokens'] += section['est_tokens']
        bucket['sections'].append(section['name'])
    total = sum(b['tokens'] for b in buckets.values()) or 1
    for bucket in buckets.values():
        bucket['share'] = round(bucket['tokens'] / total, 4)
    return [buckets[cid] for cid, _l, _e in CATEGORIES]


def limits_of(store, members):
    """{模型名: 上下文窗口}：来自任务开始时记下的 task_budget 事件（云端模型的调用记录里没有窗口大小）。 {model: context window} from the task_budget events (a cloud call's route does not carry the window)."""
    out = {}
    for run in members:
        for event in store.events(run['id']):
            if event['type'] == 'task_budget':
                for candidate in (event['payload'].get('candidates') or []):
                    if candidate.get('model') and isinstance(candidate.get('context_limit'), int):
                        out[candidate['model']] = candidate['context_limit']
    return out


def window_of(limit, used_estimate, used_server, output_reserve):
    """窗口占用：已用 / 窗口、输出预留、剩余。已用优先取服务端报告的提示 token，没有就取本地估算（并标明来源）。 Window usage: used / window, the output reserve and what is free; the server-reported prompt tokens win over the local estimate (the source is named)."""
    used = used_server if isinstance(used_server, int) else used_estimate
    out = {'limit': limit, 'used': used, 'source': 'server' if isinstance(used_server, int) else 'estimate', 'output_reserve': output_reserve}
    if isinstance(limit, int) and limit > 0:
        out['percent'] = round(used / limit, 4)
        out['free'] = max(0, limit - used - (output_reserve or 0))
    else:
        out['percent'], out['free'] = None, None
    return out


def working_memory_of(context):
    """工作记忆状态（只对带“最近结果”窗口的上下文，也就是包工人）：窗口里保留几条、更早的几条已经离开窗口、笔记有多长、有没有摘要。
    被丢掉的结果不会自动并入笔记——这一点要让人看见：没有笔记又有结果离开窗口，就标成“有风险”。
    Working-memory status (only for contexts with a recent-results window, i.e. package workers): how many results are kept, how many older ones left the window, how long the notes are, whether a summary exists.
    Results that leave the window are NOT folded into the notes automatically; that must be visible: results gone with no notes is flagged as a risk."""
    results = context.get('last_results')
    if not isinstance(results, list):
        return None
    step = context.get('step') if isinstance(context.get('step'), int) else None
    kept = len(results)
    dropped = max(0, step - 1 - kept) if step is not None else None
    notes = context.get('notes') if isinstance(context.get('notes'), str) else ''
    summary = context.get('progress_summary') if isinstance(context.get('progress_summary'), str) else ''
    digest = context.get('progress_digest') if isinstance(context.get('progress_digest'), list) else []
    pins = context.get('progress_pins') if isinstance(context.get('progress_pins'), dict) else {}
    memory = context.get('memory') if isinstance(context.get('memory'), dict) else {}
    covered = bool(notes or summary or digest or pins)  # 离开窗口的结果有没有留下任何痕迹 / do results that left the window leave any trace
    return {'step': step, 'max_steps': context.get('max_steps') if isinstance(context.get('max_steps'), int) else None, 'kept': kept, 'dropped': dropped,
            'notes_chars': len(notes), 'summary_chars': len(summary), 'digest_lines': len(digest), 'pins': sorted(pins), 'strategy': memory.get('strategy') or 'window',
            'recall': bool(memory.get('recall')), 'at_risk': bool(dropped) and not covered,
            'kept_items': [{'tool': r.get('tool'), 'ok': r.get('ok'), 'path': r.get('path'), 'chars': len(str(r.get('result', '')))} for r in results[-8:] if isinstance(r, dict)]}


def _members(store, rid):
    """同一任务（版本链）的所有 run，最新的在后。 Every run of the task (version chain), oldest first."""
    runs = {r['id']: r for r in store.all_runs()}
    if rid not in runs:
        raise MasaError('run not found')
    root = Projects.root_id(rid, runs)
    members = [r for r in runs.values() if Projects.root_id(r['id'], runs) == root]
    members.sort(key=lambda r: r['data'].get('created_at') or 0)
    return members


def _invocation(store, run_id, step, invocation):
    return store.db.execute('SELECT * FROM role_invocations WHERE run_id=? AND purpose=? AND invocation_id=?', (run_id, step, invocation)).fetchone()


def _context(store, row):
    try:
        value = store.read(row['input_ref'])
    except Exception:  # 单个坏产物不能拖垮整个列表 / one broken artifact must not break the list
        return None
    return value if isinstance(value, dict) else None


def call_list(store, rid):
    """沿版本链列出每次模型调用（旧的在前）。 Every model call along the version chain, oldest first."""
    rows = []
    members = _members(store, rid)
    limits = limits_of(store, members)
    for run in members:
        for call in _model_calls(store, run):
            row = _invocation(store, run['id'], call['step_id'], call['invocation_id'])
            context = _context(store, row) if row else None
            limit = limits.get(call['model']) or call.get('reserved_tokens')
            rows.append({
                'run_id': run['id'], 'step': call['step_id'], 'invocation': call['invocation_id'], 'attempt': call['attempt_no'],
                'status': call['status'], 'model': call['model'], 'kind': call['kind'], 'started': call['started'], 'duration_ms': call['duration_ms'],
                'prompt_tokens': call['prompt_tokens'], 'completion_tokens': call['completion_tokens'],
                'cache_hit_tokens': call['cache_hit_tokens'], 'cache_miss_tokens': call['cache_miss_tokens'],
                'notes': len(context.get('user_notes') or []) if context else 0, 'error': call['error'],
                'window': limit, 'window_percent': round(call['prompt_tokens'] / limit, 4) if limit and isinstance(call['prompt_tokens'], int) else None,
            })
    rows.sort(key=lambda r: r['started'] or 0)
    return {'calls': rows}


def _entries(value):
    """把一个分段拆成可列出的条目：字典按键、列表按序号；只给大小，不给全文。 Split a section into listable entries (sizes only)."""
    if isinstance(value, dict):
        items = list(value.items())
    elif isinstance(value, list) and len(value) > 1:
        items = [(f'#{i}', v) for i, v in enumerate(value)]
    else:
        return []
    out = [{'name': str(k)[:120], 'chars': len(_text(v)), 'est_tokens': estimate_tokens(v)} for k, v in items[:MAX_ENTRIES]]
    return out


def _text(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def sections_of(context):
    """上下文的分段（顶层字段，按它们出现在提示里的顺序）：字符数、估算 token、预览、条目。 Sections in the order they appear in the prompt."""
    rendered = json.loads(canonical(context))  # 值与发给模型的完全一致（嵌套字母序） / values exactly as the model receives them (nested keys alphabetical)
    out = []
    for name in ordered_keys(rendered):  # 与提示里的顺序相同 / the same order as in the prompt
        value = rendered[name]
        text = _text(value)
        out.append({'name': name, 'kind': type(value).__name__, 'chars': len(text), 'est_tokens': estimate_tokens(value),
                    'preview': text[:PREVIEW_CHARS], 'entries': _entries(value)})
    total = sum(s['est_tokens'] for s in out) or 1
    for s in out:
        s['share'] = round(s['est_tokens'] / total, 4)
    return out


def omitted_of(context):
    """上下文自己记录的“被省略项与原因”（智能上下文的 omitted 列表，最多 MAX_OMITTED 条）。 The omitted items (and reasons) a context records itself."""
    found = []

    def visit(name, value, depth):
        if depth > 2:
            return
        if isinstance(value, dict):
            for key, inner in value.items():
                if key == 'omitted' and isinstance(inner, list):
                    for item in inner:
                        entry = item if isinstance(item, dict) else {'id': str(item)}
                        found.append({'section': name, 'id': str(entry.get('id', ''))[:160], 'reason': str(entry.get('reason', ''))[:80]})
                else:
                    visit(name, inner, depth + 1)
    for name, value in context.items():
        if name == 'omitted' and isinstance(value, list):
            for item in value:
                entry = item if isinstance(item, dict) else {'id': str(item)}
                found.append({'section': name, 'id': str(entry.get('id', ''))[:160], 'reason': str(entry.get('reason', ''))[:80]})
        else:
            visit(name, value, 0)
    return found[:MAX_OMITTED]


def _plans_before(store, run_id, started):
    """这次调用开始之前、上一次调用开始之后写下的 context_plan 事件（分层上下文的降级/丢弃决定）。 context_plan decisions written between the previous call and this one."""
    requested = [e for e in store.events(run_id) if e['type'] == 'model_requested']
    previous = max((e['created'] for e in requested if e['created'] < started), default=0)
    return [{k: e['payload'].get(k) for k in ('where', 'window', 'total', 'kept_tokens', 'outlined', 'dropped', 'over_budget')}
            for e in store.events(run_id) if e['type'] == 'context_plan' and previous <= e['created'] <= started]


def _role_spec(step):
    from masa.roles import registry
    return registry.get(step)


def _instruction(context):
    """系统指令（与发送时相同的构造，除本地传输提示外）。 The system instruction (same construction as when sent, minus the local-transport hint)."""
    from masa.agents.protocol import instruction_for
    text = instruction_for(context)
    if context.get('user_notes'):
        from masa.runtime.user_input import NOTES_INSTRUCTION
        text += NOTES_INSTRUCTION
    return text


def _common_prefix(a, b):
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _previous_same_step(store, members, run_id, step, started):
    """同一角色上一次调用的上下文（沿版本链找最近的一次）；用来算“和上一次共享多长的前缀”。 The latest earlier call of the same role, for prefix sharing."""
    best = None
    for run in members:
        for row in store.db.execute('SELECT * FROM role_invocations WHERE run_id=? AND purpose=? AND started<?', (run['id'], step, started)):
            if best is None or row['started'] > best['started']:
                best = row
    return best


def call_detail(store, rid, step, invocation):
    """一次调用的完整检视：模型看到的各部分、省略项、缓存、响应摘要。 Full inspection of one call."""
    members = _members(store, rid)
    ordered = sorted(members, key=lambda r: r['id'] != rid)  # 先找传入的 run，再找版本链上的其它 run / the given run first, then the rest of the chain
    run_id, row = None, None
    for run in ordered:
        row = _invocation(store, run['id'], step, invocation)
        if row:
            run_id = run['id']
            break
    if not row:
        raise MasaError('call not found')
    context = _context(store, row)
    if context is None:
        raise MasaError('the context of this call is unavailable')
    try:
        route = store.read(row['route_ref'])
    except Exception:
        route = {}
    call = next((c for c in _model_calls(store, store.run(run_id)) if c['step_id'] == step and c['invocation_id'] == invocation), None) or {}
    sections = sections_of(context)
    instruction = _instruction(context)
    user_message = render(context)
    instruction_tokens = estimate_tokens(instruction)
    est_total = instruction_tokens + sum(s['est_tokens'] for s in sections)
    categories = categories_of(instruction_tokens, sections)
    limit = limits_of(store, members).get((route or {}).get('model') or call.get('model')) or (route or {}).get('context_limit') or call.get('reserved_tokens')
    spec = _role_spec(step)
    output_reserve = min([n for n in ((spec.max_output_tokens if spec is not None else None), (route or {}).get('max_output_tokens')) if isinstance(n, int)] or [0]) or None

    prefix, delta = None, None
    earlier = _previous_same_step(store, members, run_id, step, row['started'])
    if earlier is not None:
        before = _context(store, earlier)
        if before is not None:
            before_cats = categories_of(estimate_tokens(_instruction(before)), sections_of(before))
            delta = {'against': earlier['invocation_id'], 'by_category': {a['id']: a['tokens'] - b['tokens'] for a, b in zip(categories, before_cats)},
                     'total': sum(a['tokens'] for a in categories) - sum(b['tokens'] for b in before_cats)}
            a, b = _instruction(before) + render(before), instruction + user_message
            common = _common_prefix(a, b)
            prefix = {'against': {'step': step, 'invocation': earlier['invocation_id']}, 'common_chars': common, 'of_chars': len(b),
                      'share': round(common / len(b), 4) if b else None}
    response = None
    if row['output_ref']:
        try:
            out = store.read(row['output_ref'])
            text = _text(out)
            response = {'chars': len(text), 'keys': sorted(out.keys())[:30] if isinstance(out, dict) else None, 'preview': text[:PREVIEW_CHARS * 2]}
        except Exception:
            response = None
    return {
        'run_id': run_id, 'step': step, 'invocation': invocation, 'attempt': row['attempt_no'], 'status': row['status'],
        'model': (route or {}).get('model') or call.get('model'), 'provider': (route or {}).get('provider'),
        'started': row['started'], 'finished': row['finished'], 'duration_ms': call.get('duration_ms'),
        'instruction': {'chars': len(instruction), 'est_tokens': estimate_tokens(instruction)},
        'layout': layout_name(), 'sections': sections, 'est_total_tokens': est_total, 'categories': categories,
        'window': window_of(limit, est_total, call.get('prompt_tokens'), output_reserve), 'working_memory': working_memory_of(context), 'delta_vs_previous': delta,
        'usage': {'prompt_tokens': call.get('prompt_tokens'), 'completion_tokens': call.get('completion_tokens'),
                  'cache_hit_tokens': call.get('cache_hit_tokens'), 'cache_miss_tokens': call.get('cache_miss_tokens')},
        'omitted': omitted_of(context), 'context_plans': _plans_before(store, run_id, row['started']),
        'user_notes': list(context.get('user_notes') or []),
        'prefix_with_previous': prefix, 'response': response, 'error': call.get('error'),
    }
