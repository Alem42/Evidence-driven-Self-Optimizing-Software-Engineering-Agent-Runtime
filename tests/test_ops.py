"""无人值守的运维层：事件流、webhook、待处理队列（不确定的付费调用）、停滞规则、看门狗的退避，以及 /api/ops 的访问控制。全部零 token、不起真实模型。
The unattended-operation layer: the event feed, the webhook, the pending queue (uncertain paid calls), the stall rule, the watchdog's backoff and the /api/ops access control. Zero tokens, no real model."""
import http.client
import json
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from masa.application.console import Console
from masa.application.ops import EventSink, Ops
from masa.domain.models import MasaError
from masa.infrastructure.store import Store
from masa.interfaces import watchdog
from masa.interfaces.http.server import make_server
from test_recovery_classes import Base, Crash, Provider


class EventSinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sink = EventSink(Path(self.temp.name))

    def test_events_come_back_in_order_after_a_cursor(self):
        ids = [self.sink.emit('a'), self.sink.emit('b', 'two'), self.sink.emit('c', level='warn')]
        self.assertEqual([e['kind'] for e in self.sink.events()], ['a', 'b', 'c'])
        self.assertEqual([e['kind'] for e in self.sink.events(since=ids[0])], ['b', 'c'])
        self.assertEqual(self.sink.last_id(), ids[2])

    def test_the_webhook_carries_only_the_kind_ids_and_one_sentence(self):
        self.sink.save_config({'webhook_url': 'http://127.0.0.1:9/hook'})
        sent = []

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def urlopen(request, timeout=0):
            sent.append(json.loads(request.data))
            return Response()
        run_inline = lambda target, args, daemon: types.SimpleNamespace(start=lambda: target(*args))  # noqa: E731  同步执行，测试里不留线程 / run synchronously, no thread is left behind
        with patch('masa.application.ops.urllib.request.urlopen', urlopen), patch('masa.application.ops.threading.Thread', side_effect=run_inline):
            self.sink.emit('job_failed', 'x' * 500, 'run-1', 'warn')
        self.assertEqual(set(sent[0]), {'id', 'kind', 'level', 'run_id', 'message', 'ts'})
        self.assertEqual((sent[0]['kind'], sent[0]['run_id'], len(sent[0]['message'])), ('job_failed', 'run-1', 300))

    def test_a_failing_webhook_is_recorded_once_and_never_raises(self):
        self.sink.save_config({'webhook_url': 'http://127.0.0.1:9/hook'})
        run_inline = lambda target, args, daemon: types.SimpleNamespace(start=lambda: target(*args))  # noqa: E731
        with patch('masa.application.ops.urllib.request.urlopen', side_effect=OSError('refused')), patch('masa.application.ops.threading.Thread', side_effect=run_inline):
            self.sink.emit('a')
            self.sink.emit('b')
        self.assertEqual([e['kind'] for e in self.sink.events()].count('webhook_failed'), 1)

    def test_config_is_validated_and_the_token_is_stable_and_never_in_the_public_view(self):
        token = self.sink.config()['token']
        self.assertEqual(self.sink.config()['token'], token)
        self.assertNotIn('token', self.sink.save_config({'stall_minutes': 45}))
        for bad in ({'webhook_url': 'ftp://x'}, {'stall_minutes': 1}, {'stall_action': 'explode'}):
            with self.assertRaises(MasaError):
                self.sink.save_config(bad)


class PendingTests(Base):
    def fake_console(self, jobs=None):
        return types.SimpleNamespace(root=self.root, jobs=jobs if jobs is not None else {}, job_thread=None, control=lambda *a: None, bench_stop=lambda: None,
                                     _release_local_models=lambda: None, start_autonomous_project_job=lambda body, resume_job=None: {'job_id': resume_job})

    def test_a_crashed_paid_call_is_listed_and_a_retry_decision_unblocks_exactly_one_attempt(self):
        with self.assertRaises(Crash):
            self.call(Provider('cloud', 'crash'))
        ops = Ops(self.fake_console())
        item = next(p for p in ops.pending() if p['kind'] == 'uncertain_paid_call')
        self.assertEqual((item['run_id'], item['step_id'], item['retry_approved']), (self.rid, 'project_planner', False))
        ops.resolve({'kind': 'uncertain_paid_call', 'run_id': self.rid, 'step_id': 'project_planner', 'invocation_id': 'initial', 'action': 'retry'})
        self.assertTrue(next(p for p in ops.pending() if p['kind'] == 'uncertain_paid_call')['retry_approved'])
        retry = Provider('cloud')
        self.call(retry)
        self.assertEqual(retry.calls, 1)
        self.assertEqual([p for p in ops.pending() if p['kind'] == 'uncertain_paid_call'], [])  # 重发之后不再待处理 / no longer pending once it ran

    def test_a_free_local_call_is_never_listed(self):
        with self.assertRaises(Crash):
            self.call(Provider('local', 'crash', free_retry=True))
        self.assertEqual(Ops(self.fake_console()).pending(), [])

    def test_an_interrupted_job_with_an_uncertain_call_cannot_be_resumed_until_decided(self):
        with self.assertRaises(Crash):
            self.call(Provider('cloud', 'crash'))
        jobs = {'j1': {'status': 'interrupted', 'mode': 'auto', 'run_id': self.rid, 'phase': 'generation', 'started': 1, 'note': 'restarted'}}
        ops = Ops(self.fake_console(jobs))
        job = next(p for p in ops.pending() if p['kind'] == 'interrupted_job')
        self.assertTrue(job['blocked_by'])
        with self.assertRaisesRegex(MasaError, 'uncertain paid calls'):
            ops.resolve({'kind': 'interrupted_job', 'id': 'j1', 'action': 'resume'})

    def test_an_interrupted_job_without_uncertain_calls_resumes(self):
        jobs = {'j2': {'status': 'interrupted', 'mode': 'auto', 'run_id': self.rid, 'phase': 'planning', 'started': 1}}
        ops = Ops(self.fake_console(jobs))
        self.assertEqual(ops.resolve({'kind': 'interrupted_job', 'id': 'j2', 'action': 'resume'}), {'job_id': 'j2'})
        self.assertEqual(ops.auto_resume(), 'j2')  # 看门狗 --auto-resume 走的路 / the path of the watchdog's --auto-resume


