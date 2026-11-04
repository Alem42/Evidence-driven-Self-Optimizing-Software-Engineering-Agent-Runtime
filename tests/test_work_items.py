"""工作项（在已验证的项目上加一个功能）：增量的校验、与基线规格的合并、累积规格的上限、约束解码的形状。零 token。
Work items (one feature on a verified project): validating the delta, merging it into the baseline spec, the limits of an accumulated spec, the constrained-decoding shape. Zero tokens."""
import unittest

from masa.agents.schemas import response_schema
from masa.domain.models import MasaError
from masa.domain.proposals import merge_change, validate_change, validate_spec
from masa.languages.python import acceptance


def base_spec():
    spec = {'summary': 'stock ledger', 'module': 'stock', 'entrypoint': 'stock/__main__.py',
            'files': [{'path': 'pyproject.toml', 'purpose': 'meta'}, {'path': 'stock/__init__.py', 'purpose': 'pkg'},
                      {'path': 'stock/__main__.py', 'purpose': 'entry'}, {'path': 'stock/core/__init__.py', 'purpose': 'pkg'},
                      {'path': 'stock/core/core.py', 'purpose': 'run(args, stdin, stdout, stderr)'}, {'path': 'tests/test_core.py', 'purpose': 'unit tests'},
                      {'path': acceptance.PATH, 'purpose': 'generated'}],
            'acceptance': ['ADD sku qty adds stock.', 'REPORT prints one line per sku.', 'An unknown command exits with status 2.'],
            'cases': [{'name': 'add', 'accepts': [0, 1], 'args': [], 'stdin': 'ADD a 1\nREPORT\n', 'stdout': 'a 1', 'exit_code': 0},
                      {'name': 'empty', 'accepts': [1], 'args': [], 'stdin': 'REPORT\n', 'stdout': '', 'exit_code': 0},
                      {'name': 'bad', 'accepts': [2], 'args': [], 'stdin': 'NOPE\n', 'stdout': '', 'exit_code': 2}]}
    checks = [{'operation': 'py_test', 'purpose': 'unit and acceptance', 'acceptance_indices': [0, 1, 2], 'cases': [acceptance.describe(c) for c in spec['cases']]},
              {'operation': 'py_static', 'purpose': 'static', 'acceptance_indices': []}]
    return validate_spec(spec, 'python'), checks


def delta(**over):
    change = {'summary': 'REMOVE command in core.py', 'acceptance': ['REMOVE sku qty lowers stock.', 'REMOVE below zero exits with status 3.'],
              'files': [], 'retire': [],
              'cases': [{'name': 'remove', 'accepts': [0], 'args': [], 'stdin': 'ADD a 5\nREMOVE a 2\nREPORT\n', 'stdout': 'a 3', 'exit_code': 0},
                        {'name': 'too many', 'accepts': [1], 'args': [], 'stdin': 'ADD a 1\nREMOVE a 2\n', 'stdout': '', 'exit_code': 3},
                        {'name': 'remove all', 'accepts': [0], 'args': [], 'stdin': 'ADD a 1\nREMOVE a 1\nREPORT\n', 'stdout': 'a 0', 'exit_code': 0}]}
    change.update(over)
    return {'change': change, 'assumptions': ['LIMITATION: no partial removal']}


class ChangeValidationTests(unittest.TestCase):
    def test_a_valid_delta_is_normalised(self):
        out = validate_change(delta(), 'python')
        self.assertEqual(len(out['cases']), 3)
        self.assertEqual(out['assumptions'], ['LIMITATION: no partial removal'])
        self.assertEqual(out['retire'], [])

    def test_errors_name_the_field(self):
        with self.assertRaisesRegex(MasaError, 'change.acceptance'):
            validate_change(delta(acceptance=[]), 'python')
        with self.assertRaisesRegex(MasaError, 'change.files'):
            validate_change(delta(files=[{'path': 'tests/test_remove.py', 'purpose': 'tests'}]), 'python')
        with self.assertRaisesRegex(MasaError, 'change.cases'):
            validate_change(delta(cases=[{'name': 'x', 'accepts': [0], 'args': [], 'stdin': '', 'stdout': '', 'exit_code': 0}]), 'python')
        with self.assertRaisesRegex(MasaError, 'accepts'):
            validate_change(delta(cases=[dict(c, accepts=[5]) for c in delta()['change']['cases']]), 'python')

    def test_go_cannot_be_extended_yet(self):
        with self.assertRaisesRegex(MasaError, 'cannot be extended'):
            validate_change(delta(), 'go')


