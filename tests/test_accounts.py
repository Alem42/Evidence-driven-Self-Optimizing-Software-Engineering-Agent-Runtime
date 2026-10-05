"""API 账户管理、模型次序、可编辑的路由默认值；以及同级轮换与按角色起始等级。
API accounts, model order, editable routing defaults, same-level rotation and per-role start level."""
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from masa.application.router import build_snapshot
from masa.application.routing import Candidate, DEFAULT_POLICY, route, validate_policy
from masa.domain.models import MasaError
from masa.infrastructure import providers
from masa.infrastructure.settings import Settings

KEY = 'synthetic-secret-key-123'


class FakeAPI(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.headers.get('Authorization') != 'Bearer ' + KEY:
            self.send_response(401)
            self.end_headers()
            self.wfile.write(('{"error":"echo ' + KEY + '"}').encode())
            return
        bodies = {
            '/models': {'object': 'list', 'data': [
                {'id': 'deepseek-flash', 'name': 'Flash', 'context_window': 1048576, 'max_output_tokens': 393216,
                 'input_modalities': ['text', 'image'], 'effort': {'supported_levels': ['low', 'high']}},
                {'id': 'deepseek-v4-pro', 'name': 'Pro', 'context_window': 1048576, 'max_output_tokens': 393216, 'input_modalities': ['text']}]},
        }
        body = bodies.get(self.path)
        raw = json.dumps(body if body is not None else {}).encode()
        self.send_response(200 if body is not None else 404)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FakeAPI)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.settings = Settings(self.root)

    def add(self, model, key=KEY, **extra):
        return self.settings.save({'new': True, 'base_url': self.base, 'model': model, 'api_key': key, 'name': model, **extra})

    def test_live_models_are_normalized_and_unknown_providers_get_no_guessed_balance(self):
        models = providers.fetch_models(self.base, KEY)
        self.assertEqual([m['id'] for m in models], ['deepseek-flash', 'deepseek-v4-pro'])
        self.assertEqual(models[0]['context_window'], 1048576)
        self.assertTrue(models[0]['vision'])
        self.assertFalse(models[1]['vision'])
        self.assertFalse(providers.fetch_balance(self.base, KEY)['supported'])

    def test_http_errors_never_echo_the_key_or_upstream_bodies(self):
        with self.assertRaises(MasaError) as caught:
            providers.fetch_models(self.base, 'wrong-key')
        self.assertIn('401', str(caught.exception))
        self.assertNotIn(KEY, str(caught.exception))
        self.assertNotIn('echo', str(caught.exception))

    def test_accounts_group_profiles_by_url_and_key_and_hide_the_key(self):
        self.add('model-a')
        self.add('model-b')
        self.add('other', key='another-key')
        accounts = self.settings.accounts()
        self.assertEqual(sorted(len(a['profiles']) for a in accounts), [1, 2])
        dump = json.dumps(accounts)
        self.assertNotIn(KEY, dump)
        self.assertNotIn('another-key', dump)

    def test_adding_models_copies_the_key_clamps_limits_and_keeps_the_default(self):
        self.add('deepseek-v4-pro', level=3)
        default_before = self.settings.active_id
        account = self.settings.accounts()[0]['id']
        listed = {m['id']: m for m in self.settings.account_models(account)}
        self.assertTrue(listed['deepseek-v4-pro']['added'])
        self.assertFalse(listed['deepseek-flash']['added'])
        result = self.settings.add_models(account, ['deepseek-flash', 'deepseek-v4-pro'])
        self.assertEqual(result['added'], ['deepseek-flash'])  # 已有的不重复添加 / existing ones are skipped
        self.assertEqual(self.settings.active_id, default_before)
        new = next(p for p in self.settings.profiles.values() if p['model'] == 'deepseek-flash')
        self.assertEqual(new['context_limit'], 262144)  # 1M 窗口被夹到本系统上限 / clamped to the system maximum
        self.assertEqual(new['max_output_tokens'], 8192)
        self.assertEqual(len(self.settings.accounts()), 1)  # 仍是同一把 key / still one key
        with self.assertRaises(MasaError):
            self.settings.add_models(account, ['not-listed'])

    def test_ticking_models_enables_adds_and_disables_without_deleting_or_changing_the_default(self):
        self.add('deepseek-v4-pro', level=3)
        default_before = self.settings.active_id
        account = self.settings.accounts()[0]['id']
        # 勾选 flash：添加并启用；pro 没勾选：只停用，配置保留。 Tick flash: add and enable; untick pro: disable, keep the profile.
        self.settings.select_models(account, ['deepseek-flash'])
        by_model = {p['model']: p for p in self.settings.profiles.values()}
        self.assertTrue(by_model['deepseek-flash']['enabled'])
        self.assertFalse(by_model['deepseek-v4-pro']['enabled'])
        self.assertEqual(by_model['deepseek-v4-pro']['level'], 3)  # 设置还在 / settings kept
        self.assertEqual(self.settings.active_id, default_before)
        self.settings.select_models(account, ['deepseek-flash', 'deepseek-v4-pro'])
        self.assertTrue(all(p['enabled'] for p in self.settings.profiles.values()))
        self.assertEqual(len(self.settings.profiles), 2)
        with self.assertRaises(MasaError):
            self.settings.select_models(account, ['not-listed'])
        # 停用的模型不会进入路由候选。 Disabled models never become routing candidates.
        self.settings.select_models(account, ['deepseek-flash'])
        self.assertEqual([c['model'] for c in build_snapshot(self.settings)['candidates']], ['deepseek-flash'])

    def test_routing_defaults_roundtrip_validate_and_feed_snapshots(self):
        saved = self.settings.save_routing({'policy': {'max_escalations': 1, 'attempts_per_level': {'fix': 2},
                                                        'start_level_by_role': {'project_planner': 2}},
                                            'budget': {'max_cloud_tokens': 5000, 'max_cost': None}})
        self.assertEqual(saved['policy']['attempts_per_level']['planning'], 2)  # 未给的项取默认 / unspecified keep defaults
        self.assertEqual(Settings(self.root).routing()['budget']['max_cloud_tokens'], 5000)
        for bad in ({'policy': {'max_escalations': 99}}, {'policy': {'attempts_per_level': {'nope': 1}}},
                    {'policy': {'start_level_by_role': {'bogus': 2}}}, {'budget': {'max_model_calls': 0}}, {'policy': {'x': 1}}):
            with self.assertRaises(MasaError):
                self.settings.save_routing(bad)
        self.add('m')
        snapshot = build_snapshot(self.settings, budget={'max_model_calls': 7}, policy={'attempts_per_level': {'generation': 3}})
        self.assertEqual(snapshot['budget']['max_cloud_tokens'], 5000)  # 保存的默认 / saved default
        self.assertEqual(snapshot['budget']['max_model_calls'], 7)  # 任务覆盖 / per-task override
        self.assertEqual(snapshot['policy']['attempts_per_level'], {'planning': 2, 'generation': 3, 'fix': 2})
        self.assertEqual(snapshot['policy']['start_level_by_role'], {'project_planner': 2})

    def test_snapshot_honours_the_models_selected_for_the_task(self):
        a = self.add('a')['active_id']
        b = self.add('b')['active_id']
        chosen = build_snapshot(self.settings, profile_ids={b})
        self.assertEqual([c['id'] for c in chosen['candidates']], [b])
        with self.assertRaises(MasaError):
            build_snapshot(self.settings, profile_ids={'missing'})
        self.assertNotEqual(a, b)


class OrderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.settings = Settings(Path(temp.name))
        self.ids = {}
        for name, kind, level in (('local-a', 'local', 1), ('local-b', 'local', 1), ('cloud-a', 'cloud', 2)):
            result = self.settings.save({'new': True, 'name': name,
                                         'base_url': 'http://127.0.0.1:11434' if kind == 'local' else 'https://api.example.com/v1',
                                         'model': name, 'api_key': 'k' if kind == 'cloud' else '', 'model_type': kind, 'level': level})
            self.ids[name] = result['active_id']

    def order(self):
        return [p['name'] for p in self.settings.public()['profiles']]

    def test_reorder_rewrites_priority_within_a_level_and_persists(self):
        i = self.ids
        self.settings.reorder([i['local-b'], i['local-a'], i['cloud-a']])
        self.assertEqual(self.order(), ['local-b', 'local-a', 'cloud-a'])
        self.assertEqual(self.settings.profiles[i['local-b']]['priority'], 0)
        self.assertEqual(self.settings.profiles[i['local-a']]['priority'], 1)
        self.assertEqual(self.order(), [p['name'] for p in Settings(self.settings.path.parent).public()['profiles']])

    def test_levels_are_forced_non_decreasing_along_the_list(self):
        i = self.ids
        # 把云端模型拖到最前：它的等级不能比后面的本地模型更高。 A cloud model dragged first must not sit above the locals.
        self.settings.reorder([i['cloud-a'], i['local-a'], i['local-b']], {i['cloud-a']: 1})
        levels = [self.settings.profiles[x]['level'] for x in (i['cloud-a'], i['local-a'], i['local-b'])]
        self.assertEqual(levels, sorted(levels))
        self.settings.reorder([i['local-a'], i['local-b'], i['cloud-a']], {i['local-b']: 3})
        self.assertEqual([self.settings.profiles[x]['level'] for x in (i['local-a'], i['local-b'], i['cloud-a'])], [1, 3, 3])

    def test_invalid_orders_are_rejected_without_changing_anything(self):
        before = self.order()
        i = self.ids
        for bad in ([i['local-a']], [i['local-a'], i['local-a'], i['cloud-a']], 'nope', [*i.values(), 'extra']):
            with self.assertRaises(MasaError):
                self.settings.reorder(bad)
        with self.assertRaises(MasaError):
            self.settings.reorder(list(i.values()), {'missing': 2})
        self.assertEqual(self.order(), before)


