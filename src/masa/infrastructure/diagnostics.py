"""有界后台故障日志，不保存请求正文和异常原文。 Bounded faults without bodies or exception text."""
import json
import threading
from datetime import datetime, timezone
import uuid


class DiagnosticLog:
    def __init__(self, root, limit_bytes=512 * 1024):
        """仅写入忽略的本地状态目录。 Store diagnostics only in the ignored local state directory."""
        self.path = root / 'service-errors.jsonl'
        self.limit_bytes = limit_bytes
        self.lock = threading.Lock()

    def record(self, route, exc):
        """保留类型和调用位置，去掉可能包含密钥的异常文字。 Keep types and frames, omitting potentially secret-bearing messages."""
        entry = {'request_id': uuid.uuid4().hex[:12], 'time': datetime.now(timezone.utc).isoformat(),
                 'route': route, 'error_type': type(exc).__name__, 'frames': []}
        seen = set()
        while exc is not None and id(exc) not in seen:
            seen.add(id(exc))
            trace = exc.__traceback__
            while trace:
                frame = trace.tb_frame
                entry['frames'].append({'file': frame.f_code.co_filename.replace('\\', '/').rsplit('/', 1)[-1],
                                        'function': frame.f_code.co_name, 'line': trace.tb_lineno})
                trace = trace.tb_next
            exc = exc.__cause__ or exc.__context__
        entry['frames'] = entry['frames'][-16:]
        raw = json.dumps(entry, ensure_ascii=False) + '\n'
        try:
            with self.lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size >= self.limit_bytes:
                    self.path.replace(self.path.with_suffix('.jsonl.1'))
                with self.path.open('a', encoding='utf-8') as handle:
                    handle.write(raw)
        except OSError:
            pass  # Logging failure must not replace the original HTTP error.
        return entry['request_id']

    def recent(self):
        """返回最近二十条安全故障，损坏行独立跳过。 Return twenty safe faults, skipping individual corrupt lines."""
        with self.lock:
            try:
                with self.path.open('rb') as handle:
                    handle.seek(0, 2)
                    handle.seek(max(0, handle.tell() - self.limit_bytes))
                    lines = handle.read(self.limit_bytes).decode('utf-8', errors='replace').splitlines()
            except OSError:
                return {'errors': []}
        entries = []
        for line in lines:
            try:
                entry = json.loads(line)
                if isinstance(entry, dict): entries.append(entry)
            except ValueError:
                continue
        return {'errors': entries[-20:]}
