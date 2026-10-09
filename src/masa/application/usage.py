"""任务报告：从已有事件与工具账本投影 token、耗时与工具调用，不新增任何业务状态。
Task report: project token usage, timing and tool calls from existing events and ledgers; no new state."""
from masa.application.projects import Projects

_LOOPBACK = ('127.0.0.1', 'localhost', '::1')
# 终态与等待态用于结论；运行中的版本没有“结论”。 Terminal vs waiting states decide the outcome.
_WAITING = {'waiting_for_input', 'awaiting_review'}


def _is_local(route):
    """本地模型：Ollama 原生协议或回环地址。 Local = native Ollama protocol or a loopback address."""
    host = (route.get('base_url') or '').split('://')[-1].split('/')[0].rsplit(':', 1)[0].strip('[]')
    return route.get('provider') == 'ollama-native' or host in _LOOPBACK


def _int(value):
    """只接受非负整数，缺失保持 None（未知不是零）。 Non-negative ints only; missing stays None, never zero."""
    return value if type(value) is int and value >= 0 else None


def _add(total, key, value):
    if value is not None:
        total[key] = total.get(key, 0) + value


def _model_calls(store, run, events=None):
    """配对 requested 与 completed/failed，得到每次模型调用的真实用量。 Pair requests with their outcomes."""
    pending = {}
    calls = []
    for e in (events if events is not None else store.events(run['id'])):
        p = e['payload'] if isinstance(e['payload'], dict) else {}
        key = (p.get('step_id'), p.get('invocation_id'), p.get('attempt_no'))
        if e['type'] == 'model_requested':
            pending[key] = (e, p.get('route') or {})
        elif e['type'] == 'model_abandoned':
            req, route = pending.pop(key, (None, {}))
            if req:
                calls.append({
                    'run_id': run['id'], 'step_id': p.get('step_id'), 'invocation_id': p.get('invocation_id'), 'attempt_no': p.get('attempt_no'),
                    'status': 'abandoned', 'model': route.get('model') or '未知模型', 'provider': route.get('provider'),
                    'kind': 'local' if _is_local(route) else 'cloud', 'started': req['created'], 'finished': e['created'], 'duration_ms': None,
                    'prompt_tokens': None, 'completion_tokens': None, 'total_tokens': None, 'tokens_per_second': None,
                    'error': '进程中断或请求丢失，已作为新尝试重试', 'reserved_tokens': _int(route.get('context_limit'))})
        elif e['type'] in ('model_completed', 'model_failed'):
            req, route = pending.pop(key, (None, {}))
            usage = p.get('usage') or {}
            metrics = p.get('metrics') or {}
            started = req['created'] if req else None
            duration = p.get('duration_ms')
            if duration is None and started is not None:
                duration = round((e['created'] - started) * 1000)
            prompt = _int(usage.get('prompt_tokens'))
            completion = _int(usage.get('completion_tokens'))
            total = _int(usage.get('total_tokens'))
            if total is None and prompt is not None and completion is not None:
                total = prompt + completion
            calls.append({
                'run_id': run['id'], 'step_id': p.get('step_id'), 'invocation_id': p.get('invocation_id'),
                'attempt_no': p.get('attempt_no'), 'status': 'completed' if e['type'] == 'model_completed' else 'failed',
                'model': route.get('model') or '未知模型', 'provider': route.get('provider'),
                'kind': 'local' if route and _is_local(route) else 'cloud' if route else 'unknown',
                'started': started, 'finished': e['created'], 'duration_ms': duration,
                'prompt_tokens': prompt, 'completion_tokens': completion, 'total_tokens': total,
                'tokens_per_second': metrics.get('generation_tokens_per_second'),
                'error': p.get('error') or p.get('reason') or (p.get('contract_diagnostic') and '响应不符合契约') or None,
                # 上下文上限是单次调用 token 的上界：用量未知时按它预留，不当作 0。 Upper bound used when usage is unknown.
                'reserved_tokens': _int(route.get('context_limit')),
            })
    # 只有请求没有结果：说明仍在进行或结果未知，如实标记。 Requests without outcomes are in flight or unknown.
    for req, route in pending.values():
        p = req['payload']
        calls.append({
            'run_id': run['id'], 'step_id': p.get('step_id'), 'invocation_id': p.get('invocation_id'),
            'attempt_no': p.get('attempt_no'), 'status': 'pending', 'model': route.get('model') or '未知模型',
            'provider': route.get('provider'), 'kind': 'local' if _is_local(route) else 'cloud',
            'started': req['created'], 'finished': None, 'duration_ms': None, 'prompt_tokens': None,
            'completion_tokens': None, 'total_tokens': None, 'tokens_per_second': None, 'error': None,
            'reserved_tokens': _int(route.get('context_limit')),
        })
    return calls


