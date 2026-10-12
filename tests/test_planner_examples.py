"""Planner 示例：校验、提示词只在开关打开时变化、并入检查计划、真实程序核对。 Planner examples: validation, prompts change only when the switch is on, merging into the check plan, the real-program check."""
import copy
import io
import tempfile
import unittest
from pathlib import Path

from masa.agents.protocol import instruction_for, validate_response
from masa.agents.schemas import response_schema
from masa.application.checks.examples import check_examples
from masa.application.generation import ProjectGeneration
from masa.application.planning import ProjectPlanning
from masa.domain import examples as ex
from masa.domain.models import MasaError
from masa.domain.proposals import validate_checks, validate_spec
from masa.infrastructure.runner import Runner
from masa.infrastructure.store import Store
from masa.languages.registry import registry
from masa.runtime.engine import Runtime
from test_python_pack import APP, CHECKS, FILES, MAIN, RUNNER, SPEC, FakeModel, LocalFake

NL = chr(10)
pack = registry.get('python')
EXAMPLES = [
    {'name': 'two lines', 'args': [], 'stdin': '1 2' + NL + '3' + NL, 'stdout': '6' + NL, 'exit_code': 0},
    {'name': 'empty input', 'args': [], 'stdin': '', 'stdout': '0' + NL, 'exit_code': 0},
    {'name': 'no final newline', 'args': [], 'stdin': '4 5', 'stdout': '9' + NL, 'exit_code': 0},
]
SPEC_X = {**SPEC, 'summary': 'sum integers (design rationale only)', 'examples': EXAMPLES}


class ValidationTests(unittest.TestCase):
    def test_examples_are_validated_strictly(self):
        self.assertEqual(ex.validate_examples(EXAMPLES), EXAMPLES)
        bad = [EXAMPLES[:2], EXAMPLES * 4, [{**EXAMPLES[0], 'extra': 1}] + EXAMPLES[1:], [{**EXAMPLES[0], 'exit_code': '0'}] + EXAMPLES[1:],
               [{**EXAMPLES[0], 'exit_code': 300}] + EXAMPLES[1:], [EXAMPLES[0], {**EXAMPLES[1], 'name': 'TWO LINES'}, EXAMPLES[2]], [{**EXAMPLES[0], 'args': ['x'] * 9}] + EXAMPLES[1:],
               [{**EXAMPLES[0], 'stdout': 'x' * 401}] + EXAMPLES[1:], 'no']
        for value in bad:
            with self.subTest(value=str(value)[:40]), self.assertRaises(MasaError):
                ex.validate_examples(value)

    def test_the_spec_accepts_examples_as_an_optional_field(self):
        validate_spec(SPEC, 'python')  # 旧规格没有 examples：照样合法 / an old spec without examples stays valid
        validate_spec(SPEC_X, 'python')
        with self.assertRaises(MasaError):
            validate_spec({**SPEC, 'examples': EXAMPLES[:1]}, 'python')
        with self.assertRaises(MasaError):
            validate_spec({**SPEC, 'surprise': 1}, 'python')

    def test_examples_become_cli_cases_first_and_the_testers_cases_give_way(self):
        cases = ex.cases_from_examples(EXAMPLES)
        self.assertEqual(cases[0]['name'], 'example: two lines')
        self.assertIn("'6\\n'", cases[0]['expected'])
        self.assertIn('exit code 0', cases[0]['expected'])
        tester_cases = [{'name': f't{i}', 'input': 'x', 'expected': 'y', 'level': 'unit'} for i in range(14)]
        plan = [{'operation': 'py_test', 'purpose': 'p', 'acceptance_indices': [0], 'cases': tester_cases}, {'operation': 'py_static', 'purpose': 'p', 'acceptance_indices': [0]}]
        merged = ex.merge_into_checks(plan, SPEC_X, 'py_test')
        self.assertEqual(len(merged[0]['cases']), 16)  # 示例 3 个 + Tester 的 13 个 / 3 examples + 13 of the Tester's
        self.assertEqual([c['name'] for c in merged[0]['cases'][:3]], ['example: two lines', 'example: empty input', 'example: no final newline'])
        self.assertNotIn('cases', merged[1])
        validate_checks(merged, SPEC_X, require_coverage=False, language='python')
        self.assertEqual(ex.merge_into_checks(plan, SPEC, 'py_test'), plan)  # 没有示例：原样 / no examples: untouched

    def test_comparison_is_byte_exact_except_trailing_whitespace_noise(self):
        example = EXAMPLES[0]
        self.assertTrue(ex.compare(example, {'status': 'completed', 'exit_code': 0, 'stdout': '6\r\n'})['ok'])
        self.assertFalse(ex.compare(example, {'status': 'completed', 'exit_code': 0, 'stdout': '7\n'})['ok'])
        self.assertIn('exit code 1', ex.compare(example, {'status': 'completed', 'exit_code': 1, 'stdout': '6\n', 'stderr': 'boom'})['reason'])
        self.assertIn('did not complete', ex.compare(example, {'status': 'timeout'})['reason'])