def cand(cid, level, priority=0, kind='local'):
    return Candidate(cid, level, priority, kind, cid, context_limit=65536, max_output=1024)


SPEND = {'calls': 0, 'cloud_tokens': 0, 'active_seconds': 0, 'cost': 0.0}
BUDGET = {'max_model_calls': None, 'max_cloud_tokens': None, 'max_active_seconds': None, 'max_cost': None}


class RotationTests(unittest.TestCase):
    def decide(self, role, history, candidates, policy=None):
        return route(role, 'fix', candidates, history, SPEND, BUDGET, validate_policy(policy or DEFAULT_POLICY), 1000)

    def test_same_level_siblings_take_turns_before_escalating(self):
        cands = [cand('a', 1, 0), cand('b', 1, 1), cand('cloud', 2, kind='cloud')]
        policy = {'attempts_per_level': {'fix': 2}}
        first = self.decide('project_repair', [], cands, policy)
        second = self.decide('project_repair', [{'candidate': 'a', 'level': 1}], cands, policy)
        third = self.decide('project_repair', [{'candidate': 'a', 'level': 1}, {'candidate': 'b', 'level': 1}], cands, policy)
        self.assertEqual((first.candidate, second.candidate, second.reason), ('a', 'b', 'retry_same_level'))
        self.assertEqual((third.candidate, third.escalated), ('cloud', True))

    def test_a_role_can_start_at_a_higher_level_and_falls_back_when_that_is_unusable(self):
        cands = [cand('local', 1), cand('cloud', 2, kind='cloud')]
        policy = {'start_level_by_role': {'project_planner': 2}}
        self.assertEqual(self.decide('project_planner', [], cands, policy).candidate, 'cloud')
        self.assertEqual(self.decide('project_repair', [], cands, policy).candidate, 'local')
        self.assertEqual(self.decide('project_planner', [], [cand('local', 1)], policy).candidate, 'local')


if __name__ == '__main__':
    unittest.main()
