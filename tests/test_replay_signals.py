"""回放工具 replay_signals 的解析与触发规则（用合成的账本）。 The replay tool's parsing and firing rule on a synthetic ledger."""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts' / 'eval'))
import replay_signals  # noqa: E402
from masa.languages.registry import registry  # noqa: E402

B = chr(92)
GO = registry.get('go')


def check(got, want='5'):
    return {'tool': 'run_check', 'ok': False, 'result': f'test check found 1 problem(s) in your files:\nlcs_test.go:107: stdout = "{got}{B}n", want "{want}{B}n"'}


class FakeStore:
    """只有 role_invocations 与 read：每个“调用”的输入里带着上一步的结果。 Only role_invocations and read: each call's input carries the previous step's result."""

    def __init__(self, steps):
        self.inputs = {f'ref{i}': {'last_results': [s]} for i, s in enumerate(steps)}
        self.db = types.SimpleNamespace(execute=lambda *_a: types.SimpleNamespace(fetchall=lambda: [{'input_ref': ref} for ref in self.inputs]))

    def read(self, ref):
        return self.inputs[ref]


WRITE = {'tool': 'edit_file', 'ok': True, 'result': 'wrote x'}


class SignalTests(unittest.TestCase):
    def test_a_failing_assertion_is_parsed_without_its_line_number(self):
        found = replay_signals.observations(check('6')['result'], GO)
        self.assertEqual(list(found.values()), ['"6' + B + 'n"'])
        self.assertEqual(len({k[1] for k in found}), 1)

    def test_the_same_actual_value_in_three_observations_with_two_changes_fires(self):
        steps = [check('6'), WRITE, check('6'), WRITE, check('6')]
        self.assertEqual(replay_signals.scan_job(FakeStore(steps), [{'id': 'r'}], lambda r: GO), (5, 3))

    def test_one_change_is_not_enough_and_a_moving_value_never_fires(self):
        self.assertIsNone(replay_signals.scan_job(FakeStore([check('6'), WRITE, check('6'), check('6')]), [{'id': 'r'}], lambda r: GO))
        self.assertIsNone(replay_signals.scan_job(FakeStore([check('6'), WRITE, check('7'), WRITE, check('8')]), [{'id': 'r'}], lambda r: GO))


if __name__ == '__main__':
    unittest.main()