class PromptTests(unittest.TestCase):
    def test_prompts_are_unchanged_unless_examples_are_on(self):
        base = {'purpose': 'project_planner', 'goal': 'g', 'language': 'python'}
        plain = instruction_for(base)
        on = instruction_for({**base, 'examples_required': True})
        self.assertTrue(on.startswith(plain))
        self.assertIn('EXAMPLES RULE', on)
        self.assertNotIn('EXAMPLES RULE', plain)
        for purpose in ('project_developer', 'project_repair', 'project_test_revision', 'project_tester'):
            without = instruction_for({'purpose': purpose, 'spec': SPEC, 'language': 'python'})
            with_ = instruction_for({'purpose': purpose, 'spec': SPEC_X, 'language': 'python'})
            self.assertTrue(with_.startswith(without), purpose)
            self.assertIn('authoritative', with_)
            self.assertEqual('do NOT repeat them' in with_, purpose == 'project_tester')

    def test_the_planner_must_deliver_examples_when_they_are_required(self):
        context = {'purpose': 'project_planner', 'goal': 'g', 'language': 'python', 'examples_required': True}
        with self.assertRaisesRegex(MasaError, 'examples'):
            validate_response(context, copy.deepcopy(SPEC), '')
        self.assertEqual(validate_response(context, copy.deepcopy(SPEC_X), '')['examples'], EXAMPLES)
        validate_response({k: v for k, v in context.items() if k != 'examples_required'}, copy.deepcopy(SPEC), '')  # 没开：不要求 / off: not required

    def test_the_structured_output_schema_asks_for_examples_only_when_required(self):
        base = {'purpose': 'project_planner', 'language': 'python'}
        self.assertNotIn('examples', response_schema(base)['properties'])
        schema = response_schema({**base, 'examples_required': True})
        self.assertIn('examples', schema['required'])
        self.assertEqual(schema['properties']['examples']['minItems'], 3)


