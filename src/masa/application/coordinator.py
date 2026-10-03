"""Automatic project orchestration independent of HTTP and worker lifecycle."""
from masa.application.planning import ProjectPlanning
from masa.application.generation import ProjectGeneration
from masa.application.workflow import WorkflowCheckpoint
from masa.application.check_policy import format_only, test_revision_needed, repeated_assertion_signature
from masa.application.router import Router, RoutingStop
from masa.runtime.engine import Runtime
from masa.domain.models import ContextOverflow, MasaError
import time


class WorkflowCoordinator:
    def __init__(self, store, runner, provider, job, *, resuming=False, router=None):
        """共享持久任务与角色服务，不拥有 HTTP 或线程。无 router 时保持原有“固定单模型”行为。
        Share durable state without owning HTTP or threads. Without a router the legacy fixed single-model behaviour applies."""
        self.store,self.runner,self.job=store,runner,job
        self.router=router or Router.fixed(store,provider)
        self.provider=provider
        self.resuming=resuming
        self.checkpoint=WorkflowCheckpoint(job)
        self._deferred=[]  # 还没有锚点 run 时先缓存的事件 / events waiting for an anchor run

    # ───────────── 事件与路由 / events and routing ─────────────
    @property
    def _ladder(self):
        return self.router.mode=='ladder'

    def _emit(self, kind, payload, anchor=None):
        """写入路由事件；任务的第一个 run 出现之前先缓存。只在 ladder 模式记录，固定模式不产生额外事件。
        Record routing events; buffered until the task's first run exists. Fixed mode adds no events."""
        if not self._ladder:return
        self._deferred.append((kind,payload,anchor))
        self._flush()

    def _flush(self):
        remaining=[]
        for kind,payload,anchor in self._deferred:
            rid=anchor or self.job.get('run_id')
            if not rid:
                remaining.append((kind,payload,anchor));continue
            self.store.event(rid,kind,payload)
            if kind=='task_budget':self.job['budget_emitted']=True
        self._deferred=remaining

    def _phase(self, name, rid=None, attempt=None):
        """阶段检查点；顺带写出缓存的路由事件。 Checkpoint the phase and flush buffered routing events."""
        self.checkpoint.phase(name,rid,attempt)
        self._flush()

    def _history(self, chain):
        return list(self.job.get('route_history',{}).get(chain,[]))

    def _fail(self, chain, decision):
        """记录某条失败链上一次失败的尝试，供下一次路由决定是否升级。 Record a failed attempt on a failure chain."""
        if not decision or decision.action!='use':return
        all_history=dict(self.job.get('route_history',{}))
        all_history[chain]=self._history(chain)+[{'candidate':decision.candidate,'level':decision.level}]
        self.job['route_history']=all_history

    def _pick(self, role, chain, *, need=None, anchor=None, stage=None):
        """向路由器要下一次尝试的模型；stop 时抛 RoutingStop。 Ask the router for the next attempt; stop raises RoutingStop."""
        decision,provider,spend=self.router.decide(role,chain,self._history(chain),
                                                    anchor_run=anchor or self.job.get('run_id'),need=need)
        self._emit('route_decided',self.router.event_for(decision,role,chain,stage or role,spend),anchor)
        if decision.action=='stop':
            raise RoutingStop(decision.reason,decision.detail)
        return provider,decision

    def _provider_of(self, run_id):
        """恢复时必须使用当初那次调用的同一个模型。 Resume with the exact provider that made the saved call."""
        profile=self.store.run(run_id)['data'].get('project_plan',{}).get('provider')
        if self.router.mode=='fixed' or profile is None:return self.router.fixed_provider or self.provider
        return self.router.provider_matching(profile)

    def _retryable(self, run_id):
        """响应已收到但被契约或业务校验拒绝时才值得带原因重试；网络、鉴权、截断、取消都不重试。
        Retry only when a response was RECEIVED and then rejected; never for transport, auth, truncation or cancellation."""
        if not run_id:
            return None
        run = self.store.run(run_id)
        if run['cancel_requested'] or run['status'] == 'cancelled' or time.time() >= run['data']['deadline_at']:
            return None
        plan = run['data'].get('project_plan', {})
        events = [e for e in self.store.events(run_id) if e['type'] in {'model_failed', 'model_completed'}]
        if not events:
            return None
        last = events[-1]
        received = last['type'] == 'model_completed' or bool(last['payload'].get('contract_diagnostic'))
        return (plan.get('error') or 'response rejected by validation') if received else None

    def _attempts_allowed(self, chain):
        """一个阶段内最多尝试几次（含升级后的尝试）。固定模式保持原来的“初次 + 重试一次”。
        Total attempts within one stage, including after escalation; fixed mode keeps 'first try + one retry'."""
        if not self._ladder:return 2
        policy=self.router.policy
        return min(4,int(policy['attempts_per_level'].get(chain,1))*(int(policy.get('max_escalations',0))+1))

    def _attempt(self, chain, role, call, *, need=None, stage=None):
        """call(provider, feedback)；响应被校验拒绝时带原因重试，重复失败由路由器决定是否升级。
        Retry a rejected stage with its reason; repeated failures let the router decide whether to escalate."""
        feedback=None
        for n in range(self._attempts_allowed(chain)):
            provider,decision=self._pick(role,chain,need=need,stage=stage)
            try:
                return call(provider,feedback)
            except RoutingStop:
                raise
            except ContextOverflow as overflow:
                # 这个候选的窗口放不下输入：记一次失败，并用估算的真实输入量重新选择，路由器会跳过放不下的候选。
                # 固定模式没有别的候选可选，直接把明确的错误交给用户。
                # This candidate's window cannot hold the input: record it and re-route with the measured need so too-small
                # candidates are skipped. Fixed mode has no alternative, so the explicit error surfaces.
                if not self._ladder:
                    raise
                self._fail(chain,decision)
                need=overflow.estimated_tokens
                continue
            except MasaError:
                reason=self._retryable(self.job.get('run_id')) if n<self._attempts_allowed(chain)-1 else None
                if not reason:
                    raise
                self._fail(chain,decision)
                feedback='Your previous attempt was rejected: '+reason

    def _stopped(self, stop, run_id):
        """路由决定停止：保留证据，任务以说明结束，而不是报错。 A routing stop ends the task with a note and keeps all evidence."""
        self._emit('task_stopped',{'reason':stop.reason,'detail':stop.detail},run_id)
        self.job.update(status='completed',result={'id':run_id},note=str(stop))

    # ───────────── 主流程 / main flow ─────────────
    def run(self):
        """从持久检查点推进流程，等待用户时释放执行权。 Advance checkpoints and yield when user input is required."""
        store,runner=self.store,self.runner
        goal=self.job['request']['goal']
        phase=self._phase
        if self._ladder and not self.job.get('budget_emitted'):
            self._emit('task_budget',self.router.describe())
        planning=ProjectPlanning(store,runner)
        generation=ProjectGeneration(store,runner)
        checkpoint=self.job
        plan=checkpoint.get('plan_id')
        # 发布与后台检查点之间也可能中断；直接复用已发布的同一份方案。
        # Publication can precede the job checkpoint: reuse that exact saved plan.
        if self.resuming and not plan and checkpoint.get('phase')=='planning' and checkpoint.get('run_id'):
            saved=store.run(checkpoint['run_id'])['data'].get('project_plan',{})
            if saved.get('status') in {'awaiting_review','approved'}:
                plan=checkpoint['run_id'];checkpoint['plan_id']=plan
        reuse=None
        planner_feedback=None
        retry_of=None
        planner_retries=int(self.router.policy.get('planner_retries',1))
        decision=None
        for retry in range(3 if not plan else 0):
            try:
                recover_id=checkpoint.get('run_id') if self.resuming and checkpoint['phase']=='planning' and retry==0 else None
                if recover_id:
                    provider=self._provider_of(recover_id);decision=None
                else:
                    provider,decision=self._pick('project_tester' if reuse else 'project_planner','planning',
                                                 need=goal,stage='planning')
                plan=planning.generate(provider,goal,lambda rid:phase('planning',rid),reuse,resume_id=recover_id,
                                       retry_of=retry_of,retry_feedback=planner_feedback)
                if store.run(plan)['data']['project_plan'].get('status')=='waiting_for_input':
                    checkpoint.update(status='waiting_for_input',result={'id':plan})
                    return
                checkpoint['plan_id']=plan
                break
            except RoutingStop:
                raise
            except MasaError:
                failed_id=self.job['run_id']
                run=store.run(failed_id)
                # 取消和过期是工作流边界，不应伪装成 Tester 契约失败重建任务。
                # Cancellation and expiry are workflow boundaries, never retryable Tester contracts.
                if run['cancel_requested'] or run['status']=='cancelled' or time.time()>=run['data']['deadline_at']:
                    raise
                failed=run['data'].get('project_plan',{})
                if recover_id and failed.get('spec_ref'):raise
                if not failed.get('spec_ref'):
                    # Planner 的响应被契约拒绝：带原因重规划（新版本，旧失败保留）；重复失败可升级。
                    # A Planner response rejected by the contract gets a corrected re-plan (new version; the failure is kept).
                    reason=self._retryable(failed_id) if retry<planner_retries else None
                    if not reason:raise
                    self._fail('planning',decision)
                    planner_feedback=reason;retry_of=failed_id
                    phase('planning_retry',failed_id,retry+1)
                    continue
                if retry==2:
                    raise
                # 仅复用已校验 Planner 结果，Tester 最多额外调用两次。
                # Reuse validated Planner output and retry Tester at most twice.
                self._fail('planning',decision)
                reuse=failed_id
                phase('planning_retry',failed_id,retry+1)
        meta=store.run(plan)['data']['project_plan']
        if meta['status']!='approved':planning.approve(plan,{'spec_ref':meta['spec_ref'],'checks_ref':meta['checks_ref'],
                               'spec':store.read(meta['spec_ref']),'checks':store.read(meta['checks_ref']),
                               'review_mode':'automatic'})
        draft=checkpoint.get('draft_id')
        if self.resuming and checkpoint['phase'] in {'repair','test_revision'}:
            revision=checkpoint.get('run_id')
            # 结果未知时 resume_revision 拒绝重放，保留原始预算与证据。
            # Unknown results stop recovery instead of replaying a paid request.
            draft=generation.resume_revision(revision,self._provider_of(revision))
            checkpoint['draft_id']=draft
        if not draft:
            recover_id=checkpoint.get('run_id') if self.resuming and checkpoint['phase']=='generation' and checkpoint.get('run_id')!=plan else None
            phase('generation',recover_id or plan)
            saved=store.run(recover_id)['data'].get('project_plan',{}) if recover_id else {}
            if saved.get('status') in {'awaiting_review','approved'}:
                draft=recover_id
            else:
                approved=store.read(store.run(plan)['data']['project_plan']['approval_ref'])
                first=[True]
                def generate(provider,fb):
                    resume=recover_id if (fb is None and first[0]) else None
                    first[0]=False
                    if resume:provider=self._provider_of(resume)
                    return generation.generate(plan,provider,lambda rid:phase('generation',rid),
                                               resume_id=resume,retry_feedback=fb)
                draft=self._attempt('generation','project_developer',generate,need=[goal,approved],stage='generation')
            checkpoint['draft_id']=draft
        for attempt in range(checkpoint.get('attempt',0),5):
            meta=store.run(draft)['data']['project_plan']
            phase('verification',draft,attempt)
            verified=generation.approve(draft,{'files_ref':meta['files_ref'],'files':store.read(meta['files_ref']),
                                               'review_mode':'automatic'})
            phase('verification',verified,attempt)
            result=Runtime(store,runner).execute(verified)
            # 验证在进程崩溃时被中断：引擎保守地标 needs_attention 且不重放。工具幂等，所以新建一次验证（最多两次），
            # 而不是把它当成“验证失败”去修复。A verification interrupted by a crash is re-run as a new run (at most twice).
            reverified=0
            while result['status']=='needs_attention' and reverified<2 and str(store.run(verified)['reason']).startswith('uncertain_tool_state'):
                reverified+=1
                verified=generation.reverify(verified)
                phase('verification',verified,attempt)
                result=Runtime(store,runner).execute(verified)
            if result['status']=='needs_attention':
                self.job.update(status='completed',result={'id':verified},
                                note='verification needs attention ('+str(store.run(verified)['reason'])+'); inspect the run before any repair')
                return
            if result['status']=='succeeded':
                self.job.update(status='completed',result={'id':verified})
                return
            if result['status']=='cancelled':
                self.job.update(status='cancelled',result={'id':verified},note='取消已生效；不会开始新的修复。')
                return
            # 上一次由模型产出的修复没能让验证通过：记入失败链，路由器据此决定是否升级。
            # The previous model-made fix did not pass verification: record it so the router can decide about escalation.
            pending=self.job.get('pending_fix')
            if pending:
                self._fail('fix',type('D',(),{'action':'use','candidate':pending['candidate'],'level':pending['level']})())
                self.job['pending_fix']=None
            if attempt==4:
                self.job.update(status='completed',result={'id':verified},
                                        note='automatic repair limit reached; inspect failed checks')
                return
            # 失败中含测试包循环导入时，必须新建测试修订；普通修复不能改变冻结测试。
            # A test import cycle needs an explicit test revision; implementation-only repair cannot fix frozen tests.
            checks=[(store.read(t['request_ref'])['operation'],store.read(t['result_ref']))
                    for t in store.tools(verified) if t['result_ref']]
            # 连续三次相同断言提示检查规格/测试，不再诱使 Developer 迎合错误测试。
            # Three identical assertion failures require spec/test review instead of another implementation repair.
            assertion=repeated_assertion_signature(checks)
            if self.checkpoint.record_assertion(verified,assertion):
                self.job.update(status='completed',result={'id':verified},
                    note='same test assertion failed three times; inspect specification and test expectation before further repair')
                return
            outputs=[result for _,result in checks]
            evidence='\n'.join(str(o.get('stdout',''))+'\n'+str(o.get('stderr','')) for o in outputs)
            # 修复阶段的输入规模：当前整套代码 + 失败证据（估算，用于上下文准入）。
            # Input size of a fix call: the current bundle plus failure evidence (an estimate for context admission).
            bundle=store.read(store.run(verified)['data']['project_bundle']['approval_ref'])['files']
            need=[bundle,evidence[:20000]]
            used={}
            try:
                if format_only(checks):
                    phase('test_format',verified,attempt+1)
                    draft=generation.format_test_files(verified,
                        lambda rid:phase('test_format',rid,attempt+1))
                elif test_revision_needed(evidence,checks):
                    phase('test_revision',verified,attempt+1)
                    draft=self._attempt('fix','project_test_revision',lambda provider,fb:self._track(used,provider,generation.revise_tests(verified,provider,
                        'Fix the recorded test-file errors, including any gofmt failure. Preserve behavioral assertions and requirements.'+(' '+fb if fb else ''),
                        lambda rid:phase('test_revision',rid,attempt+1))),need=need,stage='test_revision')
                elif assertion and self.job.get('repeated_assertions',0)==2 and not self.job.get('arbitrated'):
                    # 实现修复后同一断言仍失败：测试期望可能与已批准规格矛盾。让 Tester 以规格为准仲裁一次，
                    # 此后仍相同则由三次规则停止并交给人。 Same assertion survives an implementation repair: let the Tester
                    # arbitrate once against the approved spec; if it still repeats, the three-strike rule hands over to a human.
                    self.job.update(arbitrated=True)
                    phase('test_revision',verified,attempt+1)
                    draft=self._attempt('fix','project_test_revision',lambda provider,fb:self._track(used,provider,generation.revise_tests(verified,provider,
                        'The same assertions kept failing after an implementation repair. Compare EACH failing assertion with the approved '
                        'spec.acceptance and the goal text. If an expected value contradicts them, correct that expectation to follow the '
                        'requirement; do not delete cases or weaken checks. If the test already follows the requirement, change nothing '
                        'about that assertion.'+(' '+fb if fb else ''),
                        lambda rid:phase('test_revision',rid,attempt+1))),need=need,stage='arbitration')
                else:
                    phase('repair',verified,attempt+1)
                    draft=self._attempt('fix','project_repair',lambda provider,fb:self._track(used,provider,generation.repair(verified,provider,fb or '',
                        lambda rid:phase('repair',rid,attempt+1),use_intelligence=True)),need=need,stage='repair')
            except RoutingStop as stop:
                self._stopped(stop,verified)
                return
            # 记住是哪个模型做的这次修复；下一次验证失败时据此计入失败链。 Remember who made this fix.
            if used:self.job['pending_fix']=dict(used)
            checkpoint.update(draft_id=draft,attempt=attempt+1)

    def _track(self, used, provider, value):
        """记录最近一次成功产出修复草稿的候选。 Remember which candidate produced the latest fix draft."""
        entry=self.router.entry_of(provider)
        if entry:used.update(candidate=entry['id'],level=entry['level'])
        return value
