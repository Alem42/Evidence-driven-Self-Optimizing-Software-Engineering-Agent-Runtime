import http.client
import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from masa.web.server import make_server
from masa.web.service import Console
from masa.web.settings import Settings
from masa.adapters.sqlite import Store
from test_runtime import FakeExecutor


class WebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / 'repo'
        self.source.mkdir()
        (self.source / 'go.mod').write_text('module demo\ngo 1.27.0\n')
        self.console = Console(self.root / 'state', 'missing', 'missing', self.root)
        self.server = make_server(self.console, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        status, html = self.request('/')
        self.assertEqual(status, 200)
        self.token = re.search(r'name="masa-token" content="([^"]+)"', html).group(1)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.console.close()
        self.temp.cleanup()

    def request(self, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        values = {'Content-Type': 'application/json', 'X-MASA-Token': getattr(self, 'token', '')}
        values.update(headers or {})
        connection.request('GET' if body is None else 'POST', path, None if body is None else json.dumps(body), values)
        response = connection.getresponse()
        status, raw = response.status, response.read().decode()
        connection.close()
        return status, json.loads(raw) if path.startswith('/api/') else raw

    def wait_run(self, rid):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status, detail = self.request('/api/runs/' + rid)
            if not detail['active']:
                self.assertIsNone(detail['worker_error'])
                return detail
            time.sleep(.02)
        self.fail('worker did not finish')

    def test_local_session_and_static_access(self):
        for headers in ({'X-MASA-Token':''}, {'Origin':'https://evil.example'}, {'Host':'evil.example'}, {'Sec-Fetch-Site':'cross-site'}):
            self.assertEqual(self.request('/api/runs', headers=headers)[0], 403)
        self.assertEqual(self.request('/api/runs')[0], 200)
        self.assertEqual(self.request('/app.js')[0], 200)
        self.assertEqual(self.request('/style.css')[0], 200)
        self.assertEqual(self.request('/../provider.json')[0], 404)
        self.assertEqual(self.request('/api/runs', [1])[0], 400)

    def test_credentials_never_returned_or_persisted(self):
        secret = 'synthetic-test-secret'
        status, result = self.request('/api/settings', {'name':'demo','base_url':'https://example.com/v1','model':'future','api_key':secret})
        self.assertEqual(status, 200)
        self.assertTrue(result['key_configured'])
        self.assertNotIn(secret, json.dumps(result))
        self.assertNotIn(secret, self.console.settings.path.read_text())
        self.assertFalse(Settings(self.root / 'state').public()['key_configured'])
        self.assertFalse(result['execution_connected'])
        self.assertEqual(self.request('/api/settings', {'base_url':'https://user:secret@example.com'})[0], 400)
        self.assertFalse(self.request('/api/settings', {'clear_key':True})[1]['key_configured'])

    def test_run_pause_resume_revision_and_evidence(self):
        executor = FakeExecutor()
        with patch('masa.web.service.Runner', return_value=executor):
            body = {'repo':str(self.source),'goal':'verify','pause_after':True}
            status, result = self.request('/api/runs', body)
            self.assertEqual(status, 200)
            rid = result['id']
            detail = self.wait_run(rid)
            self.assertEqual(detail['run']['status'], 'paused')
            ref = detail['tools'][0]['result_ref']
            self.assertEqual(self.request('/api/runs/'+rid+'/artifacts/'+ref)[0], 200)
            self.assertEqual(self.request('/api/runs/'+rid+'/artifacts/'+'a'*64)[0], 400)
            self.assertEqual(self.request('/api/runs/'+rid+'/resume', {})[0], 200)
            self.assertEqual(self.wait_run(rid)['run']['status'], 'succeeded')
            self.assertEqual(executor.calls, 1)
            self.assertIn('markdown', self.request('/api/runs/'+rid+'/report')[1])
            body['goal'] = 'revised request'
            revised = self.request('/api/runs/'+rid+'/revise', body)[1]['id']
            detail = self.wait_run(revised)
            self.assertEqual(detail['run']['data']['parent_run_id'], rid)
            self.assertTrue(any(e['type']=='human_request_revised' for e in detail['events']))
            self.assertEqual(self.request('/api/runs/'+rid)[1]['run']['data']['goal'], 'verify')
            self.request('/api/runs/'+revised+'/cancel', {})
            self.assertEqual(self.wait_run(revised)['run']['status'], 'cancelled')

    def test_pause_requested_during_tool_stops_at_boundary(self):
        entered, release = threading.Event(), threading.Event()
        class SlowExecutor(FakeExecutor):
            def execute(self, *args):
                entered.set()
                release.wait(5)
                return super().execute(*args)
        with patch('masa.web.service.Runner', return_value=SlowExecutor()):
            rid = self.request('/api/runs', {'repo':str(self.source),'goal':'pause'})[1]['id']
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.request('/api/runs', {'repo':str(self.source),'goal':'conflict'})[0], 400)
                self.assertEqual(self.request('/api/runs/'+rid+'/pause', {})[0], 200)
            finally:
                release.set()
            detail = self.wait_run(rid)
            self.assertEqual(detail['run']['status'], 'paused')
            self.assertEqual(detail['steps'][1]['status'], 'pending')
            self.request('/api/runs/'+rid+'/resume', {})
            self.assertEqual(self.wait_run(rid)['run']['status'], 'succeeded')

    def test_complete_graph_auto_continues_and_gate_requires_all_checks(self):
        """任一检查失败时其他节点仍取证，Gate 不放行。 Collect all checks but reject any failed evidence."""
        class MixedExecutor(FakeExecutor):
            def execute(self, request, *args):
                result = super().execute(request, *args)
                result['exit_code'] = int(request['operation'] == 'go_vet')
                return result
        with patch('masa.web.service.Runner', return_value=MixedExecutor()):
            status, result = self.request('/api/runs', {'repo': str(self.source), 'goal': 'complete', 'full_checks': True})
            self.assertEqual(status, 200)
            detail = self.wait_run(result['id'])
            self.assertEqual(detail['run']['status'], 'failed')
            self.assertEqual(detail['run']['tool_calls'], 3)
            self.assertEqual(detail['run']['model_calls'], 6)
            self.assertEqual(len(detail['steps']), 4)

    def test_complete_graph_supports_step_then_automatic_resume(self):
        """单步恢复不会重跑完成节点，可再次切换自动推进。 Step-resume preserves completed nodes before auto continuation."""
        executor = FakeExecutor()
        with patch('masa.web.service.Runner', return_value=executor):
            rid = self.request('/api/runs', {'repo': str(self.source), 'goal': 'step', 'full_checks': True, 'pause_after': True})[1]['id']
            self.assertEqual(self.wait_run(rid)['run']['status'], 'paused')
            self.request('/api/runs/'+rid+'/resume', {'pause_after': True})
            detail = self.wait_run(rid)
            self.assertEqual(detail['run']['status'], 'paused')
            self.assertEqual(executor.calls, 2)
            self.request('/api/runs/'+rid+'/resume', {})
            self.assertEqual(self.wait_run(rid)['run']['status'], 'succeeded')
            self.assertEqual(executor.calls, 3)
