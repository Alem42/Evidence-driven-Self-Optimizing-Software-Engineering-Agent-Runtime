"""“默认流程没有变”的差分检查：同一批脚本化场景，分别在旧提交和当前代码上跑，比较完整的事件序列与模型调用序列。零 token。
Differential check that the default loop did not change: run the same scripted scenarios on an old commit and on the current code and compare the full event sequence and the model-call sequence. Zero tokens.

用法 / usage:
    git worktree add .masa/old/ceda ceda2ae
    python scripts/eval/loop_equivalence.py --root .masa/old/ceda > .masa/eq_old.json
    python scripts/eval/loop_equivalence.py --root .                > .masa/eq_new.json
    python scripts/eval/loop_equivalence.py --compare .masa/eq_old.json .masa/eq_new.json
"""
import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HEX = re.compile(r'\b[0-9a-f]{32}\b')
VOLATILE = {'active_seconds', 'created', 'at', 'started', 'finished', 'ts', 'time', 'duration_ms', 'seconds', 'ms'}


def normalise(value):
    """去掉 id 与时间等易变的值，只留下“发生了什么”。 Strip ids and timestamps; keep what happened."""
    if isinstance(value, dict):
        return {k: normalise(v) for k, v in sorted(value.items()) if k not in VOLATILE}
    if isinstance(value, list):
        return [normalise(v) for v in value]
    if isinstance(value, str):
        # 64 位十六进制 = 内容哈希：同一份代码也会因临时目录/时间不同，所以只比较“有一个哈希”。 A 64-hex string is a content hash: it varies even on identical code (temp paths, times).
        return 'HASH' if len(value) == 64 and all(c in '0123456789abcdef' for c in value) else HEX.sub('ID', value)
    if isinstance(value, float):
        return round(value, 4)
    return value


def build_and_run(case, scenario):
    import importlib

    def load(name):
        # 结构整理之后模块在 application.orchestration 下；旧提交里在 application 下。 After the restructure the modules live under application.orchestration; old commits keep them under application.
        try:
            return importlib.import_module('masa.application.orchestration.' + name)
        except ModuleNotFoundError:
            return importlib.import_module('masa.application.' + name)

    WorkflowCoordinator = load('coordinator').WorkflowCoordinator
    Router = load('router').Router
    DEFAULT_POLICY, validate_policy = load('routing').DEFAULT_POLICY, load('routing').validate_policy
    from masa.infrastructure.jobs import Jobs
    from masa.infrastructure.store import Store
    from test_routing import BUDGET, fake_entry
    from test_fix_flow import Model, World, WorldExecutor, SYNTAX, CYCLE

    class AssertExecutor(WorldExecutor):
        def execute(self, request, workspace, cancelled):
            result = super().execute(request, workspace, cancelled)
            if request['operation'] == 'go_test':
                ok = self.world.test_ok
                result['stdout'] = '' if ok else '    internal/app/app_test.go:12: got 3, want 4'
                result['exit_code'] = 0 if ok else 1
            return result

    world = World(impl_ok=scenario.get('impl_ok', False), test_ok=scenario.get('test_ok', False))
    local = Model('small', 'local', world, **scenario.get('local', {}))
    cloud = Model('big', 'cloud', world, **scenario.get('cloud', {}))
    providers = {'local': local, 'cloud': cloud}
    with tempfile.TemporaryDirectory() as temp:
        store = Store(Path(temp))
        policy = validate_policy({**DEFAULT_POLICY, 'prefer_highest_roles': ['project_diagnoser'], **scenario.get('policy', {})})
        snapshot = {'version': 1, 'mode': 'ladder', 'policy': policy, 'budget': dict(BUDGET),
                    'candidates': [fake_entry('local', 1, 'small', 'local'), fake_entry('cloud', 2, 'big', 'cloud')]}
        jobs = Jobs(Path(temp))
        jobs['job'] = {'status': 'running', 'run_id': None, 'mode': 'auto', 'phase': 'planning', 'attempt': 0, 'started': 0, 'request': {'goal': 'Build a CLI'}}
        router = Router(store, snapshot, lambda entry: providers[entry['id']])
        executor = (AssertExecutor if scenario.get('assert_only') else WorldExecutor)(world)
        with patch('masa.intelligence.repair_context.build_repair_context', lambda s, e, r, f, ev, fb: (f, None)):
            WorkflowCoordinator(store, executor, None, jobs['job'], router=router).run()
        events = []
        for run in sorted(store.all_runs(), key=lambda r: r['created'] if 'created' in r.keys() else 0):
            for event in store.events(run['id']):
                payload = normalise(event['payload'])
                if event['type'] == 'task_budget':
                    payload.pop('policy', None)  # 策略里新增了默认关闭的键，单独比较 / new default-off policy keys are compared separately
                events.append([event['type'], payload])
        final = store.run(jobs['job']['result']['id'])['status'] if jobs['job'].get('result') else None
        policy_keys = sorted(policy)
        prefer = list(policy['prefer_highest_roles'])
        store.close()
    return {'status': final, 'note': jobs['job'].get('note'), 'model_calls': world.log, 'verifications': world.verifications, 'events': events,
            'policy_keys': policy_keys, 'prefer_highest_roles': prefer}


