"""在临时副本解析待审源码，不执行项目。 Parse draft sources in a temporary copy without executing project code."""
import json
import tempfile
import time
import uuid
from pathlib import Path
from masa.domain.models import MasaError
from masa.domain.proposals import validate_files
from masa.infrastructure.locking import owner_lock


def review_sources(store, runner, rid, body):
    """绑定可见文件版本，保存 AST 审查请求和结果。 Bind visible files and persist AST review evidence."""
    with owner_lock(store.root/'role-locks'/f'{rid}.lock'):
        run=store.run(rid);plan=run['data'].get('project_plan',{})
        if plan.get('kind')!='code' or plan.get('status')!='awaiting_review' or run['cancel_requested'] or time.time()>=run['data']['deadline_at']:
            raise MasaError('source review requires an active unapproved code draft')
        if body.get('files_ref')!=plan.get('files_ref'):raise MasaError('stale code draft')
        spec=store.read(plan['spec_approval_ref'])['spec']
        files=validate_files(body.get('files'),spec)
        input_ref=store.put(files)
        request={'protocol_version':1,'request_id':uuid.uuid4().hex,'snapshot_id':input_ref,
                 'operation':'go_index','timeout_ms':30000,'max_output_bytes':1048576}
        # 请求意图与预算在解析前提交；此检查与发布后的 Gate 工具账本分开。
        # Commit intent and budget before parsing; published Gate checks remain separate.
        with store.transaction():
            if run['tool_calls']>=run['data']['budget']['tool_calls']:raise MasaError('source review tool budget exhausted')
            store.db.execute('UPDATE runs SET tool_calls=tool_calls+1 WHERE id=?',(rid,))
            store._event(rid,'source_review_requested',{'input_ref':input_ref,'request_ref':store.put(request)})
        with tempfile.TemporaryDirectory(prefix='masa-source-review-') as temp:
            root=Path(temp)
            for name,content in files.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content,encoding='utf-8')
            result=runner.execute(request,root,lambda:bool(store.run(rid)['cancel_requested']))
        result_ref=store.put(result)
        store.event(rid,'source_review_finished',{'input_ref':input_ref,'result_ref':result_ref})
        if result.get('status')!='completed' or result.get('exit_code')!=0 or result.get('truncated'):
            raise MasaError('source review unavailable; inspect saved parser evidence')
        try:index=json.loads(result['stdout'])
        except (ValueError,KeyError):raise MasaError('invalid source review parser output') from None
        if not isinstance(index,dict) or not isinstance(index.get('files'),list):raise MasaError('invalid source review index')
        findings=[]
        for file in index['files']:
            for diagnostic in file.get('diagnostics',[]):
                findings.append({'path':file['path'],'code':'syntax_error','message':diagnostic,'severity':'error'})
            for finding in file.get('test_findings') or []:
                findings.append({'path':file['path'],**finding,'severity':'warning'})
        report={'input_ref':input_ref,'result_ref':result_ref,'status':'issues_found' if findings else 'parsed',
                'findings':findings,'scope':'Go syntax and empty test bodies; no type checking or execution'}
        store.event(rid,'source_review_report',{'report_ref':store.put(report),'input_ref':input_ref})
        return report
