"""独立模型测试计划审查入口。 Independent model test-plan review entrypoint."""
import argparse
import json
from pathlib import Path
from masa.infrastructure.store import Store
from masa.infrastructure.runner import Runner
from masa.infrastructure.settings import Settings
from masa.application.semantic_review import review_test_semantics


def main():
    """读取已保存项目规格，以本地模型配置审查。 Review persisted specifications using local model settings."""
    parser=argparse.ArgumentParser();parser.add_argument('run_id');parser.add_argument('--resume-id')
    args=parser.parse_args();root=Path.cwd();store=Store(root/'.masa')
    try:
        run=store.run(args.run_id);data=run['data'];plan=data.get('project_plan',{})
        if data.get('project_bundle'):
            bundle=store.read(data['project_bundle']['approval_ref']);approved=store.read(bundle['spec_approval_ref'])
        elif plan.get('approval_ref') and plan.get('kind')!='code':approved=store.read(plan['approval_ref'])
        else:approved={'spec':store.read(plan['spec_ref']),'checks':store.read(plan['checks_ref'])}
        result=review_test_semantics(store,Runner(root/'.tools/bin/masa-runner.exe',root/'.tools/go/bin/go.exe'),
            Settings(root/'.masa').provider(),args.run_id,approved['spec'],approved['checks'],resume_id=args.resume_id)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    finally:store.close()


if __name__=='__main__':main()