class MonitorTests(Base):
    def test_state_changes_become_events_once_and_a_stall_is_reported_once(self):
        clock = [1000.0]
        jobs = {'old': {'status': 'completed', 'mode': 'auto', 'run_id': self.rid, 'started': 1}}
        console = types.SimpleNamespace(root=self.root, jobs=jobs, job_thread=types.SimpleNamespace(is_alive=lambda: True), controlled=[])
        console.control = lambda rid, action: console.controlled.append((rid, action))
        ops = Ops(console, clock=lambda: clock[0])
        ops.sink.save_config({'stall_minutes': 5, 'stall_action': 'stop'})
        ops.tick()
        self.assertEqual([e['kind'] for e in ops.sink.events()], ['service_started'])  # 历史任务不是新闻 / a finished job from before is not news
        jobs['new'] = {'status': 'running', 'mode': 'auto', 'run_id': self.rid, 'started': 1000.0, 'phase': 'generation'}
        ops.tick()
        jobs['new']['status'] = 'completed'
        ops.tick()
        ops.tick()
        self.assertEqual([e['kind'] for e in ops.sink.events()], ['service_started', 'job_completed'])
        # 停滞：任务在跑，账本 10 分钟没有任何新事件 / a stall: the job runs and the ledger has been silent for ten minutes
        jobs['stuck'] = {'status': 'running', 'mode': 'auto', 'run_id': self.rid, 'started': 1.0, 'phase': 'repair'}
        clock[0] = 1e10
        with patch('masa.application.ops.live.get', return_value=None):
            ops.tick()
            ops.tick()
        kinds = [e['kind'] for e in ops.sink.events()]
        self.assertEqual((kinds.count('run_stalled'), kinds.count('run_stopped_stalled')), (1, 1))
        self.assertEqual(console.controlled, [(self.rid, 'cancel')])


class WatchdogTests(unittest.TestCase):
    def run_watch(self, codes, **kwargs):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        pauses, spawned = [], []

        class Process:
            def __init__(self, command, env):
                spawned.append(env)
                self.code = codes.pop(0)

            def wait(self):
                return self.code
        result = watchdog.run(Path(temp.name), ['--port', '0'], sleep=pauses.append, popen=Process, **kwargs)
        return result, pauses, spawned, [e['kind'] for e in EventSink(Path(temp.name)).events()]

    def test_a_crash_is_restarted_with_backoff_and_a_clean_exit_is_not(self):
        result, pauses, spawned, kinds = self.run_watch([1, 1, 0])
        self.assertEqual((result, pauses), (0, [1, 2]))
        self.assertEqual(kinds, ['watchdog_started_service', 'service_crashed', 'service_restarted', 'service_crashed', 'service_restarted', 'service_stopped'])
        self.assertEqual([env['MASA_RESTARTS'] for env in spawned], ['0', '1', '2'])

    def test_it_gives_up_after_too_many_restarts_and_says_so(self):
        result, pauses, spawned, kinds = self.run_watch([1, 1, 1], max_restarts=2)
        self.assertEqual(result, 1)
        self.assertEqual(kinds[-1], 'watchdog_gave_up')
        self.assertEqual(len(spawned), 3)

    def test_auto_resume_is_only_passed_down_when_asked(self):
        self.assertNotIn('MASA_AUTO_RESUME', self.run_watch([0])[2][0])
        self.assertEqual(self.run_watch([0], auto_resume=True)[2][0]['MASA_AUTO_RESUME'], '1')


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.console = Console(Path(self.temp.name) / 'state', 'missing', 'missing', Path(self.temp.name))
        self.server = make_server(self.console, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.token = self.request('/api/session', headers={'Origin': 'http://localhost:5173'})[1]['token']

    def request(self, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        values = {'Content-Type': 'application/json', **(headers or {})}
        connection.request('GET' if body is None else 'POST', path, None if body is None else json.dumps(body), values)
        response = connection.getresponse()
        status, raw = response.status, response.read().decode()
        connection.close()
        return status, json.loads(raw)

    def test_the_ops_api_works_with_the_session_token_or_the_ops_token_and_nothing_else(self):
        status, state = self.request('/api/ops/status', headers={'X-MASA-Token': self.token})
        self.assertEqual((status, state['pending'], state['worker_alive']), (200, 0, False))
        ops_token = self.request('/api/ops/config', headers={'X-MASA-Token': self.token})[1]['token']
        self.assertEqual(self.request('/api/ops/pending', headers={'X-MASA-Ops-Token': ops_token})[0], 200)
        self.assertEqual(self.request('/api/ops/events?since=0', headers={'X-MASA-Ops-Token': ops_token})[0], 200)
        self.assertEqual(self.request('/api/ops/status')[0], 403)
        self.assertEqual(self.request('/api/ops/status', headers={'X-MASA-Ops-Token': 'wrong'})[0], 403)
        self.assertEqual(self.request('/api/projects', headers={'X-MASA-Ops-Token': ops_token})[0], 403)  # 运维令牌只开 /api/ops/* / the ops token opens /api/ops/* only


if __name__ == '__main__':
    unittest.main()
