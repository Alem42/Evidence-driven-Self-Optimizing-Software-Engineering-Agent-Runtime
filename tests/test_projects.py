"""项目导航与角色状态的证据边界。 Evidence boundaries for project navigation and role states."""
import tempfile
import unittest
from pathlib import Path

from masa.application.projects import Projects
from masa.application.planning import ProjectPlanning
from masa.application.generation import ProjectGeneration
from masa.infrastructure.store import Store
from masa.runtime.engine import Runtime
from masa.domain.models import MasaError
from test_project_plan import SPEC, CHECKS, PlannerProvider
from test_project_generation import FILES, DeveloperProvider
from test_runtime import FakeExecutor


class ProjectViewTests(unittest.TestCase):
    def test_live_roles_review_and_gate_remain_distinct(self):
        """在模型调用期间检查状态，并验证审批不会使 Gate 提前通过。 Observe in-flight roles and ensure approval never passes Gate."""
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                views = Projects(store)
                observed = []

                class ObservedPlanner(PlannerProvider):
                    def respond(self, context):
                        rid = views.catalog()[0]['current_run_id']
                        observed.append([(s['role'], s['status']) for s in views.view(rid)['stages']])
                        return super().respond(context)

                runner = FakeExecutor()
                planning = ProjectPlanning(store, runner)
                parent = planning.generate(ObservedPlanner(), 'Build a CLI')
                self.assertIn(('project_planner', 'running'), observed[0])
                self.assertIn(('project_tester', 'pending'), observed[0])
                self.assertIn(('project_planner', 'succeeded'), observed[1])
                self.assertIn(('project_tester', 'running'), observed[1])
                before = views.view(parent)['stages']
                self.assertEqual(next(s for s in before if s['kind'] == 'human')['status'], 'blocked')
                self.assertEqual(before[-1]['status'], 'pending')
                plan = store.run(parent)['data']['project_plan']
                planning.approve(parent, {'spec_ref': plan['spec_ref'], 'checks_ref': plan['checks_ref'], 'spec': SPEC, 'checks': CHECKS})
                generation = ProjectGeneration(store, runner)
                draft = generation.generate(parent, DeveloperProvider())
                meta = store.run(draft)['data']['project_plan']
                child = generation.approve(draft, {'files_ref': meta['files_ref'], 'files': FILES})
                self.assertEqual(views.view(child)['stages'][-1]['status'], 'pending')
                Runtime(store, runner).execute(child)
                final = views.view(child)
                self.assertEqual(final['id'], parent)
                self.assertEqual([s['role'] for s in final['stages']], ['project_planner', 'project_tester', 'review', 'project_developer', 'review', 'executor', 'gate'])
                self.assertTrue(all(s['status'] == 'succeeded' for s in final['stages']))
                self.assertEqual(len(views.catalog()), 1)
                self.assertEqual(len(final['versions']), 3)
            finally:
                store.close()

    def test_missing_project_and_cyclic_lineage_are_rejected(self):
        """损坏的来源链不能产生误导性的图。 Reject corrupt lineage instead of inventing a graph."""
        with self.assertRaises(MasaError):
            Projects.root_id('missing', {})
        with self.assertRaises(MasaError):
            Projects.root_id('a', {'a': {'data': {'parent_run_id': 'b'}}, 'b': {'data': {'parent_run_id': 'a'}}})
