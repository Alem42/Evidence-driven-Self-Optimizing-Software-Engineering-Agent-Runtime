"""从现有运行证据投影项目、版本和角色图，不迁移或覆盖历史数据库。 Project and role projections over existing evidence."""
from masa.domain.models import MasaError
from masa.domain.projects import ProjectSummary


class Projects:
    def __init__(self, store):
        """只依赖存储读取接口。 Depend only on repository reads."""
        self.store=store

    @staticmethod
    def root_id(run_id, runs):
        """沿父链确定稳定项目标识并拒绝循环。 Resolve a stable project identity through an acyclic parent chain."""
        seen=set()
        while run_id in runs:
            if run_id in seen:raise MasaError('cyclic project lineage')
            seen.add(run_id)
            parent=runs[run_id]['data'].get('parent_run_id')
            if not parent or parent not in runs:return run_id
            run_id=parent
        raise MasaError('project not found')

    def catalog(self):
        """以项目而非零散运行作为导航，旧记录仍可读。 Navigate projects rather than isolated runs without rewriting history."""
        runs={r['id']:r for r in self.store.all_runs()}
        groups={}
        for rid,run in runs.items():
            if not (run['data'].get('project_plan') or run['data'].get('project_bundle')):continue
            root=self.root_id(rid,runs)
            groups.setdefault(root,[]).append(run)
        return [ProjectSummary(root,runs[root]['data']['goal'],items[-1]['id'],items[-1]['status'],
                               sum(bool(r['data'].get('project_bundle')) for r in items)).to_dict()
                for root,items in reversed(list(groups.items()))]

    def view(self, rid):
        """按选中版本的来源链生成真实角色阶段，未执行阶段明确标为 pending。 Build lifecycle stages from evidence; future stages remain pending."""
        runs={r['id']:r for r in self.store.all_runs()}
        root=self.root_id(rid,runs)
        members=[r for r in runs.values() if self.root_id(r['id'],runs)==root]
        chain=[];current=rid
        while current in runs:
            chain.append(runs[current]);current=runs[current]['data'].get('parent_run_id')
        chain.reverse()
        # 主图只展示选中版本的最新方案/代码/验证；旧失败保留在版本历史。
        # Show one current lifecycle; retain superseded failures in version history.
        planning=next((r for r in reversed(chain) if r['data'].get('project_plan') and r['data']['project_plan'].get('kind')!='code'),None)
        code=next((r for r in reversed(chain) if r['data'].get('project_plan',{}).get('kind')=='code'),None)
        chain=[r for r in (planning,code,runs[rid] if runs[rid]['data'].get('project_bundle') else None) if r]
        stages=[]
        def add(run,role,label,status,kind='role',**extra):
            stages.append({'id':run['id']+':'+role,'run_id':run['id'],'role':role,'label':label,
                           'status':status,'kind':kind,**extra})
        for run in chain:
            plan=run['data'].get('project_plan')
            if plan:
                events=self.store.events(run['id'])
                roles=[('project_test_revision','Tester 修订测试') if plan.get('revision_scope')=='tests' else ('project_repair','Developer 修复')] if plan.get('repair_of') else (
                    [('project_developer','Developer')] if plan.get('kind')=='code' else [('project_planner','Planner'),('project_tester','Tester')])
                for role,label in roles:
                    related=[e for e in events if e['payload'].get('step_id')==role]
                    latest=next((e['type'] for e in reversed(related) if e['type'] in {'model_requested','model_completed','model_failed'}),None)
                    started=latest=='model_requested'
                    done=latest=='model_completed'
                    failed=latest=='model_failed'
                    reused=(role=='project_planner' and any(e['type']=='planner_reused' for e in events)) or (
                        role=='project_test_revision' and any(e['type']=='test_format_applied' for e in events))
                    state='succeeded' if done or reused else 'running' if started else 'pending'
                    if role=='project_planner' and plan.get('status')=='waiting_for_input':
                        state='blocked';label='Planner · 等待你的回答'
                    if failed or (started and not done and plan['status']=='failed'):state='failed'
                    if run['status']=='cancelled' and state=='running':state='cancelled'
                    result=next((e['payload'].get('response_ref') for e in reversed(related) if e['type']=='model_completed'),None)
                    add(run,role,label,state,response_ref=result,reused=reused)
                label=('自动采用代码' if plan.get('kind')=='code' else '自动采用方案') if plan.get('review_mode')=='automatic' else ('审核代码' if plan.get('kind')=='code' else '确认方案')
                status='succeeded' if plan['status']=='approved' else 'blocked' if plan['status']=='awaiting_review' else 'pending'
                add(run,'review',label,status,'human')
            elif run['data'].get('project_bundle'):
                steps=self.store.steps(run['id']);checks=[s for s in steps if s['id']!='gate']
                state='failed' if any(s['status']=='failed' for s in checks) else 'succeeded' if checks and all(s['status']=='succeeded' for s in checks) else 'running' if run['status']=='running' else 'pending'
                add(run,'executor','Executor',state,checks=checks)
                gate=next((s for s in steps if s['id']=='gate'),{})
                add(run,'gate','Gate',gate.get('status','pending'),'gate')
        selected=runs[rid]
        p=selected['data'].get('project_plan',{})
        if p:
            if p.get('kind')!='code':
                add(selected,'future_developer','Developer','pending')
                add(selected,'future_review','审核代码','pending','human')
            add(selected,'future_executor','Executor','pending')
            add(selected,'future_gate','Gate','pending','gate')
        return {'id':root,'title':runs[root]['data']['goal'],'selected_run_id':rid,'stages':stages,
                'versions':[{'run_id':r['id'],'status':r['status'],'kind':'verification' if r['data'].get('project_bundle') else 'code' if r['data'].get('project_plan',{}).get('kind')=='code' else 'plan',
                             'parent_run_id':r['data'].get('parent_run_id')} for r in members]}
