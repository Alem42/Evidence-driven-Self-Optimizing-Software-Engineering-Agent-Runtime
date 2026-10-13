"""提示缓存计量：服务端报告的命中/未命中 token 进入用量、报告与评测；没报告就是“未知”，不是 0。
Prompt-cache metering: hit/miss tokens reported by the server flow into usage, reports and the benchmark; not reported means unknown, never zero."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from masa.application.planning import ProjectPlanning
from masa.application.usage import task_report
from masa.infrastructure.llm import ChatProvider, cache_usage
from masa.infrastructure.store import Store
from test_project_plan import PlannerProvider
from test_runtime import FakeExecutor

ANSWER = json.dumps({'verdict': 'ok'})


class CacheUsageShapeTests(unittest.TestCase):
    def test_deepseek_fields(self):
        self.assertEqual(cache_usage({'prompt_tokens': 100, 'prompt_cache_hit_tokens': 80, 'prompt_cache_miss_tokens': 20}),
                         {'prompt_cache_hit_tokens': 80, 'prompt_cache_miss_tokens': 20})

    def test_openai_compatible_cached_tokens_are_converted(self):
        self.assertEqual(cache_usage({'prompt_tokens': 100, 'prompt_tokens_details': {'cached_tokens': 64}}),
                         {'prompt_cache_hit_tokens': 64, 'prompt_cache_miss_tokens': 36})

    def test_not_reported_or_nonsense_stays_empty(self):
        for usage in ({}, {'prompt_tokens': 5}, {'prompt_cache_hit_tokens': 3}, {'prompt_cache_hit_tokens': -1, 'prompt_cache_miss_tokens': 2},
                      {'prompt_cache_hit_tokens': True, 'prompt_cache_miss_tokens': 2}, {'prompt_tokens': 5, 'prompt_tokens_details': {'cached_tokens': 9}},
                      {'prompt_tokens': 5, 'prompt_tokens_details': None}):
            with self.subTest(usage=usage):
                self.assertEqual(cache_usage(usage), {})


class Handler(BaseHTTPRequestHandler):
    usage = {}

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        body = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': ANSWER}}], 'usage': Handler.usage}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def respond(self, usage):
        Handler.usage = usage
        config = {'base_url': f'http://127.0.0.1:{self.server.server_address[1]}', 'model': 'm', 'model_type': 'cloud', 'protocol': 'openai', 'context_limit': 8192, 'max_output_tokens': 256}
        provider = ChatProvider(config, 'secret-key')
        provider.respond({'purpose': 'project_triage', 'goal': 'a small cli'})
        return provider.usage

    def test_reported_cache_fields_are_kept_and_the_old_shape_is_unchanged_when_absent(self):
        plain = self.respond({'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12})
        self.assertEqual(plain, {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12})  # 默认形状逐字不变 / the default shape is untouched
        cached = self.respond({'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12, 'prompt_cache_hit_tokens': 7, 'prompt_cache_miss_tokens': 3})
        self.assertEqual((cached['prompt_cache_hit_tokens'], cached['prompt_cache_miss_tokens']), (7, 3))


class ReportTests(unittest.TestCase):
    def test_totals_count_only_calls_that_reported_cache_data(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                rid = ProjectPlanning(store, FakeExecutor()).generate(PlannerProvider(), 'Build a CLI')
                before = task_report(store, rid)['totals']
                self.assertEqual((before['cache_hit_tokens'], before['cache_miss_tokens'], before['cache_hit_rate']), (0, 0, None))  # 没人报告：未知 / nobody reported: unknown
                route = {'model': 'm', 'provider': 'openai-compatible', 'base_url': 'https://api.example.com'}
                for name, usage in (('a', {'prompt_tokens': 100, 'completion_tokens': 5, 'total_tokens': 105, 'prompt_cache_hit_tokens': 75, 'prompt_cache_miss_tokens': 25}),
                                    ('b', {'prompt_tokens': 100, 'completion_tokens': 5, 'total_tokens': 105, 'prompt_cache_hit_tokens': 25, 'prompt_cache_miss_tokens': 75}),
                                    ('c', {'prompt_tokens': 100, 'completion_tokens': 5, 'total_tokens': 105})):  # c 没有缓存数据 / c has no cache data
                    store._event(rid, 'model_requested', {'step_id': 'x', 'invocation_id': name, 'attempt_no': 1, 'route': route})
                    store._event(rid, 'model_completed', {'step_id': 'x', 'invocation_id': name, 'attempt_no': 1, 'usage': usage})
                report = task_report(store, rid)
                totals = report['totals']
                self.assertEqual((totals['cache_hit_tokens'], totals['cache_miss_tokens'], totals['cache_hit_rate']), (100, 100, 0.5))
                mine = {c['invocation_id']: c for c in report['calls'] if c['step_id'] == 'x'}
                self.assertEqual((mine['a']['cache_hit_tokens'], mine['c']['cache_hit_tokens']), (75, None))
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
