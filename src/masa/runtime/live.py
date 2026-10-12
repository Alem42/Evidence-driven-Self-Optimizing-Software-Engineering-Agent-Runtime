"""正在进行的模型调用的实时进度（只在内存里，不进账本）。界面据此显示“正在生成…”。
Live progress of in-flight model calls (in memory only, never in the ledger); the UI shows "generating..." from it.
"""
import threading
import time

_LOCK = threading.Lock()
_LIVE = {}


TAIL_CHARS = 2400  # 只保留最近这么多字符：够人看“它在写什么”，又不会把内存和网络占满 / enough to see what it is writing without filling memory or the network


def update(run_id, step, model, info):
    """记录某个 run 当前调用的进度：已生成字符数、已用秒数；实时输出的尾巴保留。 Record the current call's progress for a run; the live-output tail is kept."""
    with _LOCK:
        old = _LIVE.get(run_id) or {}
        tail = old.get('tail', '') if old.get('step') in (None, step) else ''  # 同一步骤（或还没记步骤）保留尾巴 / keep the tail within the same step
        _LIVE[run_id] = {'step': step, 'model': model, 'chars': info.get('chars', 0), 'seconds': info.get('seconds', 0), 'updated': time.time(), 'tail': tail}


def append(run_id, text):
    """模型每吐出一段内容就追加（只在内存里）。 Append every piece of model output (memory only)."""
    with _LOCK:
        entry = _LIVE.setdefault(run_id, {'step': None, 'model': None, 'chars': 0, 'seconds': 0, 'updated': time.time(), 'tail': ''})
        entry['tail'] = (entry.get('tail', '') + text)[-TAIL_CHARS:]
        entry['updated'] = time.time()


def get(run_id):
    with _LOCK:
        entry = _LIVE.get(run_id)
        return dict(entry) if entry else None


def clear(run_id):
    with _LOCK:
        _LIVE.pop(run_id, None)
