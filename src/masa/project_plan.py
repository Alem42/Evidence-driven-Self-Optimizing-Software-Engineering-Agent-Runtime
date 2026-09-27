"""项目规划契约与角色编排。 Project planning contracts and role orchestration."""

import re
import time
import uuid
from masa.domain import Budget, MasaError, canonical
from masa.runtime import Runtime
from masa.workflow import harness_policy


def text(value, limit=2000):
    """校验有界非空文本。 Validate bounded nonempty text."""
    if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
        raise MasaError('invalid project text')
    return value


def validate_spec(spec):
    """校验可移植目录与标准库 CLI 规格，不相信模型输出。 Validate portable standard-library CLI specifications."""
    if not isinstance(spec, dict) or set(spec) != {'summary','module','entrypoint','files','acceptance'}:
        raise MasaError('invalid ProjectSpec fields')
    text(spec['summary'])
    if not re.fullmatch(r'[a-z][a-z0-9.-]*(/[a-z][a-z0-9_-]*)+', text(spec['module'], 160)):
        raise MasaError('invalid Go module name')
    if spec['entrypoint'] != 'cmd/app/main.go':
        raise MasaError('entrypoint must be cmd/app/main.go')
    files = spec['files']
    if not isinstance(files, list) or not 3 <= len(files) <= 20:
        raise MasaError('project requires 3..20 files')
    names = set()
    for item in files:
        if not isinstance(item, dict) or set(item) != {'path','purpose'}:
            raise MasaError('invalid planned file')
        name = text(item['path'], 180)
        text(item['purpose'])
        parts = name.split('/')
        if (not re.fullmatch(r'[A-Za-z0-9_./-]+', name) or any(
            p in {'', '.', '..'} or p.startswith('.') or p.endswith('.') or
            re.fullmatch(r'(?i)(con|prn|aux|nul|com[0-9]|lpt[0-9])', p.split('.')[0]) for p in parts)
            or name.lower() in names or not (name == 'go.mod' or name.endswith('.go'))):
            raise MasaError('invalid or duplicate project path')
        names.add(name.lower())
    exact_names = {f['path'] for f in files}
    if 'go.mod' not in exact_names or spec['entrypoint'] not in exact_names or not any(n.endswith('_test.go') for n in exact_names):
        raise MasaError('plan requires go.mod, entrypoint and acceptance tests')
    if any(any(other.startswith(n + '/') for other in names) for n in names):
        raise MasaError('file/directory path conflict')
    acceptance = spec['acceptance']
    if not isinstance(acceptance, list) or not 1 <= len(acceptance) <= 24:
        raise MasaError('provide 1..24 acceptance criteria')
    for criterion in acceptance:
        text(criterion)
    if len(canonical(spec).encode('utf-8')) > 40000:
        raise MasaError('project specification exceeds 40 KB')
    return spec


def validate_checks(checks, spec, require_coverage=True):
    """验证 Tester 覆盖验收项；只允许固定工具，不接受命令字符串。 Validate coverage and allowlisted checks, never shell commands."""
    if not isinstance(checks, list) or not 1 <= len(checks) <= 3:
        raise MasaError('Tester must propose 1..3 distinct allowed checks')
    operations = set()
    covered = set()
    for check in checks:
        if not isinstance(check, dict) or not {'operation','purpose','acceptance_indices'} <= set(check) or set(check)-{'operation','purpose','acceptance_indices','cases'}:
            raise MasaError('invalid check fields')
        if not isinstance(check['operation'], str) or check['operation'] not in {'go_test','go_vet','go_fmt_check'} or check['operation'] in operations:
            raise MasaError('unauthorized or duplicate check')
        operations.add(check['operation'])
        text(check['purpose'])
        indices = check['acceptance_indices']
        if not isinstance(indices, list) or any(type(i) is not int or not 0 <= i < len(spec['acceptance']) for i in indices):
            raise MasaError('invalid acceptance reference')
        covered.update(indices)
        if 'cases' in check:
            cases=check['cases']
            if not isinstance(cases,list) or not 1<=len(cases)<=16:
                raise MasaError('provide 1..16 concrete test cases')
            for case in cases:
                if not isinstance(case,dict) or set(case)!={'name','input','expected','level'}:
                    raise MasaError('invalid test case contract')
                for field in ('name','input','expected'):text(case[field],1000)
                if case['level'] not in ('unit','integration','cli'):
                    raise MasaError('invalid test level')
    if 'go_test' not in operations:
        raise MasaError('at least one real go_test check is required')
    missing = sorted(set(range(len(spec['acceptance']))) - covered)
    if missing and require_coverage:
        raise MasaError('check plan is missing acceptance criteria: ' + ', '.join(str(i+1) for i in missing))
    if len(canonical(checks).encode('utf-8')) > 16000:
        raise MasaError('check plan exceeds 16 KB')
    return checks