def _tool_calls(store, run, events=None):
    """检查步骤的真实耗时与退出码；程序运行另列。 Real durations and exit codes of check steps, plus app runs."""
    ops = {n['id']: n.get('operation') for n in run['data'].get('graph', {}).get('nodes', [])}
    results = {t['step_id']: t.get('result_ref') for t in store.tools(run['id'])}
    out = []
    for a in store.db.execute('SELECT * FROM attempts WHERE run_id=? ORDER BY started', (run['id'],)):
        if a['step_id'] == 'gate':
            kind, op = 'gate', 'gate'
        else:
            kind, op = 'tool', ops.get(a['step_id']) or a['step_id']
        item = {'run_id': run['id'], 'step_id': a['step_id'], 'operation': op, 'kind': kind, 'status': a['status'],
                'started': a['started'], 'finished': a['finished'],
                'duration_ms': round((a['finished'] - a['started']) * 1000) if a['finished'] else None,
                'exit_code': None, 'isolated': kind == 'tool'}
        ref = results.get(a['step_id'])
        if ref:
            try:
                result = store.read(ref)
                item['exit_code'] = result.get('exit_code')
                item['tool_duration_ms'] = result.get('duration_ms')
            except Exception:  # 单个坏产物不能拖垮整份报告。 One broken artifact must not break the report.
                pass
        out.append(item)
    events = events if events is not None else store.events(run['id'])
    starts = {e['payload'].get('request_id'): e for e in events if e['type'] == 'app_requested'}
    for e in events:
        if e['type'] == 'app_finished':
            s = starts.get(e['payload'].get('request_id'))
            out.append({'run_id': run['id'], 'step_id': 'app', 'operation': 'run_application', 'kind': 'app',
                        'status': 'finished', 'started': s['created'] if s else None, 'finished': e['created'],
                        'duration_ms': round((e['created'] - s['created']) * 1000) if s else None,
                        'exit_code': None, 'isolated': True})
    return out


