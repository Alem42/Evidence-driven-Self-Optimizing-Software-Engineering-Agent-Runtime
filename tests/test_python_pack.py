"""Python 语言包：确定性检查、规格校验、失败解析，以及用脚本化模型 + 真实运行器的端到端流程（零 token）。
The Python language pack: deterministic checks, spec validation, failure parsing, and an end-to-end run with scripted models and the real runner (zero tokens).
"""
import copy
import tempfile
import unittest
from pathlib import Path

from masa.agents.protocol import validate_response
from masa.application.generation import ProjectGeneration
from masa.application.planning import ProjectPlanning
from masa.domain.models import MasaError
from masa.domain.proposals import validate_checks, validate_files, validate_spec
from masa.infrastructure.runner import Runner
from masa.infrastructure.store import Store
from masa.languages.python import checks, failures
from masa.languages.registry import registry
from masa.runtime.engine import Runtime

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / '.tools/bin/masa-runner.exe'
NL = chr(10)

APP = NL.join(['"""求和。 Sum."""', '', '', 'def total(text):', '    return sum(int(item) for item in text.split())', '', '',
               'def run(args, stdin, stdout, stderr):', '    print(total(stdin.read()), file=stdout)', '    return 0', ''])
MAIN = NL.join(['import sys', '', 'from calc.app import run', '', 'sys.exit(run(sys.argv[1:], sys.stdin, sys.stdout, sys.stderr))', ''])
TEST = NL.join(['import io', 'import unittest', '', 'from calc.app import run, total', '', '',
                'class AppTests(unittest.TestCase):', '    def test_total(self):', '        self.assertEqual(total("1 2 3"), 6)', '',
                '    def test_run(self):', '        out = io.StringIO()', '        self.assertEqual(run([], io.StringIO("5 5"), out, io.StringIO()), 0)',
                '        self.assertEqual(out.getvalue(), "10" + chr(10))', ''])
SPEC = {'summary': 'sum integers from stdin', 'module': 'calc', 'entrypoint': 'calc/__main__.py',
        'files': [{'path': 'pyproject.toml', 'purpose': 'metadata'}, {'path': 'calc/__init__.py', 'purpose': 'package'},
                  {'path': 'calc/__main__.py', 'purpose': 'thin entry that calls run'}, {'path': 'calc/app.py', 'purpose': 'sum and run'},
                  {'path': 'tests/test_app.py', 'purpose': 'tests'}],
        'acceptance': ['prints the sum of the integers on stdin followed by a newline']}
CHECKS = [{'operation': 'py_test', 'purpose': 'behaviour', 'acceptance_indices': [0],
           'cases': [{'name': 'sum', 'input': '1 2 3', 'expected': 'stdout 6 and newline, exit code 0', 'level': 'cli'}]},
          {'operation': 'py_static', 'purpose': 'syntax', 'acceptance_indices': [0]}]
pack = registry.get('python')
FILES = {**pack.seed_files(SPEC), 'calc/__init__.py': '', 'calc/__main__.py': MAIN, 'calc/app.py': APP, 'tests/test_app.py': TEST}


class FakeModel:
    profile = {'provider': 'test', 'model': 'fake'}
    usage = {'total_tokens': 10}

    def __init__(self, files=None):
        self.files = files or FILES
        self.contexts = []

    def respond(self, context):
        self.contexts.append(copy.deepcopy(context))
        if context['purpose'] == 'project_planner':
            return copy.deepcopy(SPEC)
        if context['purpose'] == 'project_tester':
            return copy.deepcopy(CHECKS)
        return validate_response(context, {'files': {context['target_path']: self.files[context['target_path']]}}, '')


class LocalFake(FakeModel):
    config = {'model_type': 'local', 'protocol': 'ollama'}
    snapshot = {'version': 1, 'mode': 'fixed', 'profile_id': 'local', 'config': config}


