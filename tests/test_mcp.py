"""MCP 服务：工具函数的单测（输入校验、白名单、上限、真实 Gate 的裁决、路由预览、报告、花钱的工具默认拒绝），以及用官方 SDK 在进程内调用一次。
MCP server: unit tests of the tool functions (input validation, whitelist, limits, the real Gate verdict, route preview, report, the paid tool refused by default) plus one in-process call through the official SDK."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from masa.domain.models import MasaError
from masa.interfaces import mcp_server as mcp
from test_runtime import FakeExecutor

GOOD = {'go.mod': 'module demo\n\ngo 1.27.0\n', 'calc/calc.go': 'package calc\n\nfunc Add(a, b int) int { return a + b }\n'}


class ValidationTests(unittest.TestCase):
    def test_illegal_inputs_are_rejected(self):
        bad = {
            'empty': {}, 'not an object': ['go.mod'], 'no go.mod': {'a.go': 'package a\n'},
            'absolute path': {**GOOD, '/etc/passwd.go': 'x'}, 'windows drive': {**GOOD, 'C:/x.go': 'x'}, 'parent traversal': {**GOOD, '../x.go': 'x'},
            'hidden traversal': {**GOOD, 'a/../../x.go': 'x'}, 'empty segment': {**GOOD, 'a//x.go': 'x'}, 'not go': {**GOOD, 'run.sh': 'rm -rf /'},
            'binary content': {**GOOD, 'x.go': 'a\x00b'}, 'non-string content': {**GOOD, 'x.go': 5},
            'too large file': {**GOOD, 'big.go': 'x' * (mcp.MAX_FILE_BYTES + 1)},
            'too many files': {**GOOD, **{f'f{i}.go': 'package a\n' for i in range(mcp.MAX_FILES)}},
        }
        for name, files in bad.items():
            with self.subTest(name):
                with self.assertRaises(MasaError):
                    mcp.check_files(files)
        total = {'go.mod': GOOD['go.mod'], **{f'f{i}.go': 'x' * (mcp.MAX_FILE_BYTES - 1) for i in range(5)}}
        with self.assertRaises(MasaError):
            mcp.check_files(total)  # 总量超限 / total size over the limit

    def test_only_whitelisted_checks_are_accepted(self):
        self.assertEqual(mcp.check_operations(None), ['go_test', 'go_vet', 'go_fmt_check'])
        self.assertEqual(mcp.check_operations(['go_vet', 'go_vet']), ['go_vet'])
        for bad in ([], 'go_test', ['go_test; rm -rf /'], ['go build'], [1]):
            with self.assertRaises(MasaError):
                mcp.check_operations(bad)


class VerifyTests(unittest.TestCase):
    def test_the_gate_verdict_comes_from_the_ledger_of_an_isolated_run(self):
        executor = FakeExecutor(exit_code=0)
        result = mcp._verify(GOOD, ['go_test', 'go_vet'], 60, executor=executor)
        self.assertTrue(result['passed'])
        self.assertEqual((result['verdict'], executor.calls), ('succeeded', 2))
        self.assertEqual([c['operation'] for c in result['checks']], ['go_test', 'go_vet'])
        failing = mcp._verify(GOOD, None, 60, executor=FakeExecutor(exit_code=1))
        self.assertFalse(failing['passed'])
        self.assertEqual(len(failing['checks']), 3)
        self.assertTrue(all(c['exit_code'] == 1 for c in failing['checks']))

    def test_output_is_truncated_and_the_timeout_is_bounded(self):
        class Noisy(FakeExecutor):
            def execute(self, request, workspace, cancelled):
                result = super().execute(request, workspace, cancelled)
                result['stdout'] = 'x' * 50_000
                return result
        result = mcp._verify(GOOD, ['go_test'], 60, executor=Noisy())
        self.assertLess(len(result['checks'][0]['stdout']), mcp.MAX_OUTPUT_CHARS + 100)
        self.assertIn('truncated', result['checks'][0]['stdout'])
        for bad in (0, 121, 'x', True):
            with self.assertRaises(MasaError):
                mcp._verify(GOOD, None, bad, executor=FakeExecutor())

    def test_the_workspace_is_a_temporary_directory_that_is_removed(self):
        seen = []

        class Spy(FakeExecutor):
            def execute(self, request, workspace, cancelled):
                seen.append(Path(workspace))
                seen.append((Path(workspace) / 'calc/calc.go').is_file())
                return super().execute(request, workspace, cancelled)
        mcp._verify(GOOD, ['go_test'], 60, executor=Spy())
        self.assertTrue(seen[1] is True and not seen[0].exists())  # 执行时文件在；之后目录被删除 / files existed while running; the directory is gone afterwards

    @unittest.skipUnless(all(p.is_file() for p in mcp.runner_paths()), 'the real runner and Go toolchain are not installed')
    def test_real_runner_passes_and_fails(self):
        passing = {**GOOD, 'calc/calc_test.go': 'package calc\n\nimport "testing"\n\nfunc TestAdd(t *testing.T) {\n\tif Add(1, 2) != 3 {\n\t\tt.Fatal("bad")\n\t}\n}\n'}
        self.assertTrue(mcp.verify_project(passing, ['go_test', 'go_vet', 'go_fmt_check'])['passed'])
        broken = {**passing, 'calc/calc.go': 'package calc\n\nfunc Add(a, b int) int { return a - b }\n'}
        result = mcp.verify_project(broken, ['go_test'])
        self.assertFalse(result['passed'])
        self.assertNotEqual(result['checks'][0]['exit_code'], 0)


class RouteAndReportTests(unittest.TestCase):
    CANDIDATES = [{'id': 'small', 'level': 1, 'priority': 0, 'model_type': 'local', 'model': 'qwen', 'context_limit': 16384, 'max_output_tokens': 4096},
                  {'id': 'big', 'level': 2, 'priority': 0, 'model_type': 'cloud', 'model': 'pro', 'context_limit': 262144, 'max_output_tokens': 8192}]

    def test_route_preview_is_the_pure_policy(self):
        first = mcp.route_preview('project_developer', candidates=self.CANDIDATES)
        self.assertEqual((first['action'], first['model'], first['reason']), ('use', 'qwen', 'start_lowest_eligible'))
        top = mcp.route_preview('project_planner', candidates=self.CANDIDATES)  # 规划者默认直接用最高等级 / the planner starts at the top
        self.assertEqual(top['model'], 'pro')
        escalated = mcp.route_preview('project_developer', history=[{'candidate': 'small', 'level': 1}, {'candidate': 'small', 'level': 1}], candidates=self.CANDIDATES)
        self.assertEqual((escalated['model'], escalated['escalated']), ('pro', True))
        for bad in ('no_such_role', ):
            with self.assertRaises(MasaError):
                mcp.route_preview(bad, candidates=self.CANDIDATES)
        with self.assertRaises(MasaError):
            mcp.route_preview('project_developer', history=['x'], candidates=self.CANDIDATES)
        with self.assertRaises(MasaError):
            mcp.route_preview('project_developer', candidates=[])

    def test_report_is_read_only_and_validates_the_id(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'MASA_STATE': temp}):
            from masa.infrastructure.store import Store
            from masa.runtime.engine import Runtime
            source = Path(temp) / 'src'
            source.mkdir()
            (source / 'go.mod').write_text(GOOD['go.mod'])
            store = Store(Path(temp))
            runtime = Runtime(store, FakeExecutor())
            from masa.domain.models import Budget
            run_id = runtime.create(source, 'report me', Budget())
            runtime.execute(run_id)
            store.close()
            self.assertIn(run_id, mcp.get_run_report(run_id))
            for bad in ('../x', 'abc', 5, 'g' * 32):
                with self.assertRaises(MasaError):
                    mcp.get_run_report(bad)


class PaidToolTests(unittest.TestCase):
    def test_the_benchmark_is_refused_unless_explicitly_allowed(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('MASA_MCP_ALLOW_BENCH', None)
            with self.assertRaises(MasaError) as caught:
                mcp.run_benchmark()
            self.assertIn('MASA_MCP_ALLOW_BENCH', str(caught.exception))
        with patch.dict(os.environ, {'MASA_MCP_ALLOW_BENCH': '1'}):
            for kwargs in ({'max_cloud_tokens': 10 ** 9}, {'max_cloud_tokens': 10}, {'max_minutes': 999}, {'suite': 'nope'}):
                with self.assertRaises(MasaError):
                    mcp.run_benchmark(**kwargs)


class SdkTests(unittest.TestCase):
    def setUp(self):
        try:
            import mcp as sdk  # noqa: F401
        except ImportError:
            self.skipTest('the optional mcp SDK is not installed')

    def test_tools_are_registered_and_callable_through_the_sdk(self):
        server = mcp.build_server()
        listed = asyncio.run(server.list_tools())
        self.assertEqual({t.name for t in listed}, set(mcp.TOOLS))
        with patch('masa.interfaces.mcp_server.runner_paths', return_value=(Path('missing'), Path('missing'))):
            with self.assertRaises(Exception) as caught:  # 缺 runner 时给出清晰的错误，而不是崩溃 / a clear error when the runner is missing, not a crash
                asyncio.run(server.call_tool('verify_project', {'files': GOOD}))
            self.assertIn('runner', str(caught.exception))
        result = asyncio.run(server.call_tool('route_preview', {'role': 'project_planner', 'candidates': RouteAndReportTests.CANDIDATES}))
        self.assertIn('pro', str(result))


if __name__ == '__main__':
    unittest.main()
