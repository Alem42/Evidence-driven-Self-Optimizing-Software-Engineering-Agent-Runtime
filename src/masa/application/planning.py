from masa.domain.proposals import text, validate_spec, validate_checks
"""项目规划契约与角色编排。 Project planning contracts and role orchestration."""

import time
import uuid
from masa.domain.models import Budget, MasaError, canonical
from masa.runtime.engine import Runtime
from masa.runtime.graph import harness_policy
from masa.runtime.roles import RoleRuntime
from masa.domain.clarification import validate_question, validate_answers
from masa.application.test_review import review_test_plan








class ProjectPlanning:
    def __init__(self, store, executor):
        """复用运行记录和 artifact，不引入新调度器。 Reuse runs and artifacts without a new scheduler."""
        self.store, self.executor = store, executor

    def update(self, rid, plan, status, event):
        """原子发布规划状态和引用。 Publish plan state and references atomically."""
        with self.store.transaction():
            reason=plan.get('error') or ('code proposal: review before verification' if plan.get('kind')=='code' else 'project specification: review before generation')
            # 异步取消必须压过迟到的模型结果或异常处理，不得回到待审状态。
            # Concurrent cancellation wins over late model results and failure handlers.
            run=self.store.run(rid)
            if run['cancel_requested'] or run['status']=='cancelled':
                plan['status']='cancelled'
                status,reason,event='cancelled','cancellation_requested','project_cancelled'
            elif time.time()>=run['data']['deadline_at']:
                plan.update(status='failed',error='project deadline expired')
                status,reason,event='failed',plan['error'],'project_deadline_expired'
            self.store.save_metadata(rid,'project_plan',plan,event,status=status,reason=reason)

    def generate(self, provider, goal, on_created=None, reuse=None, resume_id=None, retry_of=None, retry_feedback=None):
        """Planner 先规划，Tester 只消费已校验规格；两次有界调用无工具执行。 Plan then design checks in two bounded calls without execution."""
        text(goal, 16000)
        if resume_id:
            run=self.store.run(resume_id)
            plan=run['data'].get('project_plan',{})
            if plan.get('kind')=='code' or plan.get('status')!='planning' or run['data']['goal']!=goal:
                raise MasaError('only interrupted planning can resume')
            rid=resume_id
        else:
            seed = self.store.root / 'planning-seeds' / uuid.uuid4().hex
            seed.mkdir(parents=True)
            (seed/'go.mod').write_text('module example.com/planning\n\ngo 1.27.0\n', encoding='utf-8')
            plan = {'status':'planning', 'provider':provider.profile, 'template':'go-cli', 'dependencies':[]}
            plan['clarification_enabled']=True
            if retry_of:
                old=self.store.run(retry_of)['data'].get('project_plan',{})
                for key in ('clarification','clarification_id','clarification_answers','requirement_revision'):
                    if key in old:plan[key]=old[key]
            rid = Runtime(self.store, self.executor).create(seed, goal, Budget(model_calls=4, deadline_seconds=86400),
                                                           graph=harness_policy(), project_plan=plan, parent_run_id=reuse or retry_of)
        if on_created:
            on_created(rid)
        try:
            if resume_id and plan.get('spec_ref'):
                spec=self.store.read(plan['spec_ref'])
            elif reuse:
                old=self.store.run(reuse)['data']['project_plan']
                spec=self.store.read(old['spec_ref'])
                self.store.event(rid,'planner_reused',{'spec_ref':old['spec_ref'],'parent_run_id':reuse})
            else:
                values={'goal':goal}
                # 上一次响应已收到但被契约拒绝时，带着具体原因让模型修正一次。
                # A received-but-rejected response is retried once with the concrete rejection reason.
                if retry_feedback:values['previous_attempt_error']=str(retry_feedback)[:1000]
                invocation='initial'
                if plan.get('clarification_enabled'):
                    values['clarification_allowed']=not bool(plan.get('clarification_answers'))
                if plan.get('clarification_answers'):
                    values.update(clarification=plan['clarification'],answers=plan['clarification_answers'])
                    invocation=plan['clarification_id']
                spec = RoleRuntime(self.store).call(rid,provider,'project_planner',values,invocation_id=invocation)
                if isinstance(spec,dict) and spec.get('kind')=='clarification_request':
                    if plan.get('clarification_answers'):
                        raise MasaError('clarification round limit reached; refine requirements in a new plan')
                    validate_question(spec)
                    plan.update(status='waiting_for_input',clarification=spec,
                                clarification_id=self.store.put(spec))
                    self.update(rid,plan,'paused','clarification_requested')
                    return rid
            validate_spec(spec)
            plan['spec_ref'] = self.store.put(spec)
            self.update(rid, plan, 'created', 'planner_proposed')
            checks = self.call(rid, provider, 'project_tester', {'goal':goal, 'spec':spec})
            validate_checks(checks,spec,require_coverage=False)
            # 审查原始 Tester 计划，避免补覆盖后把遗漏隐藏掉。
            # Review the original plan before coverage supplementation hides omissions.
            plan['test_review']=review_test_plan(spec,checks)
            covered={i for c in checks for i in c['acceptance_indices']}
            missing=sorted(set(range(len(spec['acceptance'])))-covered)
            if missing:
                # 补的是待审核计划而不是验证结果；明确暴露模型遗漏。
                # Supplement a reviewable plan, never evidence; expose the model's omissions.
                test=next(c for c in checks if c['operation']=='go_test')
                test['acceptance_indices']=sorted(set(test['acceptance_indices'])|set(missing))
                test['purpose']+='\nRuntime review required: add verification for criteria '+', '.join(str(i+1) for i in missing)+'. Coverage is not proof; manually review non-testable constraints.'
                plan['coverage_warning']='Tester 遗漏验收项 '+', '.join(str(i+1) for i in missing)+'；Runtime 已补入待审核验证计划，请确认策略是否足够。'
                self.store.event(rid,'check_plan_supplemented',{'missing_indices':missing,'policy':'human review required; not verification evidence'})
            Runtime.compile_project_checks(spec, checks)
            plan.update(status='awaiting_review', checks_ref=self.store.put(checks))
            self.update(rid, plan, 'paused', 'project_review_requested')
            return rid
        except Exception as exc:
            plan['status'] = 'failed'
            plan['error'] = str(exc) if isinstance(exc, MasaError) else 'local processing failure'
            self.update(rid, plan, 'failed', 'project_planning_failed')
            raise MasaError(f'project planning failed; run {rid}: ' + (str(exc) if isinstance(exc, MasaError) else 'local processing failure')) from None

    def call(self, rid, provider, purpose, values):
        """角色调用交给持久执行层，恢复时复用已保存响应。 Delegate calls to durable execution and reuse saved responses."""
        return RoleRuntime(self.store).call(rid,provider,purpose,values)

    def answer(self, rid, question_id, answers):
        """事务保存回答，重复相同提交幂等，冲突回答拒绝。 Persist answers atomically and reject conflicting retries."""
        with self.store.transaction():
            run=self.store.run(rid);plan=run['data'].get('project_plan',{})
            if run['cancel_requested'] or time.time()>=run['data']['deadline_at']:
                raise MasaError('clarification cancelled or deadline expired')
            if question_id!=plan.get('clarification_id') or not question_id:
                raise MasaError('stale clarification')
            validate_answers(plan['clarification'],answers)
            if plan.get('clarification_answers'):
                if plan['clarification_answers']!=answers:raise MasaError('clarification already answered differently')
                return rid
            if plan.get('status')!='waiting_for_input':raise MasaError('not waiting for clarification')
            plan.update(status='planning',clarification_answers=answers,requirement_revision=1)
            self.store.save_metadata(rid,'project_plan',plan,'clarification_answered',status='paused',reason='answer saved; resume planning')
        return rid

    def approve(self, rid, body):
        """确认最终可见规格，绑定原提案；批准规划不授权执行空项目。 Approve visible specifications without authorizing execution."""
        with self.store.transaction():
            run = self.store.run(rid)
            plan = run['data'].get('project_plan', {})
            if time.time() >= run['data']['deadline_at']:
                raise MasaError('project review deadline expired; create a new plan')
            if run['status'] != 'paused' or run['cancel_requested'] or plan.get('status') != 'awaiting_review':
                raise MasaError('project plan is not awaiting review')
            if body.get('spec_ref') != plan.get('spec_ref') or body.get('checks_ref') != plan.get('checks_ref'):
                raise MasaError('stale project proposal')
            spec = validate_spec(body.get('spec'))
            checks = body.get('checks')
            graph = Runtime.compile_project_checks(spec, checks)
            review=review_test_plan(spec,checks)
            if review['status']=='blocked':
                raise MasaError('test plan review blocked: '+next(f['message'] for f in review['findings'] if f['severity']=='blocking'))
            plan['test_review']=review
            approval = {'spec':spec, 'checks':checks, 'graph':graph.to_dict()}
            plan.update(status='approved', approval_ref=self.store.put(approval))
            if body.get('review_mode')=='automatic':plan['review_mode']='automatic'
            # 状态和审批事件一起提交，不能形成部分批准。
            # Commit status and approval evidence together.
            self.store.save_metadata(rid,'project_plan',plan,'project_plan_approved')
        return rid
