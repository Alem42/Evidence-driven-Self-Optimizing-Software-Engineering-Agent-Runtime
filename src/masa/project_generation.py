"""已批准架构到多文件草稿和隔离执行。 Approved architecture to multi-file drafts and isolated execution."""
from pathlib import Path
import tempfile
import time
import json
from masa.domain import Budget, MasaError, canonical
from masa.runtime import Runtime
from masa.project_plan import ProjectPlanning, validate_spec


def validate_files(files, spec):
    """文件集合必须严格匹配批准目录，限制内容与模块声明。 Bind bounded contents to the exact approved file set."""
    validate_spec(spec)
    if not isinstance(files, dict) or set(files) != {f['path'] for f in spec['files']}:
        raise MasaError('files must exactly match the approved project structure')
    total = 0
    for content in files.values():
        if not isinstance(content, str) or not content.strip() or '\x00' in content:
            raise MasaError('invalid generated file content')
        size = len(content.encode('utf-8'))
        if size > 60000:
            raise MasaError('file exceeds 60 KB')
        total += size
    if total > 300000:
        raise MasaError('project exceeds 300 KB')
    if files['go.mod'].strip() != f"module {spec['module']}\n\ngo 1.27.0":
        raise MasaError('go.mod must use the approved module and Go 1.27.0 without dependencies')
    return files


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
        approval_ref = self.store.put({'files':files, 'spec_approval_ref':plan['spec_approval_ref']})
        if plan.get('approval_ref') and plan['approval_ref'] != approval_ref:
            raise MasaError('approved files cannot be changed; generate a new draft')
        # 先锁定审批内容，再发布快照；中断后只允许重试同一批准版本。
        # Freeze approved content before publishing; recovery can only retry this revision.
        plan.update(approval_ref=approval_ref, status='approved')
        self.planning.update(rid, plan, 'paused', 'project_code_approved')
        for row in self.store.db.execute('SELECT id,data FROM runs'):
            data = json.loads(row['data'])
            if data.get('parent_run_id') == rid and data.get('project_bundle', {}).get('approval_ref') == approval_ref:
                return row['id']
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
