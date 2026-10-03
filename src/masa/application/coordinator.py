"""Automatic project orchestration independent of HTTP and worker lifecycle."""
from masa.application.planning import ProjectPlanning
from masa.application.generation import ProjectGeneration
from masa.application.workflow import WorkflowCheckpoint
from masa.application.check_policy import format_only, test_revision_needed, repeated_assertion_signature
from masa.runtime.engine import Runtime
from masa.domain.models import MasaError
import time


class WorkflowCoordinator:
    def __init__(self, store, runner, provider, job, *, resuming=False):
        """共享持久任务与角色服务，不拥有 HTTP 或线程。 Share durable state without owning HTTP or threads."""
        self.store,self.runner,self.provider,self.job=store,runner,provider,job
        self.resuming=resuming
        self.checkpoint=WorkflowCheckpoint(job)

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

    def _attempt(self, call, retries=1):
        """调用 call(feedback)；校验被拒绝时最多带反馈重试 retries 次。 Retry a rejected stage with the rejection reason."""
        feedback = None
        for n in range(retries + 1):
            try:
                return call(feedback)
            except MasaError:
                reason = self._retryable(self.job.get('run_id')) if n < retries else None
                if not reason:
                    raise
                feedback = 'Your previous attempt was rejected: ' + reason

    def run(self):
        """从持久检查点推进流程，等待用户时释放执行权。 Advance checkpoints and yield when user input is required."""
        store,runner,provider=self.store,self.runner,self.provider
        goal=self.job['request']['goal']
        phase=self.checkpoint.phase
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
        for retry in range(3 if not plan else 0):
            try:
                recover_id=checkpoint.get('run_id') if self.resuming and checkpoint['phase']=='planning' and retry==0 else None
                plan=planning.generate(provider,goal,lambda rid:phase('planning',rid),reuse,resume_id=recover_id,
                                       retry_of=retry_of,retry_feedback=planner_feedback)
                if store.run(plan)['data']['project_plan'].get('status')=='waiting_for_input':
                    checkpoint.update(status='waiting_for_input',result={'id':plan})
                    return
                checkpoint['plan_id']=plan
                break
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
                    # Planner 的响应被契约拒绝：带原因重规划一次（新版本，旧失败保留）。
                    # A Planner response rejected by the contract gets one corrected re-plan (new version; the failure is kept).
                    reason=self._retryable(failed_id) if retry==0 else None
                    if not reason:raise
                    planner_feedback=reason;retry_of=failed_id
                    phase('planning_retry',failed_id,retry+1)
                    continue
                if retry==2:
                    raise
                # 仅复用已校验 Planner 结果，Tester 最多额外调用两次。
                # Reuse validated Planner output and retry Tester at most twice.
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
            draft=generation.resume_revision(revision,provider)
            checkpoint['draft_id']=draft
        if not draft:
            recover_id=checkpoint.get('run_id') if self.resuming and checkpoint['phase']=='generation' and checkpoint.get('run_id')!=plan else None
            phase('generation',recover_id or plan)
            saved=store.run(recover_id)['data'].get('project_plan',{}) if recover_id else {}
            if saved.get('status') in {'awaiting_review','approved'}:
                draft=recover_id
            else:
                draft=self._attempt(lambda fb:generation.generate(plan,provider,lambda rid:phase('generation',rid),
                                                                  resume_id=recover_id if fb is None else None,retry_feedback=fb))
            checkpoint['draft_id']=draft
        for attempt in range(checkpoint.get('attempt',0),5):
            meta=store.run(draft)['data']['project_plan']
            phase('verification',draft,attempt)
            verified=generation.approve(draft,{'files_ref':meta['files_ref'],'files':store.read(meta['files_ref']),
                                               'review_mode':'automatic'})
            phase('verification',verified,attempt)
            result=Runtime(store,runner).execute(verified)
            if result['status']=='succeeded':
                self.job.update(status='completed',result={'id':verified})
                return
            if result['status']=='cancelled':
                self.job.update(status='cancelled',result={'id':verified},note='取消已生效；不会开始新的修复。')
                return
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
            if format_only(checks):
                phase('test_format',verified,attempt+1)
                draft=generation.format_test_files(verified,
                    lambda rid:phase('test_format',rid,attempt+1))
            elif test_revision_needed(evidence,checks):
                phase('test_revision',verified,attempt+1)
                draft=self._attempt(lambda fb:generation.revise_tests(verified,provider,
                    'Fix the recorded test-file errors, including any gofmt failure. Preserve behavioral assertions and requirements.'+(' '+fb if fb else ''),
                    lambda rid:phase('test_revision',rid,attempt+1)))
            elif assertion and self.job.get('repeated_assertions',0)==2 and not self.job.get('arbitrated'):
                # 实现修复后同一断言仍失败：测试期望可能与已批准规格矛盾。让 Tester 以规格为准仲裁一次，
                # 此后仍相同则由三次规则停止并交给人。 Same assertion survives an implementation repair: let the Tester
                # arbitrate once against the approved spec; if it still repeats, the three-strike rule hands over to a human.
                self.job.update(arbitrated=True)
                phase('test_revision',verified,attempt+1)
                draft=self._attempt(lambda fb:generation.revise_tests(verified,provider,
                    'The same assertions kept failing after an implementation repair. Compare EACH failing assertion with the approved '
                    'spec.acceptance and the goal text. If an expected value contradicts them, correct that expectation to follow the '
                    'requirement; do not delete cases or weaken checks. If the test already follows the requirement, change nothing '
                    'about that assertion.'+(' '+fb if fb else ''),
                    lambda rid:phase('test_revision',rid,attempt+1)))
            else:
                phase('repair',verified,attempt+1)
                draft=self._attempt(lambda fb:generation.repair(verified,provider,fb or '',lambda rid:phase('repair',rid,attempt+1),use_intelligence=True))
            checkpoint.update(draft_id=draft,attempt=attempt+1)
