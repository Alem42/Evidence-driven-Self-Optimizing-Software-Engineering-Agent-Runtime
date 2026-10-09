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
                self.assertEqual(run['data']['budget']['model_calls'], 3 * 3)  # 上限 = 文件数 ×(1+语法就地重写次数) / ceiling = files x (1 + in-stage syntax rewrites)
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


GOFMT_HOME = Path(__file__).resolve().parents[1] / '.tools' / 'go' / 'bin'


BROKEN_MAIN = 'package main' + chr(10) * 2 + 'func main() {' + chr(10) + chr(9) + 'println("oops' + chr(10) + '}' + chr(10)


class SyntaxExecutor(FakeExecutor):
    go_executable = GOFMT_HOME / 'go.exe'


class BrokenOnceDeveloper(LocalDeveloper):
    """第一次给 main.go 写出“字符串没结束”的文件（真实任务里弱模型把提示词抄进了字符串），之后写对。
    The first main.go has an unterminated string (a weak model once copied prompt text into a literal); later answers are right."""
    def respond(self, context):
        path = context.get('target_path')
        broken = [c for c in self.contexts if c.get('target_path') == path]
        if path == 'cmd/app/main.go' and not broken:
            self.contexts.append(copy.deepcopy(context))
            return validate_response(context, {'files': {path: BROKEN_MAIN}}, '')
        return super().respond(context)