class ProjectPlanning:
    def __init__(self, store, executor):
        """复用运行记录和 artifact，不引入新调度器。 Reuse runs and artifacts without a new scheduler."""
        self.store, self.executor = store, executor

    def update(self, rid, plan, status, event):
        """原子发布规划状态和引用。 Publish plan state and references atomically."""
        with self.store.transaction():
            data = self.store.run(rid)['data']
            data['project_plan'] = plan
            self.store.db.execute('UPDATE runs SET data=?,status=?,reason=? WHERE id=?',
                                  (canonical(data), status, 'project planning only; code not generated', rid))
            self.store._event(rid, event, plan)

    def generate(self, provider, goal, on_created=None, reuse=None):
        """Planner 先规划，Tester 只消费已校验规格；两次有界调用无工具执行。 Plan then design checks in two bounded calls without execution."""
        text(goal, 16000)
        seed = self.store.root / 'planning-seeds' / uuid.uuid4().hex
        seed.mkdir(parents=True)
        (seed/'go.mod').write_text('module example.com/planning\n\ngo 1.27.0\n', encoding='utf-8')
        plan = {'status':'planning', 'provider':provider.profile, 'template':'go-cli', 'dependencies':[]}
        rid = Runtime(self.store, self.executor).create(seed, goal, Budget(model_calls=2, deadline_seconds=86400),
                                                       graph=harness_policy(), project_plan=plan, parent_run_id=reuse)
        if on_created:
            on_created(rid)
        try:
            if reuse:
                old=self.store.run(reuse)['data']['project_plan']
                spec=self.store.read(old['spec_ref'])
                self.store.event(rid,'planner_reused',{'spec_ref':old['spec_ref'],'parent_run_id':reuse})
            else:
                spec = self.call(rid, provider, 'project_planner', {'goal':goal})
            validate_spec(spec)
            plan['spec_ref'] = self.store.put(spec)
            self.update(rid, plan, 'created', 'planner_proposed')
            checks = self.call(rid, provider, 'project_tester', {'goal':goal, 'spec':spec})
            validate_checks(checks,spec,require_coverage=False)
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
        """先记调用预算，再保存响应与用量，不自动重试。 Charge before calling; persist output and usage without retries."""
        context = {'purpose':purpose, **values}
        self.store.charge_model(rid, purpose, 2, provider.profile, self.store.put(context))
        started = time.monotonic()
        try:
            output = provider.respond(context)
        except Exception:
            self.store.event(rid, 'model_failed', {'step_id':purpose, 'usage':getattr(provider,'usage',None)})
            raise
        self.store.event(rid, 'model_completed', {'step_id':purpose, 'response_ref':self.store.put(output),
                         'usage':getattr(provider,'usage',None), 'duration_ms':round((time.monotonic()-started)*1000)})
        return output

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
            approval = {'spec':spec, 'checks':checks, 'graph':graph.to_dict()}
            plan.update(status='approved', approval_ref=self.store.put(approval))
            # 状态和审批事件一起提交，不能形成部分批准。
            # Commit status and approval evidence together.
            data = run['data']; data['project_plan'] = plan
            self.store.db.execute('UPDATE runs SET data=? WHERE id=?', (canonical(data), rid))
            self.store._event(rid, 'project_plan_approved', plan)
        return rid
