"""确定会失败的运行：同一失败签名在升级到最高等级之后仍不变，就停下交给人，不再继续花钱。
A doomed run: an unchanged failure signature after the strongest model has tried stops the task instead of burning more budget."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from masa.application.check_policy import failure_signature
from masa.application.coordinator import WorkflowCoordinator
from masa.application.router import Router
from masa.application.routing import DEFAULT_POLICY, validate_policy
from masa.infrastructure.jobs import Jobs
from masa.infrastructure.store import Store
from test_routing import BUDGET, ScriptedProvider, fake_entry
from test_runtime import FakeExecutor


class SameErrorExecutor(FakeExecutor):
    """每次都报同一个编译错误，只有行号在变。 Always the same compile error; only the positions change."""

    def execute(self, request, workspace, cancelled):
        result = super().execute(request, workspace, cancelled)
        if request['operation'] == 'go_test':
            result['stdout'] = './internal/app/app.go:%d:%d: undefined: Missing\n' % (self.calls + 3, self.calls)
        return result


class SignatureTests(unittest.TestCase):
    def test_the_signature_ignores_paths_and_positions_but_not_the_message(self):
        a = failure_signature([('go_test', {'exit_code': 1, 'stdout': './a/b.go:12:5: undefined: Foo\n', 'stderr': ''})])
        b = failure_signature([('go_test', {'exit_code': 1, 'stdout': 'x/y.go:99:1: undefined: Foo\n', 'stderr': ''})])
        c = failure_signature([('go_test', {'exit_code': 1, 'stdout': './a/b.go:12:5: undefined: Bar\n', 'stderr': ''})])
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(failure_signature([('go_test', {'exit_code': 0, 'stdout': 'x.go:1:1: undefined: Foo'})]), ())

    def test_go_json_test_output_and_failures_are_normalized(self):
        out = '{"Output":"--- FAIL: TestX (0.01s)\\n"}\n{"Output":"    main_test.go:41: got 3 want 5\\n"}\n'
        sig = failure_signature([('go_test', {'exit_code': 1, 'stdout': out, 'stderr': ''})])
        self.assertTrue(any('FAIL' in item for item in sig))
        self.assertTrue(any('got N want N' in item for item in sig))


class StuckRunTests(unittest.TestCase):
    def run_task(self, policy):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(Path(temp.name))
        self.addCleanup(store.close)
        local, cloud = ScriptedProvider('small', lambda: None), ScriptedProvider('big', lambda: None)
        providers = {'local': local, 'cloud': cloud}
        snapshot = {'version': 1, 'mode': 'ladder', 'policy': validate_policy({**DEFAULT_POLICY, **policy}), 'budget': dict(BUDGET),
                    'candidates': [fake_entry('local', 1, 'small', 'local'), fake_entry('cloud', 2, 'big', 'cloud')]}
        jobs = Jobs(Path(temp.name))
        jobs['job'] = {'status': 'running', 'run_id': None, 'mode': 'auto', 'phase': 'planning', 'attempt': 0, 'started': 0,
                       'request': {'goal': 'Build a CLI'}}
        router = Router(store, snapshot, lambda entry: providers[entry['id']])
        with patch('masa.intelligence.repair_context.build_repair_context', lambda s, e, r, f, ev, fb: (f, None)):
            WorkflowCoordinator(store, SameErrorExecutor(exit_code=1), None, jobs['job'], router=router).run()
        return store, jobs['job'], local, cloud

    def test_stops_only_after_the_strongest_model_has_tried(self):
        store, job, local, cloud = self.run_task({'stuck_after': 3})
        self.assertEqual(job['status'], 'completed')
        self.assertIn('stuck', job['note'])
        self.assertEqual((local.repairs, cloud.repairs), (1, 1))  # 本地一次 + 升级后的云一次，然后停 / one local, one cloud, then stop
        self.assertEqual(store.run(job['result']['id'])['status'], 'failed')
        stopped = [e['payload'] for r in store.all_runs() for e in store.events(r['id']) if e['type'] == 'task_stopped']
        self.assertEqual(stopped[0]['reason'], 'stuck')

    def test_it_does_not_stop_before_a_stronger_model_got_its_chance(self):
        # stuck_after=3 已满足，但只有本地试过：必须先升级。 The threshold is met but only the local model tried: escalate first.
        store, job, local, cloud = self.run_task({'stuck_after': 3, 'attempts_per_level': {'fix': 3}})
        self.assertGreaterEqual(cloud.repairs, 1)

    def test_a_high_threshold_leaves_the_normal_round_limit_in_charge(self):
        store, job, local, cloud = self.run_task({'stuck_after': 10})
        self.assertIn('repair limit', job['note'])
        self.assertGreater(local.repairs + cloud.repairs, 2)

    def test_the_policy_is_validated(self):
        for bad in (2, 11, 'x'):
            with self.assertRaises(Exception):
                validate_policy({'stuck_after': bad})


if __name__ == '__main__':
    unittest.main()