@unittest.skipUnless(RUNNER.is_file() and pack.probe().ok, 'built runner and a usable Python interpreter are required')
class FlowTests(unittest.TestCase):
    def build(self, store, runner, examples=True):
        class Planner(FakeModel):
            def respond(self, context):
                self.contexts.append(copy.deepcopy(context))
                if context['purpose'] == 'project_planner':
                    return copy.deepcopy(SPEC_X if context.get('examples_required') else SPEC)
                return copy.deepcopy(CHECKS)
        planner = Planner()
        planning = ProjectPlanning(store, runner)
        parent = planning.generate(planner, 'sum integers', language='python', examples=examples)
        meta = store.run(parent)['data']['project_plan']
        return planning, planner, parent, meta

    def test_examples_flow_from_the_planner_into_the_check_plan_and_the_developer_context(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            runner = Runner(RUNNER)
            try:
                planning, planner, parent, meta = self.build(store, runner)
                self.assertTrue(planner.contexts[0].get('examples_required'))
                checks = store.read(meta['checks_ref'])
                self.assertEqual([c['name'] for c in checks[0]['cases'][:3]], ['example: two lines', 'example: empty input', 'example: no final newline'])
                self.assertEqual([e['type'] for e in store.events(parent) if e['type'] == 'planner_examples'], ['planner_examples'])
                planning.approve(parent, {'spec_ref': meta['spec_ref'], 'checks_ref': meta['checks_ref'], 'spec': store.read(meta['spec_ref']), 'checks': checks})
                developer = LocalFake()
                ProjectGeneration(store, runner).generate(parent, developer)
                self.assertEqual(developer.contexts[0]['spec']['examples'], EXAMPLES)
            finally:
                store.close()

    def test_the_default_planner_call_does_not_ask_for_examples(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                planning, planner, parent, meta = self.build(store, Runner(RUNNER), examples=False)
                self.assertNotIn('examples_required', planner.contexts[0])
                self.assertNotIn('examples', store.read(meta['spec_ref']))
            finally:
                store.close()

    def test_the_real_program_is_checked_against_the_examples_after_success_and_a_wrong_example_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            runner = Runner(RUNNER)
            try:
                planning, planner, parent, meta = self.build(store, runner)
                spec = store.read(meta['spec_ref'])
                planning.approve(parent, {'spec_ref': meta['spec_ref'], 'checks_ref': meta['checks_ref'], 'spec': spec, 'checks': store.read(meta['checks_ref'])})
                service = ProjectGeneration(store, runner)
                draft = service.generate(parent, LocalFake())
                dmeta = store.run(draft)['data']['project_plan']
                verified = service.approve(draft, {'files_ref': dmeta['files_ref'], 'files': store.read(dmeta['files_ref'])})
                self.assertEqual(Runtime(store, runner).execute(verified)['status'], 'succeeded')
                report = check_examples(store, runner, verified, spec)
                self.assertEqual((report['total'], report['passed']), (3, 3), report)
                self.assertEqual([e['payload']['passed'] for e in store.events(verified) if e['type'] == 'examples_checked'], [3])
                wrong = {**spec, 'examples': [EXAMPLES[0], {**EXAMPLES[1], 'stdout': 'zero' + NL}, EXAMPLES[2]]}
                again = check_examples(store, runner, verified, wrong)
                self.assertEqual(again['passed'], 2)
                self.assertIn("want 'zero'", [r for r in again['results'] if not r['ok']][0]['reason'])
                self.assertIsNone(check_examples(store, runner, verified, SPEC))  # 没有示例：什么都不做 / no examples: nothing to do
            finally:
                store.close()


@unittest.skipUnless(RUNNER.is_file() and pack.probe().ok, 'built runner and a usable Python interpreter are required')
class StdinTests(unittest.TestCase):
    def test_run_application_passes_stdin_and_rejects_oversized_input(self):
        from masa.application.applications import run_application
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            runner = Runner(RUNNER)
            try:
                planning = ProjectPlanning(store, runner)
                parent = planning.generate(FakeModel(), 'sum integers', language='python')
                meta = store.run(parent)['data']['project_plan']
                planning.approve(parent, {'spec_ref': meta['spec_ref'], 'checks_ref': meta['checks_ref'], 'spec': SPEC, 'checks': CHECKS})
                service = ProjectGeneration(store, runner)
                draft = service.generate(parent, LocalFake())
                dmeta = store.run(draft)['data']['project_plan']
                verified = service.approve(draft, {'files_ref': dmeta['files_ref'], 'files': store.read(dmeta['files_ref'])})
                Runtime(store, runner).execute(verified)
                result = run_application(store, runner, verified, [], lambda: False, '10 20 30')
                self.assertEqual((result['status'], result['exit_code'], result['stdout'].strip()), ('completed', 0, '60'))
                with self.assertRaises(MasaError):
                    run_application(store, runner, verified, [], lambda: False, 'x' * 9000)
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
