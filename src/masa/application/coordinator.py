"""Automatic project orchestration independent of HTTP and worker lifecycle."""
from masa.application.planning import ProjectPlanning
from masa.application.generation import ProjectGeneration
from masa.application.workflow import WorkflowCheckpoint
from masa.application.check_policy import failure_signature, format_only, test_revision_needed, repeated_assertion_signature
from masa.application.router import Router, RoutingStop
from masa.application import attempts, ownership
from masa.application.flow import FlowEngine
from masa.application.workflows import FIX_V1
from masa.application.triage import assess
from masa.runtime.engine import Runtime
from masa.runtime.roles import RoleRuntime
from masa.domain.models import ContextOverflow, MasaError, TransportFailure
import time


class NoChange(MasaError):
    """这一次修复没有改动任何文件（已记入失败链）；由工作流决定下一步（诊断 / 换更强的模型重试 / 停止）。
    This fix changed nothing (already recorded as a failure); the workflow decides what next."""


class NoProgress(MasaError):
    """修复/测试修订原样返回了文件（没有任何改动），且已无更强的模型可换。重新验证同样的内容没有意义。
    The fix returned the files unchanged and no stronger model is left; re-verifying identical content is pointless."""


# 自动修复的基础轮数，以及“持续有进展”时最多额外多给几轮。
# Base number of automatic repair rounds, and how many extra rounds steady progress can earn.
BASE_ROUNDS = 4
MAX_PROGRESS_BONUS = 4
# 补丁连续这么多轮没有改善（且最强模型试过）就整体重写；每个任务最多重写几次。
# Rewrite after this many consecutive stalled patch rounds (strongest model already tried); at most this many rewrites per task.
REWRITE_AFTER_STALLS = 2
MAX_REWRITES = 1

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
        # 切换到另一个模型（或云端）之前，先释放不再使用的本地模型：不能有空挂的模型。
        # Before switching to another model (or the cloud), release local models no longer in use: no idle models.
        released=self.router.release(keep=provider)
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

    # ───────────── 主流程 / main flow ─────────────
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
        if self._ladder and not self.job.get('budget_emitted'):
            self._emit('task_budget',self.router.describe())
        planning=ProjectPlanning(store,runner)
        generation=ProjectGeneration(store,runner)
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
            analysis=ownership.analyse(checks)
            self._close_attempt(analysis)
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
            assertion=repeated_assertion_signature(checks)
            if self.checkpoint.record_assertion(verified,assertion):
                self.job.update(status='completed',result={'id':verified},
                    note='same test assertion failed three times; inspect specification and test expectation before further repair')
                return
            # 确定会失败的运行：同一失败签名在多轮后仍不变，并且更强的模型也已经试过，就停下交给人，不再继续花钱。
            # A doomed run: the same failure signature survives several rounds AND the strongest model was already tried.
            repeats=self.checkpoint.record_signature(verified,failure_signature(checks))
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

    # ───────────── 修复子图（声明式工作流）/ the repair subgraph (declarative workflow) ─────────────
    def _trace_step(self, step, verified):
        """把工作流走过的每一步写进事件，报告里可见。 Persist every workflow step as an event for the report."""
        self.store.event(verified,'workflow_node',{'workflow':f"{FIX_V1['id']}-v{FIX_V1['version']}",'node':step.node,'action':step.action,'to':step.to,'why':step.why})

    def _run_fix_flow(self, verified, checks, attempt, generation, assertion, repeats, analysis):
        """用 fix-v1 决定并执行“验证失败之后做什么”。返回 {end, outcome, facts, trace}。
        Decide and run what happens after a failed verification, using the fix-v1 workflow."""
        store=self.store
        phase=self._phase
        outputs=[result for _,result in checks]
        evidence='\n'.join(str(o.get('stdout',''))+'\n'+str(o.get('stderr','')) for o in outputs)
        # 修复阶段的输入规模：当前整套代码 + 失败证据（估算，用于上下文准入）。
        # Input size of a fix call: the current bundle plus failure evidence (an estimate for context admission).
        bundle=store.read(store.run(verified)['data']['project_bundle']['approval_ref'])['files']
        need=[bundle,evidence[:20000]]
        facts={'verified':verified,'draft':None,'halted':False,'noop':False,'noop_count':0,'diagnosed':False,'diagnosis':None,'analysis':analysis}

        def classify(ctx):
            primary=analysis['primary']
            if primary=='ambiguous' and test_revision_needed(evidence,checks):
                primary='test'  # 旧规则仍作为兜底 / the legacy rule remains as a fallback
            history=len(self._history('fix'))
            noop=int(self.job.get('noop_streak',0))
            diagnoses=int(self.job.get('diagnoses',0))
            # 只有断言失败（没有编译/准备错误）时，先让 Diagnoser 核对期望值：测试期望写错是这类失败最常见的原因，越早判越省轮数。
            # Assertion-only failures: let the Diagnoser check the expectations first; a wrong expectation is the commonest cause and the earlier it is found the fewer rounds are wasted.
            only_assertions=bool(analysis['items']) and all(i['kind']=='assertion' for i in analysis['items'])
            wants=(repeats>=2 or (primary=='ambiguous' and history>=2) or (only_assertions and not self.job.get('diagnoses')))
            can=bool(self._ladder and self.router.policy.get('diagnose') and diagnoses<int(self.router.policy.get('diagnose_max',0)))
            return {'primary':primary,'format_only':format_only(checks),'can_diagnose':can,
                    'arbitrate_due':bool(assertion) and self.job.get('repeated_assertions',0)==2 and not self.job.get('arbitrated'),
                    'needs_diagnosis':bool(can and wants),
                    'rewrite_due':bool(self._ladder and primary in ('implementation','ambiguous') and int(self.job.get('stall',0))>=REWRITE_AFTER_STALLS
                                       and int(self.job.get('rewrites',0))<MAX_REWRITES and self._strongest_tried())}

        def diagnose(ctx):
            try:
                diagnosis=self._diagnose(verified,analysis,checks,bundle)
            except RoutingStop:
                diagnosis=None  # 预算/等级不允许诊断：直接修复，不因此停下 / cannot afford it: just fix
            except Exception as exc:  # 诊断只是建议：任何失败都不能挡住修复，但要留下记录 / advisory: no failure may block the fix, but record it
                diagnosis=None
                self._emit('diagnosis_failed',{'error':str(exc)[:200]},verified,always=True)
            self.job['diagnoses']=int(self.job.get('diagnoses',0))+1
            return {'diagnosed':True,'diagnosis':diagnosis,'can_diagnose':False}

        def instructions(owner):
            parts=[ownership.hint(analysis,owner),self._attempt_summary()]
            diagnosis=ctx_diagnosis()
            if diagnosis:
                key='implementation_instructions' if owner=='implementation' else 'test_instructions'
                if diagnosis.get(key):parts.append('【Diagnoser 的诊断】'+diagnosis['rationale']+'\n【Diagnoser 的指导】'+diagnosis[key])
            return '\n'.join(p for p in parts if p)

        def ctx_diagnosis():
            return facts.get('diagnosis')

        def run_fix(chain, role, stage, make):
            used={}
            try:
                draft=self._attempt(chain,role,lambda provider,fb:self._track(used,provider,make(provider,fb)),
                                    need=need,stage=stage,reject_noop=True)
            except NoChange:
                self._log_fix(stage,None,{})
                return {'noop':True,'halted':False,'noop_count':int(facts.get('noop_count',0))+1}
            except NoProgress as exc:
                return {'halted':True,'halt_kind':'noop','halt_reason':str(exc)}
            if used:self.job['pending_fix']={**used,'chain':chain}
            self._log_fix(stage,draft,used)
            return {'draft':draft,'noop':False,'halted':False,'halt_kind':None}

        def format_files(ctx):
            phase('test_format',verified,attempt+1)
            return {'draft':generation.format_test_files(verified,lambda rid:phase('test_format',rid,attempt+1))}

        def fix_implementation(ctx):
            phase('repair',verified,attempt+1)
            return run_fix('fix:implementation','project_repair','repair',lambda provider,fb:generation.repair(
                verified,provider,'\n'.join(x for x in (instructions('implementation'),fb) if x),
                lambda rid:phase('repair',rid,attempt+1),use_intelligence=True))

        def revise_tests(ctx):
            phase('test_revision',verified,attempt+1)
            base='Fix the recorded test-file errors, including any gofmt failure. Preserve behavioral assertions and requirements.'
            flipped=not facts.get('flipped') and (facts.get('halt_kind')=='noop' or int(facts.get('noop_count',0))>=3)
            if flipped:
                # 最强模型多次审视后认为实现无需改动：逐条重新手算期望值（字节数要把换行和多字节字符算进去），只改算错的期望。
                # The strongest model reviewed the implementation repeatedly and changed nothing: recompute EVERY expected value by hand and change only the wrong ones.
                base=('The implementation was reviewed several times by the strongest model and judged correct, yet these assertions still fail. '
                      'Suspect the test expectations: recompute each failing expected value by hand from the input and the requirement '
                      '(count newline characters and multi-byte characters in byte counts), fix only expectations that are wrong, '
                      'and never delete cases or weaken checks.')
            out=run_fix('fix:test','project_test_revision','test_revision',lambda provider,fb:generation.revise_tests(
                verified,provider,'\n'.join(x for x in (base,instructions('test'),fb) if x),
                lambda rid:phase('test_revision',rid,attempt+1)))
            return {**out,'flipped':True} if flipped else out

        def rewrite_implementation(ctx):
            # 整体重写：连续几轮补丁都没有改善，说明结构本身可能是错的。最强模型丢弃现有结构，对着冻结的测试重新写实现；
            # 给全部实现文件（不做相关性裁剪），因为重写可能要改动任何一个。
            # Whole rewrite: several patch rounds without improvement suggest the structure itself is wrong. The strongest model discards it and
            # writes the implementation again against the frozen tests, with every implementation file supplied (a rewrite may touch any of them).
            self.job.update(rewrites=int(self.job.get('rewrites',0))+1,stall=0)
            self._emit('rewrite_started',{'after_rounds':len(self.job.get('fix_log',[])),'unresolved':self.job.get('unresolved_now')},verified,always=True)
            phase('repair',verified,attempt+1)
            base=('REWRITE. Several patch rounds in a row did not reduce the failures, so the current structure is probably wrong. Do NOT patch it: '
                  'discard its design and write the implementation files again from scratch so that the frozen tests pass. Keep exactly the identifiers '
                  'and signatures the tests use. Choose the simplest design that satisfies the approved acceptance criteria (for example read all input at once '
                  'instead of keeping line-by-line state) and read the earlier rounds below to avoid repeating what failed.')
            out=run_fix('fix:implementation','project_repair','rewrite',lambda provider,fb:generation.repair(
                verified,provider,chr(10).join(x for x in (base,instructions('implementation'),fb) if x),
                lambda rid:phase('repair',rid,attempt+1),use_intelligence=False))
            return {**out,'rewritten':True}

        def arbitrate_tests(ctx):
            # 实现修复后同一断言仍失败：测试期望可能与已批准规格矛盾。让 Tester 以规格为准仲裁一次，
            # 此后仍相同则由三次规则停止并交给人。 Same assertion survives an implementation repair: let the Tester
            # arbitrate once against the approved spec; if it still repeats, the three-strike rule hands over to a human.
            self.job.update(arbitrated=True)
            phase('test_revision',verified,attempt+1)
            base=('The same assertions kept failing after an implementation repair. Compare EACH failing assertion with the approved '
                  'spec.acceptance and the goal text. If an expected value contradicts them, correct that expectation to follow the '
                  'requirement; do not delete cases or weaken checks. If the test already follows the requirement, change nothing '
                  'about that assertion.')
            return run_fix('fix:ambiguous','project_test_revision','arbitration',lambda provider,fb:generation.revise_tests(
                verified,provider,'\n'.join(x for x in (base,fb) if x),lambda rid:phase('test_revision',rid,attempt+1)))

        actions={'classify':classify,'diagnose':diagnose,'format_files':format_files,'fix_implementation':fix_implementation,
                 'revise_tests':revise_tests,'arbitrate_tests':arbitrate_tests,'rewrite_implementation':rewrite_implementation}
        engine=FlowEngine(FIX_V1,actions,on_step=lambda step:self._trace_step(step,verified))
        return engine.run(facts)

    def _log_fix(self, stage, draft, used):
        """记录每次修复产出了什么（谁做的、改了哪些文件），Diagnoser 据此知道“已经试过什么”。
        Log what each fix did (who, which files) so the Diagnoser knows what was already tried."""
        changed=(self.store.run(draft)['data'].get('project_plan',{}).get('changed_files') or []) if draft else []
        log=list(self.job.get('fix_log',[]))[-5:]
        model=next((c.model for c in self.router.candidates if c.id==used.get('candidate')),None) if used else None
        log.append({'stage':stage,'changed':changed[:6],'level':used.get('level'),'candidate':used.get('candidate'),'model':model,
                    'before':self.job.get('unresolved_now')})
        self.job['fix_log']=log

    def _close_attempt(self, analysis):
        """新一轮验证出来之后，把上一次修复的结果补进日志（见 attempts.close_attempt）。 Record the previous fix's outcome (see attempts.close_attempt)."""
        attempts.close_attempt(self.job,analysis)

    def _attempt_summary(self):
        """前几轮“试过什么、结果如何”的摘要（见 attempts.summary）。 Summary of earlier rounds (see attempts.summary)."""
        return attempts.summary(self.job)

    def _extend_deadline(self, run_id):
        """用户明确要求继续时，已过期的验证 run 需要延长期限才能再做角色调用。 Extend an expired run's deadline for an explicit continuation."""
        run=self.store.run(run_id)
        if time.time()>=run['data']['deadline_at']-60:
            self.store.save_metadata(run_id,'deadline_at',time.time()+3600,'deadline_extended',payload={'reason':'continued by the user'})

    def _diagnose(self, verified, analysis, checks, bundle):
        """Diagnoser：只读、用最高等级、判断“谁的问题”并给出具体指导。结果持久化在角色账本里。
        Read-only Diagnoser at the strongest level: decides whose problem it is and gives concrete instructions."""
        store=self.store
        run=store.run(verified)
        approved=store.read(store.read(run['data']['project_bundle']['approval_ref'])['spec_approval_ref'])
        files={}
        budget=20000
        wanted=[i['path'] for i in analysis['items'] if i['path']]
        for path in dict.fromkeys(wanted):
            # 断言条目里只有文件名（如 cli_test.go），要还原成 bundle 里的完整路径；之前没还原，测试源码从未送进 Diagnoser。
            # Assertion items carry only a file name (cli_test.go): resolve it to the full bundle path (previously the test source never reached the Diagnoser).
            keys=[k for k in bundle if k==path or k.endswith('/'+path)]
            full=keys[0] if len(keys)==1 else None
            if full and full not in files and budget>0:
                files[full]=bundle[full][:6000];budget-=len(files[full])
        context={'goal':run['data']['goal'],'acceptance':approved['spec']['acceptance'],'all_files':sorted(bundle),'files':files,
                 'ownership':ownership.describe(analysis),'failure_evidence':[
                     {'operation':op,'output':(str(r.get('stdout',''))+'\n'+str(r.get('stderr','')))[:3000]} for op,r in checks if r.get('exit_code')!=0][:3],
                 'history':list(self.job.get('fix_log',[])),'noop_streak':int(self.job.get('noop_streak',0))}
        provider,decision=self._pick('project_diagnoser','diagnose',need=context,anchor=verified,stage='diagnose')
        self._extend_deadline(verified)
        try:
            diagnosis=RoleRuntime(store).call(verified,provider,'project_diagnoser',context)
        except TransportFailure:
            if self._wait_for_server(provider,0):
                diagnosis=RoleRuntime(store).call(verified,provider,'project_diagnoser',context)
            else:raise
        from masa.domain.proposals import reconcile_diagnosis, validate_diagnosis
        diagnosis=validate_diagnosis(diagnosis)
        # 结论必须服从 Diagnoser 自己逐条核对的结果（它被测试带偏过：见 reconcile_diagnosis）。
        # The verdict must obey the Diagnoser's own per-case checks (it has been anchored by the test before: see reconcile_diagnosis).
        verdict=diagnosis['owner']
        diagnosis,changed=reconcile_diagnosis(diagnosis)
        self._emit('diagnosis',{'owner':diagnosis['owner'],'rationale':diagnosis['rationale'][:400],'by':(provider.profile or {}).get('model'),
                                'reconciled_from':verdict if changed else None,
                                'mismatches':[c['case'] for c in diagnosis['expectation_checks'] if not c['matches']][:6]},verified,always=True)
        return diagnosis

    def _seed_history_from_lineage(self, verified):
        """从版本链重建“修复链历史”：每个由模型产出、之后验证仍失败的修复，记为该等级的一次失败。
        Rebuild the fix-chain history from the version lineage: each model-made fix whose verification still failed counts as a failure."""
        if any(k.startswith('fix') for k in self.job.get('route_history',{})) or not self._ladder:
            return
        by_model={c.model:c for c in self.router.candidates}
        history=[]
        current=verified
        seen=set()
        runs={r['id']:r for r in self.store.all_runs()}
        while current in runs and current not in seen:
            seen.add(current)
            run=runs[current]
            parent=run['data'].get('parent_run_id')
            if run['data'].get('project_bundle') and parent in runs:
                draft=runs[parent]
                plan=draft['data'].get('project_plan') or {}
                if plan.get('repair_of') and not plan.get('format_only'):
                    asked=[e for e in self.store.events(parent) if e['type']=='model_requested']
                    model=(asked[-1]['payload'].get('route') or {}).get('model') if asked else None
                    chain='fix:test' if plan.get('revision_scope')=='tests' else 'fix:implementation'
                    if model in by_model:
                        history.append((chain,{'candidate':by_model[model].id,'level':by_model[model].level}))
            current=parent
        history.reverse()
        if history:
            all_history=dict(self.job.get('route_history',{}))
            for chain,entry in history:
                all_history[chain]=list(all_history.get(chain,[]))+[entry]
            self.job['route_history']=all_history
            self._emit('history_seeded',{'fix_failures':{c:[e['level'] for ch,e in history if ch==c] for c in {c for c,_ in history}}},verified,always=True)

    def _prepare(self, planning, generation, goal):
        """规划（含澄清、预检）→ 批准 → 生成，返回待验证的草稿 id；等待用户、被拦下时返回 None。
        Plan (clarification, triage) → approve → generate; returns the draft id, or None when waiting for the user or blocked."""
        store=self.store
        phase=self._phase
        checkpoint=self.job
        plan=checkpoint.get('plan_id')
        # 可行性预检：确定性规则零成本；确定会失败的需求直接拦下（除非用户明确要求继续）。
        # Feasibility triage: deterministic rules are free; a requirement certain to fail is stopped unless the user forces it.
        triage=None
        triage_provider=None
        if not plan and not self.resuming:
            triage=assess(goal)
            if triage['verdict']=='infeasible' and not self.job['request'].get('force'):
                blocked=planning.record_triage_block(goal,triage)
                self.job.update(run_id=blocked,status='completed',result={'id':blocked},
                                note='triage: '+'; '.join(f['reason'] for f in triage['findings'] if f['level']=='infeasible'))
                return None
            if self._ladder and self.router.policy.get('triage_model'):
                triage_provider=self.router.local_provider('project_triage')
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
        transport_used=0
        provider=None
        for index in range((3+int(self.router.policy.get('transport_retries',0))) if not plan else 0):
            retry=index-transport_used  # 真正的规划重试次数；传输等待不计入 / real planning retries; transport waits do not count
            try:
                recover_id=checkpoint.get('run_id') if self.resuming and checkpoint['phase']=='planning' and retry==0 else None
                if recover_id:
                    provider=self._provider_of(recover_id);decision=None
                else:
                    provider,decision=self._pick('project_tester' if reuse else 'project_planner','planning',
                                                 need=goal,stage='planning')
                plan=planning.generate(provider,goal,lambda rid:phase('planning',rid),reuse,resume_id=recover_id,
                                       retry_of=retry_of,retry_feedback=planner_feedback,
                                       triage=triage if index==0 else None,triage_provider=triage_provider if index==0 else None)
                if store.run(plan)['data']['project_plan'].get('status')=='waiting_for_input':
                    checkpoint.update(status='waiting_for_input',result={'id':plan})
                    return None
                checkpoint['plan_id']=plan
                break
            except RoutingStop:
                raise
            except TransportFailure:
                if provider is not None and self._wait_for_server(provider,transport_used):
                    transport_used+=1
                    retry_of=self.job['run_id'] if not reuse else retry_of  # 新 run，沿用已回答的澄清 / new run keeping answered clarifications
                    phase('planning_retry',self.job['run_id'])
                    continue
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
                    phase('planning_retry',failed_id)
                    continue
                if retry==2:
                    raise
                # 仅复用已校验 Planner 结果，Tester 最多额外调用两次。
                # Reuse validated Planner output and retry Tester at most twice.
                self._fail('planning',decision)
                reuse=failed_id
                phase('planning_retry',failed_id)
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
                    # 测试由更强的模型先写（Tester 等级），实现由低等级模型对着冻结的测试写。固定模式与全本地时保持原行为。
                    # A stronger model writes the tests first (Tester level); the weaker model implements against the frozen tests.
                    test_provider=None
                    if self._ladder and (getattr(provider,'config',None) or {}).get('model_type')=='local':
                        candidate,_=self._pick('project_tester','gen_tests',need=[goal,approved],stage='generation')
                        if candidate is not provider and (getattr(candidate,'config',None) or {}).get('model_type')!='local':
                            test_provider=candidate
                    return generation.generate(plan,provider,lambda rid:phase('generation',rid),
                                               resume_id=resume,retry_feedback=fb,test_provider=test_provider)
                # 逐文件生成时 previous_files 会随文件数增长：按每个已生成文件约 4000 字符估计（启发式，写在文档里）。
                # previous_files grows with each file call: roughly 4000 chars per already generated file (a documented heuristic).
                growth='x'*4000*max(0,len(approved['spec']['files'])-1)
                draft=self._attempt('generation','project_developer',generate,need=[goal,approved,growth],stage='generation')
            checkpoint['draft_id']=draft
        return draft

    def _track(self, used, provider, value):
        """记录最近一次成功产出修复草稿的候选。 Remember which candidate produced the latest fix draft."""
        entry=self.router.entry_of(provider)
        if entry:used.update(candidate=entry['id'],level=entry['level'])
        return value
