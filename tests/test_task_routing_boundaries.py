"""Regression tests across the settings, job, ledger and provider boundaries."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from masa.agents.protocol import validate_response
from masa.application.console import Console
from masa.application.usage import task_report
from masa.domain.models import Budget, ContextOverflow, MasaError
from masa.infrastructure.llm import ChatProvider
from masa.infrastructure.store import Store
from masa.runtime.engine import Runtime
from test_acceptance_cases import CONTEXT, answer
from test_project_plan import SPEC, CHECKS
from test_runtime import FakeExecutor


class RoutingBoundaryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.console = Console(self.root, Path('runner'), Path('go'), Path.cwd())
        self.console._runner = FakeExecutor
        self.console._local_digests = lambda: {}
        self.settings = self.console.settings
        self.cloud = self.settings.save({'new': True, 'model': 'cloud', 'base_url': 'https://example.org',
                                         'api_key': 'fake'})['active_id']

    def launch(self, body):
        result = self.console.start_autonomous_project_job({'goal': 'sum integers', 'routing': 'ladder', **body})
        self.console.job_thread.join(10)
        self.assertFalse(self.console.job_thread.is_alive())
        return self.console.jobs[result['job_id']]

    def test_one_paid_request_covers_the_architect_and_all_auxiliary_roles(self):
        with patch.object(ChatProvider, 'respond', return_value=validate_response(CONTEXT, answer(), '')) as respond:
            job = self.launch({'runtime': 'v2repair', 'language': 'python', 'budget': {'max_model_calls': 1}})
        self.assertEqual(respond.call_count, 1)
        self.assertNotEqual(job['status'], 'running')
        store = Store(self.root)
        try:
            report = task_report(store, job['run_id'])
            self.assertEqual(len(report['calls']), 1)
            self.assertGreater(report['routing']['spend']['cloud_tokens'], 0)
            self.assertEqual(report['routing']['spend']['cloud_tokens'], report['calls'][0]['reserved_tokens'])
        finally:
            store.close()

    def test_saved_settings_and_task_overrides_take_precedence(self):
        self.settings.save_routing({'budget': {'max_model_calls': 2}, 'policy': {'test_audit': False,
                                    'assertion_referee': False, 'acceptance_cases': False}})
        body = {'runtime': 'v2repair', 'budget': {}}
        snapshot = self.console._task_routing(body)
        self.assertEqual(snapshot['budget']['max_model_calls'], 2)
        self.assertTrue(all(not snapshot['policy'][k] for k in ('test_audit', 'assertion_referee', 'acceptance_cases')))
        snapshot = self.console._task_routing({**body, 'policy': {'test_audit': True}, 'budget': {'max_model_calls': 3}})
        self.assertEqual(snapshot['budget']['max_model_calls'], 3)
        self.assertTrue(snapshot['policy']['test_audit'])

    def test_partial_saved_budget_keeps_unspecified_runtime_policy(self):
        baseline = self.console._task_routing({'runtime': 'v2repair'})
        self.settings.save_routing({'budget': {'max_model_calls': 2},
                                    'policy': {'attempts_per_level': {'fix': 4}}})
        snapshot = self.console._task_routing({'runtime': 'v2repair'})
        self.assertEqual(snapshot['budget']['max_model_calls'], 2)
        self.assertEqual(snapshot['policy']['test_audit'], baseline['policy']['test_audit'])
        self.assertEqual(snapshot['policy']['attempts_per_level'],
                         {**baseline['policy']['attempts_per_level'], 'fix': 4})

    def test_preview_uses_allowed_profiles_and_saved_role_policy(self):
        local = self.settings.save({'new': True, 'model_type': 'local', 'model': 'small',
                                    'base_url': 'http://127.0.0.1:11434', 'level': 1})['active_id']
        self.settings.save({'id': self.cloud, 'level': 2})
        self.settings.save_routing({'policy': {'start_level_by_role': {'project_repair': 2}}})
        preview = self.console.routing_preview({'model_ids': [local, self.cloud]})
        self.assertEqual(preview['project_developer'], 'small')
        self.assertEqual(preview['project_repair'], 'cloud')
        preview = self.console.routing_preview({'model_ids': [local]})
        self.assertEqual(preview['project_repair'], 'small')

    def test_last_overflow_ends_the_job(self):
        def respond(context, **kwargs):
            if context['purpose'] == 'project_architect':
                return {'spec': SPEC, 'checks': CHECKS}
            raise ContextOverflow(50000, 32768, 'cloud')
        with patch.object(ChatProvider, 'respond', side_effect=respond):
            job = self.launch({'policy': {'max_escalations': 0, 'attempts_per_level': {'generation': 1}}})
        self.assertEqual(job['status'], 'failed')
        self.assertIn('attempts exhausted', job['error'])

    def test_auto_repair_reuses_the_original_local_only_snapshot(self):
        local = self.settings.save({'new': True, 'model_type': 'local', 'model': 'small',
                                    'base_url': 'http://127.0.0.1:11434'})['active_id']
        frozen = self.console._task_routing({'model_ids': [local], 'budget': {'max_local_calls': 3}})
        store = Store(self.root)
        try:
            rid = Runtime(store, FakeExecutor()).create(Path('tests/fixtures/go-pass'), 'repair', Budget(),
                                                        project_bundle={'approval_ref': 'example'})
            store.set_status(rid, 'failed', 'check failed')
        finally:
            store.close()
        self.console.jobs['origin'] = {'mode': 'auto', 'status': 'completed', 'run_id': rid,
            'request': {'runtime': 'v2repair'}, 'routing': frozen}
        with patch('masa.application.orchestration.coordinator.WorkflowCoordinator.run'):
            result = self.console.auto_fix(rid, {'routing': 'ladder', 'api_profile_id': self.cloud})
            self.console.job_thread.join(10)
        job = self.console.jobs[result['job_id']]
        self.assertEqual(job['routing'], frozen)
        self.assertEqual(job['request']['runtime'], 'v2repair')

    def test_role_aliases_and_worker_authority_are_checked_before_transport(self):
        for allowed, purpose, extra in [(['project_planner'], 'project_architect', {}),
                                        (['project_repair'], 'package_worker', {'repair': {'scope': ['calc']}}),
                                        (['project_developer'], 'package_worker', {})]:
            provider = ChatProvider({'model': 'm', 'base_url': 'https://example.org', 'roles': allowed}, 'fake')
            with patch.object(provider, 'messages', side_effect=RuntimeError('authorized')):
                with self.assertRaisesRegex(RuntimeError, 'authorized'):
                    provider.respond({'purpose': purpose, **extra})
            if purpose == 'package_worker':
                wrong = {} if extra else {'repair': {'scope': ['calc']}}
                with self.assertRaisesRegex(MasaError, 'does not allow role'):
                    provider.respond({'purpose': purpose, **wrong})
