"""Browser application service: one worker, separate SQLite connection per thread."""

from dataclasses import asdict
from pathlib import Path
import threading

from masa.adapters.runner import Runner
from masa.adapters.sqlite import Store
from masa.domain import Budget, MasaError, OPERATIONS
from masa.report import render
from masa.runtime import Runtime
from masa.web.settings import Settings
from masa.workflow import full_verification_policy, collaboration_policy, harness_policy
from masa.codegen import CodeGeneration
from masa.workspace import verify_snapshot
from masa.project_plan import ProjectPlanning


class Console:
    def __init__(self, state_dir, runner, go, project):
        self.root = Path(state_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.runner_path, self.go_path, self.project = (
            Path(runner),
            Path(go),
            Path(project),
        )
        self.settings = Settings(self.root)
        self.lock = threading.RLock()
        self.active = None
        self.worker = None
        self.errors = {}
        self.closing = False
        Store(self.root).close()

    def bootstrap(self):
        """返回前端配置与真实能力标记。 Return frontend defaults and actual capability flags."""
        return {
            "console_version": "workspace-console-v2",
            "default_repo": str(self.project / "tests/fixtures/go-pass"),
            "runner_ready": self.runner_path.is_file() and self.go_path.is_file(),
            "active_run": self.active,
            "provider": "scripted-v1",
            "capabilities": {
                "execute": True,
                "rerun": True,
                "project_planning": True,
                "graph": True,
                "node_pause": True,
                "revise_as_new_run": True,
                "code_edit": True,
                "code_generation": True,
                "live_llm": True,
                "multi_agent": False,
                "code_intelligence": True,
                "readonly_role_protocol": True,
            },
        }

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
            # A read transaction yields one coherent run/step/event view.
            store.db.execute("BEGIN")
            run = store.run(rid)
            events = store.events(rid)
            return {
                "run": run,
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
            for row in store.steps(rid) + store.tools(rid):
                refs.update(v for k, v in row.items() if k.endswith("_ref") and v)
            for event in store.events(rid):
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

    @staticmethod
    def _budget(body):
        """完整图共享总预算，节点不能自行扩充额度。 Full graphs share a budget that nodes cannot expand."""
        defaults = asdict(
            Budget(
                deadline_seconds=1800,
                model_calls=8 if body.get("full_checks") else 6 if body.get('role_demo') else 4,
                tool_calls=6 if body.get("full_checks") else 3,
            )
        )
        supplied = body.get("budget", {})
        if not isinstance(supplied, dict) or set(supplied) - set(defaults):
            raise MasaError("unknown budget fields")
        budget = Budget(**{**defaults, **supplied})
        budget.validate()
        if (
            budget.deadline_seconds > 86400
            or budget.model_calls > 100
            or budget.tool_calls > 100
        ):
            raise MasaError("console budget exceeds local limits")
        return budget

    def create(self, body, parent=None):
        """创建隔离任务并选择是否使用证据上下文。 Create an isolated run with optional evidence context."""
        with self.lock:
            self._available()
            # 先校验传输层选项，禁止拼写错误静默变成离线运行。
            # Validate transport options before side effects; never silently downgrade a mistyped provider.
            if body.get("provider", "scripted") not in {"scripted", "live"}:
                raise MasaError("unsupported provider; choose scripted or live")
            for flag in ("full_checks", "pause_after", "intelligence", "role_demo"):
                if flag in body and type(body[flag]) is not bool:
                    raise MasaError(flag + " must be boolean")
            if body.get('role_demo') and (body.get('full_checks') or body.get('provider') == 'live'):
                raise MasaError('read-only role demo requires scripted provider and a separate graph')
            store = Store(self.root)
            try:
                source = body.get("repo", "")
                goal = body.get("goal", "")
                operation = body.get("operation", "go_test")
                if (
                    not isinstance(source, str)
                    or not source.strip()
                    or not isinstance(goal, str)
                ):
                    raise MasaError("repository and goal are required")
                if operation not in OPERATIONS:
                    raise MasaError("unsupported operation")
                budget = self._budget(body)
                provider = (
                    self.settings.provider(body.get("api_profile_id"))
                    if body.get("provider") == "live"
                    else None
                )
                runtime = Runtime(
                    store, Runner(self.runner_path, self.go_path), provider
                )
                graph = full_verification_policy() if body.get("full_checks") else None
                if body.get('role_demo'):
                    graph = collaboration_policy(operation)
                rid = runtime.create(
                    Path(source),
                    goal,
                    budget,
                    operation,
                    parent_run_id=parent,
                    intelligence=body.get("intelligence") is True,
                    graph=graph,
                )
            finally:
                store.close()
            self._launch(rid, 1 if body.get("pause_after") is True else 0, provider)
            return {"id": rid}

    def results(self, rid):
        """读取工具账本中的实际输出。 Read actual outputs from the tool ledger."""
        store = Store(self.root)
        try:
            store.run(rid)
            return {'checks': [
                {'id': row['id'], 'step_id': row['step_id'],
                 'operation': store.read(row['request_ref'])['operation'],
                 'result': store.read(row['result_ref']) if row['result_ref'] else None}
                for row in store.tools(rid)]}
        finally:
            store.close()

    def rerun(self, rid):
        """复制已完成的代码快照重新验证，不调用付费模型。 Verify a completed snapshot in a new run without paid models."""
        with self.lock:
            self._available()
            store = Store(self.root)
            try:
                original = store.run(rid)
                data = original['data']
                if data.get('project_plan'):
                    raise MasaError('project plan only: generate code before verification')
                if original['status'] not in {'succeeded', 'failed', 'needs_attention'}:
                    raise MasaError('finish or approve the current run before rerunning')
                if data.get('codegen') and data['codegen'].get('status') != 'approved':
                    raise MasaError('human review required before rerunning generated code')
                source = Path(data['workspace'])
                verify_snapshot(source, data['snapshot_id'])
                runtime = Runtime(store, Runner(self.runner_path, self.go_path))
                new_id = runtime.create(source, data['goal'], self._budget({'full_checks': True}),
                                        graph=harness_policy(), parent_run_id=rid)
                # 复制前后都绑定同一版本，避免外部改动绕过审核。
                # Bind both sides of the copy to the reviewed version.
                if store.run(new_id)['data']['snapshot_id'] != data['snapshot_id']:
                    store.set_status(new_id, 'needs_attention', 'snapshot_mismatch')
                    raise MasaError('snapshot changed while preparing rerun')
            finally:
                store.close()
            self._launch(new_id, 0)
            return {'id': new_id}

    def _available(self):
        if self.closing:
            raise MasaError("console is shutting down")
        if self.active:
            raise MasaError("another run is executing; pause or cancel it first")

    def resume(self, rid, pause_after=False):
        """恢复运行，支持单节点或自动推进。 Resume one node or automatically advance the graph."""
        with self.lock:
            self._available()
            store = Store(self.root)
            try:
                if store.run(rid)['data'].get('project_plan'):
                    raise MasaError('project plan only: generate code before execution')
                generation = store.run(rid)['data'].get('codegen')
                if generation and generation.get('status') != 'approved':
                    raise MasaError('请先审核代码提案并点击批准执行 / human review required')
                if store.run(rid)["status"] not in {"created", "paused", "running"}:
                    raise MasaError(
                        "this run cannot be resumed; create a new run instead"
                    )
            finally:
                store.close()
            self._launch(rid, 1 if pause_after is True else 0, self._provider_for(rid))
            return {"id": rid}

    def plan_project(self, body):
        """串行规划与角色交接，保存为可恢复预览的运行记录。 Serialize role planning into a persistent preview run."""
        with self.lock:
            self._available()
            provider = self.settings.provider(body.get('api_profile_id'))
            store = Store(self.root)
            try:
                rid = ProjectPlanning(store, Runner(self.runner_path, self.go_path)).generate(provider, body.get('goal'))
                return {'id':rid}
            finally:
                store.close()

    def approve_project(self, rid, body):
        """仅确认规格，不启动工具或生成代码。 Approve specifications without tools or code generation."""
        with self.lock:
            self._available()
            store = Store(self.root)
            try:
                ProjectPlanning(store, Runner(self.runner_path, self.go_path)).approve(rid, body)
                return {'id':rid}
            finally:
                store.close()

    def generate(self, body):
        """为需求生成待审核草稿；单服务串行保护生成与执行。 Generate a pending draft while serializing generation/execution."""
        with self.lock:
            self._available()
            provider = self.settings.provider(body.get('api_profile_id'))
            store = Store(self.root)
            try:
                rid = CodeGeneration(store, Runner(self.runner_path, self.go_path)).generate(
                    provider, body.get('goal',''), body.get('repo',''), body.get('target','solution.go'),
                    body.get('tests',''), body.get('parent_run_id'))
                return {'id':rid}
            finally:
                store.close()

    def review_code(self, rid, body):
        """人工拒绝或批准后启动确定性真实工具验证。 Reject or approve before deterministic real-tool verification."""
        with self.lock:
            self._available()
            store = Store(self.root)
            try:
                service = CodeGeneration(store, Runner(self.runner_path, self.go_path))
                if body.get('action') == 'reject':
                    service.reject(rid)
                    return {'id':rid}
                if body.get('action') != 'approve':
                    raise MasaError('choose approve or reject')
                service.approve(rid, body.get('proposal_ref'), body.get('content'))
            finally:
                store.close()
            self._launch(rid, 0)
            return {'id':rid}

    def _provider_for(self, rid):
        """按运行身份解析配置，避免默认切换影响恢复。 Resolve the frozen identity independently of the selected default."""
        store = Store(self.root)
        try:
            expected = store.run(rid)["data"].get("model_profile")
            return self.settings.provider(expected=expected) if expected else None
        finally:
            store.close()

    def _launch(self, rid, pause_after, provider=None):
        """后台执行绑定了 provider 的任务。 Run with a bound provider in the background."""
        self.active = rid
        self.errors.pop(rid, None)

        def work():
            store = None
            try:
                store = Store(self.root)
                Runtime(
                    store, Runner(self.runner_path, self.go_path), provider
                ).execute(rid, pause_after)
            except Exception as exc:
                # Provider failures are sanitized; never expose credentials in worker diagnostics.
                self.errors[rid] = str(exc)
            finally:
                if store is not None:
                    store.close()
                with self.lock:
                    self.active = None

        self.worker = threading.Thread(target=work, name=f"masa-{rid[:8]}", daemon=True)
        self.worker.start()

    def control(self, rid, action):
        """持久化人工控制，无 worker 时直接完成取消。 Persist human control and finalize detached cancellation."""
        with self.lock:
            store = Store(self.root)
            try:
                if action == "pause":
                    store.request_pause(rid)
                else:
                    store.cancel(rid)
                    # 另一个任务的 worker 不会处理本任务的取消。
                    # A different run's worker cannot observe this run's cancellation.
                    if self.active != rid:
                        store.set_status(rid, "cancelled", "cancellation_requested")
            finally:
                store.close()
        return {"id": rid, "requested": action}

    def close(self):
        with self.lock:
            self.closing = True
            rid, worker = self.active, self.worker
        if rid:
            store = Store(self.root)
            try:
                try:
                    store.cancel(rid)
                except MasaError:
                    pass
            finally:
                store.close()
        if worker:
            worker.join(timeout=15)
