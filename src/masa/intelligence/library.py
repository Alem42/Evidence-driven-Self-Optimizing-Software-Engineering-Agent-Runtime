"""经验库（最小版）：记录“失败签名 → 被验证有效的做法”，有容量上限，按效用淘汰，命中才取。全部确定性，0 token。
Experience library (minimal): "failure signature -> a remedy that was verified to help", bounded in size, evicted by utility, read only on a hit. Fully deterministic, zero tokens.

设计要点 / design (docs/design/IDEA_EXPERIENCE_LIBRARY.md):
  · 只存**有证据**的做法：修复之后未解决条目确实变少或验证通过，才记为成功；做法文本来自账本事实（改了哪些文件、Diagnoser 的结论），不让模型自由总结。
    Only evidence-backed remedies: a remedy counts as a success only when the unresolved items really dropped or verification passed; its text comes from ledger facts, never a free model summary.
  · 签名 = 失败类别 + 归一化后的错误信息（去掉路径、行号、数字、引号里的内容），所以不同任务里“同一种错”会落到同一条。
    Signature = failure class + normalised message (paths, line numbers, numbers and quoted text removed), so the same kind of mistake in different tasks lands on one entry.
  · 延迟检索：只在修复阶段（已经失败）才查，命中且有成功记录才注入；大多数任务一轮就过，所以不增加 token。
    Lazy retrieval: consulted only in the repair stage (after a failure), injected only on a hit with a success record, so tasks that pass the first time pay nothing.
  · 容量上限（字节和条数）；超出时淘汰效用最低的：效用 = 成功率（拉普拉斯平滑）× (1 + ln(1 + 命中次数)) × 新近度衰减。
    Bounded (bytes and entries); the lowest-utility entry goes first: utility = smoothed success rate x (1 + ln(1 + hits)) x recency decay.
"""
import math
import re
import sqlite3
import time
from pathlib import Path

CAPACITY_BYTES = 64 * 1024
MAX_ENTRIES = 200
HALF_LIFE_DAYS = 30.0
_PATH = re.compile(r'(?:[A-Za-z]:)?[\w./\\-]*\.go(?::\d+){0,2}')
_QUOTED = re.compile(r'"[^"]*"|\'[^\']*\'|`[^`]*`')
_NUMBER = re.compile(r'\d+(?:\.\d+)?')


def signature(kind, message):
    """失败类别 + 归一化信息；类别也进签名，避免不同归属的同句话混在一起。 Class + normalised message; the class is part of it so equal wording with a different owner stays apart."""
    text = _PATH.sub('FILE', str(message))
    text = _QUOTED.sub('S', text)
    text = _NUMBER.sub('N', text)
    text = re.sub(r'\s+', ' ', text).strip().lower()[:160]
    return f'{kind}|{text}'


class Library:
    def __init__(self, path, capacity_bytes=CAPACITY_BYTES, max_entries=MAX_ENTRIES, clock=time.time):
        self.capacity_bytes, self.max_entries, self.clock = capacity_bytes, max_entries, clock
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.execute('''CREATE TABLE IF NOT EXISTS entries (
            signature TEXT PRIMARY KEY, kind TEXT NOT NULL, remedy TEXT NOT NULL DEFAULT '', evidence TEXT NOT NULL DEFAULT '',
            hits INTEGER NOT NULL DEFAULT 0, successes INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, last_used REAL NOT NULL, bytes INTEGER NOT NULL)''')
        self.db.commit()

    def close(self):
        self.db.close()

    # ───────────── 写 / write ─────────────
    def record_failure(self, kind, message):
        """一次失败出现：命中次数加一（没有就新建）。返回签名。 A failure occurred: hits + 1 (created if new). Returns the signature."""
        sig, now = signature(kind, message), self.clock()
        self.db.execute('INSERT INTO entries(signature,kind,created,last_used,bytes,hits) VALUES(?,?,?,?,?,1) ON CONFLICT(signature) DO UPDATE SET hits=hits+1,last_used=?',
                        (sig, kind, now, now, len(sig), now))
        self.db.commit()
        self.evict()
        return sig

    def record_remedy(self, sig, remedy, evidence, success):
        """一次修复之后的结果：成功才保存做法（并计一次成功）；失败只是不加分。 The outcome of a fix: a success stores the remedy and counts; a failure only adds no credit."""
        if not success:
            return
        remedy = str(remedy)[:400]
        self.db.execute('UPDATE entries SET remedy=?,evidence=?,successes=successes+1,last_used=?,bytes=? WHERE signature=?',
                        (remedy, str(evidence)[:80], self.clock(), len(sig) + len(remedy) + 80, sig))
        self.db.commit()
        self.evict()

    # ───────────── 读 / read ─────────────
    def lookup(self, kind, message, min_successes=1):
        """命中且有成功记录的做法；否则 None。 A remedy with at least `min_successes` successes, else None."""
        row = self.db.execute('SELECT * FROM entries WHERE signature=?', (signature(kind, message),)).fetchone()
        return dict(row) if row and row['remedy'] and row['successes'] >= min_successes else None

    def utility(self, row):
        age_days = max(0.0, (self.clock() - row['last_used']) / 86400)
        return (row['successes'] + 1) / (row['hits'] + 2) * (1 + math.log1p(row['hits'])) * 0.5 ** (age_days / HALF_LIFE_DAYS)

    def evict(self):
        """超过字节或条数上限时，淘汰效用最低的条目。返回被淘汰的签名。 Over the byte or entry cap, evict the lowest-utility entries; returns the evicted signatures."""
        gone = []
        while True:
            total, count = self.db.execute('SELECT COALESCE(SUM(bytes),0),COUNT(*) FROM entries').fetchone()
            if total <= self.capacity_bytes and count <= self.max_entries:
                return gone
            rows = self.db.execute('SELECT * FROM entries').fetchall()
            worst = min(rows, key=lambda r: (self.utility(r), r['last_used']))
            self.db.execute('DELETE FROM entries WHERE signature=?', (worst['signature'],))
            self.db.commit()
            gone.append(worst['signature'])

    def stats(self):
        rows = self.db.execute('SELECT * FROM entries ORDER BY hits DESC, successes DESC').fetchall()
        return {'entries': len(rows), 'bytes': sum(r['bytes'] for r in rows), 'with_remedy': sum(1 for r in rows if r['remedy']),
                'top': [{'signature': r['signature'], 'hits': r['hits'], 'successes': r['successes']} for r in rows[:10]]}


def default_path(root):
    return Path(root) / 'library.sqlite3'
