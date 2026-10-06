"""模型响应的流式读取：增量解析 + 取消 + stall 检测。不依赖第三方库（urllib 的响应本来就可以增量读）。
Streaming reads of model responses: incremental parsing + cancellation + stall detection. No third-party library (urllib responses can be read incrementally).

为什么流式（M2 的 W1）：之前一次请求只能整个等回来，所以①“停止”要等当前调用返回才生效（最长几分钟）；②分不清“慢”和“卡死”；
③界面看不到“正在生成”。流式之后每个数据块之间都能检查取消标志，没有任何字节到达超过阈值就判为 stall，进度可以实时显示。
Why streaming (W1 of M2): a blocking request could only be awaited, so (1) "stop" took effect only after the call returned (minutes), (2) slow and stuck looked the same,
(3) the UI could not show that something was being generated. With streaming every chunk is a chance to check cancellation, silence beyond a threshold is a stall,
and progress is live.

两种线路格式 / two wire formats:
- Ollama 原生 /api/chat：每行一个 JSON（NDJSON），最后一行 done=true 并带用量。 One JSON object per line; the last has done=true plus the usage counters.
- OpenAI 兼容：SSE，`data: {...}` 行，最后 `data: [DONE]`；用量在 include_usage 的末尾块里。 SSE `data:` lines ending with `[DONE]`; usage arrives in a trailing include_usage chunk.
一个完整的非流式 JSON 响应同样能被解析（它就是只有一个块的流），所以旧的测试替身和不支持流式的服务仍然可用。
A complete non-streaming JSON body parses too (it is a stream of one chunk), so old test doubles and servers without streaming keep working.
"""
import json
import queue
import socket
import threading
import time

READ_SIZE = 65536
CANCEL_POLL_SECONDS = 0.3
MAX_BYTES = 16 * 1048576  # 原始字节的安全上限（SSE 每个 token 约 250 字节外壳，所以要远大于内容上限）/ safety cap on RAW bytes (SSE adds about 250 bytes of framing per token, so it must far exceed the content cap)
MAX_CHARS = 1048576  # 解码后的内容上限：这才是“响应太大”的真正含义 / cap on the decoded CONTENT, which is what "response too large" really means


class StreamCancelled(Exception):
    """调用方要求取消。 The caller asked to cancel."""


class StreamStalled(Exception):
    """太久没有任何字节到达。started=已经收到过内容。 No bytes for too long; started tells whether content had begun."""

    def __init__(self, started):
        super().__init__('stalled')
        self.started = started


class StreamTimeout(Exception):
    """超过整个请求的时限。 The whole-request deadline passed."""


class StreamTooLarge(Exception):
    """响应超过字节上限。 The response exceeded the byte limit."""


class StreamError(Exception):
    """服务端在流里报了错（Ollama 会在流中途发 {"error": ...}）。 The server reported an error inside the stream."""


class StreamAssembler:
    """把数据块拼回和非流式响应同形状的结果，下游的用量/完成标记/校验逻辑完全不用改。
    Rebuilds the same shape a non-streaming response has, so downstream usage / completion / validation code is unchanged."""

    def __init__(self, protocol):
        self.ollama = protocol == 'ollama'
        self.buffer = b''
        self.content = []
        self.chars = 0
        self.last = None  # Ollama：最后一个块 / the last chunk
        self.finish_reason = None
        self.usage = None
        self.refusal = None
        self.done = False

    def feed(self, data):
        """喂入新字节，返回新增的内容字符数。 Feed bytes; returns how many content characters were added."""
        self.buffer += data
        before = self.chars
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            self._line(line.strip())
        return self.chars - before

    def _add(self, text):
        if text:
            self.content.append(text)
            self.chars += len(text)

    def _line(self, line):
        if not line or line.startswith(b':'):  # 空行与 SSE 注释（keep-alive）只算“有字节”，不含内容 / blank lines and SSE comments carry no content
            return
        if line.startswith(b'data:'):
            line = line[5:].strip()
            if line == b'[DONE]':
                self.done = True
                return
        try:
            chunk = json.loads(line)
        except ValueError:
            return  # 半行或非 JSON：忽略，缺的内容会在 finish() 里表现为不完整 / partial or non-JSON: ignored; missing content shows up as incomplete in finish()
        if not isinstance(chunk, dict):
            return
        if isinstance(chunk.get('error'), (str, dict)):
            raise StreamError(str(chunk['error'])[:300])
        if self.ollama:
            self._add((chunk.get('message') or {}).get('content') or '')
            self.last = chunk
            return
        if isinstance(chunk.get('usage'), dict):
            self.usage = chunk['usage']
        for choice in chunk.get('choices') or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get('delta') or choice.get('message') or {}
            self._add(delta.get('content') or '')
            if delta.get('refusal'):
                self.refusal = delta['refusal']
            if choice.get('finish_reason'):
                self.finish_reason = choice['finish_reason']

    def finish(self):
        """返回与非流式相同形状的 dict；什么都没收到就抛 ValueError。 Same shape as a non-streaming response; ValueError when nothing was received."""
        if self.buffer.strip():  # 最后一行没有换行结尾 / a last line without a trailing newline
            self._line(self.buffer.strip())
            self.buffer = b''
        text = ''.join(self.content)
        if self.ollama:
            if self.last is None:
                raise ValueError('empty stream')
            decoded = dict(self.last)
            decoded['message'] = {'role': 'assistant', 'content': text}
            return decoded
        if self.finish_reason is None and not self.content and self.usage is None and not self.done:
            raise ValueError('empty stream')
        message = {'role': 'assistant', 'content': text}
        if self.refusal:
            message['refusal'] = self.refusal
        return {'choices': [{'finish_reason': self.finish_reason, 'message': message}], 'usage': self.usage}


