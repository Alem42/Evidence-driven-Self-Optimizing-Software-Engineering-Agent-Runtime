"""控制台的只读视图：项目/运行列表、详情、产物、报告、调用检视、实时视图、日志、预检与估计。 The console's read-only views."""
from pathlib import Path
import threading
import time
import os
from masa.infrastructure.store import Store
from masa.domain.models import MasaError
from masa.languages.registry import registry
from masa.application.reports import render
from masa.application.projects import Projects
from masa.application.usage import task_report
from masa.runtime.roles import RoleRuntime
from masa.application.checks.check_policy import repair_advice


class ViewsMixin:
    def projects(self, rid=None):
        """为前端提供项目中心读取视图。 Expose project-centered read models to the interface."""
        store=Store(self.root)
        try:
            # 同一读取事务避免跨阶段混用元数据与事件。 Read metadata and events from one consistent snapshot.
            store.db.execute('BEGIN')
            return Projects(store).view(rid) if rid else {'projects':Projects(store).catalog()}
        finally:store.close()

    def triage(self, body):
        """需求可行性预检（确定性规则，无模型调用），供新建任务页实时提示。 Rules-only feasibility triage for the New Task page."""
        goal = body.get('goal')
        if not isinstance(goal, str) or len(goal) > 16000:
            raise MasaError('goal must be text up to 16000 characters')
        from masa.application.checks.triage import assess
        pack = registry.get(body.get('language') or registry.default_id())  # 规则属于所选语言的包 / the rules belong to the chosen language's pack
        result = assess(goal, pack)
        environment = pack.environment()
        if environment is not None:  # 点名了第三方包：告诉页面要装什么、按什么策略 / the goal names third-party packages: tell the page what to install and under which policy
            wanted = environment.required_packages(goal)
            if wanted['named']:
                result = {**result, 'packages': {**wanted, **environment.summary()}}
        return result

    def estimate(self, body):
        """估计这个需求大概多难、云端 API 要多少 token、多少钱，以及系统能不能处理。mode=rules：确定性规则，立刻返回；mode=model：再让本地模型看一眼（只用本地模型，不写账本，没有本地模型或正在跑任务就退回规则）。
        Estimate how hard the goal is, how many cloud API tokens and how much money it needs, and whether the system can handle it. mode=rules: deterministic and instant; mode=model: the local model looks as well (local only, no ledger entry; without one, or while a task runs, the rules answer)."""
        from masa.application import estimate as sizing
        from masa.application.checks.triage import assess
        goal = body.get('goal')
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 16000:
            raise MasaError('goal must be text up to 16000 characters')
        pack = registry.get(body.get('language') or registry.default_id())
        cloud = [p for _i, p in self.settings.ready_profiles() if p.get('model_type') == 'cloud']
        ready = pack.probe(self._probe_executor()).ok if self.runner_path.is_file() else True
        rules = assess(goal, pack)
        answer, note, name = None, '', ''
        if body.get('mode') == 'model':
            answer, note, name = self._ask_local_estimator(goal, pack.id)
        result = sizing.build(goal, rules, answer, cloud, ready, source='local-model' if answer else 'rules')
        return {**result, 'model': name, 'note': note, 'mode': 'model' if body.get('mode') == 'model' else 'rules'}

    def _ask_local_estimator(self, goal, language):
        """(回答 | None, 说明, 模型名)。同一个目标 10 分钟内只问一次；任务在跑时不动本地模型。 (answer | None, note, model name). The same goal is asked once per 10 minutes; the local model is left alone while a task runs."""
        import time as _time
        import threading
        key = (goal.strip(), language)
        cache = self.__dict__.setdefault('_estimates', {})
        hit = cache.get(key)
        if hit and _time.time() - hit[0] < 600:
            return hit[1], hit[2], hit[3]
        if self.job_thread and self.job_thread.is_alive():
            return None, '有任务正在运行，本地模型留给它，这里按规则估计。', ''
        local = [(i, p) for i, p in self.settings.ready_profiles() if p.get('model_type') == 'local']
        if not local:
            return None, '没有可用的本地模型，按规则估计。', ''
        ident, profile = local[0]
        try:
            provider = self.settings.provider(ident)
            answer = provider.respond({'purpose': 'task_estimator', 'goal': goal, 'language': language, 'reason_language': '中文' if any('一' <= ch <= '鿿' for ch in goal) else 'English'}, timeout=90)
        except Exception as exc:  # 估计只是建议：任何失败都退回规则 / advisory: any failure falls back to the rules
            return None, '本地模型没能给出估计（' + str(exc)[:80] + '），按规则估计。', profile.get('model', '')
        finally:
            timer = self.__dict__.get('_estimate_timer')
            if timer:
                timer.cancel()
            timer = threading.Timer(self.ESTIMATE_IDLE_SECONDS, self._release_if_idle)
            timer.daemon = True
            timer.start()
            self._estimate_timer = timer
        if len(cache) >= 64:
            cache.clear()
        cache[key] = (_time.time(), answer, '', profile.get('model', ''))
        return answer, '', profile.get('model', '')

    def project_report(self, rid):
        """任务级用量/耗时/工具调用报告。 Task-level usage, timing and tool-call report."""
        store = Store(self.root)
        try:
            store.db.execute('BEGIN')
            return task_report(store, rid)
        finally:
            store.close()

    def list_runs(self):
        store = Store(self.root)
        try:
            ids = [
                r[0]
                for r in store.db.execute(
                    "SELECT id FROM runs ORDER BY rowid DESC LIMIT 100"
                )
            ]
            result = []
            for rid in ids:
                run = store.run(rid)
                result.append(
                    {
                        "id": rid,
                        "status": run["status"],
                        "goal": run["data"]["goal"],
                        "created_at": run["data"]["created_at"],
                        "reason": run["reason"],
                        "active": rid == self.active,
                    }
                )
            return result
        finally:
            store.close()

    def detail(self, rid):
        store = Store(self.root)
        try:
            roles=RoleRuntime(store)
            # A read transaction yields one coherent run/step/event view.
            store.db.execute("BEGIN")
            run = store.run(rid)
            events = store.events(rid)
            return {
                "run": run,
                "role_calls": roles.states(rid),
                "role_active": bool(self.job_thread and self.job_thread.is_alive()) and any(
                    j.get('status')=='running' and j.get('run_id')==rid for j in self.jobs.values()),
                "steps": store.steps(rid),
                "events": events[-500:],
                "event_count": len(events),
                "tools": store.tools(rid),
                "attempts": [
                    dict(r)
                    for r in store.db.execute(
                        "SELECT * FROM attempts WHERE run_id=? ORDER BY started", (rid,)
                    )
                ],
                "active": rid == self.active,
                "worker_error": self.errors.get(rid),
                "pause_requested": store.pause_requested(rid),
            }
        finally:
            store.close()

    def artifact(self, rid, ref):
        store = Store(self.root)
        try:
            run = store.run(rid)
            refs = {run["data"]["manifest_ref"]}
            if run['data'].get('model_snapshot_ref'):refs.add(run['data']['model_snapshot_ref'])
            if run['data'].get('project_bundle'):
                # 只允许读取本次发布快照绑定的完整代码 artifact。
                # Expose only the exact approval artifact bound to this published snapshot.
                refs.add(run['data']['project_bundle']['approval_ref'])
            for row in store.steps(rid) + store.tools(rid):
                refs.update(v for k, v in row.items() if k.endswith("_ref") and v)
            for event in store.events(rid):
                if not isinstance(event['payload'],dict):continue
                refs.update(
                    v
                    for k, v in event["payload"].items()
                    if k.endswith("_ref") and isinstance(v, str)
                )
            if ref not in refs:
                raise MasaError("artifact is not referenced by this run")
            return store.read(ref)
        finally:
            store.close()

    def report(self, rid):
        store = Store(self.root)
        try:
            return render(store, rid)
        finally:
            store.close()

    def delivery(self, rid, smoke=False):
        """交付报告（结构化 + Markdown）：由运行时从账本生成，不是模型写的。 The delivery report, structured plus Markdown, generated by the runtime from the ledger."""
        from masa.application import delivery
        store = Store(self.root)
        try:
            data = delivery.build(store, rid, self._runner() if smoke else None, run_smoke=smoke)
            return {'delivery': data, 'markdown': delivery.render(data), 'inconsistencies': delivery.check_consistency(store, data)}
        finally:
            store.close()

    def results(self, rid):
        """读取工具账本中的实际输出。 Read actual outputs from the tool ledger."""
        store = Store(self.root)
        try:
            pack = registry.for_run(store.run(rid)['data'])
            checks = [
                {'id': row['id'], 'step_id': row['step_id'],
                 'operation': store.read(row['request_ref'])['operation'],
                 'result': store.read(row['result_ref']) if row['result_ref'] else None}
                for row in store.tools(rid)]
            completed=[(c['operation'],c['result']) for c in checks if c['result']]
            from masa.application.checks import ownership
            analysis=ownership.analyse(completed,pack)
            return {'checks':checks,'repair_advice':repair_advice(completed,pack),
                    'ownership':{'primary':analysis['primary'],'lines':ownership.describe(analysis)}}
        finally:
            store.close()

    def call_list(self, rid):
        """这个任务（整条版本链）的每一次模型调用。 Every model call of the task (the whole version chain)."""
        from masa.application import call_inspector
        store=Store(self.root)
        try:return call_inspector.call_list(store,rid)
        finally:store.close()

    def call_detail(self, rid, step, invocation):
        """一次调用的检视：模型看到了什么、各部分大小、省略项、缓存命中、响应摘要。 Inspect one call: what the model saw, section sizes, omissions, cache hits, response summary."""
        from masa.application import call_inspector
        store=Store(self.root)
        try:return call_inspector.call_detail(store,rid,step,invocation)
        finally:store.close()

    def live_view(self, rid):
        """这个 run 此刻正在进行的模型调用：哪个角色、哪个模型、写了多少，以及正在写的文件内容（从还没写完的 JSON 里解出）。只在内存里，没有调用时返回 None。
        The model call in flight for this run: role, model, progress, and the file content being written (decoded from the unfinished JSON). Memory only; None when idle."""
        from masa.runtime import live as live_calls
        from masa.runtime.streamfiles import extract_files
        entry=live_calls.get(rid)
        if not entry or not entry.get('step'):
            return {'live':None}
        text=entry.get('text','')
        return {'live':{'step':entry['step'],'invocation':entry.get('invocation'),'model':entry.get('model'),'chars':entry.get('chars',0),'seconds':entry.get('seconds',0),
                        'seq':entry.get('seq',0),'truncated':bool(entry.get('truncated')),'files':extract_files(text),'tail':entry.get('tail','')[-600:],
                        'idle_seconds':round(time.time()-entry.get('updated',time.time()),1)}}

    def project_logs(self, rid):
        """汇总来源链的角色事件与工具输出，便于复制。 Collect lineage events and tool outputs for copying."""
        lines=[];seen=set()
        while rid and rid not in seen and len(seen)<30:
            seen.add(rid);d=self.detail(rid)
            lines.append(f"RUN {rid} | {d['run']['status']} | {d['run']['data']['goal']}")
            for event in d['events']:
                lines.append(f"{event['created']} {event['type']} {event['payload']}")
                if event['type']=='app_finished':
                    store=Store(self.root)
                    try:lines.append(str(store.read(event['payload']['result_ref'])))
                    finally:store.close()
            for check in self.results(rid)['checks']:
                result=check['result']
                lines.append(f"{check['operation']}: {result}")
                if result:
                    lines.extend(['STDOUT:',result.get('stdout',''),'STDERR:',result.get('stderr','')])
            rid=d['run']['data'].get('parent_run_id')
        return {'text':'\n'.join(lines)}

    def open_workspace(self, rid):
        """仅打开该运行绑定的目录，不接受任意路径或命令。 Open only the workspace bound to this run."""
        run=self.detail(rid)['run']
        if run['data'].get('project_plan'):
            raise MasaError('approve generated code before opening the project folder')
        path=Path(run['data']['workspace']).resolve()
        if not path.is_relative_to(self.root/'workspaces') or not path.is_dir():
            raise MasaError('workspace unavailable')
        if os.name!='nt':
            raise MasaError('folder opening currently supports Windows; copy the workspace path')
        os.startfile(str(path))
        return {'path':str(path)}
