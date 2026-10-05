"""流式传输（W1）：增量解析、真取消、stall 检测、实时进度。含一个真实的本地 HTTP 服务器做集成测试。
Streaming (W1): incremental parsing, real cancellation, stall detection and live progress, with a real local HTTP server for the integration tests."""
import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from masa.domain.models import CallCancelled, MasaError, TransportFailure
from masa.infrastructure.llm import ChatProvider
from masa.infrastructure.streaming import (StreamAssembler, StreamCancelled, StreamError, StreamStalled, StreamTimeout, StreamTooLarge, read_stream)

NL = chr(10)
ANSWER = '{"verdict":"ok","reasons":[],"suggestions":[]}'  # project_triage 的合法回答 / a valid project_triage answer
PIECES = [ANSWER[i:i + 9] for i in range(0, len(ANSWER), 9)]


def ndjson(pieces, final=None):
    lines = [json.dumps({'message': {'role': 'assistant', 'content': p}, 'done': False}) for p in pieces]
    lines.append(json.dumps(final or {'message': {'role': 'assistant', 'content': ''}, 'done': True, 'done_reason': 'stop', 'prompt_eval_count': 11, 'eval_count': 22,
                                      'total_duration': 3_000_000_000, 'load_duration': 1_000_000_000, 'prompt_eval_duration': 500_000_000, 'eval_duration': 2_000_000_000}))
    return ''.join(line + NL for line in lines).encode()


def sse(pieces, usage=True):
    out = [': keep-alive']
    out += ['data: ' + json.dumps({'choices': [{'delta': {'content': p}}]}) for p in pieces]
    out.append('data: ' + json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}]}))
    if usage:
        out.append('data: ' + json.dumps({'choices': [], 'usage': {'prompt_tokens': 5, 'completion_tokens': 9, 'total_tokens': 14}}))
    out.append('data: [DONE]')
    return ''.join(line + NL + NL for line in out).encode()


