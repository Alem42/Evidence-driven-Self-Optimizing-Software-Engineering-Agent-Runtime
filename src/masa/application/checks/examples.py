"""Planner 示例的黑盒核对：任务成功之后，用真实程序把每个示例跑一遍，和示例里写的逐字节输出/退出码比较。
Black-box check of the Planner's examples: after a task succeeds, run every example through the real program and compare with the byte-exact stdout and exit code written in the example.

只记录，不改变裁决（Gate 仍是唯一的裁决者）。它回答两个问题：
  1. “测试都过了，程序真的做了 Planner 说的事吗？”（接线错误、测试与规格一起错）；
  2. “Planner 的示例本身对不对？”（示例与独立判官不一致时，错的是示例）。
Recorded only; the Gate stays the single verdict. It answers two questions: do the passing tests mean the program really does what the Planner said (wiring bugs, tests and spec wrong together), and are the Planner's examples themselves right.
"""
import uuid
from pathlib import Path

from masa.domain.examples import compare
from masa.infrastructure.workspaces import verify_snapshot
from masa.languages.registry import registry


def check_examples(store, runner, rid, spec, cancelled=lambda: False):
    """对一个已验证成功的运行逐个核对示例；返回 {'total','passed','results'} 并写事件 examples_checked。 Check the examples of a verified run; returns {'total','passed','results'} and writes an examples_checked event."""
    examples = spec.get('examples') or []
    run = store.run(rid)
    data = run['data']
    pack = registry.for_run(data)
    operation = pack.op_id('run')
    if not examples or operation is None or not data.get('project_bundle'):
        return None
    workspace = Path(data['workspace'])
    verify_snapshot(workspace, data['snapshot_id'])
    results = []
    for example in examples:
        request = {'protocol_version': 1, 'request_id': uuid.uuid4().hex, 'snapshot_id': data['snapshot_id'], 'operation': operation,
                   'argv': example['args'], 'stdin': example['stdin'], 'timeout_ms': 20000, 'max_output_bytes': 65536}
        try:
            outcome = compare(example, runner.execute(request, workspace, cancelled))
        except Exception as exc:  # 核对只是证据：运行器出错只记下，不影响任务 / this is evidence only: a runner error is recorded, never fails the task
            outcome = {'ok': False, 'reason': f'could not run: {type(exc).__name__}: {str(exc)[:80]}'}
        results.append({'name': example['name'], **outcome})
    report = {'total': len(results), 'passed': sum(1 for r in results if r['ok']), 'results': results}
    store.event(rid, 'examples_checked', report)
    return report
