"""正在进行的模型调用的实时进度（只在内存里，不进账本）。界面据此显示“正在生成…”。
Live progress of in-flight model calls (in memory only, never in the ledger); the UI shows "generating..." from it.
"""
import threading
import time

_LOCK = threading.Lock()
_LIVE = {}


def update(run_id, step, model, info):
    """记录某个 run 当前调用的进度：已生成字符数、已用秒数。 Record the current call's progress for a run."""
    with _LOCK:
        _LIVE[run_id] = {'step': step, 'model': model, 'chars': info.get('chars', 0), 'seconds': info.get('seconds', 0), 'updated': time.time()}


def get(run_id):
    with _LOCK:
        entry = _LIVE.get(run_id)
        return dict(entry) if entry else None


def clear(run_id):
    with _LOCK:
        _LIVE.pop(run_id, None)
