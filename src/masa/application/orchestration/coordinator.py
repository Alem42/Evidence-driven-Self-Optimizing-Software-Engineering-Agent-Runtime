"""Automatic project orchestration independent of HTTP and worker lifecycle."""
from masa.application.planning import ProjectPlanning
from masa.application.generation import ProjectGeneration
from masa.application.orchestration.workflow import WorkflowCheckpoint
from masa.application.checks.check_policy import failure_signature, repeated_assertion_signature
from masa.application.orchestration.router import Router, RoutingStop
from masa.application.checks import ownership
from masa.languages.registry import registry as languages
from masa.runtime.engine import Runtime
from masa.domain.models import ContextOverflow, MasaError, TransportFailure
from masa.application.orchestration.limits import NoChange, NoProgress, BASE_ROUNDS, MAX_PROGRESS_BONUS, REWRITE_AFTER_STALLS, MAX_REWRITES  # noqa: F401 (re-exported)
import time
from masa.application.orchestration.fixflow import FixFlowMixin
from masa.application.orchestration.prepare import PrepareMixin


class WorkflowCoordinator(FixFlowMixin, PrepareMixin):
    def __init__(self, store, runner, provider, job, *, resuming=False, router=None):
        """共享持久任务与角色服务，不拥有 HTTP 或线程。无 router 时保持原有“固定单模型”行为。
        Share durable state without owning HTTP or threads. Without a router the legacy fixed single-model behaviour applies."""
        self.store,self.runner,self.job=store,runner,job
        self.router=router or Router.fixed(store,provider)
        self.provider=provider
        self.resuming=resuming
        self.checkpoint=WorkflowCheckpoint(job)
        self._deferred=[]  # 还没有锚点 run 时先缓存的事件 / events waiting for an anchor run
        self.language=(job.get('request') or {}).get('language') or languages.default_id()  # 这个任务的语言（任务期间不变）/ the task's language, fixed for its lifetime
        self.pack=languages.for_run({'language':self.language})

    # ───────────── 事件与路由 / events and routing ─────────────
    @property
    def _ladder(self):
        return self.router.mode=='ladder'

    def _emit(self, kind, payload, anchor=None, always=False):
        """写入路由事件；任务的第一个 run 出现之前先缓存。只在 ladder 模式记录，固定模式不产生额外事件（always=True 除外）。
        Record routing events; buffered until the task's first run exists. Fixed mode adds none unless always=True."""
        if not self._ladder and not always:return
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

    def _pause_gate(self):
        """用户点了“暂停”：在节点边界（下一次模型调用之前）停住，直到“继续”或“停止”。不会打断正在进行的调用。
        The user pressed pause: hold at a node boundary (before the next model call) until resume or stop. A call in flight is never interrupted."""
        if not self.job.get('paused'):
            return
        self._emit('task_paused',{},always=True)
        while self.job.get('paused'):
            rid=self.job.get('run_id')
            try:
                if rid and self.store.run(rid)['cancel_requested']:
                    break  # 停止优先：交给后面的调用去报告取消 / stop wins: the next call reports the cancellation
            except Exception:
                break
            time.sleep(0.5)
        self._emit('task_resumed',{},always=True)

    def _phase(self, name, rid=None, attempt=None):
        """阶段检查点；顺带写出缓存的路由事件。 Checkpoint the phase and flush buffered routing events."""
        self._pause_gate()
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

    def _pick(self, role, chain, *, need=None, anchor=None, stage=None, release=True):
        """向路由器要下一次尝试的模型；stop 时抛 RoutingStop。 Ask the router for the next attempt; stop raises RoutingStop."""
        self._pause_gate()
        decision,provider,spend=self.router.decide(role,chain,self._history(chain),
                                                    anchor_run=anchor or self.job.get('run_id'),need=need)
        self._emit('route_decided',self.router.event_for(decision,role,chain,stage or role,spend),anchor)
        if decision.action=='stop':
            raise RoutingStop(decision.reason,decision.detail)
        # 切换到另一个模型（或云端）之前，先释放不再使用的本地模型：不能有空挂的模型。
        # Before switching to another model (or the cloud), release local models no longer in use: no idle models.
        # release=False：同一阶段里本地模型马上还要用（例如云端先写测试、本地再写实现），卸载只会让它再冷加载一次。 release=False: the local model is needed again within the same stage (cloud writes the tests, then the local model the implementation); unloading it only forces another cold load.
        released=self.router.release(keep=provider) if release else []
        if released:self._emit('models_released',{'models':released,'reason':'switching'},anchor,always=True)
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

    def _wait_for_server(self, provider, used):
        """本地服务暂时不可达（被杀、重启中）：等它恢复再重试，不计入失败链，也不触发升级。云端没有这个概念。
        A local server is temporarily unreachable: wait for it and retry, without counting a failure or escalating. Local only."""
        policy=self.router.policy
        if not self._ladder or used>=int(policy.get('transport_retries',0)):return False
        wait=getattr(provider,'wait_until_reachable',None)
        if wait is None or (getattr(provider,'config',None) or {}).get('model_type')!='local':return False
        self._emit('transport_retry',{'attempt':used+1,'max':int(policy['transport_retries']),'wait_seconds':int(policy.get('transport_wait_seconds',900))})
        return bool(wait(int(policy.get('transport_wait_seconds',900))))

    def _strongest_tried(self):
        """修复链里是否已经用过最高等级（固定模式没有更强的模型，视为已用过）。 Has the fix chain already used the top level?"""
        if not self._ladder:return True
        top=max(c.level for c in self.router.candidates)
        return any(h['level']==top for key in self.job.get('route_history',{}) if key.startswith('fix') for h in self._history(key))

    def _attempts_allowed(self, chain):
        """一个阶段内最多尝试几次（含升级后的尝试）。固定模式保持原来的“初次 + 重试一次”。
        Total attempts within one stage, including after escalation; fixed mode keeps 'first try + one retry'."""
        if not self._ladder:return 2
        policy=self.router.policy
        # 上限 6：每级 2 次 × 3 级，最高等级才有机会出场（之前上限 4，梯子在到达最高等级前就停了）。
        # Cap 6 = 2 attempts x 3 levels so the top level gets a turn (the old cap of 4 ended the ladder before it).
        return min(6,int(policy['attempts_per_level'].get(chain.split(':')[0],1))*(int(policy.get('max_escalations',0))+1))

    def _attempt(self, chain, role, call, *, need=None, stage=None, reject_noop=False):
        """call(provider, feedback)；响应被校验拒绝时带原因重试，重复失败由路由器决定是否升级。
        Retry a rejected stage with its reason; repeated failures let the router decide whether to escalate."""
        feedback=None
        n=0
        transport_used=0
        reachable_failures=0
        while n<self._attempts_allowed(chain):
            provider,decision=self._pick(role,chain,need=need,stage=stage)
            # 发请求之前就记下“这次由谁修复”：请求中途崩溃后，恢复时路由器仍知道这个候选已经试过。
            # Record WHO is attempting the fix BEFORE the request, so a crash mid-call still tells the router this candidate was tried.
            if chain.startswith('fix'):
                self.job['pending_fix']={'candidate':decision.candidate,'level':decision.level,'chain':chain}
            try:
                value=call(provider,feedback)
                if reject_noop and chain.startswith('fix') and not self.store.run(value)['data'].get('project_plan',{}).get('changed_files'):
                    # 模型原样返回了文件：重新验证只会得到同样的失败。当作被拒绝，换更强的模型（或带提示再试），而不是白白消耗一轮。
                    # The model returned the files unchanged: re-verifying would only repeat the failure. Treat it as rejected.
                    self.job['noop_streak']=int(self.job.get('noop_streak',0))+1
                    self._emit('noop_revision',{'stage':stage,'noop_streak':self.job['noop_streak']},always=True)
                    self.job['pending_fix']=None
                    self._fail(chain,decision)
                    raise NoChange('the fix changed nothing')
                if chain.startswith('fix'):self.job['noop_streak']=0
                return value
            except (RoutingStop,NoChange):
                raise  # 由工作流决定下一步 / the workflow decides what next
            except TransportFailure:
                if chain.startswith('fix'):self.job['pending_fix']=None  # 传输失败不是模型的错 / not the model's fault
                # 服务器明明可达（探测立刻成功）、同一个请求却连续两次没有响应：这不是“等服务器恢复”能解决的（典型：大模型冷加载超过时限）。
                # 记一次失败让路由器升级到下一个候选，而不是原地重复到用完 transport_retries。服务器真的不可达时仍按原来的方式等待。
                # The server is reachable (the probe succeeds at once) yet the same request got no response twice: waiting cannot fix that (typically a big model's cold load exceeding the limit).
                # Record a failure so the router escalates, instead of repeating in place until transport_retries runs out. A server that is really down is still waited for as before.
                probe=getattr(provider,'reachable',None)
                if callable(probe) and (getattr(provider,'config',None) or {}).get('model_type')=='local' and probe():
                    reachable_failures+=1
                    if reachable_failures>=2:
                        self._fail(chain,decision)
                        n+=1
                        reachable_failures=0
                        continue
                if self._wait_for_server(provider,transport_used):
                    transport_used+=1
                    continue
                raise
            except ContextOverflow as overflow:
                # 这个候选的窗口放不下输入：记一次失败，并用估算的真实输入量重新选择，路由器会跳过放不下的候选。
                # 固定模式没有别的候选可选，直接把明确的错误交给用户。
                # This candidate's window cannot hold the input: record it and re-route with the measured need so too-small
                # candidates are skipped. Fixed mode has no alternative, so the explicit error surfaces.
                if not self._ladder:
                    raise
                if chain.startswith('fix'):self.job['pending_fix']=None  # 这次失败已直接记入失败链，避免重复计数 / already recorded below
                self._fail(chain,decision)
                need=overflow.estimated_tokens
                n+=1
                continue
            except MasaError:
                reason=self._retryable(self.job.get('run_id')) if n<self._attempts_allowed(chain)-1 else None
                if not reason:
                    raise
                if chain.startswith('fix'):self.job['pending_fix']=None
                self._fail(chain,decision)
                feedback='Your previous attempt was rejected: '+reason
                n+=1
        if reject_noop and int(self.job.get('noop_streak',0))>0:
            raise NoProgress('the fix returned the files unchanged on every attempt, including the strongest available model')

    def _stopped(self, stop, run_id):
        """路由决定停止：保留证据，任务以说明结束，而不是报错。 A routing stop ends the task with a note and keeps all evidence."""
        self._emit('task_stopped',{'reason':stop.reason,'detail':stop.detail},run_id)
        self.job.update(status='completed',result={'id':run_id},note=str(stop))

    def run(self):
        """从持久检查点推进流程。无论怎样结束（完成、失败、等待人、被取消），都释放本地模型：不能有空挂的模型。
        Advance the workflow. However it ends, release local models: no idle model may stay in VRAM."""
        try:
            self._run()
        finally:
            released=self.router.release()
            if released and self._ladder:
                try:self._emit('models_released',{'models':released},always=True)
                except Exception:pass

    def _run(self):
        """从持久检查点推进流程，等待用户时释放执行权。 Advance checkpoints and yield when user input is required."""
        store,runner=self.store,self.runner
        goal=self.job['request']['goal']
        phase=self._phase
        origin=self.job['request'].get('continue_from')
        if origin:  # 从某个失败的验证继续：沿用那个运行的语言 / continuing from a failed verification: keep that run's language
            self.pack=languages.for_run(store.run(origin)['data']);self.language=self.pack.id
        if self._ladder and not self.job.get('budget_emitted'):
            self._emit('task_budget',self.router.describe())
        planning=ProjectPlanning(store,runner)
        if self.job['request'].get('runtime')=='v2repair':  # 实验：生成沿用 V1，验证失败后的实现修复由 V2 工人只重开出问题的包 / experimental: V1 generation, V2 workers reopen only the failing package when a repair is needed
            from masa.application.v2.workers import V2RepairOnly
            generation=V2RepairOnly(store,runner)
        elif self.job['request'].get('runtime')=='v2':  # 实验：按包推进的工人循环（与 V1 并存，默认关闭）/ experimental per-package worker loops (coexists with V1, off by default)
            from masa.application.v2.workers import V2Generation
            generation=V2Generation(store,runner)
        else:
            generation=ProjectGeneration(store,runner)
        generation.context_budget=bool(self._ladder and self.router.policy.get('context_budget'))
        generation.memory_strategy=(self.router.policy.get('memory_strategy') or 'window') if self._ladder else 'window'  # 包工人的记忆策略 / the package worker's memory strategy
        generation.pause_gate=self._pause_gate  # V2 工人的每一步之前也检查暂停 / V2 workers check for a pause before every step
        if getattr(generation,'workers',False) and self._ladder and self.router.policy.get('assertion_referee'):
            generation.referee_provider=self.router.cheapest('assertion_referee')  # V2 的工人在同一个断言一再失败时请裁判 / V2 workers ask the referee when the same assertion keeps failing
        checkpoint=self.job
        # 从某个失败的验证处继续自动修复（用户在自动流程停下之后点“继续自动修复”）：跳过规划与生成，
        # 失败链历史从版本链重建，路由器因此知道之前哪些模型已经试过。
        # Continue the automatic repair from a failed verification: skip planning and generation; the router learns which models already tried.
        continue_from=self.job['request'].get('continue_from')
        if continue_from:
            self._seed_history_from_lineage(continue_from)
            draft=None
        else:
            draft=self._prepare(planning,generation,goal)
            if draft is None:
                return
        resume_failed=continue_from
        for attempt in range(checkpoint.get('attempt',0),BASE_ROUNDS+MAX_PROGRESS_BONUS+1):
            if resume_failed:
                verified=resume_failed
                resume_failed=None
                result={'status':'failed'}
                phase('verification',verified,attempt)
            else:
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
            checks=[(store.read(t['request_ref'])['operation'],store.read(t['result_ref']))
                    for t in store.tools(verified) if t['result_ref']]
            analysis=ownership.analyse(checks,self.pack)
            self._close_attempt(analysis)
            # 测试修订之后同一批断言原样还在失败：说明修订没有真正改到点子上（策略 assertion_referee 时给出明确提示）。
            # The same assertions still fail unchanged after a test revision: the revision did not hit the point (a clear hint when policy assertion_referee is on).
            assertions=sorted(i['message'] for i in analysis['items'] if i['kind']==ownership.ASSERTION)
            if self._ladder and self.router.policy.get('assertion_referee') and assertions and assertions==self.job.get('last_assertions') \
                    and str((self.job.get('pending_fix') or {}).get('chain','')).endswith('test'):
                self.job['revision_ineffective']=assertions[:3]
            else:
                self.job.pop('revision_ineffective',None)
            self.job['last_assertions']=assertions
            # 上一次由模型产出的修复：只有“它负责的那一类问题还在”才算失败。本地模型修好了实现、剩下的是测试的问题，
            # 这不是它的失败，也不该因此升级。A model-made fix fails only if ITS class of problem is still present:
            # a local model that fixed the implementation must not be escalated because a test defect remains.
            pending=self.job.get('pending_fix')
            if pending:
                owner=str(pending.get('chain') or 'fix').split(':')[-1]
                # 本类“已解决”＝剩下的失败全部属于另一类。没有可解析的诊断、只有断言失败等情况一律算没解决。
                # "Resolved" means everything that remains belongs to the OTHER class; unparsed or assertion-only failures count as unresolved.
                resolved={'implementation':analysis['test_blocking'] and not analysis['implementation_blocking'] and analysis['assertion_count']==0,
                          'test':analysis['implementation_blocking'] and not analysis['test_blocking']}.get(owner,False)
                still=not resolved
                if still:
                    self._fail(pending.get('chain') or 'fix',type('D',(),{'action':'use','candidate':pending['candidate'],'level':pending['level']})())
                self.job['pending_fix']=None
            # 轮数上限随“真实进展”延长：未解决条目数每创新低一次，多给一轮（最多再加 MAX_PROGRESS_BONUS 轮）。
            # 没有进展就按基础轮数停下；正在收敛的任务不该被固定上限截断。
            # The round limit stretches with real progress: each new low in unresolved items earns one more round (capped); no progress stops at the base limit.
            score=len(analysis['items'])
            best=self.job.get('best_score')
            if best is None or score<best:
                self.job['best_score']=score
                if best is not None:self.job['bonus']=min(MAX_PROGRESS_BONUS,int(self.job.get('bonus',0))+1)
                if best is not None:self._emit('rounds_extended',{'unresolved':score,'was':best,'bonus':self.job['bonus']},verified,always=True)
            if attempt>=BASE_ROUNDS+int(self.job.get('bonus',0)):
                self.job.update(status='completed',result={'id':verified},
                                        note='automatic repair limit reached; inspect failed checks')
                return
            # 连续三次相同断言提示检查规格/测试，不再诱使 Developer 迎合错误测试。
            # Three identical assertion failures require spec/test review instead of another implementation repair.
            assertion=repeated_assertion_signature(checks,self.pack)
            if self.checkpoint.record_assertion(verified,assertion):
                self.job.update(status='completed',result={'id':verified},
                    note='same test assertion failed three times; inspect specification and test expectation before further repair')
                return
            # 确定会失败的运行：同一失败签名在多轮后仍不变，并且更强的模型也已经试过，就停下交给人，不再继续花钱。
            # A doomed run: the same failure signature survives several rounds AND the strongest model was already tried.
            repeats=self.checkpoint.record_signature(verified,failure_signature(checks,self.pack))
            if repeats>=int(self.router.policy.get('stuck_after',99)) and self._strongest_tried():
                self._emit('task_stopped',{'reason':'stuck','detail':f'同一失败签名连续 {repeats} 次未变化'},verified,always=True)
                self.job.update(status='completed',result={'id':verified},
                    note=f'stuck: the same failure signature repeated {repeats} times, including attempts by the strongest model; a human needs to look (specification, tests or requirement)')
                return
            try:
                outcome=self._run_fix_flow(verified,checks,attempt,generation,assertion,repeats,analysis)
            except RoutingStop as stop:
                self._stopped(stop,verified)
                return
            if outcome['outcome']=='halt':
                reason=outcome['facts'].get('halt_reason') or 'the repair workflow halted'
                self._emit('task_stopped',{'reason':'halted','detail':reason},verified,always=True)
                self.job.update(status='completed',result={'id':verified},note='halted: '+reason)
                return
            draft=outcome['facts']['draft']
            checkpoint.update(draft_id=draft,attempt=attempt+1)