class AssemblerTests(unittest.TestCase):
    def test_ollama_chunks_are_reassembled_with_the_final_usage(self):
        a = StreamAssembler('ollama')
        data = ndjson(PIECES)
        for i in range(0, len(data), 7):  # 任意切分字节边界 / arbitrary byte boundaries
            a.feed(data[i:i + 7])
        decoded = a.finish()
        self.assertEqual(decoded['message']['content'], ANSWER)
        self.assertEqual((decoded['done'], decoded['eval_count'], decoded['done_reason']), (True, 22, 'stop'))
        self.assertEqual(a.chars, len(ANSWER))

    def test_openai_sse_with_keepalive_comments_and_trailing_usage(self):
        a = StreamAssembler('openai')
        a.feed(sse(PIECES))
        decoded = a.finish()
        self.assertEqual(decoded['choices'][0]['message']['content'], ANSWER)
        self.assertEqual(decoded['choices'][0]['finish_reason'], 'stop')
        self.assertEqual(decoded['usage']['total_tokens'], 14)

    def test_a_complete_non_streaming_body_is_just_a_stream_of_one_chunk(self):
        o = StreamAssembler('ollama')
        o.feed(json.dumps({'message': {'content': ANSWER}, 'done': True, 'done_reason': 'stop', 'eval_count': 3}).encode())  # 没有结尾换行 / no trailing newline
        self.assertEqual(o.finish()['message']['content'], ANSWER)
        c = StreamAssembler('openai')
        c.feed(json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': ANSWER}}], 'usage': {'total_tokens': 4}}).encode())
        self.assertEqual((c.finish()['choices'][0]['message']['content'], c.usage['total_tokens']), (ANSWER, 4))

    def test_a_cut_off_stream_has_no_finish_reason_so_it_is_not_mistaken_for_a_complete_answer(self):
        a = StreamAssembler('openai')
        a.feed(('data: ' + json.dumps({'choices': [{'delta': {'content': '{"verdict":'}}]}) + NL + NL).encode())
        self.assertIsNone(a.finish()['choices'][0]['finish_reason'])  # 下游据此判定“输出不完整” / downstream flags it as incomplete

    def test_in_stream_errors_and_empty_streams(self):
        a = StreamAssembler('ollama')
        with self.assertRaises(StreamError):
            a.feed(json.dumps({'error': 'model not found'}).encode() + NL.encode())
        with self.assertRaises(ValueError):
            StreamAssembler('ollama').finish()
        with self.assertRaises(ValueError):
            StreamAssembler('openai').finish()


class FakeResponse:
    """按时间表给出字节块；到点没数据就像套接字超时一样抛出。 Yields chunks on a schedule; raises like a socket timeout when nothing is due."""

    def __init__(self, schedule, clock):
        self.schedule, self.clock = list(schedule), clock

    def read1(self, n):
        if not self.schedule:
            return b''
        due, data = self.schedule[0]
        if self.clock.now < due:
            self.clock.now += 1.0  # 一次短读超时 / one short read timeout
            raise socket.timeout()
        self.schedule.pop(0)
        return data


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


class ReadLoopTests(unittest.TestCase):
    def run_loop(self, schedule, **kw):
        clock = Clock()
        assembler = StreamAssembler('ollama')
        read_stream(FakeResponse(schedule, clock), assembler, clock=clock, threaded=False, **{'deadline': 10_000, **kw})
        return assembler

    def test_cancellation_is_noticed_between_chunks_and_during_silence(self):
        flag = {'cancel': False}
        clock = Clock()
        response = FakeResponse([(0, ndjson(PIECES[:1])), (500, b'never')], clock)  # 第二块要到 500 秒才来 / the second chunk is 500 s away
        calls = []

        def should_cancel():
            calls.append(clock.now)
            return clock.now >= 3  # 3 秒后取消 / cancel after 3 s
        with self.assertRaises(StreamCancelled):
            read_stream(response, StreamAssembler('ollama'), deadline=10_000, should_cancel=should_cancel, clock=clock, first_token_seconds=1000, stall_seconds=1000, threaded=False)
        self.assertLess(clock.now, 10)  # 静默期间也能在几秒内响应，而不是等 500 秒 / responds within seconds even in silence
        del flag

    def test_silence_before_the_first_token_uses_the_wider_threshold(self):
        with self.assertRaises(StreamStalled) as caught:
            self.run_loop([(500, b'x')], first_token_seconds=30, stall_seconds=5)
        self.assertFalse(caught.exception.started)

    def test_silence_after_content_started_uses_the_stall_threshold(self):
        with self.assertRaises(StreamStalled) as caught:
            self.run_loop([(0, ndjson(PIECES[:1])), (500, b'x')], first_token_seconds=1000, stall_seconds=5)
        self.assertTrue(caught.exception.started)

    def test_the_whole_request_deadline_and_the_byte_limit(self):
        with self.assertRaises(StreamTimeout):
            self.run_loop([(500, b'x')], deadline=20, first_token_seconds=1000)
        with self.assertRaises(StreamTooLarge):
            self.run_loop([(0, b'x' * 100)], max_bytes=50)

    def test_progress_is_reported_with_character_counts(self):
        seen = []
        clock = Clock()
        schedule = [(i, ndjson([PIECES[i]]).split(NL.encode())[0] + NL.encode()) for i in range(len(PIECES))]
        read_stream(FakeResponse(schedule, clock), StreamAssembler('ollama'), deadline=10_000, clock=clock, on_progress=seen.append, threaded=False)
        self.assertTrue(seen)
        self.assertEqual([s['chars'] for s in seen], sorted(s['chars'] for s in seen))  # 单调增加 / monotonic


class Handler(BaseHTTPRequestHandler):
    mode = 'ndjson'
    disconnected = threading.Event()

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        self.send_response(200)
        self.send_header('Content-Type', 'application/x-ndjson')
        self.end_headers()
        try:
            if self.mode == 'ndjson':
                self.wfile.write(ndjson(PIECES))
            elif self.mode == 'sse':
                self.wfile.write(sse(PIECES))
            elif self.mode == 'endless':  # 一直发，直到客户端断开 / keep sending until the client disconnects
                while True:
                    self.wfile.write(ndjson(['x'])[:-len(ndjson([]))] if False else (json.dumps({'message': {'content': 'x'}, 'done': False}) + NL).encode())
                    self.wfile.flush()
                    time.sleep(0.1)
            elif self.mode == 'stall':  # 发一块之后沉默 / one chunk, then silence
                self.wfile.write((json.dumps({'message': {'content': '{"verdict"'}, 'done': False}) + NL).encode())
                self.wfile.flush()
                time.sleep(14)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            Handler.disconnected.set()


class ServerCase(unittest.TestCase):
    def setUp(self):
        Handler.disconnected = threading.Event()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)  # 先加的后执行：先 shutdown 再 close / cleanups run in reverse: shutdown first, then close
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def provider(self, protocol='ollama', **extra):
        config = {'base_url': f'http://127.0.0.1:{self.port}', 'model': 'm', 'model_type': 'local', 'protocol': protocol, 'context_limit': 8192, 'max_output_tokens': 256, **extra}
        return ChatProvider(config, '')

    CONTEXT = {'purpose': 'project_triage', 'goal': 'a small cli'}