class ChecksTests(unittest.TestCase):
    def test_syntax_errors_carry_line_and_column(self):
        found = checks.parse_errors({'a.py': 'def f(:' + NL}, ['a.py'])
        self.assertRegex(found['a.py'], r'a\.py:1:\d+: SyntaxError')
        self.assertEqual(checks.parse_errors({'a.py': 'x = 1' + NL}, ['a.py']), {})

    def test_unused_imports_are_removed_and_missing_standard_modules_added(self):
        source = 'import os' + NL + 'from collections import OrderedDict, Counter' + NL + NL + 'def f(a):' + NL + '    return sys.argv, Counter(a)' + NL
        fixed, changes = checks.fix_imports(source)
        self.assertNotIn('import os', fixed)
        self.assertIn('from collections import Counter', fixed)
        self.assertNotIn('OrderedDict', fixed)
        self.assertIn('import sys', fixed)
        self.assertEqual(sorted(changes), [('add', 'sys'), ('remove', 'OrderedDict'), ('remove', 'os')])
        self.assertEqual(checks.fix_imports(fixed)[1], [])  # 幂等 / idempotent

    def test_imports_of_modules_the_spec_lacks_and_third_party_packages_are_reported(self):
        planned = [f['path'] for f in SPEC['files']]
        files = {'calc/app.py': 'from calc.ghost import x' + NL + 'import requests' + NL, 'calc/__main__.py': MAIN, 'tests/test_app.py': TEST}
        found = checks.unknown_import_messages(files, list(files), 'calc', planned)
        self.assertEqual(list(found), ['calc/app.py'])
        self.assertIn('calc.ghost', found['calc/app.py'])
        self.assertIn('calc.app', found['calc/app.py'])  # 列出真正能导入的模块 / lists what can really be imported
        self.assertIn('third-party package requests', found['calc/app.py'])
        self.assertEqual(checks.unknown_import_messages({'calc/app.py': 'from . import app' + NL, 'calc/__init__.py': ''}, ['calc/app.py'], 'calc', planned), {})

    def test_an_entry_that_reads_input_itself_is_refused(self):
        bad = 'import sys' + NL + 'for line in sys.stdin:' + NL + '    print(line)' + NL
        self.assertIn('for ... in sys.stdin', checks.entry_problem_messages({'calc/__main__.py': bad}, ['calc/__main__.py'], 'calc/__main__.py')['calc/__main__.py'])
        self.assertEqual(checks.entry_problem_messages({'calc/__main__.py': MAIN}, ['calc/__main__.py'], 'calc/__main__.py'), {})  # 只把 sys.stdin 传下去是允许的 / passing sys.stdin on is fine

    def test_tests_that_can_never_fail_are_refused(self):
        vacuous = 'import unittest' + NL + 'class T(unittest.TestCase):' + NL + '    def test_a(self):' + NL + '        self.assertTrue(True)' + NL
        self.assertTrue(checks.test_problems(vacuous))
        self.assertTrue(checks.test_problems('x = 1' + NL))
        self.assertTrue(checks.test_problems('import unittest' + NL + 'class T(unittest.TestCase):' + NL + '    @unittest.skip("x")' + NL + '    def test_a(self):' + NL + '        self.assertEqual(1, 2)' + NL))
        self.assertEqual(checks.test_problems(TEST), [])

    def test_outline_declares_and_test_function_source(self):
        outline = checks.outline(APP)
        self.assertIn('def total(text): ...', outline)
        self.assertIn('def run(args, stdin, stdout, stderr): ...', outline)
        self.assertNotIn('sum(', outline)  # 函数体被去掉 / bodies are removed
        self.assertTrue(checks.declares(APP, 'run') and not checks.declares(APP, 'missing'))
        source = checks.test_function_source({'tests/test_app.py': TEST}, 'test_app.py', 9)
        self.assertIn('def test_total', source)
        self.assertNotIn('def test_run', source)


