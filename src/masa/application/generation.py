from masa.domain.proposals import validate_files, validate_repair
"""已批准架构到多文件草稿和隔离执行。 Approved architecture to multi-file drafts and isolated execution."""
from pathlib import Path
import tempfile
import time
import json
from masa.domain.models import Budget, MasaError, canonical
from masa.runtime.engine import Runtime
from masa.application.planning import ProjectPlanning, validate_spec
from masa.infrastructure.workspaces import verify_snapshot






class ProjectGeneration:
    def __init__(self, store, executor):
        """沿用运行账本与审批存储。 Reuse the run ledger and approval artifacts."""
        self.store, self.executor = store, executor
        self.planning = ProjectPlanning(store, executor)

    def generate(self, parent, provider, on_created=None):
        """只根据已批准规格生成独立草稿，不写实现文件。 Generate an independent draft from approved specifications only."""
        original = self.store.run(parent)
        plan = original['data'].get('project_plan', {})
        if plan.get('status') != 'approved' or plan.get('kind') == 'code' or original['cancel_requested']:
            raise MasaError('approve the project specification first')
        approved = self.store.read(plan['approval_ref'])
        graph = Runtime.compile_project_checks(approved['spec'], approved['checks'])
        metadata = {'kind':'code','status':'generating','spec_approval_ref':plan['approval_ref'], 'provider':provider.profile}
        rid = Runtime(self.store, self.executor).create(Path(original['data']['workspace']), original['data']['goal'],
              Budget(model_calls=2, tool_calls=3, deadline_seconds=86400), graph=graph,
              parent_run_id=parent, project_plan=metadata)
        if on_created:
            on_created(rid)
        try:
            files = self.planning.call(rid, provider, 'project_developer', {'goal':original['data']['goal'], **approved})
            validate_files(files, approved['spec'])
            metadata.update(status='awaiting_review', files_ref=self.store.put(files))
            self.planning.update(rid, metadata, 'paused', 'project_code_review_requested')
            return rid
        except Exception as exc:
            metadata.update(status='failed',error=str(exc) if isinstance(exc,MasaError) else 'generation failed')
            self.planning.update(rid, metadata, 'failed', 'project_code_generation_failed')
            raise MasaError(f'project generation failed; run {rid}: {metadata["error"]}') from None

    def repair(self, parent, provider, feedback='', on_created=None):
        """以真实失败证据生成一次修复草稿，不自动批准或循环。 Propose one evidence-backed repair without auto-approval or loops."""
        original=self.store.run(parent)
        data=original['data']
        if original['status']!='failed' or not data.get('project_bundle'):
            raise MasaError('repair requires a failed generated-project verification')
        if not isinstance(feedback,str) or len(feedback)>4000:
            raise MasaError('feedback must be at most 4000 characters')
        verify_snapshot(Path(data['workspace']),data['snapshot_id'])
        base_ref=data['project_bundle']['approval_ref']
        base=self.store.read(base_ref)
        approved=self.store.read(base['spec_approval_ref'])
        evidence=[]
        for call in self.store.tools(parent):
            if not call['result_ref']:continue
            result=self.store.read(call['result_ref'])
            evidence.append({'operation':self.store.read(call['request_ref'])['operation'],
                'result_ref':call['result_ref'],'status':result['status'],'exit_code':result.get('exit_code'),
                'stdout':result.get('stdout','')[-12000:],'stderr':result.get('stderr','')[-12000:],
                'output_may_be_truncated':True})
        if not any(e['status']=='completed' and e['exit_code']!=0 for e in evidence):
            raise MasaError('repair requires a recorded failed check; unknown results need inspection')
        metadata={'kind':'code','status':'generating','spec_approval_ref':base['spec_approval_ref'],
                  'base_approval_ref':base_ref,'repair_of':parent,'provider':provider.profile}
        rid=Runtime(self.store,self.executor).create(Path(data['workspace']),data['goal'],
            Budget(model_calls=1,tool_calls=3,deadline_seconds=86400),
            graph=Runtime.compile_project_checks(approved['spec'],approved['checks']),
            parent_run_id=parent,project_plan=metadata)
        if on_created:on_created(rid)
        try:
            changes=self.planning.call(rid,provider,'project_repair',{
                'goal':data['goal'],**approved,'original_files':base['files'],
                'failure_evidence':evidence,'feedback':feedback})
            files=validate_repair(changes,base['files'])
            validate_files(files,approved['spec'])
            metadata.update(status='awaiting_review',files_ref=self.store.put(files),
                            changed_files=[p for p in changes if files[p]!=base['files'][p]])
            self.planning.update(rid,metadata,'paused','project_repair_review_requested')
            return rid
        except Exception as exc:
            metadata.update(status='failed',error=str(exc) if isinstance(exc,MasaError) else 'repair generation failed')
            self.planning.update(rid,metadata,'failed','project_repair_failed')
            raise MasaError(f'repair failed; run {rid}: {metadata["error"]}') from None

    def approve(self, rid, body):
        """先准备完整目录再创建可运行快照；重试复用已发布运行。 Prepare all files before publishing a run; retries reuse it."""
        run = self.store.run(rid)
        plan = run['data'].get('project_plan', {})
        if time.time() >= run['data']['deadline_at']:
            raise MasaError('code review deadline expired; generate a new draft')
        if run['status'] != 'paused' or run['cancel_requested'] or plan.get('kind') != 'code' or plan.get('status') not in {'awaiting_review','approved'}:
            raise MasaError('code draft is not awaiting approval')
        if body.get('files_ref') != plan.get('files_ref'):
            raise MasaError('stale code draft')
        spec = self.store.read(plan['spec_approval_ref'])
        files = validate_files(body.get('files'), spec['spec'])
        if plan.get('base_approval_ref'):
            base=self.store.read(plan['base_approval_ref'])['files']
            if any(files[p]!=content for p,content in base.items() if p=='go.mod' or p.endswith('_test.go')):
                raise MasaError('repair approval cannot change frozen tests or module')
        approval_ref = self.store.put({'files':files, 'spec_approval_ref':plan['spec_approval_ref']})
        if plan.get('approval_ref') and plan['approval_ref'] != approval_ref:
            raise MasaError('approved files cannot be changed; generate a new draft')
        # 先锁定审批内容，再发布快照；中断后只允许重试同一批准版本。
        # Freeze approved content before publishing; recovery can only retry this revision.
        plan.update(approval_ref=approval_ref, status='approved')
        self.planning.update(rid, plan, 'paused', 'project_code_approved')
        published=self.store.published_child(rid,approval_ref)
        if published:
            return published
        # 临时准备目录不会暴露为成功项目，Runtime 复制完毕才发布运行记录。
        # Staging is never a published project; Runtime publishes only after copying a complete snapshot.
        with tempfile.TemporaryDirectory(prefix='project-', dir=self.store.root) as temp:
            source = Path(temp)
            for name, content in files.items():
                target = source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding='utf-8', newline='\n')
            child = Runtime(self.store,self.executor).create(source,run['data']['goal'],
                    Budget(tool_calls=3,deadline_seconds=1800),
                    graph=Runtime.compile_project_checks(spec['spec'],spec['checks']),parent_run_id=rid,
                    project_bundle={'approval_ref':approval_ref})
        return child
