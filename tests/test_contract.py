"""包间接口契约：什么时候需要、怎么校验、生成后核对承诺的导出；规划阶段的接入（默认关闭、失败只是忽略、进入批准内容）。全部用假模型。
Package interface contract: when it is wanted, how it is validated, the check of promised exports after generation; the planning hook (off by default, a failure is ignored, it enters the approved content). Fake models only."""
import copy
import tempfile
import unittest
from pathlib import Path

from masa.application.checks.contract import contract_problem_messages, internal_dirs, validate_contract, wanted
from masa.application.orchestration.routing import validate_policy
from masa.application.planning import ProjectPlanning
from masa.domain.models import MasaError
from masa.infrastructure.store import Store

from test_project_plan import CHECKS, SPEC
from test_runtime import FakeExecutor


def multi_spec(packages=('parser', 'store', 'rules')):
    spec = copy.deepcopy(SPEC)
    spec['files'] = [{'path': 'go.mod', 'purpose': 'module'}, {'path': 'cmd/app/main.go', 'purpose': 'entry'}]
    for name in packages:
        spec['files'] += [{'path': f'internal/{name}/{name}.go', 'purpose': name}, {'path': f'internal/{name}/{name}_test.go', 'purpose': name + ' tests'}]
    return spec


GOOD = {'packages': [{'dir': 'internal/parser', 'exports': [{'name': 'Parse', 'signature': 'func Parse(line string) (Command, error)'}]},
                     {'dir': 'internal/store', 'exports': [{'name': 'Store', 'signature': 'type Store struct'}, {'name': 'Apply', 'signature': 'func (s *Store) Apply(c Command) string'}]}],
        'rules': ['amount is checked before the account exists']}


class LogicTests(unittest.TestCase):
    def test_a_contract_is_wanted_only_for_three_or_more_internal_packages(self):
        self.assertFalse(wanted(SPEC))
        self.assertFalse(wanted(multi_spec(('a', 'b'))))
        self.assertTrue(wanted(multi_spec()))
        self.assertEqual(internal_dirs(multi_spec()), ['internal/parser', 'internal/rules', 'internal/store'])

    def test_validation_accepts_a_good_contract_and_rejects_every_bad_shape(self):
        spec = multi_spec()
        self.assertEqual(validate_contract(GOOD, spec)['packages'][1]['exports'][1]['name'], 'Apply')
        bad = {
            'not an object': [], 'no packages': {'packages': [], 'rules': []}, 'unknown package': {'packages': [{'dir': 'internal/ghost', 'exports': [{'name': 'X', 'signature': 'func X()'}]}], 'rules': []},
            'cmd is not a package': {'packages': [{'dir': 'cmd/app', 'exports': [{'name': 'X', 'signature': 'func X()'}]}], 'rules': []},
            'repeated package': {'packages': [GOOD['packages'][0], GOOD['packages'][0]], 'rules': []},
            'lower-case export': {'packages': [{'dir': 'internal/parser', 'exports': [{'name': 'parse', 'signature': 'func parse()'}]}], 'rules': []},
            'no exports': {'packages': [{'dir': 'internal/parser', 'exports': []}], 'rules': []},
            'too many exports': {'packages': [{'dir': 'internal/parser', 'exports': [{'name': f'F{i}', 'signature': 'func F()'} for i in range(13)]}], 'rules': []},
            'long signature': {'packages': [{'dir': 'internal/parser', 'exports': [{'name': 'F', 'signature': 'x' * 241}]}], 'rules': []},
            'too many rules': {**GOOD, 'rules': ['r'] * 13}, 'rule not a string': {**GOOD, 'rules': [5]},
        }
        for name, raw in bad.items():
            with self.subTest(name):
                with self.assertRaises(MasaError):
                    validate_contract(raw, spec)

    def test_the_promised_exports_are_checked_when_the_last_file_of_a_package_is_written(self):
        contract = validate_contract(GOOD, multi_spec())
        planned = [i['path'] for i in multi_spec()['files']]
        files = {'internal/parser/parser.go': 'package parser\n\nfunc Parse(line string) (Command, error) { return Command{}, nil }\n',
                 'internal/store/store.go': 'package store\n\ntype Store struct{}\n'}
        found = contract_problem_messages(files, ['internal/parser/parser.go', 'internal/store/store.go'], contract, planned)
        self.assertNotIn('internal/parser/parser.go', found)  # 承诺的都写了 / everything promised is there
        self.assertIn('Apply', found['internal/store/store.go'])  # 缺 Apply，点名 / Apply is missing and named
        self.assertNotIn('Store —', found['internal/store/store.go'])
        files['internal/store/store.go'] += '\nfunc (s *Store) Apply(c Command) string { return "ok" }\n'
        self.assertEqual(contract_problem_messages(files, ['internal/store/store.go'], contract, planned), {})
        self.assertEqual(contract_problem_messages(files, ['internal/store/store.go'], None, planned), {})  # 没有契约：什么都不查 / no contract: nothing is checked
        self.assertEqual(contract_problem_messages({'internal/store/store_test.go': 'x'}, ['internal/store/store_test.go'], contract, planned), {})  # 测试文件不查 / tests are not checked


