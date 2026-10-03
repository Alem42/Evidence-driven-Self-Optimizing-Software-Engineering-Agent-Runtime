"""本地逐文件生成的输出边界与中断恢复。 Local file-generation contracts and interruption recovery."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from masa.agents.protocol import instruction_for, validate_response
from masa.application.generation import ProjectGeneration
from masa.application.planning import ProjectPlanning
from masa.domain.models import Budget, MasaError
from masa.domain.proposals import validate_file_proposal
from masa.infrastructure.store import Store
from masa.runtime.engine import Runtime
from masa.runtime.roles import RoleRuntime
from test_project_plan import SPEC, CHECKS, PlannerProvider
from test_project_generation import FILES, DeveloperProvider
from test_role_recovery import Crash
from test_runtime import FakeExecutor


class LocalDeveloper:
    profile = {'provider': 'ollama-native', 'model': 'fake-local'}
    config = {'model_type': 'local', 'protocol': 'ollama'}
    snapshot = {'version': 1, 'mode': 'fixed', 'profile_id': 'local', 'config': config}
    usage = {'total_tokens': 10}

    def __init__(self):
        self.contexts = []

    def respond(self, context):
        self.contexts.append(copy.deepcopy(context))
        path = context.get('target_path')
        files = {path: FILES[path]} if path else dict(FILES)
        # 与真实 ChatProvider 使用相同解码后契约，测试无需调用模型。
        # Exercise the same decoded contract as ChatProvider without network calls.
        return validate_response(context, {'files': files}, '')


class LocalGenerationTests(unittest.TestCase):
    def test_cli_entrypoint_package_is_checked_before_publication(self):
        """真实 Qwen 生成的库包不能伪装为 CLI，注释内声明也不算。 Library packages and commented declarations cannot masquerade as CLI entrypoints."""
        from masa.domain.proposals import validate_files
        target=SPEC['entrypoint']
        for source in ('package app\nfunc main() {}','/* package main */\npackage app\n'):
            with self.subTest(source=source),self.assertRaisesRegex(MasaError,'package main'):
                validate_file_proposal({target:source},SPEC,target)
            with self.assertRaisesRegex(MasaError,'package main'):
                validate_files({**FILES,target:source},SPEC)
        source='\ufeff// Header\n/* package app */\npackage /* inline */ main\nfunc main() {}\n'
        self.assertEqual(validate_file_proposal({target:source},SPEC,target),{target:source})

    def approved_parent(self, store, spec=SPEC):
        """建立已批准规格，保持生成阶段测试独立。 Build approved specs independently of generation."""
        planning = ProjectPlanning(store, FakeExecutor())
        parent = planning.generate(PlannerProvider(), 'Build a small CLI')
        meta = store.run(parent)['data']['project_plan']
        planning.approve(parent, {'spec_ref': meta['spec_ref'], 'checks_ref': meta['checks_ref'],
                                 'spec': spec, 'checks': CHECKS})
        return parent

    def test_order_budget_progress_and_final_bundle(self):
        spec = copy.deepcopy(SPEC)
        # 故意先排列测试，执行策略仍先生成全部实现。
        # Deliberately put tests first; execution must still generate implementations first.
        spec['files'] = [spec['files'][0], spec['files'][3], spec['files'][1], spec['files'][2]]
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                parent = self.approved_parent(store, spec)
                provider = LocalDeveloper()
                rid = ProjectGeneration(store, FakeExecutor()).generate(parent, provider)
                run = store.run(rid)
                meta = run['data']['project_plan']
                self.assertEqual(meta['generation_mode'], 'files-v1')
                self.assertEqual(run['data']['budget']['model_calls'], 3)
                self.assertEqual(run['model_calls'], 3)
                self.assertEqual(run['tool_calls'], 0)
                self.assertEqual(meta['gen_progress'], {'completed': 3, 'total': 3, 'current': None})
                self.assertEqual(store.read(meta['files_ref']), FILES)
                self.assertEqual(store.read(meta['partial_files_ref']), FILES)
                paths = [context['target_path'] for context in provider.contexts]
                self.assertEqual(paths, ['cmd/app/main.go', 'internal/app/app.go', 'internal/app/app_test.go'])
                self.assertEqual(list(provider.contexts[0]['previous_files']), ['go.mod'])
                self.assertEqual(provider.contexts[-1]['previous_files']['internal/app/app.go'], FILES['internal/app/app.go'])
                calls = RoleRuntime(store).states(rid)
                self.assertEqual([row['invocation_id'] for row in calls], ['initial', 'file:2', 'file:3'])
                self.assertTrue(all(row['status'] == 'completed' for row in calls))
                events = store.events(rid)
                self.assertEqual(sum(row['type'] == 'project_file_generated' for row in events), 3)
                self.assertEqual(run['status'], 'paused')
                self.assertFalse((Path(run['data']['workspace']) / 'cmd/app/main.go').exists())
            finally:
                store.close()

    def test_saved_second_file_recovers_without_replaying_completed_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root)
            parent = self.approved_parent(store)
            service = ProjectGeneration(store, FakeExecutor())
            update = service.planning.update
            ids = []

            def interrupt(rid, meta, status, event):
                if event == 'project_file_generated' and meta['gen_progress']['completed'] == 2:
                    raise Crash()
                return update(rid, meta, status, event)

            provider = LocalDeveloper()
            with patch.object(service.planning, 'update', side_effect=interrupt):
                with self.assertRaises(Crash):
                    service.generate(parent, provider, on_created=ids.append)
            rid = ids[0]
            self.assertEqual(store.run(rid)['model_calls'], 2)
            store.close()
            store = Store(root)
            try:
                provider = LocalDeveloper()
                service = ProjectGeneration(store, FakeExecutor())
                self.assertEqual(service.generate(parent, provider, resume_id=rid), rid)
                self.assertEqual([c['target_path'] for c in provider.contexts], ['internal/app/app_test.go'])
                self.assertEqual(store.run(rid)['model_calls'], 3)
                self.assertEqual(store.read(store.run(rid)['data']['project_plan']['files_ref']), FILES)
            finally:
                store.close()

    def test_unknown_second_file_is_not_replayed_or_bypassed(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                parent = self.approved_parent(store)
                provider = LocalDeveloper()
                respond = provider.respond
                ids = []

                def interrupt(context):
                    if context['target_path'] == 'internal/app/app.go':
                        raise Crash()
                    return respond(context)

                with patch.object(provider, 'respond', side_effect=interrupt):
                    with self.assertRaises(Crash):
                        ProjectGeneration(store, FakeExecutor()).generate(parent, provider, on_created=ids.append)
                with patch.object(provider, 'respond') as call:
                    with self.assertRaisesRegex(MasaError, 'result unavailable'):
                        ProjectGeneration(store, FakeExecutor()).generate(parent, provider, resume_id=ids[0])
                    call.assert_not_called()
                self.assertEqual(store.run(ids[0])['model_calls'], 2)
                rows = RoleRuntime(store).states(ids[0])
                self.assertEqual([row['status'] for row in rows], ['completed', 'running'])
                self.assertEqual(store.run(ids[0])['data']['project_plan']['status'], 'failed')
            finally:
                store.close()

    def test_target_path_limits_and_prompt(self):
        target = 'internal/app/app.go'
        for value in ({target: 'package app', '../evil.go': 'bad'}, {'go.mod': 'bad'}, {},
                      {target: ''}, {target: 'a' * 60001}, {target: 'package app\x00'}):
            with self.subTest(value=list(value)):
                with self.assertRaises(MasaError):
                    validate_file_proposal(value, SPEC, target)
        with self.assertRaises(MasaError):
            validate_file_proposal({'go.mod': FILES['go.mod']}, SPEC, 'go.mod')
        self.assertEqual(validate_file_proposal({target: FILES[target]}, SPEC, target), {target: FILES[target]})
        context = {'purpose': 'project_developer', 'generation_mode': 'files-v1', 'spec': SPEC, 'target_path': target}
        with self.assertRaises(MasaError):
            validate_response(context, {'files': FILES}, '')
        instruction = instruction_for(context)
        self.assertIn('ONE file', instruction)
        self.assertIn('t.Fatal', instruction)
        self.assertIn('package directory', instruction)

    def test_cancel_after_first_file_prevents_next_call_and_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                parent = self.approved_parent(store)
                service = ProjectGeneration(store, FakeExecutor())
                update = service.planning.update
                ids = []

                def cancel(rid, meta, status, event):
                    if event == 'project_file_generated':
                        store.cancel(rid)
                    return update(rid, meta, status, event)

                provider = LocalDeveloper()
                with patch.object(service.planning, 'update', side_effect=cancel):
                    with self.assertRaisesRegex(MasaError, 'cancelled'):
                        service.generate(parent, provider, on_created=ids.append)
                run = store.run(ids[0])
                self.assertEqual(len(provider.contexts), 1)
                self.assertEqual(run['status'], 'cancelled')
                self.assertEqual(run['data']['project_plan']['status'], 'cancelled')
                self.assertNotIn('files_ref', run['data']['project_plan'])
                self.assertEqual(RoleRuntime(store).states(ids[0])[0]['status'], 'completed')
            finally:
                store.close()

    def test_bulk_legacy_and_cloud_are_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                parent = self.approved_parent(store)
                service = ProjectGeneration(store, FakeExecutor())
                cloud = service.generate(parent, DeveloperProvider())
                self.assertNotIn('generation_mode', store.run(cloud)['data']['project_plan'])
                self.assertEqual(store.run(cloud)['model_calls'], 1)
                run = store.run(parent)
                approved_ref = run['data']['project_plan']['approval_ref']
                approved = store.read(approved_ref)
                provider = LocalDeveloper()
                legacy = Runtime(store, FakeExecutor()).create(Path(run['data']['workspace']), run['data']['goal'],
                    Budget(model_calls=2, tool_calls=3, deadline_seconds=86400),
                    graph=Runtime.compile_project_checks(approved['spec'], approved['checks']),
                    parent_run_id=parent, project_plan={'kind': 'code', 'status': 'generating',
                        'spec_approval_ref': approved_ref, 'provider': provider.profile})
                self.assertEqual(service.generate(parent, provider, resume_id=legacy), legacy)
                self.assertNotIn('target_path', provider.contexts[0])
                self.assertEqual(store.run(legacy)['model_calls'], 1)
                self.assertEqual(store.read(store.run(legacy)['data']['project_plan']['files_ref']), FILES)
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
