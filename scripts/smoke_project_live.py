"""真实项目生成验收；显式运行会调用付费 API。 Live project acceptance; explicit invocation spends API tokens."""
from pathlib import Path
import argparse
import json
from masa.infrastructure.store import Store
from masa.infrastructure.runner import Runner
from masa.infrastructure.settings import Settings
from masa.application.planning import ProjectPlanning
from masa.application.generation import ProjectGeneration
from masa.runtime.engine import Runtime


def main():
    """分阶段保存引用，支持生成后人工审查再执行。 Persist stage references so code can be inspected before execution."""
    parser=argparse.ArgumentParser()
    parser.add_argument('--draft',help='approve and execute an inspected draft without another model call')
    parser.add_argument('--goal-file',type=Path,help='UTF-8 realistic project requirements; calls the real API')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    store=Store(root/'.masa')
    runner=Runner(root/'.tools/bin/masa-runner.exe',root/'.tools/go/bin/go.exe')
    try:
        if not args.draft:
            provider=Settings(root/'.masa').provider()
            planning=ProjectPlanning(store,runner)
            goal=('Build a minimal Go standard-library CLI that accepts integer command-line arguments and prints their sum. '
                  'No arguments prints 0. Negative integers work. Invalid integers return exit code 2 and an error on stderr. '
                  'Use exactly 4 files: go.mod, cmd/app/main.go, internal/app/sum.go, internal/app/sum_test.go. '
                  'Avoid overflow requirements; test empty, negative and invalid inputs. Keep code small and gofmt formatted.')
            if args.goal_file:
                goal=args.goal_file.read_text(encoding='utf-8')
            parent=planning.generate(provider,goal)
            print('PLAN',parent,flush=True)
            plan=store.run(parent)['data']['project_plan']
            spec=store.read(plan['spec_ref']);checks=store.read(plan['checks_ref'])
            planning.approve(parent,{'spec_ref':plan['spec_ref'],'checks_ref':plan['checks_ref'],'spec':spec,'checks':checks})
            draft=ProjectGeneration(store,runner).generate(parent,provider)
            print('DRAFT',draft,flush=True)
            meta=store.run(draft)['data']['project_plan']
            print(json.dumps(store.read(meta['files_ref']),ensure_ascii=False,indent=2))
        else:
            meta=store.run(args.draft)['data']['project_plan']
            child=ProjectGeneration(store,runner).approve(args.draft,{'files_ref':meta['files_ref'],'files':store.read(meta['files_ref'])})
            result=Runtime(store,runner).execute(child)
            print('EXECUTION',child,result['status'],result['data']['workspace'])
            for call in store.tools(child):
                print(json.dumps(store.read(call['result_ref']),ensure_ascii=False))
    finally:
        store.close()


if __name__=='__main__':
    main()
