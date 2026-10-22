"""规划（含澄清、预检）→ 批准 → 生成，直到得到待验证的草稿。 Plan (clarification, triage), approve and generate until there is a draft to verify."""
from masa.application.orchestration.router import RoutingStop
from masa.application.checks.triage import assess
from masa.domain.models import MasaError, TransportFailure
import time


class PrepareMixin:
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
        # 用例审计（默认关闭）：只在天梯模式且有云端候选时才有审计者。 Case audit (off by default): only in ladder mode with a cloud candidate.
        audit_provider=self.router.cheapest('case_deriver') if (self._ladder and self.router.policy.get('test_audit')) else None
        if not plan and not self.resuming:
            triage=assess(goal,self.pack)
            if triage['verdict']=='infeasible' and not self.job['request'].get('force'):
                blocked=planning.record_triage_block(goal,triage,self.language)
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
                                       triage=triage if index==0 else None,triage_provider=triage_provider if index==0 else None,
                                       audit_provider=audit_provider,
                                       language=self.language)
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
                # 仅复用已校验 Planner 结果，Tester 最多额外调用两次；重试时把被拒绝的原因和契约要求告诉它。
                # Reuse validated Planner output and retry Tester at most twice, telling it why the last answer was rejected and what the contract requires.
                self._fail('planning',decision)
                rejected=self._retryable(failed_id) or str(failed.get('error') or 'response rejected by validation')
                planner_feedback=(rejected+'. Contract reminder: return {"checks":[...]} with 1 to 3 checks; each check has EXACTLY the fields operation, purpose, '
                                  'acceptance_indices (a list of zero-based integers that are valid acceptance indices) and, only when concrete cases are given, cases; '
                                  'operation is '+self.pack.op_id('test')+' (required), '+' or '.join(self.pack.operations[k].id for k in ('static','format') if k in self.pack.operations)+', each at most once; every case has exactly name, input, expected, level '
                                  '(unit, integration or cli), where input is a string (may be empty) and name and expected are non-empty strings.')[:1000]
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
                        candidate,_=self._pick('project_tester','gen_tests',need=[goal,approved],stage='generation',release=False)
                        if candidate is not provider and (getattr(candidate,'config',None) or {}).get('model_type')!='local':
                            test_provider=candidate
                    # 云端整包生成被截断后，本任务的生成改为逐文件（记在 job 里，之后的重试不再回到整包）。
                    # After a cloud whole-bundle answer was cut off, generation for this task goes file by file (remembered in the job).
                    if fb and 'incomplete output' in str(fb) and not self.job.get('per_file'):
                        self.job['per_file']=True
                        self._emit('generation_per_file',{'reason':'cloud output was cut off'},always=True)
                    if getattr(generation,'workers',False):
                        # V2：重试时上一次尝试里已封包的包直接沿用，卡住的包带着写了一半的文件由更强的模型接着做 / V2: on a retry the previous attempt's sealed packages are reused and a stuck package continues from its half-written files with the stronger model
                        generation.seed_run=self.job.get('run_id') if fb else None
                    return generation.generate(plan,provider,lambda rid:phase('generation',rid),
                                               resume_id=resume,retry_feedback=fb,test_provider=test_provider,
                                               per_file=bool(self.job.get('per_file')))
                # 逐文件生成时 previous_files 会随文件数增长：按每个已生成文件约 4000 字符估计（启发式，写在文档里）。
                # previous_files grows with each file call: roughly 4000 chars per already generated file (a documented heuristic).
                growth='x'*4000*max(0,len(approved['spec']['files'])-1)
                draft=self._attempt('generation','project_developer',generate,need=generation.admission_need(goal,approved,growth),stage='generation')
            checkpoint['draft_id']=draft
        return draft
