"""真实 HTTP 协议边界的离线测试。 Offline tests of the real HTTP protocol boundary."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest

from masa.infrastructure.llm import ChatProvider
from masa.infrastructure.store import Store
from masa.domain.models import Budget, MasaError
from masa.runtime.engine import Runtime
from masa.infrastructure.settings import Settings
from test_runtime import FakeExecutor


class ChatTests(unittest.TestCase):
    def setUp(self):
        """本地假服务只模拟提供商，调用路径仍走 HTTP。 Mock only the provider, keeping real HTTP transport."""
        self.action = {"type": "tool_call", "operation": "go_test", "arguments": {}}
        self.status = 200
        self.finish = "stop"
        self.requests = []
        self.request_metadata = []
        self.envelope = None
        self.dynamic = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                outer.requests.append(payload)
                outer.request_metadata.append((self.path, self.headers.get('Authorization')))
                action = outer.action
                context = json.loads(payload["messages"][1]["content"])
                if outer.dynamic and context.get("tool_results"):
                    action = {"type": "final", "summary": "Everything passed!"}
                raw = (
                    outer.envelope
                    if outer.envelope is not None
                    else {
                        "choices": [
                            {
                                "finish_reason": outer.finish,
                                "message": {
                                    "content": json.dumps(action),
                                    "reasoning_content": "PRIVATE_REASONING",
                                },
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 40,
                            "completion_tokens": 10,
                            "total_tokens": 50,
                        },
                    }
                )
                self.send_response(outer.status)
                if outer.status == 302:
                    self.send_header("Location", "/redirect-target")
                self.end_headers()
                self.wfile.write(json.dumps(raw).encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.config = {
            "base_url": f"http://127.0.0.1:{self.server.server_port}",
            "model": "test-model",
            "token_parameter": "max_tokens",
            "thinking": "disabled",
        }
        self.provider = ChatProvider(self.config, "synthetic-private-key")
        self.context = {"operation": "go_test", "tool_results": []}

    def tearDown(self):
        """关闭本地服务，避免端口与线程泄漏。 Close the local server and join its thread."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_planner_and_tester_use_distinct_prompts_and_validated_contracts(self):
        from test_project_plan import SPEC, CHECKS
        self.action = SPEC
        spec = self.provider.respond({'purpose':'project_planner','goal':'CSV CLI'})
        self.assertEqual(spec, SPEC)
        self.assertIn('You are Planner', self.requests[-1]['messages'][0]['content'])
        self.action = {'checks':CHECKS}
        checks = self.provider.respond({'purpose':'project_tester','spec':spec,'goal':'CSV CLI'})
        self.assertEqual(checks,CHECKS)
        self.assertIn('You are Tester', self.requests[-1]['messages'][0]['content'])
        self.action = {'type':'tool_call','operation':'go_test','arguments':{}}
        with self.assertRaises(MasaError):
            self.provider.respond({'purpose':'project_planner','goal':'CSV CLI'})

    def test_repair_schema_rejects_test_rewrites(self):
        from test_project_generation import FILES
        self.action={'files':{'internal/app/app.go':'package app\n'}}
        context={'purpose':'project_repair','original_files':FILES}
        self.assertEqual(self.provider.respond(context),self.action['files'])
        self.assertIn('repairing',self.requests[-1]['messages'][0]['content'])
        self.action={'files':{'internal/app/app_test.go':'package app\n'}}
        with self.assertRaises(MasaError):self.provider.respond(context)

    def test_developer_only_returns_approved_file_set(self):
        from test_project_plan import SPEC
        from test_project_generation import FILES
        self.action={'files':FILES}
        self.assertEqual(self.provider.respond({'purpose':'project_developer','spec':SPEC}),FILES)
        self.assertIn('You are Developer',self.requests[-1]['messages'][0]['content'])
        self.action={'files':{**FILES,'surprise.go':'package surprise'}}
        with self.assertRaises(MasaError):
            self.provider.respond({'purpose':'project_developer','spec':SPEC})

    def test_usage_and_deepseek_parameters(self):
        """核对兼容参数与真实返回的用量。 Verify compatible parameters and returned usage."""
        self.assertEqual(self.provider.respond(self.context), self.action)
        self.assertEqual(self.provider.usage["total_tokens"], 50)
        self.assertIn("max_tokens", self.requests[0])
        self.assertEqual(self.requests[0]["thinking"], {"type": "disabled"})
        self.assertNotIn("synthetic-private-key", json.dumps(self.provider.profile))

    def test_generation_schema_is_separate_from_tool_actions(self):
        """生成模式只接受完整文件草稿，不能执行工具提案。 Generation accepts complete drafts, never tool actions."""
        context = {'purpose':'code_generation','goal':'Add','target':'solution.go','files':{}}
        with self.assertRaises(MasaError):
            self.provider.respond(context)
        self.action = {'type':'code_proposal','summary':'Add','content':'package solution\n'}
        self.assertEqual(self.provider.respond(context)['content'], 'package solution\n')
        self.action['extra'] = 'unexpected'
        with self.assertRaises(MasaError):
            self.provider.respond(context)

    def test_pasted_endpoint_and_key_whitespace_are_normalized(self):
        """支持完整端点与首尾空白密钥，仍拒绝内部换行。 Normalize pasted endpoint/key boundaries, rejecting embedded newlines."""
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp))
            settings.save({**self.config, 'base_url':self.config['base_url']+'/chat/completions/', 'api_key':' \nsynthetic-private-key\n '})
            provider = settings.provider()
            self.assertEqual(provider.config['base_url'], self.config['base_url'])
            self.assertEqual(provider.key, 'synthetic-private-key')
            with self.assertRaises(MasaError):
                settings.save({'api_key':'invalid\nkey'})

    def test_untrusted_actions_and_envelopes_are_rejected(self):
        """拒绝越权提案和异常响应结构。 Reject unauthorized actions and malformed envelopes."""
        for action in (
            {"type": "tool_call", "operation": "shell", "arguments": {}},
            {
                "type": "tool_call",
                "operation": "go_test",
                "arguments": {"command": "evil"},
            },
            {"type": "final", "summary": 42},
            {"type": "final", "summary": "ok", "extra": True},
            [],
        ):
            with self.subTest(action=action):
                self.action = action
                with self.assertRaises(MasaError):
                    self.provider.respond(self.context)
        for envelope in ([], {}, {"choices": []}, {"choices": [{"message": []}]}):
            self.envelope = envelope
            with self.assertRaises(MasaError):
                self.provider.respond(self.context)

    def test_ollama_native_uses_no_auth_and_normalizes_actual_usage(self):
        """原生本地请求真实走HTTP，保持JSON和用量契约。 Exercise native HTTP without auth while preserving JSON/usage contracts."""
        self.envelope={'done':True,'done_reason':'stop','message':{'content':'{"type":"final","summary":"local result"}','thinking':'PRIVATE'},
                       'prompt_eval_count':12,'eval_count':8}
        provider=ChatProvider({**self.config,'model_type':'local','protocol':'ollama','timeout_seconds':180})
        result=provider.respond(self.context)
        self.assertEqual(result['summary'],'local result')
        self.assertEqual(provider.usage['total_tokens'],20)
        self.assertFalse(self.requests[0]['stream']);self.assertEqual(self.requests[0]['format'],'json')
        self.assertEqual(self.requests[0]['options']['num_ctx'],8192)
        self.assertFalse(self.requests[0]['think'])
        self.assertEqual(self.request_metadata, [('/api/chat', None)])

    def test_received_invalid_json_has_safe_diagnostic_without_retry(self):
        """已收坏 JSON 保留用量，只报告位置，不回显内容或重试。 Keep usage and safe locations without exposing content or retrying."""
        self.envelope={'choices':[{'finish_reason':'stop','message':{'content':'{"private": bad}'}}],
                       'usage':{'prompt_tokens':4,'completion_tokens':3,'total_tokens':7}}
        with self.assertRaisesRegex(MasaError,'JSON invalid at line 1, column') as caught:
            self.provider.respond(self.context)
        self.assertNotIn('private',str(caught.exception))
        self.assertEqual(len(self.requests),1)
        self.assertEqual(self.provider.usage['total_tokens'],7)

    def test_truncation_errors_and_redirects_never_retry(self):
        """错误与重定向不产生隐式额外请求。 Errors and redirects never cause implicit requests."""
        self.finish = "length"
        with self.assertRaisesRegex(MasaError, "incomplete"):
            self.provider.respond(self.context)
        self.assertEqual(self.provider.usage["total_tokens"], 50)
        for code in (429, 302, 401):
            self.status = code
            before = len(self.requests)
            with self.assertRaisesRegex(MasaError, str(code)) as error:
                self.provider.respond(self.context)
            self.assertNotIn("synthetic-private-key", str(error.exception))
            self.assertEqual(len(self.requests), before + 1)
            self.assertIsNone(self.provider.usage)

    def test_gate_rejects_model_success_claim_against_failed_evidence(self):
        """模型成功声明不能覆盖失败证据。 A model success claim cannot override failed evidence."""
        self.dynamic = True
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "repo"
            source.mkdir()
            (source / "go.mod").write_text("module demo\ngo 1.27.0\n")
            store = Store(Path(temp) / "state")
            try:
                runtime = Runtime(store, FakeExecutor(exit_code=1), self.provider)
                rid = runtime.create(source, "verify", Budget())
                self.assertEqual(runtime.execute(rid)["status"], "failed")
                self.assertEqual(store.run(rid)["model_calls"], 2)
                events = json.dumps(store.events(rid))
                self.assertNotIn("PRIVATE_REASONING", events)
                self.assertNotIn("synthetic-private-key", events)
                self.assertIn("total_tokens", events)
            finally:
                store.close()

    def test_profile_management_and_resume_identity(self):
        """配置切换不改变已有运行身份或持久化密钥。 Switching profiles preserves run identity without persisting keys."""
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp))
            first = settings.save({**self.config, "api_key": "synthetic-private-key"})[
                "active_id"
            ]
            expected = settings.provider().profile
            second = settings.save(
                {
                    **self.config,
                    "model": "second",
                    "new": True,
                    "api_key": "another-key",
                }
            )["active_id"]
            self.assertNotEqual(first, second)
            self.assertEqual(settings.provider(expected=expected).profile, expected)
            settings.save({"id": first, "base_url": "https://example.com"})
            self.assertFalse(settings.public()["key_configured"])
            with self.assertRaises(MasaError):
                settings.provider(expected=expected)
            settings.save({"action": "delete", "id": first})
            self.assertEqual(len(settings.public()["profiles"]), 1)
            self.assertNotIn("another-key", settings.path.read_text())
            self.assertFalse(Settings(Path(temp)).public()["key_configured"])

    def test_runtime_rejects_early_final_repeated_tool_and_exhausted_budget(self):
        """模型不能提前结束、重复工具或突破预算。 Reject premature completion, tool replay and excess calls."""
        for case in ('early_final', 'repeat_tool', 'budget'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                source = Path(temp) / 'repo'
                source.mkdir()
                (source / 'go.mod').write_text('module demo\ngo 1.27.0\n')
                store = Store(Path(temp) / 'state')
                executor = FakeExecutor()
                self.action = ({'type': 'final', 'summary': 'passed'} if case == 'early_final'
                               else {'type': 'tool_call', 'operation': 'go_test', 'arguments': {}})
                try:
                    runtime = Runtime(store, executor, self.provider)
                    rid = runtime.create(source, 'verify', Budget(model_calls=1 if case == 'budget' else 4))
                    result = runtime.execute(rid)
                    self.assertEqual(result['status'], 'failed' if case == 'budget' else 'needs_attention')
                    self.assertIn({'early_final': 'final requires real tool evidence', 'repeat_tool': 'refusing duplicate',
                                   'budget': 'model_call_budget_exhausted'}[case], result['reason'])
                    self.assertEqual(executor.calls, 0 if case == 'early_final' else 1)
                    self.assertEqual(store.run(rid)['model_calls'], 2 if case == 'repeat_tool' else 1)
                finally:
                    store.close()
