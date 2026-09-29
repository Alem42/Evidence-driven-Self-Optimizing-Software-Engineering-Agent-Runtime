"""执行批准的 CLI，结果与验证 Gate 分开保存。 Execute approved applications independently of verification verdicts."""
import uuid
from pathlib import Path
from masa.domain.models import MasaError
from masa.infrastructure.workspaces import verify_snapshot
from masa.infrastructure.locking import owner_lock


def run_application(store, runner, rid, argv, cancelled):
    """固定入口、结构化参数、快照校验及持久输出。 Use a fixed entrypoint, structured argv and durable output."""
    if not isinstance(argv,list) or len(argv)>64 or any(not isinstance(a,str) or len(a)>4096 or '\x00' in a for a in argv):
        raise MasaError('argv must contain at most 64 bounded strings')
    run=store.run(rid);data=run['data']
    if run['status']!='succeeded' or not data.get('project_bundle'):
        raise MasaError('application execution requires a verified generated project')
    with owner_lock(store.root/'runtime.lock'):
        workspace=Path(data['workspace'])
        verify_snapshot(workspace,data['snapshot_id'])
        request={'protocol_version':1,'request_id':uuid.uuid4().hex,'snapshot_id':data['snapshot_id'],
                 'operation':'app_run','argv':argv,'timeout_ms':30000,'max_output_bytes':65536}
        store.event(rid,'app_requested',{'request_ref':store.put(request),'request_id':request['request_id']})
        result=runner.execute(request,workspace,cancelled)
        verify_snapshot(workspace,data['snapshot_id'])
        store.event(rid,'app_finished',{'request_id':request['request_id'],'result_ref':store.put(result)})
        return result
