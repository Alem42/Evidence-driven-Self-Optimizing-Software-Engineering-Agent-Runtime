"""可执行的验收用例：结构化用例 → 确定性生成的黑盒测试文件。校验、推导、真的运行、审计同步。零 token（用本机的 Python 真的跑生成的测试）。
Executable acceptance cases: structured cases -> a deterministic black-box test file. Validation, derivation, a real run, audit sync. Zero tokens (the generated tests really run on the local Python)."""
import ast
import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from masa.agents.protocol import validate_response
from masa.application.planning import ProjectPlanning
from masa.domain.models import MasaError
from masa.infrastructure.store import Store
from masa.languages.python import acceptance
from masa.languages.registry import registry
from test_python_pack import APP, CHECKS, MAIN, RUNNER, SPEC, TEST
from masa.infrastructure.runner import Runner
from test_runtime import FakeExecutor

pack = registry.get('python')
CASES = [
    {'name': 'sum of three', 'accepts': [0], 'args': [], 'stdin': '1 2 3', 'stdout': '6\n', 'exit_code': 0},
    {'name': 'empty input', 'accepts': [0], 'args': [], 'stdin': '', 'stdout': '0\n', 'exit_code': 0},
    {'name': 'negative', 'accepts': [0], 'args': [], 'stdin': '5 -7', 'stdout': '-2\n', 'exit_code': 0},
    {'name': '中文名字 / non-ascii name', 'accepts': [0], 'args': [], 'stdin': '10\n20\n', 'stdout': '30\n', 'exit_code': 0},
]
CONTEXT = {'purpose': 'project_architect', 'language': 'python', 'acceptance_cases': True}


def answer(**changes):
    spec = copy.deepcopy(SPEC)
    spec['cases'] = copy.deepcopy(CASES)
    spec.update(changes)
    return {'spec': spec, 'checks': [{'operation': 'py_test', 'purpose': 'behaviour', 'acceptance_indices': []}, {'operation': 'py_static', 'purpose': 'syntax', 'acceptance_indices': [0]}],
            'assumptions': ['empty input sums to 0']}


class ValidationTests(unittest.TestCase):
    def test_a_structured_answer_is_normalised_and_the_check_cases_are_derived_from_it(self):
        out = validate_response(CONTEXT, answer(), '')
        spec = out['spec']
        self.assertEqual(len(spec['cases']), 4)
        self.assertIn(acceptance.PATH, [f['path'] for f in spec['files']])  # 生成的测试文件自动列进文件清单 / the generated test file is listed automatically
        test = next(c for c in out['checks'] if c['operation'] == 'py_test')
        self.assertEqual(len(test['cases']), 4)  # 与结构化用例一一对应 / one to one with the structured cases
        self.assertEqual(test['cases'][0], {'name': 'sum of three', 'input': 'args=[]; stdin="1 2 3"', 'expected': 'stdout="6\\n"; exit code 0', 'level': 'cli'})
        self.assertEqual(test['acceptance_indices'], [0])  # 覆盖关系来自用例的 accepts / coverage comes from the cases' accepts

    def test_without_the_flag_nothing_changes_and_cases_are_ignored(self):
        legacy = answer()
        legacy['checks'][0]['cases'] = [{'name': 'x', 'input': '1', 'expected': '1', 'level': 'cli'}]
        out = validate_response({**CONTEXT, 'acceptance_cases': False}, legacy, '')
        self.assertNotIn('cases', out['spec'])
        self.assertNotIn(acceptance.PATH, [f['path'] for f in out['spec']['files']])

    def test_a_missing_or_malformed_case_names_the_case_and_the_field(self):
        no_cases = answer()
        del no_cases['spec']['cases']
        with self.assertRaisesRegex(MasaError, 'needs spec.cases'):
            validate_response(CONTEXT, no_cases, '')
        for change, pattern in (
            (lambda c: c.__setitem__('exit_code', '0'), r'cases\[1\].exit_code'),
            (lambda c: c.__setitem__('args', 'x'), r'cases\[1\].args'),
            (lambda c: c.__setitem__('stdout', None), r'cases\[1\].stdout'),
            (lambda c: c.__setitem__('accepts', [5]), r'cases\[1\].accepts'),
            (lambda c: c.__setitem__('extra', 1), r'cases\[1\]: a case has exactly'),
            (lambda c: c.pop('stdin'), r'cases\[1\]: a case has exactly'),
        ):
            bad = answer()
            change(bad['spec']['cases'][1])
            with self.subTest(pattern=pattern), self.assertRaisesRegex(MasaError, pattern):
                validate_response(CONTEXT, bad, '')
        with self.assertRaisesRegex(MasaError, '3..24'):
            validate_response(CONTEXT, answer(cases=CASES[:2]), '')

    def test_go_has_no_acceptance_capability_and_keeps_its_old_contract(self):
        from masa.languages.registry import registry as languages
        self.assertFalse(languages.get('go').supports('acceptance'))
        self.assertTrue(pack.supports('acceptance'))


