"""Browser application service: one worker, separate SQLite connection per thread."""

from dataclasses import asdict
from pathlib import Path
import threading
import uuid
import time

from masa.infrastructure.runner import Runner
from masa.infrastructure.store import Store
from masa.domain.models import Budget, Graph, MasaError
from masa.languages.registry import registry
from masa.runtime.engine import Runtime
from masa.infrastructure.settings import Settings
from masa.runtime.graph import full_verification_policy, collaboration_policy, harness_policy
from masa.languages.go.single_file import CodeGeneration
from masa.infrastructure.workspaces import verify_snapshot
from masa.application.planning import ProjectPlanning
from masa.application.generation import ProjectGeneration
from masa.infrastructure.jobs import Jobs
from masa.infrastructure.hardware import HardwareMonitor
from masa.infrastructure import capability
from masa.infrastructure.diagnostics import DiagnosticLog


from masa.application.checks.check_policy import test_revision_needed, repair_advice, test_format_only, format_only, repeated_assertion_signature  # noqa: F401 (re-exported)
from masa.application.console_languages import LanguageMixin
from masa.application.console_bench import BenchMixin
from masa.application.console_views import ViewsMixin
from masa.application.console_jobs import JobsMixin


class Console(LanguageMixin, BenchMixin, ViewsMixin, JobsMixin):
    def __init__(self, state_dir, runner, go, project):
        self.root = Path(state_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.runner_path, self.go_path, self.project = (
            Path(runner),
            Path(go),
            Path(project),
        )
        self.settings = Settings(self.root)
        # 如果跑过 scripts/eval/calibrate_tokens.py，就用真实调用拟合的系数。 Use fitted coefficients when calibration has been run.
        from masa.domain.tokens import configure as configure_tokens
        configure_tokens(self.root / 'token-calibration.json')
        self.hardware = HardwareMonitor()
        self.diagnostics = DiagnosticLog(self.root)
        self.lock = threading.RLock()
        self.active = None
        self.worker = None
        self.errors = {}
        self.closing = False
        self.jobs = Jobs(self.root)
        self.app_cancel = threading.Event()
        self.app_run_id = None
        self.job_thread = None
        self.bench = None
        self.bench_thread = None
        Store(self.root).close()

    def _runner(self):
        """执行器；语言的工具链属于各自的语言包，缺失时只影响那种语言的任务（探测会告诉用户）。 The executor; a language's toolchain belongs to its pack, so a missing one only affects tasks in that language (the probe tells the user)."""
        return Runner(self.runner_path, self.go_path if self.go_path.is_file() else None)

    def bootstrap(self):
        """返回前端配置与真实能力标记。 Return frontend defaults and actual capability flags."""
        # 持久 running 不足以代表活着；以当前进程的 worker 为准。
        # A persisted running row does not prove liveness; consult this process's worker.
        active_job = None
        if self.job_thread and self.job_thread.is_alive():
            for ident, job in list(self.jobs.items()):
                if job.get('status') == 'running':
                    active_job = {k: job.get(k) for k in ('run_id','mode','phase','started','model','provider')}
                    active_job.update(job_id=ident,status='running',stage=job.get('phase'))
                    break
        return {
            "console_version": "workspace-console-v2",
            "local_models": dict(capability.STATE),  # 本机能否跑本地模型（启动时检测）/ whether this host can run local models (checked at start)
            "demo": bool(getattr(self, "demo", False)),  # 只读演示部署：前端据此显示提示条 / read-only demo deployment: the UI shows a banner
            # 路由默认值：前端据此显示默认预算与策略。 Routing defaults shown by the frontend.
            "routing_defaults": self.settings.routing(),
            "default_repo": str(self.project / "tests/fixtures/go-pass"),
            "runner_ready": self.runner_path.is_file() and self.go_path.is_file(),
            "runner_exists": self.runner_path.is_file(),
            "language": registry.default_id(),
            "languages": [{"id": i["id"], "title": i.get("title"), "ready": bool((i.get("readiness") or {}).get("ok")), "ui": i.get("ui", {}), "default": bool(i.get("default"))}
                          for i in registry.describe(self._probe_executor()) if "error" not in i],
            "active_run": self.active,
            "active_job": active_job,
            "interrupted_jobs": [{'job_id':ident,'run_id':job.get('run_id'),'phase':job.get('phase')}
                                 for ident,job in self.jobs.items() if job['status']=='interrupted' and job.get('mode')=='auto'],
            "provider": "scripted-v1",
            "capabilities": {
                "execute": True,
                "rerun": True,
                "project_planning": True,
                "project_generation": True,
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

    # ───────────── 新任务页的“开跑前估计” / the pre-run estimate of the New Task page ─────────────
    ESTIMATE_IDLE_SECONDS = 45  # 估计完后本地模型最多再驻留这么久（用户还在打字时不必反复冷加载），之后释放显存 / how long the local model may stay loaded after an estimate (no cold load per keystroke), then the VRAM is freed

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
                language = body.get("language") or registry.default_id()
                operation = body.get("operation") or registry.get(language).op_id("test")
                if (
                    not isinstance(source, str)
                    or not source.strip()
                    or not isinstance(goal, str)
                ):
                    raise MasaError("repository and goal are required")
                if operation not in registry.get(language).operation_ids():
                    raise MasaError("unsupported operation")
                budget = self._budget(body)
                provider = (
                    self.settings.provider(body.get("api_profile_id"))
                    if body.get("provider") == "live"
                    else None
                )
                runtime = Runtime(
                    store, self._runner(), provider
                )
                graph = full_verification_policy(language) if body.get("full_checks") else None
                if body.get('role_demo'):
                    graph = collaboration_policy(operation, language)
                rid = runtime.create(
                    Path(source),
                    goal,
                    budget,
                    operation,
                    parent_run_id=parent,
                    language=language,
                    intelligence=body.get("intelligence") is True,
                    graph=graph,
                )
            finally:
                store.close()
            self._launch(rid, 1 if body.get("pause_after") is True else 0, provider)
            return {"id": rid}

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
                runtime = Runtime(store, self._runner())
                # 项目重验保留已批准 Agent 图，旧演示继续使用完整固定检查。
                # Preserve approved agent graphs on reruns; legacy demos retain full checks.
                graph=Graph.from_dict(data['graph']) if data['graph'].get('policy_version')=='agent-check-plan-v1' else harness_policy()
                new_id = runtime.create(source, data['goal'], self._budget({'full_checks': True}),
                                        graph=graph, parent_run_id=rid,project_bundle=data.get('project_bundle'))
                # 复制前后都绑定同一版本，避免外部改动绕过审核。
                # Bind both sides of the copy to the reviewed version.
                if store.run(new_id)['data']['snapshot_id'] != data['snapshot_id']:
                    store.set_status(new_id, 'needs_attention', 'snapshot_mismatch')
                    raise MasaError('snapshot changed while preparing rerun')
            finally:
                store.close()
            self._launch(new_id, 0)
            return {'id': new_id}

    def review_project_sources(self, rid, body):
        """解析可见草稿，结果只用于审查而不替代 Gate。 Parse the visible draft without replacing Gate verification."""
        from masa.application.review.source_review import review_sources
        with self.lock:
            self._available()
            store=Store(self.root)
            try:return review_sources(store,self._runner(),rid,body)
            finally:store.close()

    def answer_clarification(self, rid, body):
        """先持久回答，再由现有后台入口继续；自动模式保留原任务。 Save answers before resuming the existing workflow."""
        with self.lock:
            self._available()
            store=Store(self.root)
            try:
                ProjectPlanning(store,self._runner()).answer(
                    rid,body.get('question_id'),body.get('answers'))
                status=store.run(rid)['data']['project_plan']['status']
            finally:store.close()
        if status!='planning':
            ident=uuid.uuid4().hex
            self.jobs[ident]={'status':'completed','run_id':rid,'result':{'id':rid},'started':time.time()}
            return {'job_id':ident}
        for ident,job in self.jobs.items():
            if job.get('mode')=='auto' and job.get('run_id')==rid and job.get('status')=='waiting_for_input':
                return self.start_autonomous_project_job({},resume_job=ident)
        return self.start_project_job({**body,'resume_project':True},rid)

    def user_input(self, rid, body):
        """人在回路：任务运行中追加提示（第 1 级）或控制（第 2 级：暂停/继续/停止）。都先写账本，再生效；只在节点边界生效，不打断正在进行的调用。
        Human in the loop: add a note (level 1) or a control (level 2: pause / resume / stop) while a task runs. Ledger first, then effect; only at node boundaries, never interrupting a call in flight."""
        from masa.runtime import user_input
        body=body or {}
        kind=body.get('kind')
        store=Store(self.root)
        try:
            store.run(rid)  # 不存在就抛错 / raises when the run does not exist
            if kind=='note':
                text=user_input.clean_note(body.get('text'))
                user_input.record(store,rid,'note',text=text)
                return {'ok':True,'kind':'note','applies':'the next model call that starts'}
            action=body.get('action')
            if kind!='control' or action not in {'pause','resume','stop'}:
                raise MasaError('input needs kind=note with text, or kind=control with action pause/resume/stop')
            job=self._job_for(store,rid)
            if action=='stop':
                user_input.record(store,rid,'control',action='stop')
            elif job is None:
                raise MasaError('there is no running automatic task to '+action)
            else:
                job['paused']=(action=='pause')
                user_input.record(store,rid,'control',action=action)
        finally:
            store.close()
        if action=='stop':
            return self.control(rid,'cancel')
        return {'ok':True,'kind':'control','action':action}

    def _job_for(self, store, rid):
        """这个 run 所在任务（同一条版本链）当前正在运行的自动任务；没有返回 None。 The running automatic job of the task (same version chain) this run belongs to, or None."""
        def root(run_id):
            seen=set()
            while run_id and run_id not in seen:
                seen.add(run_id)
                try:parent=store.run(run_id)['data'].get('parent_run_id')
                except Exception:return run_id
                if not parent:return run_id
                run_id=parent
            return run_id
        target=root(rid)
        for job in self.jobs.values():
            if job.get('status')=='running' and job.get('mode')=='auto' and job.get('run_id') and root(job['run_id'])==target:
                return job
        return None

    def plan_project(self, body, on_created=None):
        """串行规划与角色交接，保存为可恢复预览的运行记录。 Serialize role planning into a persistent preview run."""
        with self.lock:
            self._available()
            provider = self.settings.provider(body.get('api_profile_id'))
            store = Store(self.root)
            try:
                retry=body.get('retry_run_id')
                goal=body.get('goal')
                if retry:
                    old=store.run(retry)
                    if old['status']!='failed' or not old['data'].get('project_plan'):
                        raise MasaError('only a failed plan can be retried')
                    goal=old['data']['goal']
                rid = ProjectPlanning(store, self._runner()).generate(provider, goal,on_created,retry_of=retry,language=None if retry else self._language_of(body))
                return {'id':rid}
            finally:
                store.close()

    def approve_project(self, rid, body):
        """仅确认规格，不启动工具或生成代码。 Approve specifications without tools or code generation."""
        with self.lock:
            self._available()
            store = Store(self.root)
            try:
                ProjectPlanning(store, self._runner()).approve(rid, body)
                return {'id':rid}
            finally:
                store.close()

    def generate_project(self, rid, body, on_created=None):
        """生成多文件草稿，密钥继续只在服务内存。 Generate a multi-file draft with session-only credentials."""
        with self.lock:
            self._available()
            provider = self.settings.provider(body.get('api_profile_id'))
            store = Store(self.root)
            try:
                return {'id':ProjectGeneration(store, self._runner()).generate(rid,provider,on_created)}
            finally:
                store.close()

    def repair_project(self, rid, body, on_created=None):
        """用户显式发起一次证据驱动修复。 Start one evidence-driven repair on explicit request."""
        with self.lock:
            self._available()
            provider=self.settings.provider(body.get('api_profile_id'))
            store=Store(self.root)
            try:
                return {'id':ProjectGeneration(store,self._runner()).repair(rid,provider,body.get('feedback',''),on_created,use_intelligence=True)}
            finally:store.close()

    def revise_project_tests(self, rid, body, on_created=None):
        """显式创建只修改测试的草稿。 Create a test-only draft on explicit request."""
        with self.lock:
            self._available()
            provider=self.settings.provider(body.get('api_profile_id'))
            store=Store(self.root)
            try:
                return {'id':ProjectGeneration(store,self._runner()).revise_tests(
                    rid,provider,body.get('feedback',''),on_created)}
            finally:store.close()

    def format_project_tests(self, rid):
        """纯格式错误直接产生待审测试修订。 Create a reviewable test formatting revision without model usage."""
        with self.lock:
            self._available()
            store=Store(self.root)
            try:
                return {'id':ProjectGeneration(store,self._runner()).format_test_files(rid)}
            finally:store.close()

    def approve_project_code(self, rid, body):
        """批准整套文件后启动独立检查，重复请求不重复运行。 Approve the bundle and launch checks without duplicate runs."""
        with self.lock:
            self._available()
            store = Store(self.root)
            try:
                child = ProjectGeneration(store, self._runner()).approve(rid,body)
                status = store.run(child)['status']
            finally:
                store.close()
            if status == 'created':
                self._launch(child,0)
            return {'id':child}

    def generate(self, body):
        """为需求生成待审核草稿；单服务串行保护生成与执行。 Generate a pending draft while serializing generation/execution."""
        with self.lock:
            self._available()
            provider = self.settings.provider(body.get('api_profile_id'))
            store = Store(self.root)
            try:
                rid = CodeGeneration(store, self._runner()).generate(
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
                service = CodeGeneration(store, self._runner())
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
                    store, self._runner(), provider
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
        # 网络调用可能持有 Console 锁；控制写入使用独立 SQLite 事务，立即保存意图。
        # A network call may hold the Console lock; persist control in its own SQLite transaction.
        store = Store(self.root)
        try:
            if action == "pause":
                store.request_pause(rid)
            else:
                store.cancel(rid)
                # 角色 HTTP 请求待返回后收尾，已收到结果会保留，但不会发布代码。
                # Role HTTP calls drain before shutdown; received results stay saved without publication.
                if self.active != rid:
                    store.set_status(rid, "cancelled", "cancellation_requested")
        finally:
            store.close()
        return {"id": rid, "requested": action}

    def close(self):
        self.app_cancel.set()
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
        if self.app_run_id and self.job_thread:
            self.job_thread.join(timeout=15)
