"""运行显式变异集合并打印评测报告。 Run an explicit mutation suite and print its report."""
import argparse
import json
from pathlib import Path
from masa.infrastructure.store import Store
from masa.infrastructure.runner import Runner
from masa.application.mutation import evaluate_mutations


def main():
    """接受验证版本和 JSON 变异文件，不自动生成或执行 shell。 Accept a verified run and explicit JSON replacements."""
    parser=argparse.ArgumentParser()
    parser.add_argument('run_id');parser.add_argument('suite',type=Path)
    parser.add_argument('--root',type=Path,default=Path.cwd())
    args=parser.parse_args();root=args.root.resolve()
    store=Store(root/'.masa')
    try:
        report=evaluate_mutations(store,Runner(root/'.tools/bin/masa-runner.exe',root/'.tools/go/bin/go.exe'),args.run_id,
                                  json.loads(args.suite.read_text(encoding='utf-8-sig')))
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:store.close()


if __name__=='__main__':main()
