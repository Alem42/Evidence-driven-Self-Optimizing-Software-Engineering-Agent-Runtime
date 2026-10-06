"""P3 调优器：补丁校验、评分、逐代淘汰、预算、覆盖项的传递。全部离线，评估器用假的（不调用任何模型）。
P3 tuner: patch validation, scoring, successive halving, budgets and how overrides are passed. Offline; the evaluator is fake (no model is called)."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from masa.bench.report import PASS, GATE_FAIL, aggregate
from masa.bench.runner import BenchRunner, count_mechanisms, resolve_config
from masa.domain.models import MasaError
from masa.infrastructure.settings import Settings
from masa.infrastructure.store import Store
from masa.roles import registry
from masa.roles.jsonschema import SchemaError, validate as validate_schema
from masa.tuning import space
from masa.tuning.apply import apply_policy
from masa.tuning.proposer import LLMProposer, RandomProposer
from masa.tuning.score import objective, summarize
from masa.tuning.search import search

from test_conductor import ASSERT_LINE, AssertExecutor, AssertWorld, ConductorCase, Scripted  # noqa: F401
from test_fix_flow import FixFlowCase  # noqa: F401


def records(passed, total=6, cloud=1000, false_pass=0):
    """合成的评测记录：前 passed 个通过，其余未通过。 Synthetic records: the first `passed` pass."""
    out = []
    for i in range(total):
        status = PASS if i < passed else GATE_FAIL
        out.append({'task': f't{i}', 'level': i, 'repeat': 1, 'status': status, 'cloud_tokens': cloud, 'local_tokens': 0, 'seconds': 10, 'calls': 5,
                    'rounds': 2, 'escalations': 0, 'mechanisms': {}})
    for i in range(false_pass):
        out[i]['status'] = 'false_pass'
    return out


def outcome(passed, cloud=1000, false_pass=0):
    return {'aggregate': aggregate(records(passed, cloud=cloud, false_pass=false_pass)), 'result_id': f'r{passed}'}


class PatchTests(unittest.TestCase):
    def test_legal_patches_apply_and_do_not_mutate_the_original(self):
        base = space.empty()
        new = space.apply(base, [{'op': 'set', 'path': 'policy.max_escalations', 'value': 3}, {'op': 'set', 'path': 'policy.attempts_per_level.fix', 'value': 2},
                                 {'op': 'drop_edge', 'from': 'classify', 'to': 'rewrite', 'when': 'rewrite_due'}])
        self.assertEqual(new['policy'], {'max_escalations': 3, 'attempts_per_level': {'fix': 2}})
        self.assertEqual(new['drop_edges'], [{'from': 'classify', 'to': 'rewrite', 'when': 'rewrite_due'}])
        self.assertEqual(base, space.empty())
        again = space.apply(new, [{'op': 'keep_edge', 'from': 'classify', 'to': 'rewrite', 'when': 'rewrite_due'}])
        self.assertEqual(again['drop_edges'], [])

    def test_every_illegal_patch_is_rejected(self):
        bad = {
            'unknown path': [{'op': 'set', 'path': 'policy.budget.max_cost', 'value': 5}],
            'out of range': [{'op': 'set', 'path': 'policy.max_escalations', 'value': 9}],
            'wrong type': [{'op': 'set', 'path': 'policy.conductor', 'value': 1}],
            'bool as int': [{'op': 'set', 'path': 'policy.max_escalations', 'value': True}],
            'unknown role': [{'op': 'set', 'path': 'policy.prefer_highest.project_developer', 'value': True}],
            'level out of range': [{'op': 'set', 'path': 'policy.start_level.project_planner', 'value': 7}],
            'always edge': [{'op': 'drop_edge', 'from': 'classify', 'to': 'repair', 'when': 'always'}],
            'guard not droppable': [{'op': 'drop_edge', 'from': 'classify', 'to': 'format', 'when': 'format_only'}],
            'edge does not exist': [{'op': 'drop_edge', 'from': 'repair', 'to': 'classify', 'when': 'rewrite_due'}],
            'too many operations': [{'op': 'set', 'path': 'policy.max_escalations', 'value': 1}] * 4,
            'empty': [],
            'not a list': {'op': 'set'},
            'unknown op': [{'op': 'exec', 'path': 'x'}],
            'extra field': [{'op': 'set', 'path': 'policy.max_escalations', 'value': 1, 'code': 'import os'}],
        }
        for name, patch_ in bad.items():
            with self.subTest(name):
                with self.assertRaises(space.PatchError):
                    space.apply(space.empty(), patch_)

    def test_materialize_gives_a_valid_graph_and_policy(self):
        config = space.apply(space.empty(), [{'op': 'drop_edge', 'from': 'classify', 'to': 'diagnose', 'when': 'needs_diagnosis'},
                                             {'op': 'set', 'path': 'policy.conductor', 'value': True}])
        overrides = space.materialize(config)
        self.assertTrue(overrides['policy']['conductor'])
        self.assertFalse([e for e in overrides['workflow']['edges'] if e['from'] == 'classify' and e.get('when') == 'needs_diagnosis'])
        self.assertIsNone(space.materialize(space.empty())['workflow'])  # 没有改图就不传图 / an unchanged graph is not passed

    def test_prefer_highest_and_start_level_are_data_edits(self):
        config = space.apply(space.empty(), [{'op': 'set', 'path': 'policy.prefer_highest.project_planner', 'value': False}])
        self.assertNotIn('project_planner', config['policy']['prefer_highest_roles'])
        self.assertIn('project_tester', config['policy']['prefer_highest_roles'])
        config = space.apply(config, [{'op': 'set', 'path': 'policy.start_level.project_developer', 'value': 2}])
        self.assertEqual(config['policy']['start_level_by_role'], {'project_developer': 2})

    def test_fingerprint_ignores_edge_order(self):
        a = {'policy': {}, 'drop_edges': [{'from': 'a', 'to': 'b', 'when': 'x'}, {'from': 'c', 'to': 'd', 'when': 'y'}]}
        b = {'policy': {}, 'drop_edges': list(reversed(a['drop_edges']))}
        self.assertEqual(space.fingerprint(a), space.fingerprint(b))

    def test_describe_and_catalog(self):
        self.assertEqual(space.describe(space.empty()), ['(与用户已保存的设置相同 / identical to the saved settings)'])
        catalog = space.catalog()
        self.assertIn('policy.max_escalations', catalog['set'])
        self.assertTrue(all(any(w in item for w in space.DROPPABLE) for item in catalog['drop_edge / keep_edge']))


class ScoreTests(unittest.TestCase):
    def test_objective_rewards_passes_and_penalises_cost_and_false_passes(self):
        four, five = aggregate(records(4)), aggregate(records(5))
        self.assertGreater(objective(five), objective(four))
        self.assertLess(objective(aggregate(records(4, cloud=50_000))), objective(four))  # 更贵更差 / dearer is worse
        self.assertLess(objective(aggregate(records(4, false_pass=1))), objective(four))
        self.assertEqual(objective(aggregate(records(0, total=3, cloud=0))), 0)
        self.assertIsNone(objective(aggregate([])))
        self.assertLess(objective(aggregate(records(0, total=3, cloud=20_000))), 0)  # 花了钱没有产出 / money spent, nothing delivered
        self.assertEqual(summarize(aggregate(records(4)))['passed'], 4)


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)

    @staticmethod
    def world(config, cap):
        """假世界：max_escalations=3 多通过 1 题；开指挥者多花 token。 Fake world: max_escalations=3 passes one more; the conductor costs tokens."""
        passed = 3 + (config['policy'].get('max_escalations') == 3)
        cost = 1000 + (20_000 if config['policy'].get('conductor') else 0)
        return outcome(passed, cloud=cost)

    def scripted(self, patches):
        queue = list(patches)

        class P:
            def propose(self, incumbent, history):
                return queue.pop(0) if queue else None
        return P()

    def test_the_baseline_is_evaluated_first_and_a_real_gain_wins(self):
        calls = []

        def evaluate(config, cap):
            calls.append(copy.deepcopy(config))
            return self.world(config, cap)
        good = [{'op': 'set', 'path': 'policy.max_escalations', 'value': 3}]
        bad = [{'op': 'set', 'path': 'policy.conductor', 'value': True}]
        result = search(evaluate, self.scripted([good, bad]), self.out, width=2, generations=1, tuning_id='t1')
        self.assertEqual(calls[0], space.empty())  # 第一个评估的是基线 / the baseline goes first
        self.assertEqual(result['verdict'], 'improved')
        self.assertEqual(result['best']['config']['policy'], {'max_escalations': 3})
        self.assertGreater(result['gain'], 2.0)
        folder = self.out / 't1'
        for name in ('gen_00.json', 'gen_01.json', 'best.json', 'state.json', 'report.md'):
            self.assertTrue((folder / name).is_file(), name)
        self.assertIn('policy.max_escalations = 3', (folder / 'report.md').read_text(encoding='utf-8'))
        self.assertIn('样本量', (folder / 'report.md').read_text(encoding='utf-8'))
        saved = json.loads((folder / 'best.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['best']['overrides']['policy'], {'max_escalations': 3})

    def test_a_gain_smaller_than_min_gain_is_noise_and_does_not_replace_the_baseline(self):
        def evaluate(config, cap):
            return outcome(4 + (config['policy'].get('stuck_after') == 5), cloud=1000)
        # 多通过 1 题只多约 +2 分以内（加权后），设置很高的 min_gain 就应该保持基线。 With a high min_gain the baseline stays.
        result = search(evaluate, self.scripted([[{'op': 'set', 'path': 'policy.stuck_after', 'value': 5}]]), self.out, width=1, generations=1, min_gain=50, tuning_id='t2')
        self.assertEqual(result['verdict'], 'no_improvement')
        self.assertEqual(result['best']['changes'], ['(与用户已保存的设置相同 / identical to the saved settings)'])

    def test_illegal_duplicate_and_missing_proposals_are_discarded_without_evaluation(self):
        calls = []

        def evaluate(config, cap):
            calls.append(config)
            return self.world(config, cap)
        proposals = [[{'op': 'set', 'path': 'policy.evil', 'value': 1}], [{'op': 'set', 'path': 'policy.max_escalations', 'value': 3}],
                     [{'op': 'set', 'path': 'policy.max_escalations', 'value': 3}], None]
        result = search(evaluate, self.scripted(proposals), self.out, width=2, generations=1, tuning_id='t3')
        self.assertEqual(len(calls), 2)  # 基线 + 一个合法候选 / baseline + the one legal candidate
        state = json.loads((self.out / 't3' / 'state.json').read_text(encoding='utf-8'))
        reasons = [d['reason'] for d in state['discarded']]
        self.assertTrue(any('not tunable' in r for r in reasons))
        self.assertTrue(any('duplicate' in r for r in reasons))
        self.assertGreaterEqual(result['discarded'], 3)

    def test_the_candidate_cap_stops_the_search(self):
        calls = []

        def evaluate(config, cap):
            calls.append(config)
            return self.world(config, cap)
        result = search(evaluate, RandomProposer(1), self.out, width=4, generations=3, max_candidates=3, tuning_id='t4')
        self.assertEqual(len(calls), 3)
        self.assertIn('候选数', result['stop_reason'])

    def test_the_token_budget_stops_the_search_and_the_cap_shrinks(self):
        caps = []

        def evaluate(config, cap):
            caps.append(cap)
            return outcome(3, cloud=10_000)  # 每个候选花 6 万 token / each candidate spends 60k
        result = search(evaluate, RandomProposer(2), self.out, width=4, generations=3, total_cloud_tokens=100_000, tuning_id='t5')
        self.assertLessEqual(len(caps), 3)
        self.assertEqual(caps[0], 100_000)
        self.assertLess(caps[1], caps[0])  # 剩余预算越来越少，传给评估器的上限随之缩小 / the cap passed on shrinks with the budget
        self.assertIn('token', result['stop_reason'])

    def test_the_time_budget_stops_the_search(self):
        now = [0.0]

        def clock():
            now[0] += 400
            return now[0]
        result = search(lambda c, cap: self.world(c, cap), RandomProposer(3), self.out, width=2, generations=3, total_minutes=10, clock=clock, tuning_id='t6')
        self.assertIn('时间', result['stop_reason'])

    def test_a_crashing_evaluator_propagates_instead_of_hiding_the_failure(self):
        def evaluate(config, cap):
            raise RuntimeError('benchmark failed')
        with self.assertRaises(RuntimeError):
            search(evaluate, RandomProposer(0), self.out, tuning_id='t7')

    def test_survivors_are_the_better_half(self):
        scores = {}

        def evaluate(config, cap):
            passed = 2 + (config['policy'].get('max_escalations') or 0) + (config['policy'].get('stuck_after') == 8)
            return outcome(min(passed, 6))
        proposals = [[{'op': 'set', 'path': 'policy.max_escalations', 'value': v}] for v in (1, 2, 3)]
        search(evaluate, self.scripted(proposals), self.out, width=3, generations=1, tuning_id='t8')
        gen = json.loads((self.out / 't8' / 'gen_01.json').read_text(encoding='utf-8'))
        self.assertEqual(len(gen['survivors']), 2)  # 4 个候选（含基线）淘汰一半 / 4 candidates including the baseline, half dropped
        self.assertIn('c4', gen['survivors'])  # 得分最高的 max_escalations=3 / the highest scorer


class ProposerTests(unittest.TestCase):
    def test_random_proposer_is_reproducible_and_only_proposes_legal_patches(self):
        a, b = RandomProposer(7), RandomProposer(7)
        first = [a.propose(space.empty(), []) for _ in range(10)]
        self.assertEqual(first, [b.propose(space.empty(), []) for _ in range(10)])
        for patch_ in first:
            space.apply(space.empty(), patch_)

    def test_llm_proposer_passes_the_space_and_history_and_survives_garbage(self):
        seen = []

        def ask(context):
            seen.append(context)
            return {'rationale': '升级多但没收益', 'patch': [{'op': 'set', 'path': 'policy.max_escalations', 'value': 1}]}
        proposer = LLMProposer(ask)
        history = [{'changes': ['x'], 'score': 3.5, 'summary': {'pass_rate': 0.5}}]
        self.assertEqual(proposer.propose(space.empty(), history)[0]['value'], 1)
        context = seen[0]
        self.assertEqual(context['purpose'], 'workflow_tuner')
        self.assertIn('policy.max_escalations', context['space']['set'])
        self.assertEqual(context['tried'], [{'changes': ['x'], 'score': 3.5}])
        self.assertEqual(proposer.last_rationale, '升级多但没收益')
        for garbage in (None, 'text', {'patch': 'nope'}, {}):
            self.assertIsNone(LLMProposer(lambda c, g=garbage: g).propose(space.empty(), []))

        def crash(context):
            raise MasaError('proposer down')
        self.assertIsNone(LLMProposer(crash).propose(space.empty(), []))

    def test_the_tuner_role_is_data_and_its_schema_bounds_the_patch(self):
        spec = registry.get('workflow_tuner')
        self.assertFalse(spec.routable)  # 不进任务路由 / never routed into a task
        self.assertEqual(spec.permissions['writes'], 'none')
        good = {'rationale': 'r', 'patch': [{'op': 'set', 'path': 'policy.max_escalations', 'value': 2}]}
        validate_schema(good, spec.output_schema)
        for bad in ({'rationale': 'r', 'patch': []}, {'rationale': 'r', 'patch': [{'op': 'rm -rf'}]}, {'rationale': 'r', 'patch': [{'op': 'set', 'code': 'x'}]},
                    {'rationale': 'r', 'patch': [{'op': 'set'}] * 4}, {'rationale': 'r', 'patch': [{'op': 'set', 'value': 'str'}]}):
            with self.assertRaises(SchemaError):
                validate_schema(bad, spec.output_schema)


class OverrideTests(ConductorCase):
    def test_a_workflow_override_reaches_the_engine(self):
        """去掉“需要诊断”这条边：断言失败不再走 Diagnoser，而是直接修复。 Dropping the needs_diagnosis edge sends an assertion failure straight to repair."""
        config = space.apply(space.empty(), [{'op': 'drop_edge', 'from': 'classify', 'to': 'diagnose', 'when': 'needs_diagnosis'}])
        workflow = space.materialize(config)['workflow']
        world, store, job, local, cloud = self.run_assert(request={'workflow': workflow})
        self.assertEqual(self.nodes(store)[0], ('classify', 'repair'))
        self.assertFalse([p for p in world.log if p.startswith('project_diagnoser')])
        world, store, job, local, cloud = self.run_assert()  # 没有覆盖：原行为 / no override: unchanged behaviour
        self.assertEqual(self.nodes(store)[0], ('classify', 'diagnose'))

    def test_the_http_body_cannot_carry_a_workflow(self):
        """workflow 只能由进程内调用方传入；评测请求里带 overrides/workflow 会被忽略。 resolve_config ignores workflow and overrides in a request."""
        config = resolve_config({'suite': 'canary', 'workflow': {'id': 'evil'}, 'overrides': {'policy': {'conductor': True}}})
        self.assertNotIn('workflow', config)
        self.assertNotIn('overrides', config)

    def run_assert(self, **kwargs):  # noqa: D401  让基类的 run_assert 接受 request / extend the base helper with `request`
        request = kwargs.pop('request', None)
        world = AssertWorld()
        local = Scripted('small', 'local', world)
        cloud = Scripted('big', 'cloud', world, diagnosis={'owner': 'test', 'rationale': 'r', 'implementation_instructions': '', 'test_instructions': 'x', 'expectation_checks': []})
        store, jobs, router = self.build(local, cloud, world, request=request)
        from masa.application.orchestration.coordinator import WorkflowCoordinator
        with patch('masa.intelligence.repair_context.build_repair_context', lambda s, e, r, f, ev, fb: (f, None)):
            WorkflowCoordinator(store, AssertExecutor(world), None, jobs['job'], router=router).run()
        return world, store, jobs['job'], local, cloud


class BenchOverrideTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_an_illegal_override_fails_when_the_runner_is_built(self):
        config = {**resolve_config({'suite': 'canary'}), 'overrides': {'policy': {'max_escalations': 99}, 'drop_edges': []}}
        with self.assertRaises(space.PatchError):
            BenchRunner(self.root, 'r', 'g', self.root, config)

    def test_overrides_become_the_policy_and_workflow_of_every_task_and_never_touch_settings(self):
        config = space.apply(space.empty(), [{'op': 'set', 'path': 'policy.conductor', 'value': True},
                                             {'op': 'drop_edge', 'from': 'classify', 'to': 'rewrite', 'when': 'rewrite_due'}])
        resolved = {**resolve_config({'suite': 'canary'}), 'overrides': config}
        runner = BenchRunner(self.root, 'r', 'g', self.root, resolved)
        seen = {}

        class Console:
            root = self.root
            settings = type('S', (), {'profiles': {'p': {}}})()
            go_path = Path('g')

            def start_autonomous_project_job(self, body, resume_job=None, *, workflow=None):
                seen.update(body=body, workflow=workflow)
                return {'job_id': 'j'}

            def project_job(self, job_id):
                return {'status': 'failed', 'run_id': None, 'error': 'stop here'}

            def _release_local_models(self):
                pass
        runner.console = Console()
        task = next(iter(resolve_config({'suite': 'canary'})['task_ids']))
        from masa.bench.tasks import BY_ID
        record = runner._run_task_for_real(BY_ID[task], 0, {'cloud_tokens': 1000, 'seconds': 60})
        self.assertEqual(record['status'], 'error')
        self.assertEqual(seen['body']['policy'], {'conductor': True})
        self.assertFalse([e for e in seen['workflow']['edges'] if e.get('when') == 'rewrite_due' and e['from'] == 'classify'])
        self.assertFalse((self.root / 'routing.json').exists())  # 用户的设置没被碰 / the user's settings are untouched

    def test_no_overrides_means_the_legacy_call(self):
        runner = BenchRunner(self.root, 'r', 'g', self.root, resolve_config({'suite': 'canary'}))
        self.assertIsNone(runner.overrides)

    def test_conductor_mechanisms_are_counted(self):
        store = Store(self.root / 's')
        self.addCleanup(store.close)
        report = {'process': [{'kind': 'conductor_decided', 'data': {}}, {'kind': 'conductor_decided', 'data': {}}, {'kind': 'conductor_rejected', 'data': {}},
                              {'kind': 'skeptic_verdict', 'data': {}}, {'kind': 'code_review', 'data': {}}]}
        counts = count_mechanisms(report, store, [])
        self.assertEqual((counts['conductor'], counts['conductor_rejected'], counts['skeptic'], counts['code_review']), (2, 1, 1, 1))


class ApplyTests(unittest.TestCase):
    def test_apply_policy_merges_into_the_saved_policy_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = Settings(root)
            settings.save_routing({'policy': {'max_escalations': 1, 'attempts_per_level': {'fix': 2}}})
            best = {'best': {'overrides': {'policy': {'conductor': True, 'attempts_per_level': {'generation': 3}}, 'workflow': None}}}
            (root / 'best.json').write_text(json.dumps(best), encoding='utf-8')
            policy = apply_policy(settings, root / 'best.json')
            self.assertTrue(policy['conductor'])
            self.assertEqual(policy['max_escalations'], 1)  # 没被调优改动的保持原值 / untouched values stay
            self.assertEqual((policy['attempts_per_level']['fix'], policy['attempts_per_level']['generation']), (2, 3))
            best['best']['overrides']['policy'] = {'max_escalations': 99}
            (root / 'best.json').write_text(json.dumps(best), encoding='utf-8')
            with self.assertRaises(MasaError):
                apply_policy(settings, root / 'best.json')
            self.assertEqual(settings.routing()['policy']['max_escalations'], 1)  # 不合法：什么都没写 / illegal: nothing written


if __name__ == '__main__':
    unittest.main()