class MergeTests(unittest.TestCase):
    def test_merge_appends_items_and_cases_and_records_history(self):
        spec, checks = base_spec()
        change = {**validate_change(delta(), 'python'), 'request': 'add a REMOVE command'}
        merged, plan, retired = merge_change(spec, checks, change, 'python')
        self.assertEqual(len(merged['acceptance']), 5)
        self.assertEqual([c['name'] for c in merged['cases']], ['add', 'empty', 'bad', 'remove', 'too many', 'remove all'])
        # 新用例的 accepts 从“新验收项下标”换算成合并后的下标 / the new cases' accepts move to merged indices
        self.assertEqual(merged['cases'][3]['accepts'], [3])
        self.assertEqual(merged['cases'][4]['accepts'], [4])
        self.assertEqual(merged['history'], [{'no': 1, 'change': 'add a REMOVE command', 'summary': 'REMOVE command in core.py'}])
        self.assertEqual(retired, [])
        test = next(c for c in plan if c['operation'] == 'py_test')
        self.assertEqual(test['acceptance_indices'], [0, 1, 2, 3, 4])
        self.assertEqual([c['name'] for c in test['cases']], ['remove', 'too many', 'remove all'])  # 只有这次的新用例 / only this change's cases

    def test_retire_and_new_files_with_package_markers(self):
        spec, checks = base_spec()
        change = {**validate_change(delta(retire=[2], files=[{'path': 'stock/undo/undo.py', 'purpose': 'undo stack'}]), 'python'), 'request': 'r'}
        merged, _, retired = merge_change(spec, checks, change, 'python')
        self.assertEqual([c['name'] for c in retired], ['bad'])
        self.assertNotIn('bad', [c['name'] for c in merged['cases']])
        paths = {f['path'] for f in merged['files']}
        self.assertIn('stock/undo/undo.py', paths)
        self.assertIn('stock/undo/__init__.py', paths)  # 由程序补上 / added by the harness
        with self.assertRaisesRegex(MasaError, 'only 3 current cases'):
            merge_change(spec, checks, {**validate_change(delta(retire=[7]), 'python'), 'request': 'r'}, 'python')

    def test_existing_files_are_never_dropped_and_a_second_change_extends_history(self):
        spec, checks = base_spec()
        first, plan, _ = merge_change(spec, checks, {**validate_change(delta(), 'python'), 'request': 'one'}, 'python')
        second, _, _ = merge_change(first, plan, {**validate_change(delta(acceptance=['STATS prints the number of skus.'], cases=[
            {'name': 's', 'accepts': [0], 'args': [], 'stdin': 'STATS\n', 'stdout': '0', 'exit_code': 0}] * 3), 'python'), 'request': 'two'}, 'python')
        self.assertEqual([h['no'] for h in second['history']], [1, 2])
        self.assertTrue({f['path'] for f in spec['files']} <= {f['path'] for f in second['files']})


class VerifiedCasesTests(unittest.TestCase):
    def test_cases_are_read_back_from_a_revised_acceptance_file(self):
        """R1 第 3 步：测试修订改正了验收文件里的一个期望，规格里的旧用例不能在下一次合并时把它改回去。 R1 step 3: a revision corrected one expectation in the file; the spec's old copy must not undo it."""
        from masa.languages.registry import registry
        pack = registry.get('python')
        spec, _ = base_spec()
        text = acceptance.source(spec)
        revised = text.replace("'stdout': 'a 1'", "'stdout': 'a: 1'")
        self.assertNotEqual(text, revised)
        cases = pack.cases_in_files({acceptance.PATH: revised}, len(spec['acceptance']))
        self.assertEqual(cases[0]['stdout'], 'a: 1')
        self.assertEqual(cases[1:], spec['cases'][1:])
        self.assertIsNone(pack.cases_in_files({acceptance.PATH: 'CASES = oops('}, 3))
        self.assertIsNone(registry.get('go').cases_in_files({}, 3))