class GeneratedTestTests(unittest.TestCase):
    def spec(self):
        return validate_response(CONTEXT, answer(), '')['spec']

    def test_the_generated_file_is_valid_python_and_part_of_the_seed(self):
        spec = self.spec()
        source = acceptance.source(spec)
        ast.parse(source)
        self.assertEqual(pack.seed_files(spec)[acceptance.PATH], source)
        self.assertNotIn(acceptance.PATH, pack.seed_files(SPEC))  # 没有用例就没有这个文件 / no cases, no file

    def _run(self, files):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for path, text in files.items():
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text(text, encoding='utf-8', newline='\n')
            done = subprocess.run([sys.executable, '-E', '-s', '-B', '-X', 'utf8', '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=120)
            return done.returncode, done.stdout + done.stderr

    def project(self, app=APP):
        spec = self.spec()
        return {**pack.seed_files(spec), 'calc/__init__.py': '', 'calc/__main__.py': MAIN, 'calc/app.py': app}

    def test_a_correct_program_passes_every_case(self):
        code, output = self._run(self.project())
        self.assertEqual(code, 0, output)
        self.assertEqual(output.count(' ... ok'), 4)

    def test_a_wrong_program_fails_with_the_case_and_its_input_in_the_message(self):
        wrong = APP.replace('sum(int(item) for item in text.split())', 'abs(sum(int(item) for item in text.split()))')
        code, output = self._run(self.project(wrong))
        self.assertNotEqual(code, 0)
        self.assertIn('negative', output)
        self.assertIn('args=[]', output)
        self.assertIn('"5 -7"', output)

    def test_trailing_noise_is_ignored_but_content_is_not(self):
        padded = APP.replace('print(total(stdin.read()), file=stdout)', 'print(str(total(stdin.read())) + "   ", file=stdout); print("", file=stdout)')
        self.assertEqual(self._run(self.project(padded))[0], 0)
        wrong_content = APP.replace('print(total(stdin.read()), file=stdout)', 'print(total(stdin.read()) + 1, file=stdout)')
        self.assertNotEqual(self._run(self.project(wrong_content))[0], 0)


class PlanningTests(unittest.TestCase):
    class Provider:
        profile = {'provider': 'test', 'model': 'fake'}
        usage = {'total_tokens': 10}

        def __init__(self, suspect=()):
            self.suspect, self.contexts = set(suspect), []

        def respond(self, context):
            self.contexts.append(copy.deepcopy(context))
            if context['purpose'] == 'project_architect':
                return validate_response(context, answer(), '')
            if context['purpose'] == 'case_deriver':
                return {'answers': [{'index': c['index'], 'result': 'whatever'} for c in context['cases']]}
            return {'answers': [{'index': p['index'], 'same': p['index'] not in self.suspect} for p in context['pairs']]}

    def plan(self, provider, audit=False, flag=True):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(Path(temp.name))
        self.addCleanup(store.close)
        rid = ProjectPlanning(store, FakeExecutor()).generate(provider, 'sum', audit_provider=provider if audit else None, language='python', acceptance_cases=flag)
        return store, rid

    def test_the_flag_reaches_the_architect_and_the_cases_land_in_the_stored_spec(self):
        provider = self.Provider()
        store, rid = self.plan(provider)
        self.assertTrue(provider.contexts[0]['acceptance_cases'])
        plan = store.run(rid)['data']['project_plan']
        self.assertEqual(plan['status'], 'awaiting_review')
        self.assertEqual(len(store.read(plan['spec_ref'])['cases']), 4)
        self.assertEqual(plan['assumptions'], ['empty input sums to 0'])
        self.assertNotIn('acceptance_cases', self.Provider().__dict__)

    def test_without_the_flag_the_architect_is_not_asked_for_cases(self):
        provider = self.Provider()
        provider.respond = lambda context: validate_response({**CONTEXT, 'acceptance_cases': False}, {'spec': copy.deepcopy(SPEC), 'checks': copy.deepcopy(CHECKS)}, '')
        store, rid = self.plan(provider, flag=False)
        self.assertNotIn('cases', store.read(store.run(rid)['data']['project_plan']['spec_ref']))

    def test_cases_the_audit_drops_leave_the_structured_cases_too(self):
        provider = self.Provider(suspect={1})
        store, rid = self.plan(provider, audit=True)
        plan = store.run(rid)['data']['project_plan']
        self.assertEqual(plan['case_audit']['dropped'], [1])
        spec = store.read(plan['spec_ref'])
        self.assertEqual([c['name'] for c in spec['cases']], ['sum of three', 'negative', '中文名字 / non-ascii name'])
        checks = store.read(plan['checks_ref'])
        self.assertEqual([c['name'] for c in checks[0]['cases']], [c['name'] for c in spec['cases']])


@unittest.skipUnless(RUNNER.is_file() and pack.probe().ok, 'built runner and a usable Python interpreter are required')
class EndToEndTests(unittest.TestCase):
    """脚本化模型 + 真实运行器：Architect 的结构化用例 → 规格 → 逐文件生成（验收测试文件由程序放进去）→ 真实 unittest 验证 → Gate。 Scripted models + the real runner."""

    class Scripted:
        profile = {'provider': 'test', 'model': 'fake'}
        usage = {'total_tokens': 10}
        config = {'model_type': 'local'}
        snapshot = {'version': 1, 'mode': 'fixed', 'profile_id': 'local', 'config': {'model_type': 'local', 'protocol': 'ollama'}}

        def __init__(self, files):
            self.files, self.paths = files, []

        def respond(self, context):
            if context['purpose'] == 'project_architect':
                return validate_response(context, answer(), '')
            self.paths.append(context['target_path'])
            return validate_response(context, {'files': {context['target_path']: self.files[context['target_path']]}}, '')

    def run_flow(self, app):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            runner = Runner(RUNNER)
            try:
                from masa.application.generation import ProjectGeneration
                from masa.runtime.engine import Runtime
                model = self.Scripted({**{'calc/__main__.py': MAIN, 'calc/app.py': app}, 'tests/test_app.py': TEST})
                planning = ProjectPlanning(store, runner)
                parent = planning.generate(model, 'sum integers', language='python', acceptance_cases=True)
                meta = store.run(parent)['data']['project_plan']
                planning.approve(parent, {'spec_ref': meta['spec_ref'], 'checks_ref': meta['checks_ref'], 'spec': store.read(meta['spec_ref']), 'checks': store.read(meta['checks_ref'])})
                service = ProjectGeneration(store, runner)
                draft = service.generate(parent, model)
                meta = store.run(draft)['data']['project_plan']
                generated = store.read(meta['files_ref'])
                verified = service.approve(draft, {'files_ref': meta['files_ref'], 'files': generated})
                result = Runtime(store, runner).execute(verified)
                return result, generated, model.paths, store.tools(verified), store
            finally:
                store.close()

    def test_the_acceptance_file_comes_from_the_harness_and_the_gate_runs_it(self):
        result, generated, paths, tools, _ = self.run_flow(APP)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertIn(acceptance.PATH, generated)
        self.assertNotIn(acceptance.PATH, paths)  # 模型一次都没有被要求写它 / no model call was ever made to write it
        self.assertIn("'stdin': '5 -7'", generated[acceptance.PATH])  # 用例是文件里的数据，一行一个 / the cases are data in the file, one per line
        self.assertEqual(len(tools), 2)  # py_test 与 py_static 都跑了 / both checks ran

    def test_a_wrong_implementation_fails_the_gate_on_the_acceptance_case(self):
        wrong = APP.replace('sum(int(item) for item in text.split())', 'sum(abs(int(item)) for item in text.split())')
        result, generated, _, tools, store = self.run_flow(wrong)
        self.assertEqual(result['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
