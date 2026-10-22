"""控制台的后台任务部分：自动任务的启动/恢复/失败处理、运行策略与预算、本地模型的释放。 The console's background-job part."""
import threading
import uuid
import time
from masa.infrastructure.store import Store
from masa.domain.models import MasaError
from masa.application.planning import ProjectPlanning
from masa.application.generation import ProjectGeneration
from masa.application.orchestration.router import Router, build_snapshot
from masa.infrastructure.ollama import OllamaControl
from masa.infrastructure import capability


class JobsMixin:
    def _release_if_idle(self):
        if not (self.job_thread and self.job_thread.is_alive()):
            self._release_local_models()

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
        if runtime in ('v2','v2repair'):
            # 工人循环的记忆策略：按当前模型的有效窗口自动选一档（见 docs/design/AUTO_COMPACTION.md；真实 A/B：全程 V2 的 grades、bank 各 1 次，window 对 auto 都通过，输入 token −8% / −30%，方向性）。请求里显式给出的值不改。
            # Memory strategy of the worker loops: a tier chosen automatically from the current model's effective window (real A/B, full V2, one run each on grades and bank: both pass with window and auto, input tokens -8% / -30%, directional). An explicit request value stays.
            policy={'memory_strategy':'auto',**(policy if isinstance(policy,dict) else {})}
        if runtime=='v2repair':
            # “V1 生成 + 工人修复”默认开用例审计与断言裁判：它们是已测得有效的独立证据（配对评测里审计 更好5/更差0/平12；LCS 回放里审计 ¥0.002 抓到 2–3/3 个错用例），也是信号 S1 触发仲裁的前提。请求里显式给出的值不改。
            # "V1 generation + worker repair" turns the case audit and the assertion referee on by default: independent evidence measured to work (paired audit 5 better / 0 worse / 12 equal; the LCS replay caught 2-3 of 3 wrong cases for 0.002 CNY) and the precondition for signal S1 to trigger arbitration. Explicit request values stay.
            policy={'test_audit':True,'assertion_referee':True,**(policy if isinstance(policy,dict) else {})}
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
