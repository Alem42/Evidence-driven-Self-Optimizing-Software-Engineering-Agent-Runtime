"""显式小规模变异评测，不修改已验证项目。 Explicit small mutation evaluation without editing verified projects."""
import json
import tempfile
import uuid
from pathlib import Path
from masa.domain.models import MasaError
from masa.infrastructure.workspaces import verify_snapshot


def evaluate_mutations(store, runner, rid, mutations):
    """验证基线后逐个测试实现替换，区分杀死、存活和无效变异。 Test isolated mutants after a passing baseline."""
    run=store.run(rid);data=run['data']
    if run['status']!='succeeded' or not data.get('project_bundle'):
        raise MasaError('mutation evaluation requires a verified generated project')
    if not isinstance(mutations,list) or not 1<=len(mutations)<=5:raise MasaError('provide 1..5 explicit mutations')
    files=store.read(data['project_bundle']['approval_ref'])['files']
    names=set()
    for mutation in mutations:
        if not isinstance(mutation,dict) or set(mutation)!={'name','path','before','after'}:
            raise MasaError('invalid mutation fields')
        name,path=mutation['name'],mutation['path']
        if not isinstance(name,str) or not name.strip() or len(name)>100 or name in names:raise MasaError('invalid mutation name')
        names.add(name)
        if not isinstance(path,str) or path not in files or not path.endswith('.go') or path.endswith('_test.go'):raise MasaError('mutations may only change implementation')
        if any(not isinstance(mutation[k],str) or not mutation[k] or len(mutation[k])>4000 for k in ('before','after')):
            raise MasaError('invalid mutation replacement')
        if mutation['before']==mutation['after'] or files[path].count(mutation['before'])!=1:raise MasaError('mutation requires one exact distinct replacement')
    verify_snapshot(Path(data['workspace']),data['snapshot_id'])
    suite_ref=store.put(mutations)
    store.event(rid,'mutation_evaluation_requested',{'suite_ref':suite_ref})
    def execute(bundle):
        """在临时副本运行同一批测试，记录每份输入和真实输出。 Run the same tests on isolated copies and persist evidence."""
        input_ref=store.put(bundle)
        request={'protocol_version':1,'request_id':uuid.uuid4().hex,'snapshot_id':input_ref,
                 'operation':'go_test','timeout_ms':120000,'max_output_bytes':262144}
        store.event(rid,'mutation_tool_requested',{'input_ref':input_ref,'request_ref':store.put(request)})
        with tempfile.TemporaryDirectory(prefix='masa-mutation-') as temp:
            root=Path(temp)
            for name,content in bundle.items():
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content,encoding='utf-8')
            result=runner.execute(request,root,lambda:bool(store.run(rid)['cancel_requested']))
        result_ref=store.put(result)
        store.event(rid,'mutation_tool_finished',{'input_ref':input_ref,'result_ref':result_ref})
        return result,result_ref
    baseline,baseline_ref=execute(files)
    if baseline.get('status')!='completed' or baseline.get('exit_code')!=0 or baseline.get('truncated'):
        raise MasaError('mutation baseline unavailable or failed')
    results=[]
    for mutation in mutations:
        changed=dict(files);path=mutation['path'];changed[path]=changed[path].replace(mutation['before'],mutation['after'],1)
        result,ref=execute(changed)
        frames=[]
        for line in result.get('stdout','').splitlines():
            try:frames.append(json.loads(line))
            except ValueError:pass
        invalid=any(isinstance(f,dict) and (f.get('Action')=='build-fail' or f.get('FailedBuild')) for f in frames)
        invalid=invalid or '[setup failed]' in result.get('stdout','')
        status='inconclusive' if result.get('status')!='completed' or result.get('truncated') else 'invalid' if invalid else 'survived' if result['exit_code']==0 else 'killed'
        results.append({'name':mutation['name'],'status':status,'result_ref':ref})
    verify_snapshot(Path(data['workspace']),data['snapshot_id'])
    report={'run_id':rid,'suite_ref':suite_ref,'baseline_ref':baseline_ref,'results':results,
            'scope':'explicit mutation probes, not coverage or correctness proof; Gate unchanged'}
    store.event(rid,'mutation_evaluation_report',{'report_ref':store.put(report)})
    return report