class SpecTests(unittest.TestCase):
    def test_a_valid_spec_and_files_pass(self):
        validate_spec(SPEC, 'python')
        validate_checks(CHECKS, SPEC, language='python')
        validate_files(FILES, SPEC, 'python')

    def test_go_checks_are_not_accepted_for_python(self):
        with self.assertRaisesRegex(MasaError, 'unauthorized'):
            validate_checks([{**CHECKS[0], 'operation': 'go_test'}], SPEC, language='python')

    def test_layout_rules(self):
        def broken(**change):
            spec = copy.deepcopy(SPEC)
            spec.update(change)
            return spec
        extra = SPEC['files'] + [{'path': 'helper.py', 'purpose': 'x'}]
        for spec, message in ((broken(files=extra), 'subdirectories'),
                              (broken(files=[f for f in SPEC['files'] if f['path'] != 'calc/__init__.py']), '__init__'),
                              (broken(module='Calc'), 'module name'),
                              (broken(entrypoint='main.py'), 'entrypoint must be calc/__main__.py')):
            with self.subTest(message=message), self.assertRaisesRegex(MasaError, message):
                validate_spec(spec, 'python')
        with self.assertRaisesRegex(MasaError, 'pyproject.toml'):
            validate_files({**FILES, 'pyproject.toml': '[project]' + NL + 'name = "other"' + NL}, SPEC, 'python')
        with self.assertRaisesRegex(MasaError, 'third-party'):
            validate_files({**FILES, 'calc/app.py': 'import numpy' + NL + APP}, SPEC, 'python')

    def test_the_pack_describes_itself_and_its_operations(self):
        self.assertEqual(pack.op_id('test'), 'py_test')
        self.assertEqual(pack.kind_of('py_static'), 'static')
        self.assertIsNone(pack.op_id('format'))
        self.assertFalse(pack.supports('index'))
        self.assertTrue(pack.is_test('tests/test_app.py') and pack.is_source('calc/app.py') and not pack.is_source('tests/test_app.py'))
        self.assertEqual(pack.entrypoint(SPEC), 'calc/__main__.py')


UNITTEST_OUTPUT = '''test_boom (test_app.AppTests.test_boom) ... ERROR
test_wrong (test_app.AppTests.test_wrong) ... FAIL

======================================================================
ERROR: test_boom (test_app.AppTests.test_boom)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:/work/workspaces/0123456789abcdef0123456789abcdef/tests/test_app.py", line 20, in test_boom
    total("x")
  File "C:/work/workspaces/0123456789abcdef0123456789abcdef/calc/app.py", line 2, in total
    return sum(int(x) for x in text.split())
ValueError: invalid literal for int() with base 10: 'x'

======================================================================
FAIL: test_wrong (test_app.AppTests.test_wrong)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "C:/work/workspaces/0123456789abcdef0123456789abcdef/tests/test_app.py", line 12, in test_wrong
    self.assertEqual(total("1 2"), 4)
AssertionError: 3 != 4

----------------------------------------------------------------------
Ran 2 tests in 0.001s
'''
IMPORT_FAILURE = '''======================================================================
ERROR: test_app (unittest.loader._FailedTest.test_app)
----------------------------------------------------------------------
ImportError: Failed to import test module: test_app
Traceback (most recent call last):
  File "C:/Python312/Lib/unittest/loader.py", line 396, in _find_test_path
    module = self._get_module_from_name(name)
  File "C:/work/workspaces/0123456789abcdef0123456789abcdef/tests/test_app.py", line 4, in <module>
    from calc.app import missing
ImportError: cannot import name 'missing' from 'calc.app' (C:/work/workspaces/0123456789abcdef0123456789abcdef/calc/app.py)
'''
PYTEST_OUTPUT = '''============================= FAILURES =============================
_____________________________ test_total ______________________________
tests/test_app.py:5: in test_total
    assert total("1 2") == 4
E   assert 3 == 4
E    +  where 3 = total('1 2')
'''


