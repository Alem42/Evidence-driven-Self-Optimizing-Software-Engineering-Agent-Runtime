"""以保留的真实缺导入草稿验证修复；显式运行会调用 API。 Validate repair from a preserved broken draft; spends API tokens."""
import argparse
from pathlib import Path
from masa.adapters.sqlite import Store
from masa.adapters.runner import Runner
from masa.domain import Budget
from masa.project_generation import ProjectGeneration
from masa.runtime import Runtime
from masa.web.settings import Settings


def main():
    """先产生真实失败再生成修复供审查；--approve 只运行已审查草稿。 Produce real failure then a reviewable repair; approve separately."""
    parser=argparse.ArgumentParser();parser.add_argument('--approve');args=parser.parse_args()
    root=Path(__file__).resolve().parents[1];store=Store(root/'.masa')
    runner=Runner(root/'.tools/bin/masa-runner.exe',root/'.tools/go/bin/go.exe')
    service=ProjectGeneration(store,runner)
    try:
        if args.approve:
            meta=store.run(args.approve)['data']['project_plan']
            child=service.approve(args.approve,{'files_ref':meta['files_ref'],'files':store.read(meta['files_ref'])})
            result=Runtime(store,runner).execute(child)
            print('EXECUTION',child,result['status'])
            return
        origin=store.run('4e73993c6005486e98f72cab58c55315')
        old=origin['data']['project_plan']
        spec=store.read(old['spec_approval_ref'])
        metadata={'kind':'code','status':'awaiting_review','spec_approval_ref':old['spec_approval_ref'],
                  'files_ref':old['files_ref'],'fixture':'original missing-import draft; tests unchanged'}
        draft=Runtime(store,runner).create(Path(origin['data']['workspace']),origin['data']['goal'],
            Budget(deadline_seconds=86400),graph=Runtime.compile_project_checks(spec['spec'],spec['checks']),
            parent_run_id=origin['id'],project_plan=metadata)
        store.set_status(draft,'paused','explicit repair acceptance fixture')
        child=service.approve(draft,{'files_ref':old['files_ref'],'files':store.read(old['files_ref'])})
        result=Runtime(store,runner).execute(child)
        print('BASELINE',child,result['status'],flush=True)
        assert result['status']=='failed'
        repair=service.repair(child,Settings(root/'.masa').provider(),'Fix the compiler failure using recorded evidence; keep all tests unchanged.')
        print('REPAIR',repair,flush=True)
        meta=store.run(repair)['data']['project_plan'];files=store.read(meta['files_ref'])
        for path in meta['changed_files']:print(path+'\n'+files[path])
    finally:store.close()


if __name__=='__main__':main()
