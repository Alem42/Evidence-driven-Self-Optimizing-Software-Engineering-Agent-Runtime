"""提示排布：稳定内容在前，只改顺序、不改内容；前缀缓存因此能命中更长的部分。
Prompt layout: stable content first; order only, never content; a prefix cache can therefore reuse a longer part."""
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from masa.domain.models import canonical, digest
from masa.domain.prompt_layout import ORDER, layout_name, ordered_keys, render
from masa.infrastructure.llm import ChatProvider

WORKER = {'purpose': 'package_worker', 'goal': 'count words', 'acceptance': ['a', 'b'], 'language': 'go', 'language_notes': 'n' * 300,
          'package': 'internal/wc', 'interfaces': {'x': 'func X()'}, 'own_files': {}, 'notes': 'none yet', 'files': {'go.mod': 'module m'},
          'allowed_tools': ['write_file'], 'step': 1, 'last_results': [], 'max_steps': 12}


def common(a, b):
    n = 0
    while n < min(len(a), len(b)) and a[n] == b[n]:
        n += 1
    return n


class OrderTests(unittest.TestCase):
    def test_the_rendering_is_valid_json_with_exactly_the_same_content(self):
        for layout in ('stable_first', 'sorted'):
            with self.subTest(layout=layout):
                self.assertEqual(json.loads(render(WORKER, layout)), WORKER)

    def test_the_sorted_layout_is_the_old_canonical_form_byte_for_byte(self):
        self.assertEqual(render(WORKER, 'sorted'), canonical(WORKER))

    def test_stable_fields_come_before_volatile_ones(self):
        keys = ordered_keys(WORKER, 'stable_first')
        for earlier, later in (('purpose', 'goal'), ('goal', 'package'), ('acceptance', 'files'), ('files', 'step'), ('step', 'last_results'), ('max_steps', 'package')):
            self.assertLess(keys.index(earlier), keys.index(later), (earlier, later))
        self.assertEqual(keys[0], 'purpose')
        self.assertEqual(keys[-1], 'last_results')

    def test_unknown_fields_sit_in_the_middle_in_alphabetical_order_and_the_result_is_deterministic(self):
        context = {'zeta': 1, 'alpha': 2, 'goal': 'g', 'step': 3, 'purpose': 'p', 'files': {}}
        keys = ordered_keys(context, 'stable_first')
        self.assertEqual(keys, ['purpose', 'goal', 'alpha', 'zeta', 'files', 'step'])
        self.assertEqual(render(context, 'stable_first'), render(dict(reversed(list(context.items()))), 'stable_first'))  # 与传入顺序无关 / independent of insertion order

    def test_the_table_has_no_duplicates_and_one_wildcard(self):
        self.assertEqual(len(ORDER), len(set(ORDER)))
        self.assertIn('*', ORDER)

    def test_the_identity_hash_of_a_context_does_not_depend_on_the_layout(self):
        self.assertEqual(digest(WORKER), digest(json.loads(render(WORKER, 'stable_first'))))  # 账本里的输入身份不变 / ledger input identity is unchanged

    def test_the_environment_switch_restores_the_old_layout(self):
        with patch.dict(os.environ, {'MASA_PROMPT_LAYOUT': 'sorted'}):
            self.assertEqual((layout_name(), render(WORKER)), ('sorted', canonical(WORKER)))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('MASA_PROMPT_LAYOUT', None)
            self.assertEqual(layout_name(), 'stable_first')


class PrefixSharingTests(unittest.TestCase):
    def test_two_consecutive_worker_steps_share_a_longer_prefix_than_before(self):
        step1 = dict(WORKER)
        step2 = dict(WORKER, step=2, last_results=[{'tool': 'write_file', 'ok': True}], own_files={'internal/wc/wc.go': 'package wc'})
        old = common(render(step1, 'sorted'), render(step2, 'sorted'))
        new = common(render(step1, 'stable_first'), render(step2, 'stable_first'))
        self.assertGreater(new, old)
        self.assertGreater(new / len(render(step1, 'stable_first')), 0.6)  # 大部分内容落在可复用的前缀里 / most of the prompt is reusable prefix


class Handler(BaseHTTPRequestHandler):
    bodies = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        Handler.bodies.append(json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0)))))
        body = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({'verdict': 'ok'})}}], 'usage': {}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class WireTests(unittest.TestCase):
    def test_the_model_receives_the_rendered_prompt(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        config = {'base_url': f'http://127.0.0.1:{server.server_address[1]}', 'model': 'm', 'model_type': 'cloud', 'protocol': 'openai', 'context_limit': 8192, 'max_output_tokens': 256}
        context = {'purpose': 'project_triage', 'goal': 'a small cli', 'language': 'go'}
        Handler.bodies.clear()
        ChatProvider(config, 'secret-key').respond(context)
        sent = Handler.bodies[0]['messages'][1]['content']
        self.assertEqual(sent, render(context))
        self.assertTrue(sent.startswith('{"purpose":"project_triage","language":"go","goal":"a small cli"'))


if __name__ == '__main__':
    unittest.main()
