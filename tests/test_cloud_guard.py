"""Cloud policy must stop local execution before network or process side effects."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from masa.application.console import Console
from masa.domain.models import MasaError
from masa.infrastructure import capability
from masa.infrastructure.llm import ChatProvider
from masa.infrastructure.settings import Settings
from masa.interfaces.http.server import make_server


LOCAL = {'model_type': 'local', 'base_url': 'http://127.0.0.1:11434', 'model': 'local'}
CLOUD = {'model_type': 'cloud', 'base_url': 'https://api.example.com/v1', 'model': 'api'}


class CloudGuardTests(unittest.TestCase):
    def setUp(self):
        guard = patch.dict(capability.STATE, {'allowed': False, 'reason': 'cloud policy'}, clear=True)
        guard.start()
        self.addCleanup(guard.stop)

    def test_migrated_local_profile_with_key_is_never_a_route_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch.dict(capability.STATE, allowed=True):
                settings = Settings(root)
                local_id = settings.save({**LOCAL, 'api_key': 'synthetic', 'persist_key': True})['active_id']
                snapshot = settings.provider(local_id).snapshot
                cloud_id = settings.save({**CLOUD, 'new': True, 'api_key': 'synthetic'})['active_id']
            settings = Settings(root)
            self.assertFalse(next(p for p in settings.public()['profiles'] if p['id'] == local_id)['ready'])
            self.assertEqual([i for i, _ in settings.ready_profiles()], [cloud_id])
            with self.assertRaisesRegex(MasaError, 'local models are disabled'):
                settings.provider(local_id)
            with self.assertRaisesRegex(MasaError, 'local models are disabled'):
                settings.provider(snapshot=snapshot)
            self.assertEqual(settings.provider(cloud_id).config['model_type'], 'cloud')

    def test_local_save_and_cloud_label_cannot_bypass_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(Path(temp))
            with self.assertRaisesRegex(MasaError, 'local models are disabled'):
                settings.save(LOCAL)
            self.assertFalse(settings.profiles)
            for config in (LOCAL, {**LOCAL, 'model_type': 'cloud', 'protocol': 'openai'}):
                with self.assertRaisesRegex(MasaError, 'local models are disabled'):
                    ChatProvider(config, 'synthetic')
            ChatProvider(CLOUD, 'synthetic')

    def test_all_ollama_http_endpoints_reject_before_side_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            console = Console(Path(temp), 'missing', 'missing', Path(temp))
            server = make_server(console, 0, ('http://localhost:5173',))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                def request(method, path, body=None, token=None):
                    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                    headers = {'Origin': 'http://localhost:5173', 'Content-Type': 'application/json'}
                    if token:
                        headers['X-MASA-Token'] = token
                    connection.request(method, path, json.dumps(body) if body is not None else None, headers)
                    response = connection.getresponse()
                    data = json.loads(response.read())
                    connection.close()
                    return response.status, data
                status, session = request('GET', '/api/session')
                self.assertEqual(status, 200)
                with patch('masa.infrastructure.ollama.OllamaControl.catalog') as catalog, \
                        patch('masa.infrastructure.orphans.clean_orphans') as clean:
                    for method, path, body in (
                        ('GET', '/api/ollama', None), ('GET', '/api/ollama/orphans', None),
                        ('POST', '/api/ollama/action', {'action': 'load', 'model': 'm'}),
                        ('POST', '/api/ollama/orphans/clean', {}),
                    ):
                        status, data = request(method, path, body, session['token'])
                        self.assertEqual(status, 403, path)
                        self.assertIn('local models are disabled', data['error'])
                    catalog.assert_not_called()
                    clean.assert_not_called()
            finally:
                server.shutdown()
                server.server_close()
                worker.join()
                console.close()


if __name__ == '__main__':
    unittest.main()