class FailureParsingTests(unittest.TestCase):
    def parse(self, text, operation='py_test'):
        return pack.parse_failures([(operation, {'stdout': '', 'stderr': text, 'exit_code': 1, 'status': 'completed'})])

    def test_unittest_errors_and_assertions_get_the_right_owner(self):
        items = self.parse(UNITTEST_OUTPUT)
        self.assertEqual([(i['owner'], i['kind'], i['path'], i['line']) for i in items],
                         [('implementation', 'compile', 'calc/app.py', 2), ('ambiguous', 'assertion', 'tests/test_app.py', 12)])
        self.assertEqual(pack.assertion_of(items[1]['message']), {'call': 'self.assertEqual(total("1 2"), 4)', 'got': '3', 'want': '4'})

    def test_a_failing_test_import_is_a_test_setup_problem(self):
        items = self.parse(IMPORT_FAILURE)
        self.assertEqual([(i['owner'], i['kind'], i['path']) for i in items], [('test', 'setup', 'tests/test_app.py')])
        self.assertTrue(pack.needs_test_revision(IMPORT_FAILURE, [('py_test', {'stdout': '', 'stderr': IMPORT_FAILURE, 'exit_code': 1, 'status': 'completed'})]))
        self.assertFalse(pack.needs_test_revision(UNITTEST_OUTPUT))

    def test_pytest_assertions_and_static_check_lines_are_parsed(self):
        items = self.parse(PYTEST_OUTPUT)
        self.assertEqual((items[0]['kind'], items[0]['path'], items[0]['line']), ('assertion', 'tests/test_app.py', 5))
        self.assertEqual(pack.assertion_of(items[0]['message'])['want'], '4')
        lint = self.parse('calc/app.py:3:5: SyntaxError: invalid syntax' + NL, 'py_static')
        self.assertEqual([(i['owner'], i['kind'], i['path'], i['line']) for i in lint], [('implementation', 'compile', 'calc/app.py', 3)])

    def test_signatures_ignore_line_numbers(self):
        a = pack.assertion_signature([('py_test', {'stdout': '', 'stderr': UNITTEST_OUTPUT, 'exit_code': 1})])
        b = pack.assertion_signature([('py_test', {'stdout': '', 'stderr': UNITTEST_OUTPUT.replace('line 12', 'line 99'), 'exit_code': 1})])
        self.assertEqual(a, b)
        self.assertTrue(a)
        self.assertTrue(pack.failure_signature([('py_test', {'stdout': '', 'stderr': UNITTEST_OUTPUT, 'exit_code': 1})]))


@unittest.skipUnless(RUNNER.is_file() and pack.probe().ok, 'built runner and a usable Python interpreter are required')
class EndToEndTests(unittest.TestCase):
    """脚本化模型 + 真实运行器：规划 → 批准 → 逐文件生成（带就地检查）→ 批准 → 真实 unittest 验证 → Gate。 Scripted models + the real runner."""

    def run_flow(self, files):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            runner = Runner(RUNNER)
            try:
                planning = ProjectPlanning(store, runner)
                parent = planning.generate(FakeModel(), 'sum integers', language='python')
                meta = store.run(parent)['data']['project_plan']
                self.assertEqual(store.run(parent)['data']['language'], 'python')
                self.assertEqual(meta['template'], 'py-cli')
                planning.approve(parent, {'spec_ref': meta['spec_ref'], 'checks_ref': meta['checks_ref'], 'spec': SPEC, 'checks': CHECKS})
                service = ProjectGeneration(store, runner)
                developer = LocalFake(files)
                draft = service.generate(parent, developer)
                self.assertTrue(all(c.get('language') == 'python' for c in developer.contexts))
                meta = store.run(draft)['data']['project_plan']
                generated = store.read(meta['files_ref'])
                verified = service.approve(draft, {'files_ref': meta['files_ref'], 'files': generated})
                result = Runtime(store, runner).execute(verified)
                operations = [store.read(c['request_ref'])['operation'] for c in store.tools(verified)]
                return result, operations, developer, generated, store.run(verified)['data']
            finally:
                store.close()

    def test_a_python_project_is_generated_verified_and_passes_the_gate(self):
        result, operations, developer, generated, data = self.run_flow(FILES)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(sorted(operations), ['py_static', 'py_test'])
        self.assertEqual(data['language'], 'python')
        self.assertEqual(data['profile']['language']['id'], 'python')
        self.assertIn('python_executable', data['profile'])
        self.assertEqual(generated['calc/app.py'], APP)

    def test_a_wrong_expectation_fails_the_gate_with_a_parsed_assertion(self):
        wrong = {**FILES, 'tests/test_app.py': TEST.replace('"1 2 3"), 6', '"1 2 3"), 7')}
        result, operations, developer, generated, data = self.run_flow(wrong)
        self.assertEqual(result['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