SCENARIOS = {
    'both_defects': {},
    'assertion_only_diagnose': {'impl_ok': True, 'assert_only': True, 'cloud': {'diagnosis': {'owner': 'test', 'rationale': 'r', 'implementation_instructions': '', 'test_instructions': 'x', 'expectation_checks': []}}},
    'noop_revisions_escalate': {'impl_ok': True, 'local': {'noop_tests': 5}, 'cloud': {'diagnosis': {'owner': 'test', 'rationale': 'r', 'implementation_instructions': '', 'test_instructions': 'fix', 'expectation_checks': []}}},
    'spec_verdict_halts': {'impl_ok': True, 'local': {'noop_tests': 1}, 'cloud': {'diagnosis': {'owner': 'spec', 'rationale': 'r', 'implementation_instructions': '', 'test_instructions': '', 'expectation_checks': []}}},
    'local_never_fixes_escalates': {'local': {'fixes_impl': False, 'fixes_test': False}},
    'noop_everywhere_halts': {'impl_ok': True, 'policy': {'diagnose': False}, 'local': {'noop_tests': 99}, 'cloud': {'noop_tests': 99}},
}


def run_all(root):
    root = Path(root).resolve()
    sys.path[:0] = [str(root / 'src'), str(root / 'tests')]
    import unittest

    class Holder(unittest.TestCase):
        def runTest(self):
            pass
    case = Holder()
    return {name: build_and_run(case, scenario) for name, scenario in SCENARIOS.items()}


def compare(a_path, b_path):
    a, b = (json.loads(Path(p).read_text(encoding='utf-8')) for p in (a_path, b_path))
    bad = 0
    for name in a:
        x, y = a[name], b[name]
        for key in ('status', 'note', 'model_calls', 'verifications', 'events'):
            if x[key] != y[key]:
                bad += 1
                print(f'DIFFERENT {name}.{key}')
                if key == 'events':
                    for i, (e1, e2) in enumerate(zip(x[key], y[key])):
                        if e1 != e2:
                            print('   first difference at event', i, '\n   old:', json.dumps(e1, ensure_ascii=False)[:300], '\n   new:', json.dumps(e2, ensure_ascii=False)[:300])
                            break
                    else:
                        print('   lengths', len(x[key]), len(y[key]))
                else:
                    print('   old:', x[key], '\n   new:', y[key])
        if x['prefer_highest_roles'] != y['prefer_highest_roles']:
            print(f"NOTE {name}: prefer_highest_roles old={x['prefer_highest_roles']} new={y['prefer_highest_roles']}")
        added = sorted(set(y['policy_keys']) - set(x['policy_keys']))
        if added:
            print(f'NOTE {name}: policy keys added (default off): {added}')
    print('IDENTICAL on every compared field' if not bad else f'{bad} differences')
    return 1 if bad else 0


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--root')
    ap.add_argument('--compare', nargs=2)
    args = ap.parse_args()
    if args.compare:
        raise SystemExit(compare(*args.compare))
    print(json.dumps(run_all(args.root), ensure_ascii=False, indent=1))
