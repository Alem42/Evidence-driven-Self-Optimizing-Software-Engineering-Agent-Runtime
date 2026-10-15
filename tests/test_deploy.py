"""容器部署：演示只读模式、同源静态托管、Host 白名单、环境变量默认值。
Container deployment: read-only demo mode, same-origin static hosting, Host allow-list, env-var defaults."""
import http.client
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from masa.application.console import Console
from masa.infrastructure import proc
from masa.interfaces.http.server import make_server


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.web = self.root / 'web'
        (self.web / 'assets').mkdir(parents=True)
        (self.web / 'index.html').write_text('<html>spa</html>')
        (self.web / 'assets' / 'app.js').write_text('console.log(1)')
        (self.root / 'secret.txt').write_text('outside')
        self.console = Console(self.root / 'state', 'missing', 'missing', self.root)
        self.server = make_server(self.console, 0, ('https://demo.example.com',), hosts=('demo.example.com',), static_dir=self.web, demo=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.console.close()
        self.temp.cleanup()

    def get(self, path, headers=None, host=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        values = {'Host': host or f'127.0.0.1:{self.port}'}
        values.update(headers or {})
        connection.request('GET', path, None, values)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, raw, response

    def post(self, path, body, token):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        connection.request('POST', path, json.dumps(body), {'Content-Type': 'application/json', 'X-MASA-Token': token, 'Host': f'127.0.0.1:{self.port}'})
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, json.loads(raw or b'{}')

    def token(self):
        status, raw, _ = self.get('/api/session', {'Sec-Fetch-Site': 'same-origin'})
        self.assertEqual(status, 200)
        return json.loads(raw)['token']

    def test_static_files_spa_fallback_and_traversal(self):
        """静态文件、前端路由回退、目录穿越。 Static files, SPA fallback and path traversal."""
        status, raw, response = self.get('/')
        self.assertEqual((status, raw), (200, b'<html>spa</html>'))
        self.assertEqual(response.getheader('Cache-Control'), 'no-cache')
        status, raw, response = self.get('/settings/bench')
        self.assertEqual((status, raw), (200, b'<html>spa</html>'))
        status, raw, response = self.get('/assets/app.js')
        self.assertEqual(status, 200)
        self.assertIn('immutable', response.getheader('Cache-Control'))
        self.assertEqual(response.getheader('X-Content-Type-Options'), 'nosniff')
        self.assertIn("frame-ancestors 'none'", response.getheader('Content-Security-Policy'))
        self.assertEqual(self.get('/assets/missing.js')[0], 404)
        for path in ('/../secret.txt', '/%2e%2e/secret.txt', '/assets/..%2f..%2fsecret.txt'):
            status, raw, _ = self.get(path)
            self.assertNotIn(b'outside', raw, path)

    def test_demo_blocks_writes_and_sensitive_reads(self):
        """演示模式：读展示数据可以，发起任务/用密钥的接口被拒。 Demo mode: display data readable; task and key-using endpoints refused."""
        token = self.token()
        headers = {'X-MASA-Token': token}
        for path in ('/api/bootstrap', '/api/routing', '/api/roles', '/api/projects'):
            status, raw, _ = self.get(path, headers)
            self.assertEqual(status, 200, path)
        for path in ('/api/accounts', '/api/hardware', '/api/ollama', '/api/diagnostics'):
            status, raw, _ = self.get(path, headers)
            self.assertEqual(status, 403, path)
        status, body = self.post('/api/projects/plan', {'goal': 'x'}, token)
        self.assertEqual(status, 403)
        self.assertIn('演示', body['error'])
        self.assertEqual(self.post('/api/bench/start', {}, token)[0], 403)
        self.assertTrue(json.loads(self.get('/api/bootstrap', headers)[1])['demo'])

    def test_host_allow_list_and_origin(self):
        """Host 白名单（防 DNS rebinding）与跨站来源。 Host allow-list (DNS rebinding) and cross-site origins."""
        self.assertEqual(self.get('/', host='evil.example.net')[0], 403)
        self.assertEqual(self.get('/', host='demo.example.com')[0], 200)
        self.assertEqual(self.get('/', {'Origin': 'https://evil.example.net'})[0], 403)
        self.assertEqual(self.get('/', {'Sec-Fetch-Site': 'cross-site'})[0], 403)
        # 经 TLS 代理的 https 同源被接受。 https same origin behind a TLS proxy is accepted.
        self.assertEqual(self.get('/api/session', {'Origin': 'https://demo.example.com', 'Sec-Fetch-Site': 'same-origin'}, host='demo.example.com')[0], 200)

    def test_session_without_origin_needs_same_origin_fetch_metadata(self):
        """同源页面没有 Origin 头：必须带 Sec-Fetch-Site: same-origin 才发令牌。 A same-origin page sends no Origin; the token needs Sec-Fetch-Site: same-origin."""
        self.assertEqual(self.get('/api/session')[0], 403)
        self.assertEqual(self.get('/api/session', {'Sec-Fetch-Site': 'same-origin'})[0], 200)


class EnvDefaultTests(unittest.TestCase):
    def run_cli(self, env):
        from masa.interfaces import cli
        captured = {}
        with patch.dict(os.environ, env, clear=False), patch('masa.interfaces.http.server.serve', side_effect=lambda *a, **k: captured.update(args=a, kwargs=k)):
            cli.main(['serve', '--port', '1'])
        return captured['kwargs']

    def test_environment_defaults_reach_serve(self):
        """容器的环境变量就是命令行默认值。 Container env vars are the CLI defaults."""
        kwargs = self.run_cli({'MASA_BIND': '0.0.0.0', 'MASA_ALLOWED_HOSTS': 'a.example.com, b.example.com', 'MASA_STATIC_DIR': '/app/web', 'MASA_DEMO': '1'})
        self.assertEqual(kwargs['bind'], '0.0.0.0')
        self.assertEqual(tuple(kwargs['hosts']), ('a.example.com', 'b.example.com'))
        self.assertEqual(str(kwargs['static_dir']).replace('\\', '/'), '/app/web')
        self.assertTrue(kwargs['demo'])


@unittest.skipIf(os.name == 'nt', 'POSIX process groups')
class ProcessGroupTests(unittest.TestCase):
    def test_kill_group_kills_the_whole_session(self):
        """新会话里的子孙进程随组一起被杀（超时不留孤儿）。 Descendants in the new session die together (no orphans after a timeout)."""
        parent = subprocess.Popen([sys.executable, '-c', 'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);time.sleep(60)'], **proc.NEW_SESSION)
        try:
            proc.kill_group(parent.pid)
            self.assertEqual(parent.wait(5), -signal.SIGKILL)
        finally:
            parent.kill()


class NonPosixTests(unittest.TestCase):
    def test_new_session_is_empty_on_windows(self):
        """Windows 上不传 start_new_session，kill_group 是空操作。 No start_new_session on Windows; kill_group is a no-op."""
        if os.name == 'nt':
            self.assertEqual(proc.NEW_SESSION, {})
            proc.kill_group(0)
        else:
            self.assertEqual(proc.NEW_SESSION, {'start_new_session': True})


if __name__ == '__main__':
    unittest.main()
