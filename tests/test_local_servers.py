"""本地 OpenAI 兼容服务探测：在线/离线、llama.cpp 的 /props、非回环地址被拒。 Probing of local OpenAI-compatible servers."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from masa.infrastructure import local_servers
from masa.infrastructure.llm import validate_config


def serve(routes):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = routes.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            raw = json.dumps(body).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class LocalServersTests(unittest.TestCase):
    def test_llama_cpp_reports_model_and_context(self):
        server = serve({'/v1/models': {'data': [{'id': 'qwen-q3'}]},
                        '/props': {'default_generation_settings': {'n_ctx': 16384}, 'model_path': 'E:\\models\\x.gguf', 'build_info': 'b1'}})
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        info = local_servers.probe(f'http://127.0.0.1:{server.server_port}/v1')
        self.assertEqual((info['reachable'], info['engine'], info['served'], info['n_ctx']), (True, 'llama.cpp', ['qwen-q3'], 16384))

    def test_other_servers_have_unknown_context(self):
        server = serve({'/v1/models': {'data': [{'id': 'strata-model'}]}})
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        info = local_servers.probe(f'http://127.0.0.1:{server.server_port}/v1')
        self.assertEqual((info['engine'], info['served'], 'n_ctx' in info), ('openai-compatible', ['strata-model'], False))

    def test_offline_and_non_loopback(self):
        self.assertEqual(local_servers.probe('http://127.0.0.1:1/v1'), {'reachable': False})
        self.assertFalse(local_servers.probe('http://example.com/v1')['reachable'])

    def test_runtimes_marks_default_and_model_mismatch(self):
        server = serve({'/v1/models': {'data': [{'id': 'real-name'}]}})
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        url = f'http://127.0.0.1:{server.server_port}/v1'
        good = validate_config({'name': 'a', 'model_type': 'local', 'protocol': 'openai', 'base_url': url, 'model': 'real-name'})
        bad = validate_config({'name': 'b', 'model_type': 'local', 'protocol': 'openai', 'base_url': url, 'model': 'other'})
        result = local_servers.runtimes({'a': good, 'b': bad}, 'a')
        by_id = {e['id']: e for e in result['profiles']}
        self.assertTrue(by_id['a']['is_default'] and by_id['a']['model_listed'])
        self.assertFalse(by_id['b']['is_default'] or by_id['b']['model_listed'])

    def test_temperature_is_optional_and_validated(self):
        base = {'model_type': 'local', 'protocol': 'openai', 'base_url': 'http://127.0.0.1:8080/v1', 'model': 'm'}
        self.assertNotIn('temperature', validate_config(base))
        self.assertEqual(validate_config({**base, 'temperature': 0.3})['temperature'], 0.3)
        for bad in (-1, 3, 'hot', float('nan')):
            with self.assertRaises(Exception):
                validate_config({**base, 'temperature': bad})


if __name__ == '__main__':
    unittest.main()