class Provider:
    profile = {'provider': 'test', 'model': 'fake'}
    usage = {'total_tokens': 10}

    def __init__(self, spec, contract=None, fail=False):
        self.spec, self.contract, self.fail, self.log, self.contexts = spec, contract, fail, [], []

    def respond(self, context):
        purpose = context['purpose']
        self.log.append(purpose)
        self.contexts.append(context)
        if purpose == 'project_planner':
            return copy.deepcopy(self.spec)
        if purpose == 'package_contract':
            if self.fail:
                raise RuntimeError('architect down')
            return copy.deepcopy(self.contract)
        checks = copy.deepcopy(CHECKS)
        return checks


class PlanningTests(unittest.TestCase):
    def plan(self, provider, enabled):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(Path(temp.name))
        self.addCleanup(store.close)
        rid = ProjectPlanning(store, FakeExecutor()).generate(provider, 'goal', contract_provider=provider if enabled else None)
        return store, rid

    def test_off_by_default_makes_no_extra_call(self):
        provider = Provider(multi_spec(), GOOD)
        store, rid = self.plan(provider, enabled=False)
        self.assertNotIn('package_contract', provider.log)
        self.assertNotIn('contract', store.run(rid)['data']['project_plan'])
        self.assertFalse(validate_policy({})['package_contract'])

    def test_a_small_project_never_gets_a_contract_even_when_enabled(self):
        provider = Provider(SPEC, GOOD)
        store, rid = self.plan(provider, enabled=True)
        self.assertNotIn('package_contract', provider.log)

    def test_the_contract_reaches_the_tester_the_plan_and_the_approved_content(self):
        provider = Provider(multi_spec(), GOOD)
        store, rid = self.plan(provider, enabled=True)
        plan = store.run(rid)['data']['project_plan']
        self.assertEqual(plan['contract']['rules'], ['amount is checked before the account exists'])
        tester = next(c for c in provider.contexts if c['purpose'] == 'project_tester')
        self.assertEqual(tester['contract'], plan['contract'])
        self.assertEqual([e['type'] for e in store.events(rid) if e['type'] == 'package_contract'], ['package_contract'])
        ProjectPlanning(store, FakeExecutor()).approve(rid, {'spec_ref': plan['spec_ref'], 'checks_ref': plan['checks_ref'], 'spec': store.read(plan['spec_ref']),
                                                             'checks': store.read(plan['checks_ref']), 'review_mode': 'automatic'})
        approval = store.read(store.run(rid)['data']['project_plan']['approval_ref'])
        self.assertEqual(approval['contract'], plan['contract'])  # Developer / Repair 的上下文会展开 **approved / spread into the Developer and Repair contexts

    def test_a_failing_or_invalid_contract_is_ignored_and_planning_goes_on(self):
        for provider in (Provider(multi_spec(), GOOD, fail=True), Provider(multi_spec(), {'packages': [], 'rules': []})):
            store, rid = self.plan(provider, enabled=True)
            plan = store.run(rid)['data']['project_plan']
            self.assertEqual(plan['status'], 'awaiting_review')
            self.assertNotIn('contract', plan)
            self.assertTrue([e for e in store.events(rid) if e['type'] == 'package_contract_failed'])


if __name__ == '__main__':
    unittest.main()