def _read_loop(response, assembler, *, deadline, should_cancel=None, first_token_seconds=600, stall_seconds=120, on_progress=None,
                clock=time.monotonic, max_bytes=MAX_BYTES, max_chars=MAX_CHARS, poll=CANCEL_POLL_SECONDS, threaded=True):
    """读完整个流。每次循环（至多每秒一次，因为套接字有短读超时）检查：取消、总时限、是否 stall。
    Read the whole stream. Every iteration (at least once a second thanks to a short socket read timeout) checks cancellation, the deadline and stalls."""
    started = last_bytes = clock()
    last_check = last_report = 0.0
    total = 0
    chunks = None
    if threaded:
        # 套接字读超时之后，同一个响应对象再读会抛 “cannot read from timed out object”，所以不能靠短读超时来轮询。
        # 标准做法：读取线程把数据块放进队列，主循环带超时地取；取消/stall 时关闭连接，阻塞中的读取线程随之结束。
        # After a socket read timeout the same response object refuses further reads, so short read timeouts cannot be used for polling. Standard fix: a reader thread feeds a
        # queue and the main loop polls it with a timeout; on cancel or stall the connection is closed, which ends the blocked reader.
        chunks = queue.Queue()

        def pump():
            try:
                while True:
                    data = response.read1(READ_SIZE)
                    chunks.put(data)
                    if not data:
                        return
            except BaseException as failure:  # 连接被关闭或服务端断开 / connection closed or server gone
                chunks.put(failure)

        threading.Thread(target=pump, daemon=True, name='masa-stream-reader').start()
    while True:
        now = clock()
        if now >= deadline:
            raise StreamTimeout()
        if should_cancel is not None and now - last_check >= poll:
            last_check = now
            if should_cancel():
                raise StreamCancelled()
        # 还没有任何内容时用更宽的“首字”阈值：本地模型加载、长提示词的预填充都可能很久才出第一个字。
        # Before any content, use the wider first-token threshold: loading a local model or prefilling a long prompt can take a long time.
        limit = stall_seconds if assembler.chars else first_token_seconds
        if now - last_bytes > limit:
            raise StreamStalled(started=bool(assembler.chars))
        if chunks is not None:
            try:
                chunk = chunks.get(timeout=0.25)
            except queue.Empty:
                continue
            if isinstance(chunk, BaseException):
                raise chunk
        else:
            try:
                chunk = response.read1(READ_SIZE)
            except (socket.timeout, TimeoutError):
                continue
        if not chunk:
            return
        last_bytes = clock()
        total += len(chunk)
        if total > max_bytes:
            raise StreamTooLarge()
        assembler.feed(chunk)
        if assembler.chars > max_chars:
            raise StreamTooLarge()
        if on_progress is not None and last_bytes - last_report >= 0.5:
            last_report = last_bytes
            on_progress({'chars': assembler.chars, 'seconds': round(last_bytes - started, 1)})
    return


def abandon(response):
    """异常退出时丢弃连接，且**不让调用方等待**：先 shutdown 底层套接字，再把 close() 交给后台线程。
    同步 close() 会等读取线程释放 BufferedReader 的读锁，而读取线程正阻塞在 recv 里——要等到服务端的下一个字节或超时才会返回
    （实测：stall 在 5 秒就检测到了，同步关闭却又等了 9 秒）。在 Windows 上对一个带超时的阻塞 recv 做 shutdown 也唤不醒它，所以关闭必须异步。
    Discard the connection after an exceptional exit WITHOUT making the caller wait: shut the socket down, then close() in a background thread. A synchronous close() waits for
    the reader thread to release the BufferedReader lock while that thread is blocked in recv, i.e. for the next byte or a timeout (a stall detected at 5 s then waited 9 s more).
    On Windows shutdown() cannot wake a timed-out blocking recv either, so the close has to be asynchronous."""
    sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def close():
        try:
            response.close()
        except Exception:
            pass

    threading.Thread(target=close, daemon=True, name='masa-stream-closer').start()


read_stream = _read_loop