class ProblemCountTests(unittest.TestCase):
    def test_every_failing_acceptance_case_is_its_own_problem(self):
        """R1 第 10 步：所有验收用例都在同一行断言失败，消息开头相同；以前去重只看前 80 个字符，6 个失败与 1 个失败都算 1 个问题，工人的改进被丢掉。
        R1 step 10: every acceptance case fails on the same assertion line with the same start; deduplicating on 80 characters made 6 failures and 1 failure both "1 problem" and the worker's progress was discarded."""
        from masa.languages.registry import registry
        pack = registry.get('python')

        def block(number, got, want):
            return ('=' * 70 + '\n' + f'FAIL: test_{number:02d} (tests.test_acceptance.AcceptanceTests.test_{number:02d})\n' + '-' * 70 + '\n'
                    'Traceback (most recent call last):\n'
                    '  File "./tests/test_acceptance.py", line 130, in test\n'
                    "    self.assertEqual(normalise(stdout), normalise(case['stdout']), label)\n"
                    f"AssertionError: '{got}' != '{want}' : case {number} | args=[] stdin=\"STATS\\n\"\n\n")
        six = ''.join(block(n, '', f'skus: {n}') for n in range(1, 7))
        one = block(4, 'skus: 1\\nunits: 8', 'skus: 1\\nunits: 6')
        result = lambda text: [('py_test', {'status': 'completed', 'exit_code': 1, 'stdout': '', 'stderr': text + 'FAILED (failures=1)\n'})]
        self.assertEqual(len(pack.parse_failures(result(six))), 6)
        self.assertEqual(len(pack.parse_failures(result(one))), 1)
        # 同一个失败报告两次（同一位置、同一消息）仍然只算一次 / the same failure reported twice (same place, same message) still counts once
        self.assertEqual(len(pack.parse_failures(result(one + one))), 1)


class ResumeTests(unittest.TestCase):
    def test_a_change_job_killed_mid_revision_continues_that_revision(self):
        """R1 的 kill -9 测试：进程在测试修订中途被杀，恢复后必须接着那个修订，而不是回到原草稿重新分类（那会去“修”正确的实现）。
        R1's kill -9 test: killed in the middle of a test revision, the resume must continue that revision, not go back to the original draft and re-classify (which "repairs" a correct implementation)."""
        from types import SimpleNamespace
        from masa.application.orchestration.prepare import PrepareMixin
        calls = []
        generation = SimpleNamespace(resume_revision=lambda rid, provider: calls.append((rid, provider)) or 'revised-draft')
        job = {'request': {'change_of': 'base', 'change': 'x'}, 'phase': 'test_revision', 'run_id': 'revision-run', 'draft_id': 'original-draft'}
        fake = SimpleNamespace(store=None, _phase=lambda *a: None, job=job, resuming=True, _provider_of=lambda rid: 'provider-of-' + rid)
        self.assertEqual(PrepareMixin._prepare_change(fake, None, generation, 'goal'), 'revised-draft')
        self.assertEqual(calls, [('revision-run', 'provider-of-revision-run')])
        self.assertEqual(job['draft_id'], 'revised-draft')
        fake.resuming = False
        job['phase'] = 'verification'
        self.assertEqual(PrepareMixin._prepare_change(fake, None, generation, 'goal'), 'revised-draft')  # 已有草稿：直接用 / a saved draft is used as is


class AccumulatedLimitTests(unittest.TestCase):
    def test_one_shot_limits_are_unchanged(self):
        spec, _ = base_spec()
        many = dict(spec, acceptance=[f'Requirement number {i} holds.' for i in range(25)])
        with self.assertRaisesRegex(MasaError, '1..24'):
            validate_spec(many, 'python')

    def test_an_accumulated_spec_may_grow(self):
        spec, _ = base_spec()
        history = [{'no': 1, 'change': 'c', 'summary': 's'}]
        files = spec['files'] + [{'path': f'stock/core/m{i}.py', 'purpose': 'more'} for i in range(20)]
        grown = dict(spec, history=history, files=files, acceptance=[f'Requirement number {i} holds.' for i in range(40)])
        validate_spec(grown, 'python')
        with self.assertRaisesRegex(MasaError, 'history'):
            validate_spec(dict(grown, history=[{'no': 1}]), 'python')


class SchemaTests(unittest.TestCase):
    def test_structured_cases_live_in_the_spec_not_in_the_checks(self):
        schema = response_schema({'purpose': 'project_architect', 'language': 'python', 'acceptance_cases': True})
        spec = schema['properties']['spec']
        self.assertIn('cases', spec['required'])
        test = schema['properties']['checks']['properties']['py_test']
        self.assertNotIn('cases', test['properties'])
        legacy = response_schema({'purpose': 'project_architect', 'language': 'python'})
        self.assertIn('cases', legacy['properties']['checks']['properties']['py_test']['required'])

    def test_change_mode_schema(self):
        schema = response_schema({'purpose': 'project_architect', 'language': 'python', 'change_request': 'add X'})
        self.assertEqual(schema['required'], ['change'])
        self.assertIn('cases', schema['properties']['change']['required'])


if __name__ == '__main__':
    unittest.main()
