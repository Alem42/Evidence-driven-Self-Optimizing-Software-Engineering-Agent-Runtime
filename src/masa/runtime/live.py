"""正在进行的模型调用的实时进度（只在内存里，不进账本）。界面据此显示“正在生成…”。
Live progress of in-flight model calls (in memory only, never in the ledger); the UI shows "generating..." from it.
"""
import threading
import time

_LOCK = threading.Lock()
_LIVE = {}


MAX_TEXT = 262144  # 一次调用保留的完整文本上限 / full text kept for one call
TAIL_CHARS = 2400  # 只保留最近这么多字符：够人看“它在写什么”，又不会把内存和网络占满 / enough to see what it is writing without filling memory or the network


def begin(run_id, step, invocation_id, model):
    """一次新的模型调用开始：清掉上一次调用的文本，记下是哪个角色的哪次调用。前端据此知道“现在这一段输出属于哪次调用”。
    A new model call starts: drop the previous call's text and remember which call this is, so the UI knows what the text belongs to."""
    with _LOCK:
        _LIVE[run_id] = {'step': step, 'invocation': invocation_id, 'model': model, 'chars': 0, 'seconds': 0, 'updated': time.time(), 'tail': '', 'parts': [], 'size': 0, 'seq': 0, 'truncated': False}


def update(run_id, step, model, info):
    """记录某个 run 当前调用的进度：已生成字符数、已用秒数；实时输出的尾巴保留。 Record the current call's progress for a run; the live-output tail is kept."""
    with _LOCK:
        old = _LIVE.get(run_id) or {}
        tail = old.get('tail', '') if old.get('step') in (None, step) else ''  # 同一步骤（或还没记步骤）保留尾巴 / keep the tail within the same step
        _LIVE[run_id] = {**old, 'step': step, 'model': model, 'chars': info.get('chars', 0), 'seconds': info.get('seconds', 0), 'updated': time.time(), 'tail': tail}


def append(run_id, text):
    """模型每吐出一段内容就追加（只在内存里）。 Append every piece of model output (memory only)."""
    with _LOCK:
        entry = _LIVE.setdefault(run_id, {'step': None, 'model': None, 'chars': 0, 'seconds': 0, 'updated': time.time(), 'tail': ''})
        entry['tail'] = (entry.get('tail', '') + text)[-TAIL_CHARS:]
        # 完整文本（上限 256 KB）：前端要从里面解出正在写的文件。超限后停止追加并标记，宁可显示不全也不显示错位的内容。
        # The full text (256 KB cap) from which the UI decodes the file being written. Past the cap we stop appending and flag it: incomplete beats misaligned.
        if not entry.get('truncated'):
            if entry.get('size', 0) + len(text) > MAX_TEXT:
                entry['truncated'] = True
            else:
                entry.setdefault('parts', []).append(text)  # 追加到列表，读取时才拼接：不会每个数据块都复制整段文本 / appended to a list and joined on read, never re-copying the whole text per chunk
                entry['size'] = entry.get('size', 0) + len(text)
        entry['seq'] = entry.get('seq', 0) + 1
        entry['updated'] = time.time()


def get(run_id):
    with _LOCK:
        entry = _LIVE.get(run_id)
        if not entry:
            return None
        copy = {k: v for k, v in entry.items() if k not in ('parts', 'size')}
        copy['text'] = ''.join(entry.get('parts', ()))
        return copy


def clear(run_id):
    with _LOCK:
        _LIVE.pop(run_id, None)
