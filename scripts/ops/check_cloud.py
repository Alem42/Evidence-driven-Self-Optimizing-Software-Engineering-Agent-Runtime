"""Probe a running cloud deployment without calling a paid model API."""
import json
import os
import urllib.error
import urllib.request


def check():
    host = os.environ.get('MASA_PROBE_HOST', '127.0.0.1:8765')
    def request(path, token=None, body=None):
        headers = {'Host': host, 'Sec-Fetch-Site': 'same-origin'}
        if token:
            headers['X-MASA-Token'] = token
        if body is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request('http://127.0.0.1:8765' + path,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
    status, _ = request('/')
    assert status == 200, ('frontend', status)
    status, raw = request('/api/session')
    assert status == 200, ('session', status)
    token = json.loads(raw)['token']
    status, raw = request('/api/bootstrap', token)
    assert status == 200, ('bootstrap', status)
    bootstrap = json.loads(raw)
    assert not bootstrap['local_models']['allowed'], bootstrap['local_models']
    assert bootstrap['runner_ready'], 'runner or Go toolchain missing'
    if os.environ.get('MASA_REQUIRE_IDLE') == '1':
        assert not bootstrap['active_run'] and not bootstrap['active_job'], 'active work: defer deployment'
    assert bootstrap['demo'] == (os.environ.get('MASA_DEMO', '0') == '1'), 'deployment mode mismatch'
    for path, body in (('/api/ollama', None), ('/api/ollama/orphans', None),
                       ('/api/ollama/action', {'action': 'load', 'model': 'invalid'}),
                       ('/api/ollama/orphans/clean', {})):
        status, _ = request(path, token, body)
        assert status == 403, (path, status)
    from masa.infrastructure import capability
    from masa.infrastructure.llm import ChatProvider
    from masa.domain.models import MasaError
    capability.configure()
    try:
        ChatProvider({'model_type': 'local', 'base_url': 'http://127.0.0.1:11434', 'model': 'm'}, 'synthetic')
    except MasaError:
        pass
    else:
        raise AssertionError('local provider accepted')
    print('PASS: frontend, session, bootstrap, local endpoints and provider guard; no paid calls')


if __name__ == '__main__':
    check()