class TestsFirstTests(unittest.TestCase):
    approved_parent = LocalGenerationTests.approved_parent

    def test_a_stronger_test_author_writes_the_tests_first_and_the_implementer_sees_them(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                parent = self.approved_parent(store)
                weak, strong = LocalDeveloper(), LocalDeveloper()
                strong.profile = {'provider': 'cloud', 'model': 'fake-strong'}
                strong.config = {'model_type': 'cloud', 'protocol': 'openai'}
                strong.snapshot = {'version': 1, 'mode': 'fixed', 'profile_id': 'strong', 'config': strong.config}  # 不同的冻结配置：真实任务里曾因此被拒绝 / a different frozen config was rejected in a real task
                rid = ProjectGeneration(store, FakeExecutor()).generate(parent, weak, test_provider=strong)
                self.assertEqual([c['target_path'] for c in strong.contexts], ['internal/app/app_test.go'])
                self.assertTrue(all(not c['target_path'].endswith('_test.go') for c in weak.contexts))
                first_impl = weak.contexts[0]
                self.assertIn('internal/app/app_test.go', first_impl['previous_files'])  # 实现者看得到冻结的测试 / the implementer sees the frozen tests
                self.assertEqual(store.read(store.run(rid)['data']['project_plan']['files_ref']), FILES)
            finally:
                store.close()


class SloppyImportsDeveloper(LocalDeveloper):
    """main.go 漏了 import "fmt" 还多 import 了 "bufio"——弱模型最常见的两种编译错误。
    main.go forgets "fmt" and imports an unused "bufio": the two most common weak-model compile errors."""
    def respond(self, context):
        if context.get('target_path') == 'cmd/app/main.go':
            self.contexts.append(copy.deepcopy(context))
            nl = chr(10)
            source = 'package main' + nl + nl + 'import "bufio"' + nl + nl + 'func main() { fmt.Println("hello") }' + nl
            return validate_response(context, {'files': {'cmd/app/main.go': source}}, '')
        return super().respond(context)


class LeakingDeveloper(LocalDeveloper):
    """第一次把提示词字段写进了测试文件（真实任务里发生过），之后写对。 First answer leaks a prompt field into the test file (it happened); later answers are right."""
    def respond(self, context):
        path = context.get('target_path')
        if path == 'internal/app/app_test.go' and not [c for c in self.contexts if c.get('target_path') == path]:
            self.contexts.append(copy.deepcopy(context))
            source = FILES[path].replace('wrong value', 'previous_attempt_error: wrong value')
            return validate_response(context, {'files': {path: source}}, '')
        return super().respond(context)



class VacuousTestDeveloper(LocalDeveloper):
    """第一次的测试文件只有 t.Log、永远不会失败；之后写对。 The first test file only logs and can never fail; later answers are right."""
    def respond(self, context):
        path = context.get('target_path')
        if path == 'internal/app/app_test.go' and not [c for c in self.contexts if c.get('target_path') == path]:
            self.contexts.append(copy.deepcopy(context))
            nl = chr(10)
            source = 'package app' + nl + nl + 'import "testing"' + nl + nl + 'func TestValue(t *testing.T) { t.Log(Value()) }' + nl
            return validate_response(context, {'files': {path: source}}, '')
        return super().respond(context)



class ReadingMainDeveloper(LocalDeveloper):
    """第一次的 main.go 自己读 stdin（评测里 wc/lru 的真实问题）；之后写成薄入口。 The first main.go reads stdin itself (the real wc/lru bug); later answers are thin."""
    def respond(self, context):
        path = context.get('target_path')
        if path == 'cmd/app/main.go' and not [c for c in self.contexts if c.get('target_path') == path]:
            self.contexts.append(copy.deepcopy(context))
            nl = chr(10)
            source = 'package main' + nl + nl + 'import (' + nl + chr(9) + '"bufio"' + nl + chr(9) + '"fmt"' + nl + chr(9) + '"os"' + nl + ')' + nl + nl
            source += 'func main() {' + nl + chr(9) + 's := bufio.NewScanner(os.Stdin)' + nl + chr(9) + 'for s.Scan() {' + nl + chr(9) + chr(9) + 'fmt.Println(s.Text())' + nl + chr(9) + '}' + nl + '}' + nl
            return validate_response(context, {'files': {path: source}}, '')
        return super().respond(context)


class GhostImportDeveloper(LocalDeveloper):
    """第一次的 main.go 导入了规格里不存在的内部包（第一次 10 文件项目的真实问题）；之后写对。 The first main.go imports an internal package the spec lacks (the real problem of the first 10-file run); later answers are right."""
    def respond(self, context):
        path = context.get('target_path')
        if path == 'cmd/app/main.go' and not [c for c in self.contexts if c.get('target_path') == path]:
            self.contexts.append(copy.deepcopy(context))
            nl = chr(10)
            source = 'package main' + nl + nl + 'import (' + nl + chr(9) + '"os"' + nl + nl + chr(9) + '"example.com/task/internal/ghost"' + nl + ')' + nl + nl
            source += 'func main() { os.Exit(ghost.Run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr)) }' + nl
            return validate_response(context, {'files': {path: source}}, '')
        return super().respond(context)


@unittest.skipUnless((GOFMT_HOME / 'gofmt.exe').is_file() or (GOFMT_HOME / 'gofmt').is_file(), 'Go toolchain not installed')
class SyntaxGateTests(unittest.TestCase):
    approved_parent = LocalGenerationTests.approved_parent

    def test_a_file_that_does_not_parse_is_rewritten_in_stage_with_the_exact_error(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                parent = self.approved_parent(store)
                provider = BrokenOnceDeveloper()
                rid = ProjectGeneration(store, SyntaxExecutor()).generate(parent, provider)
                tries = [c for c in provider.contexts if c.get('target_path') == 'cmd/app/main.go']
                self.assertEqual(len(tries), 2)
                self.assertNotIn('previous_attempt_error', tries[0])
                self.assertRegex(tries[1]['previous_attempt_error'], r'cmd/app/main\.go:\d+:\d+')
                self.assertIn('not terminated', tries[1]['previous_attempt_error'])
                meta = store.run(rid)['data']['project_plan']
                self.assertEqual(store.read(meta['files_ref']), FILES)
                self.assertIn('initial:syntax1', [row['invocation_id'] for row in RoleRuntime(store).states(rid)])
            finally:
                store.close()

    def test_missing_and_unused_imports_are_fixed_without_asking_the_model_again(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                parent = self.approved_parent(store)
                provider = SloppyImportsDeveloper()
                rid = ProjectGeneration(store, SyntaxExecutor()).generate(parent, provider)
                files = store.read(store.run(rid)['data']['project_plan']['files_ref'])
                self.assertIn('"fmt"', files['cmd/app/main.go'])  # 补上 fmt、删掉 bufio / fmt added, bufio removed
                self.assertNotIn('bufio', files['cmd/app/main.go'])
                self.assertEqual(len([c for c in provider.contexts if c.get('target_path') == 'cmd/app/main.go']), 1)  # 没有再问模型 / no second model call
                fixed = [e['payload'] for e in store.events(rid) if e['type'] == 'imports_fixed']
                self.assertEqual(fixed[0]['files']['cmd/app/main.go'], ['remove bufio', 'add fmt'])
            finally:
                store.close()

    def test_prompt_text_in_a_generated_file_is_rewritten_in_stage_even_though_it_parses(self):
        # 提示词泄漏在语法上是合法的 Go（字符串里），所以必须有独立的确定性检查。 It is valid Go (inside a string), so only a dedicated check catches it.
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                parent = self.approved_parent(store)
                provider = LeakingDeveloper()
                rid = ProjectGeneration(store, SyntaxExecutor()).generate(parent, provider)
                tries = [c for c in provider.contexts if c.get('target_path') == 'internal/app/app_test.go']
                self.assertEqual(len(tries), 2)
                self.assertIn('harness prompt text', tries[1]['previous_attempt_error'])
                files = store.read(store.run(rid)['data']['project_plan']['files_ref'])
                self.assertNotIn('previous_attempt_error', files['internal/app/app_test.go'])
            finally:
                store.close()

    def test_a_test_file_that_can_never_fail_is_rewritten_before_it_is_frozen(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                parent = self.approved_parent(store)
                provider = VacuousTestDeveloper()
                rid = ProjectGeneration(store, SyntaxExecutor()).generate(parent, provider)
                tries = [c for c in provider.contexts if c.get('target_path') == 'internal/app/app_test.go']
                self.assertEqual(len(tries), 2)
                self.assertIn('no test ever fails', tries[1]['previous_attempt_error'])
                self.assertEqual(store.read(store.run(rid)['data']['project_plan']['files_ref']), FILES)
            finally:
                store.close()

    def test_a_main_go_that_reads_input_itself_is_rewritten_before_it_can_pass_unnoticed(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                parent = self.approved_parent(store)
                provider = ReadingMainDeveloper()
                rid = ProjectGeneration(store, SyntaxExecutor()).generate(parent, provider)
                tries = [c for c in provider.contexts if c.get('target_path') == 'cmd/app/main.go']
                self.assertEqual(len(tries), 2)
                self.assertIn('reads or parses input', tries[1]['previous_attempt_error'])
                self.assertEqual(store.read(store.run(rid)['data']['project_plan']['files_ref'])['cmd/app/main.go'], FILES['cmd/app/main.go'])
            finally:
                store.close()

    def test_an_import_of_a_package_the_spec_does_not_have_is_rewritten_in_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                parent = self.approved_parent(store)
                provider = GhostImportDeveloper()
                rid = ProjectGeneration(store, SyntaxExecutor()).generate(parent, provider)
                tries = [c for c in provider.contexts if c.get('target_path') == 'cmd/app/main.go']
                self.assertEqual(len(tries), 2)
                self.assertIn('example.com/task/internal/ghost', tries[1]['previous_attempt_error'])
                self.assertIn('example.com/task/internal/app', tries[1]['previous_attempt_error'])  # 列出真正能导入的包 / lists what can really be imported
                self.assertEqual(store.read(store.run(rid)['data']['project_plan']['files_ref'])['cmd/app/main.go'], FILES['cmd/app/main.go'])
            finally:
                store.close()

    def test_the_final_bundle_check_refuses_unparseable_files(self):
        generation = ProjectGeneration(None, SyntaxExecutor())
        nl = chr(10)
        self.assertIn('a.go', generation._syntax_errors({'a.go': 'package a' + nl + 'func ('}, ['a.go']))
        self.assertEqual(generation._syntax_errors({'a.go': 'package a' + nl}, ['a.go', 'go.mod']), {})