class IntegrationTests(ServerCase):
    def test_an_ollama_ndjson_stream_gives_the_same_result_and_metrics_as_a_one_shot_response(self):
        Handler.mode = 'ndjson'
        provider = self.provider()
        self.assertEqual(provider.respond(self.CONTEXT)['verdict'], 'ok')
        self.assertEqual(provider.usage, {'prompt_tokens': 11, 'completion_tokens': 22, 'total_tokens': 33})
        self.assertEqual(provider.metrics['generation_tokens_per_second'], 11.0)  # 22 token / 2 秒 / 22 tokens over 2 s

    def test_an_openai_sse_stream_with_usage(self):
        Handler.mode = 'sse'
        provider = self.provider('openai')
        self.assertEqual(provider.respond(self.CONTEXT)['verdict'], 'ok')
        self.assertEqual(provider.usage['total_tokens'], 14)

    def test_live_progress_reaches_the_callback(self):
        Handler.mode = 'ndjson'
        provider = self.provider()
        seen = []
        provider.on_progress = seen.append
        provider.respond(self.CONTEXT)
        self.assertTrue(seen and seen[-1]['chars'] > 0)

    def test_cancellation_mid_stream_closes_the_connection_quickly_and_the_server_notices(self):
        Handler.mode = 'endless'
        provider = self.provider()
        started = time.monotonic()
        provider.should_cancel = lambda: time.monotonic() - started > 0.8
        with self.assertRaises(CallCancelled):
            provider.respond(self.CONTEXT)
        self.assertLess(time.monotonic() - started, 3.0)  # 以前要等整个响应结束 / previously the whole response had to finish first
        self.assertTrue(Handler.disconnected.wait(3.0))  # 服务端看到了断开（Ollama 据此停止生成） / the server saw the disconnect (Ollama stops generating)

    def test_a_stalled_local_call_is_a_free_retry_but_a_stalled_cloud_call_is_never_replayed(self):
        Handler.mode = 'stall'
        local = self.provider(stall_seconds=5, first_token_seconds=10)
        started = time.monotonic()
        with self.assertRaises(TransportFailure) as local_failure:  # 免费本地调用：可安全重试 / free: safe to retry
            local.respond(self.CONTEXT)
        self.assertIn('stalled after it started answering', str(local_failure.exception))  # 确认是 stall 而不是别的传输错误 / really a stall, not some other transport error
        self.assertLess(time.monotonic() - started, 8.0)
        cloud = self.provider(stall_seconds=5, first_token_seconds=10)
        cloud.config['model_type'] = 'cloud'  # 测试里复用本地回环服务器；映射只看 model_type / reuse the loopback server; the mapping only looks at model_type
        with self.assertRaises(MasaError) as caught:
            cloud.respond(self.CONTEXT)
        self.assertNotIsInstance(caught.exception, TransportFailure)  # 结果未知、不重放 / outcome unknown, never replayed
        self.assertIn('stalled after it started answering', str(caught.exception))
        self.assertIn('billing may be unknown', str(caught.exception))

    def test_streaming_can_be_switched_off_per_profile(self):
        Handler.mode = 'ndjson'
        provider = self.provider(streaming=False)
        self.assertEqual(provider.respond(self.CONTEXT)['verdict'], 'ok')  # 服务端仍然流式回答，旧路径也能解析 / the old path parses it too


if __name__ == '__main__':
    unittest.main()
