"""Browser application service: one worker, separate SQLite connection per thread."""

from dataclasses import asdict
from pathlib import Path
import threading
import uuid
import time
import os
import json
import re

from masa.infrastructure.runner import Runner
from masa.infrastructure.store import Store
from masa.domain.models import Budget, Graph, MasaError
from masa.languages.registry import registry
from masa.application.reports import render
from masa.runtime.engine import Runtime
from masa.infrastructure.settings import Settings
from masa.runtime.graph import full_verification_policy, collaboration_policy, harness_policy
from masa.languages.go.single_file import CodeGeneration
from masa.infrastructure.workspaces import verify_snapshot
from masa.application.planning import ProjectPlanning
from masa.application.generation import ProjectGeneration
from masa.application.projects import Projects
from masa.application.usage import task_report
from masa.application.orchestration.router import Router, build_snapshot
from masa.application.orchestration.routing import DEFAULT_BUDGET, DEFAULT_POLICY
from masa.runtime.roles import RoleRuntime
from masa.infrastructure.jobs import Jobs
from masa.infrastructure.ollama import OllamaControl
from masa.infrastructure.hardware import HardwareMonitor
from masa.infrastructure import capability
from masa.infrastructure.diagnostics import DiagnosticLog


from masa.application.checks.check_policy import test_revision_needed, repair_advice, test_format_only, format_only, repeated_assertion_signature  # noqa: F401 (re-exported)


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

    def languages(self):
        """每个语言包的说明、就绪状态与当前配置（设置页用）。 Every pack's description, readiness and current configuration (for the Settings page)."""
        return {'default': registry.default_id(), 'languages': registry.describe(self._probe_executor()), 'extra_dirs': self.settings.load_languages().get('extra_dirs', [])}

    def save_languages(self, body):
        self.settings.save_languages(body)
        return self.languages()

    def _language_of(self, body):
        """任务的语言：请求里的优先，否则默认语言；必须已注册且工具链就绪。 The task's language: the request's, else the default; it must be registered and its toolchain ready."""
        language = body.get('language') or registry.default_id()
        pack = registry.get(language)
        # 没有运行器时（测试/开发替身）不做就绪检查，真正的错误会在执行时明确报出 / without a runner (a test or dev harness) readiness is not checked; the real error is reported at execution
        ready = pack.probe(self._probe_executor()) if self.runner_path.is_file() else None
        if ready is not None and not ready.ok:
            raise MasaError(f'language {pack.title} is not ready: ' + '; '.join(ready.details))
        return pack.id

    def _probe_executor(self):
        """给语言包探测工具链用的执行器；运行器不存在时给一个空对象（探测会如实说缺什么）。 The executor language packs probe their toolchains with; an empty object when there is no runner (the probe then says what is missing)."""
        import types
        try:
            return self._runner()
        except MasaError:
            return types.SimpleNamespace()

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
        return assess(goal, registry.get(body.get('language') or registry.default_id()))  # 规则属于所选语言的包 / the rules belong to the chosen language's pack

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

    # ───────────── 评测 / benchmark ─────────────
    def bench_tasks(self):
        from masa.bench.tasks import describe
        return describe()

    def bench_start(self, body):
        """启动一次评测（后台线程）。评测在独立的临时状态目录里跑，不污染历史，也不占用主界面的任务槽，但会占用 GPU 和云端额度，所以不允许与正在执行的任务同时跑。
        Start a benchmark in a background thread. It runs in a scratch state directory (no pollution of history) but uses the GPU and cloud budget, so it never overlaps a running task."""
        from masa.bench.runner import BenchRunner, resolve_config
        with self.lock:
            if self.bench_thread and self.bench_thread.is_alive():
                raise MasaError('a benchmark is already running')
            if (self.job_thread and self.job_thread.is_alive()) or self.active:
                raise MasaError('a task is running; start the benchmark after it finishes')
            config=resolve_config(body)
            self.bench=BenchRunner(self.root,self.runner_path,self.go_path,self.project,config)
            self.bench_thread=threading.Thread(target=self.bench.run,daemon=True,name='masa-bench')
            self.bench_thread.start()
            return {'id':self.bench.id}

    def bench_status(self):
        return self.bench.snapshot() if self.bench else {'state':'idle'}

    def bench_stop(self):
        if self.bench and self.bench_thread and self.bench_thread.is_alive():
            self.bench.stop()
        return self.bench_status()

    def bench_results(self):
        from masa.bench.runner import list_results
        return {'results':list_results(self.root)}

    def bench_result(self, result_id):
        from masa.bench.runner import load_result
        from masa.bench.report import aggregate
        data=load_result(self.root,result_id)
        data['aggregate']=aggregate(data.get('records',[]))
        return data

    def _available(self):
        if self.bench_thread and self.bench_thread.is_alive():
            raise MasaError('a benchmark is running; stop it first (it uses the GPU and cloud budget)')
        if self.job_thread and self.job_thread.is_alive() and threading.current_thread() is not self.job_thread:
            raise MasaError('project generation is busy; wait for the current job')
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

    def start_project_job(self, body, rid=None):
        """快速返回任务标识，让浏览器轮询真实阶段。 Return a job ID immediately for polling actual stages."""
        # 回答落盘后启动前崩溃，通用恢复入口必须保留原自动流程。
        # Preserve the automatic workflow when recovery follows a saved answer.
        if body.get('resume_project') and rid:
            for ident,job in self.jobs.items():
                if job.get('mode')=='auto' and job.get('run_id')==rid and job.get('status') in {'waiting_for_input','interrupted'}:
                    return self.start_autonomous_project_job({},resume_job=ident)
        with self.lock:
            self._available()
            ident=uuid.uuid4().hex
            self.jobs[ident]={'status':'running','run_id':None,'started':time.time()}
            def created(run_id):
                self.jobs[ident]['run_id']=run_id
            def work():
                try:
                    result=self.resume_project(rid,body,created) if body.get('resume_project') else (self.revise_project_tests(rid,body,created) if body.get('test_revision') else self.repair_project(rid,body,created) if body.get('repair') else self.generate_project(rid,body,created)) if rid else self.plan_project(body,created)
                    store=Store(self.root)
                    try:plan=store.run(result['id'])['data'].get('project_plan',{})
                    finally:store.close()
                    status=plan.get('status') if plan.get('status') in {'waiting_for_input','cancelled','failed'} else 'completed'
                    self.jobs[ident].update(status=status,result=result)
                except Exception as exc:
                    self._fail_job(ident,exc,'worker:project')
                finally:self._release_local_models()
            self.job_thread=threading.Thread(target=work,daemon=True,name='masa-project-job')
            self.job_thread.start()
            return {'job_id':ident}

    def resume_project(self, rid, body, on_created=None):
        """恢复中断的规划或生成，复用已落盘角色结果。 Resume interrupted planning/generation from persisted role outputs."""
        with self.lock:
            self._available()
            store=Store(self.root)
            try:
                run=store.run(rid);data=run['data'];plan=data.get('project_plan',{})
                snapshot=store.read(data['model_snapshot_ref']) if data.get('model_snapshot_ref') else None
                provider=self.settings.provider(snapshot=snapshot) if snapshot else self.settings.provider(body.get('api_profile_id'))
                if plan.get('provider')!=provider.profile:
                    raise MasaError('select the original API profile to resume')
                runner=self._runner()
                if plan.get('repair_of'):
                    if on_created:on_created(rid)
                    result=ProjectGeneration(store,runner).resume_revision(rid,provider)
                elif plan.get('kind')=='code':
                    result=ProjectGeneration(store,runner).generate(data['parent_run_id'],provider,on_created,resume_id=rid)
                else:
                    result=ProjectPlanning(store,runner).generate(provider,data['goal'],on_created,resume_id=rid)
                return {'id':result}
            finally:store.close()

    def start_application(self,rid,body):
        """后台运行已验证程序，可停止且不改变 Gate。 Run a verified CLI asynchronously without changing its Gate."""
        with self.lock:
            self._available()
            ident=uuid.uuid4().hex
            self.app_cancel.clear();self.app_run_id=rid
            self.jobs[ident]={'status':'running','run_id':rid,'started':time.time(),'phase':'application'}
            def work():
                store=Store(self.root)
                try:
                    from masa.application.applications import run_application
                    result=run_application(store,self._runner(),rid,body.get('argv',[]),self.app_cancel.is_set,body.get('stdin',''))
                    self.jobs[ident].update(status='completed',result={'id':rid,'app_result':result})
                except Exception as exc:
                    self.diagnostics.record('worker:application',exc)
                    self.jobs[ident].update(status='failed',error=str(exc) if isinstance(exc,MasaError) else 'application execution interrupted; inspect saved evidence')
                finally:
                    self.app_run_id=None;store.close()
            self.job_thread=threading.Thread(target=work,daemon=True,name='masa-application')
            self.job_thread.start()
            return {'job_id':ident}

    def stop_application(self,rid):
        """仅取消当前程序，不撤销已有验证。 Cancel the active application without revoking verification."""
        if self.app_run_id!=rid:raise MasaError('no active application for this run')
        self.app_cancel.set()
        return {'id':rid}

    def _release_local_models(self):
        """手动任务结束后释放所有已启用的本地 Ollama 模型：不留空挂的模型，显卡随时可还给用户。尽力而为。
        After a manual job, unload every enabled local Ollama model so no model idles in VRAM. Best effort."""
        try:
            for ident,_ in self.settings.ready_profiles():
                profile=self.settings.profiles.get(ident,{})
                if profile.get('model_type')=='local':
                    try:self.settings.provider(ident).unload()
                    except Exception:pass
        except Exception:pass

    @staticmethod
    def _v2_policy(body):
        """V2 的生成阶段每个等级只试一次：同一个等级的第二次尝试从第一次写了一半的文件接着做，真实运行里 6 次里 6 次都以同样的方式卡住（本地模型反复 finish），白白多花 5–10 分钟；卡住后直接升级。用户显式给的 attempts_per_level 不改。
        A V2 generation stage tries each level once: the second attempt at the same level continues from the first one's half-written files and in real runs it got stuck the same way every time (the local model finishing over and over), costing 5-10 minutes for nothing; after being stuck it escalates at once. An explicit attempts_per_level is left alone."""
        policy=body.get('policy')
        if body.get('runtime')=='v2' and not (isinstance(policy,dict) and 'attempts_per_level' in policy):
            return {'assertion_referee':True,**(policy if isinstance(policy,dict) else {}),'attempts_per_level':{'generation':1}}
        if body.get('runtime')=='v2' and isinstance(policy,dict) and 'assertion_referee' not in policy:
            return {**policy,'assertion_referee':True}  # 工人的测试期望常常算错，断言裁判便宜且实测有效 / worker-written test expectations are often wrong; the referee is cheap and measured to work
        return policy

    def _runtime_policy(self, body):
        """V2 与“V1 生成 + V2 修复”的工人从最低的云端等级起步：本地 glm-4.7-flash 在真实运行里当不了包工人（7 次本地先试只封过 1 个很小的包，一次失败的尝试 1–12 分钟）。用户显式给的 start_level_by_role 不改；没有云端模型时不加。
        Workers of V2 and of "V1 generation + V2 repair" start at the lowest cloud level: the local glm-4.7-flash could not be a package worker in real runs (7 local-first tries sealed one tiny package, a failed try took 1-12 minutes). An explicit start_level_by_role is left alone; nothing is added without a cloud model."""
        policy=self._v2_policy(body)
        runtime=body.get('runtime')
        if runtime in ('v2','v2repair'):
            wanted=set(body['model_ids']) if isinstance(body.get('model_ids'),list) else None
            levels=[p['level'] for i,p in self.settings.ready_profiles() if p['model_type']=='cloud' and (wanted is None or i in wanted)]
            if levels:
                start=dict((policy or {}).get('start_level_by_role') or {})
                for role in ('project_repair',)+(('project_developer',) if runtime=='v2' else ()):
                    start.setdefault(role,min(levels))
                policy={**(policy or {}),'start_level_by_role':start}
        return policy

    @staticmethod
    def _v2_budget(body):
        """V2 的工人循环每一步都是一次模型调用（10 文件的项目一次运行要 80–150 次，而默认的 40 次上限会在中途把任务停掉）：V2 默认放宽到 150，用户显式给的预算不改。
        Every step of a V2 worker loop is a model call (a 10-file project takes 80-150 per run and the default limit of 40 would stop it half way): V2 defaults to 150, an explicit user budget is left alone."""
        budget=body.get('budget')
        if body.get('runtime') in ('v2','v2repair') and not (isinstance(budget,dict) and 'max_model_calls' in budget):
            return {**(budget if isinstance(budget,dict) else {}),'max_model_calls':150}
        return budget

    def auto_fix(self, rid, body):
        """从一次失败的验证继续自动修复：跳过规划与生成，沿用任务的路由与预算设置。
        Continue the automatic repair from a failed verification (no re-planning); history is rebuilt from the version lineage."""
        store=Store(self.root)
        try:
            run=store.run(rid)
            goal=run['data'].get('goal')
        finally:store.close()
        request=dict(body or {})
        request.update(goal=goal,continue_from=rid,auto_verify=True)
        return self.start_autonomous_project_job(request)

    def start_autonomous_project_job(self, body, resume_job=None, *, workflow=None):
        """一次选择后有界完成生成、校验与最多四轮修复。 Complete a bounded project loop after one explicit choice."""
        with self.lock:
            self._available()
            if resume_job:
                previous=self.jobs.get(resume_job)
                if not previous or previous.get('mode')!='auto' or previous['status'] not in {'interrupted','waiting_for_input'}:
                    raise MasaError('only interrupted automatic workflows can resume')
                if previous.get('phase') in {'test_format','planning_retry'}:
                    raise MasaError('interrupted revision requires inspection of its saved draft; automatic replay is disabled')
                if previous.get('run_id'):
                    store=Store(self.root)
                    try:
                        plan=store.run(previous['run_id'])['data'].get('project_plan',{})
                        if plan.get('status')=='waiting_for_input':
                            raise MasaError('answer the pending clarification before resuming')
                    finally:store.close()
                body=previous['request']
            # 路由：ladder = 本地优先、有界升级、任务级预算；固定模式保持原行为。恢复时沿用任务开始时冻结的候选集合。
            # Routing: ladder = local first, bounded escalation, task budget; fixed keeps legacy behaviour. Resume reuses the frozen set.
            routing=previous.get('routing') if resume_job else None
            if not resume_job and body.get('routing')=='ladder':
                routing=build_snapshot(self.settings,mode='ladder',budget=self._v2_budget(body),policy=self._runtime_policy(body),
                                       profile_ids=set(body['model_ids']) if isinstance(body.get('model_ids'),list) else None,digests=self._local_digests())
            snapshot=previous.get('model_snapshot') if resume_job else None
            if routing:
                provider=None
            else:
                provider=self.settings.provider(snapshot=snapshot) if snapshot else self.settings.provider(body.get('api_profile_id'))
                if resume_job and previous.get('provider',provider.profile)!=provider.profile:
                    raise MasaError('select the original API profile to resume')
            goal=body.get('goal')
            if not isinstance(goal,str) or not goal.strip():
                raise MasaError('project goal required')
            if workflow:
                from masa.application.orchestration import flow
                flow.validate(workflow)  # 图只能引用已注册的 guard 与动作 / the graph may only reference registered guards and actions
            ident=resume_job or uuid.uuid4().hex
            if resume_job:self.jobs[ident].update(status='running',note=None)
            else:self.jobs[ident]={'status':'running','run_id':None,'started':time.time(),
                              'mode':'auto','phase':'planning','attempt':0,
                              'provider':provider.profile if provider else {},'model_snapshot':getattr(provider,'snapshot',None),
                              'routing':routing,'model':('本地优先 · 有界升级' if routing else None),
                              'request':{'goal':goal,'language':self._language_of(body) if not body.get('continue_from') else None,**({'runtime':body['runtime']} if body.get('runtime') in ('v2','v2repair') else {}),'continue_from':body.get('continue_from'),'api_profile_id':body.get('api_profile_id') or self.settings.active_id,'force':body.get('force') is True,
                                         **({'workflow':workflow} if workflow else {})}}  # workflow 只能由进程内的调用方（评测/调优器）传入，HTTP 接口不接受 / only in-process callers (benchmark/tuner) may pass it; the HTTP API does not
            from masa.application.orchestration.coordinator import WorkflowCoordinator
            def work():
                store=Store(self.root)
                runner=self._runner()
                try:
                    router=None
                    if routing:
                        # 候选按冻结快照重建；本地权重摘要变了的候选自动不可用。 Rebuild from the frozen set; changed local weights make a candidate unavailable.
                        router=Router(store,routing,lambda entry:self.settings.provider(snapshot=entry['snapshot']),
                                      live_digests=self._local_digests() if resume_job else None)
                        if not self.jobs[ident].get('routing_ref'):
                            self.jobs[ident]['routing_ref']=store.put(routing)
                    WorkflowCoordinator(store,runner,provider,self.jobs[ident],resuming=bool(resume_job),router=router).run()
                except Exception as exc:
                    self._fail_job(ident,exc,'worker:auto')
                finally:store.close()
            self.job_thread=threading.Thread(target=work,daemon=True,name='masa-auto-project-job')
            self.job_thread.start()
            return {'job_id':ident}

    def _local_digests(self):
        """本地模型权重摘要（best effort；Ollama 不可达则返回 None，不阻塞任务）。 Local weight digests, best effort."""
        try:
            return {m['name']:m.get('digest') for m in OllamaControl().catalog().get('models',[])}
        except Exception:
            return None

    def _fail_job(self, ident, exc, route):
        """业务取消不改成失败；其余故障保存安全调用位置。 Preserve cancellation and log safe locations for other faults."""
        cancelled=False
        rid=self.jobs[ident].get('run_id')
        if rid:
            store=Store(self.root)
            try:
                run=store.run(rid)
                cancelled=bool(run['cancel_requested'] or run['status']=='cancelled')
            finally:store.close()
        if cancelled:
            self.jobs[ident].update(status='cancelled',note='取消已生效；当前模型请求已收尾，保留已收到的证据。')
        else:
            request_id=self.diagnostics.record(route,exc)
            self.jobs[ident].update(status='failed',error=str(exc) if isinstance(exc,MasaError) else '后台任务失败，请查看诊断。',request_id=request_id)

    def project_job(self, ident):
        """返回服务端进度，不编造 token 百分比。 Return server progress without inventing token percentages."""
        if ident not in self.jobs:
            raise MasaError('job unavailable after restart; check saved run records')
        job=dict(self.jobs[ident])
        if job['run_id']:
            detail=self.detail(job['run_id'])
            job['run_status']=detail['run']['status']
            job['generation_progress']=detail['run']['data'].get('project_plan',{}).get('gen_progress')
            requested=[e for e in detail['events'] if e['type']=='model_requested']
            job['stage']=job.get('phase') if job.get('mode')=='auto' else requested[-1]['payload']['step_id'] if requested else 'preparing'
            # 流式传输的实时进度：正在生成的字符数与用时（只在内存里）。 Live streaming progress: characters generated so far and elapsed time (memory only).
            from masa.runtime import live as live_calls
            job['live']={k:v for k,v in (live_calls.get(job['run_id']) or {}).items() if k!='text'} or None  # 完整文本只走 /live，不放进每秒一次的任务轮询 / the full text travels only via /live, not in the per-second job poll
            # 顶栏显示“此刻真正在用的模型”，而不是选择框里的默认模型。 Show the model actually in use, not the default in a selector.
            if requested:
                route=requested[-1]['payload'].get('route') or {}
                job['current_model']=route.get('model')
                job['current_kind']=('local' if route.get('provider')=='ollama-native' else 'cloud') if route else None
            # 版本切换后仍显示同任务最近的实际速度，不伪造当前请求吞吐。
            # Keep the latest measured speed across revisions, never pretend it is the current live rate.
            store=Store(self.root)
            try:
                rid=job['run_id'];seen=set();measurements=[]
                while rid and rid not in seen and len(seen)<30:
                    seen.add(rid)
                    measurements.extend(e for e in store.events(rid) if e['type'] in {'model_completed','model_failed'} and e['payload'].get('metrics'))
                    rid=store.run(rid)['data'].get('parent_run_id')
                if measurements:
                    latest=max(measurements,key=lambda e:e['seq'])
                    job['last_model_metrics']=latest['payload']['metrics']
                if detail.get('role_active') and requested:job['stage']=requested[-1]['payload']['step_id']
            finally:store.close()
        job['model']=job.get('provider',{}).get('model') or job.get('model')
        return job

    def ollama_action(self,body):
        """受控动作共用单后台槽，避免与项目推理争抢。 Use one worker slot for local controls and project inference."""
        action=body.get('action');model=body.get('model');control=OllamaControl()
        if not capability.local_allowed():raise MasaError('local models are disabled on this host: '+capability.STATE['reason'])
        if action=='show':return control.show(model)
        if action not in {'select','load','unload','test'}:raise MasaError('unsupported Ollama action')
        with self.lock:
            self._available()
            installed=control.installed(model)
            if action=='select':
                matches=[i for i,p in self.settings.profiles.items() if p['model_type']=='local' and p['protocol']=='ollama' and p['base_url']==control.base and p['model']==model]
                values={'model_type':'local','protocol':'ollama','base_url':control.base,'model':model,'enabled':True}
                if matches:values['id']=matches[0]
                else:values.update(new=True,name='Ollama '+model,level=1,priority=0,context_limit=16384,max_output_tokens=8192,timeout_seconds=600,thinking='disabled')
                profiles=self.settings.save(values)
                return {'settings':profiles,'model':installed}
            ident=uuid.uuid4().hex
            self.jobs[ident]={'status':'running','mode':'ollama','phase':action,'run_id':None,'started':time.time(),'model':model}
            def work():
                try:
                    if action=='test':
                        from masa.infrastructure.llm import ChatProvider
                        provider=ChatProvider({'model_type':'local','base_url':control.base,'model':model,'timeout_seconds':600,
                                               'context_limit':8192,'max_output_tokens':256,'thinking':'disabled'})
                        output=provider.respond({'operation':'go_test','goal':'Request the allowed go_test tool.','tool_results':[]})
                        result={'output':output,'usage':provider.usage,'metrics':provider.metrics}
                    else:result=control.residency(model,action=='load')
                    self.jobs[ident].update(status='completed',result=result)
                except Exception as exc:
                    self.jobs[ident].update(status='failed',error=str(exc) if isinstance(exc,MasaError) else 'local model control failed')
            self.job_thread=threading.Thread(target=work,daemon=True,name='masa-ollama-control');self.job_thread.start()
            return {'job_id':ident}

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

    def plan_project(self, body, on_created=None):
        """串行规划与角色交接，保存为可恢复预览的运行记录。 Serialize role planning into a persistent preview run."""
        with self.lock:
            self._available()
            provider = self.settings.provider(body.get('api_profile_id'))
            store = Store(self.root)
            try:
                reuse=body.get('retry_run_id')
                goal=body.get('goal')
                if reuse:
                    old=store.run(reuse)
                    if old['status']!='failed' or not old['data'].get('project_plan',{}).get('spec_ref'):
                        raise MasaError('only failed plans with saved Planner output can retry Tester')
                    goal=old['data']['goal']
                rid = ProjectPlanning(store, self._runner()).generate(provider, goal,on_created,reuse,language=None if reuse else self._language_of(body))
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