def task_report(store, run_id):
    """汇总同一任务（版本链所有成员）的用量、耗时与工具调用。 Aggregate every version of one task."""
    runs = {r['id']: r for r in store.all_runs()}
    root = Projects.root_id(run_id, runs)
    members = [r for r in runs.values() if Projects.root_id(r['id'], runs) == root]
    calls, tools, versions = [], [], []
    budget_events, decisions, process = [], [], []
    for run in members:
        events = store.events(run['id'])
        calls += _model_calls(store, run, events)
        tools += _tool_calls(store, run, events)
        budget_events += [e for e in events if e['type'] == 'task_budget']
        decisions += [{**e['payload'], 'run_id': run['id'], 'at': e['created']} for e in events if e['type'] == 'route_decided']
        process += [{'kind': e['type'], 'run_id': run['id'], 'at': e['created'], 'data': _process_data(e)} for e in events if e['type'] in _PROCESS_EVENTS]
        plan = run['data'].get('project_plan')
        versions.append({'run_id': run['id'], 'status': run['status'], 'plan_status': (plan or {}).get('status'),
                         'kind': 'verification' if run['data'].get('project_bundle') else 'code' if (plan or {}).get('kind') == 'code' else 'plan',
                         'created_at': run['data'].get('created_at')})
    calls.sort(key=lambda c: (c['started'] or c['finished'] or 0))
    tools.sort(key=lambda t: t['started'] or 0)

    totals = {'calls': len(calls), 'failed_calls': 0, 'unknown_usage_calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0,
              'total_tokens': 0, 'local_tokens': 0, 'cloud_tokens': 0, 'model_ms': 0}
    by_model, by_step = {}, {}
    for c in calls:
        totals['failed_calls'] += c['status'] == 'failed'
        known = c['total_tokens'] is not None
        totals['unknown_usage_calls'] += (not known) and c['status'] != 'pending'
        _add(totals, 'prompt_tokens', c['prompt_tokens'])
        _add(totals, 'completion_tokens', c['completion_tokens'])
        _add(totals, 'total_tokens', c['total_tokens'])
        _add(totals, 'local_tokens' if c['kind'] == 'local' else 'cloud_tokens', c['total_tokens'])
        _add(totals, 'model_ms', c['duration_ms'])
        for table, key in ((by_model, (c['model'], c['kind'])), (by_step, (c['step_id'],))):
            row = table.setdefault(key, {'calls': 0, 'failed': 0, 'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0, 'model_ms': 0})
            row['calls'] += 1
            row['failed'] += c['status'] == 'failed'
            for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                _add(row, k, c[k])
            _add(row, 'model_ms', c['duration_ms'])
    models = [{'model': k[0], 'kind': k[1], **v} for k, v in by_model.items()]
    steps = [{'step_id': k[0], **v} for k, v in by_step.items()]

    stamps = [c['started'] for c in calls if c['started']] + [t['started'] for t in tools if t['started']] + [v['created_at'] for v in versions if v['created_at']]
    ends = [c['finished'] for c in calls if c['finished']] + [t['finished'] for t in tools if t['finished']]
    started_at = min(stamps) if stamps else None
    ended_at = max(ends) if ends else None

    latest = max(members, key=lambda r: r['data'].get('created_at') or 0)
    selected = runs[run_id]
    # 结论取该任务最新版本：通过=成功；失败/取消；等待用户；其余为进行中或待继续。
    # The verdict follows the task's latest version; waiting states are not failures.
    plan = latest['data'].get('project_plan') or {}
    if latest['status'] == 'succeeded':
        outcome = 'succeeded'
    elif plan.get('status') in _WAITING:
        outcome = 'waiting'
    elif latest['status'] == 'cancelled':
        outcome = 'cancelled'
    elif latest['status'] in ('failed', 'needs_attention'):
        outcome = 'failed'
    else:
        outcome = 'running'
    routing = _routing_section(report_calls=calls, tools=tools, totals=totals, budget_events=budget_events, decisions=decisions)
    return {
        'project_id': root, 'selected_run_id': selected['id'], 'latest_run_id': latest['id'], 'title': runs[root]['data']['goal'],
        'outcome': outcome, 'started_at': started_at, 'ended_at': ended_at,
        'wall_seconds': round(ended_at - started_at, 2) if started_at and ended_at else None,
        'totals': totals, 'by_model': sorted(models, key=lambda m: -m['total_tokens']), 'by_step': steps,
        'calls': calls, 'tools': tools, 'versions': versions, 'routing': routing,
        'process': sorted(process, key=lambda p: p['at']),
    }


# 修复过程里值得在报告里展示的事件：子图走过的节点、诊断、模型释放、轮数延长、无改动拒收、停止原因。
# Events shown as the "process" of a task: workflow steps, diagnoses, model releases, extended rounds, rejected no-op fixes, stop reasons.
_PROCESS_EVENTS = {'workflow_node', 'diagnosis', 'diagnosis_failed', 'models_released', 'rounds_extended', 'noop_revision', 'task_stopped', 'transport_retry',
                   'rewrite_started', 'imports_fixed', 'conductor_decided', 'conductor_rejected', 'skeptic_verdict', 'skeptic_failed', 'code_review', 'code_review_skipped', 'best_of_n', 'best_of_n_skipped', 'case_audit', 'case_audit_failed', 'library_hit'}


def _process_data(event):
    """只保留短字段，避免把大段诊断原文塞进报告。 Keep short fields only."""
    return {k: (v[:600] if isinstance(v, str) else v) for k, v in event['payload'].items()
            if isinstance(v, (str, int, float, bool, list)) and k not in {'prompt', 'files'}}


def _routing_section(report_calls, tools, totals, budget_events, decisions):
    """路由与预算：模式、限额、当前花费（含预留）、每次决策与升级链。没有 task_budget 事件的旧任务返回 None。
    Routing and budget view; legacy tasks without a task_budget event yield None."""
    from masa.application.orchestration.routing import spend_from_report
    if not budget_events:
        return None
    latest = max(budget_events, key=lambda e: e['created'])['payload']
    decisions.sort(key=lambda d: d['at'])
    prices = {m: tuple(v) for m, v in (latest.get('prices') or {}).items()}
    spend = spend_from_report({'calls': report_calls, 'tools': tools, 'totals': totals}, prices)
    stop = next((d for d in reversed(decisions) if d.get('action') == 'stop'), None)
    return {'mode': latest.get('mode'), 'budget': latest.get('budget'), 'policy': latest.get('policy'),
            'candidates': latest.get('candidates', []), 'spend': spend, 'decisions': decisions,
            'escalations': sum(1 for d in decisions if d.get('escalated')),
            'stopped': {'reason': stop['reason'], 'detail': stop.get('detail')} if stop else None}
