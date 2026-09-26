"""Short transactions for runtime facts; immutable, hash-checked artifacts."""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from masa.domain import MasaError, canonical


class Store:
    def __init__(self, root: Path):
        """打开独立连接并补齐兼容表。 Open an independent connection and add compatible tables."""
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / "runtime.sqlite3", timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise MasaError(f"unsupported database schema: {version}")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY, status TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
              data TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
              model_calls INTEGER NOT NULL DEFAULT 0, tool_calls INTEGER NOT NULL DEFAULT 0,
              reason TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS steps (
              run_id TEXT REFERENCES runs(id), id TEXT, status TEXT NOT NULL,
              result_ref TEXT, PRIMARY KEY(run_id,id));
            CREATE TABLE IF NOT EXISTS attempts (
              id TEXT PRIMARY KEY, run_id TEXT, step_id TEXT, ordinal INTEGER,
              status TEXT, started REAL, finished REAL, result_ref TEXT,
              UNIQUE(run_id,step_id,ordinal),
              FOREIGN KEY(run_id,step_id) REFERENCES steps(run_id,id));
            CREATE TABLE IF NOT EXISTS tool_calls (
              id TEXT PRIMARY KEY, run_id TEXT, step_id TEXT, attempt_id TEXT REFERENCES attempts(id),
              status TEXT, request_ref TEXT, result_ref TEXT,
              FOREIGN KEY(run_id,step_id) REFERENCES steps(run_id,id));
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT REFERENCES runs(id),
              type TEXT, payload TEXT, created REAL);
            CREATE INDEX IF NOT EXISTS events_run ON events(run_id,seq);
            CREATE TABLE IF NOT EXISTS run_controls (
              run_id TEXT PRIMARY KEY REFERENCES runs(id), pause_requested INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS patches (
              run_id TEXT PRIMARY KEY REFERENCES runs(id), status TEXT NOT NULL,
              request_ref TEXT NOT NULL, result_ref TEXT);
            PRAGMA user_version=1;
        """)

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _event(self, run_id: str, kind: str, payload: dict):
        self.db.execute("INSERT INTO events(run_id,type,payload,created) VALUES(?,?,?,?)",
                        (run_id, kind, canonical(payload), time.time()))
        self.db.execute("UPDATE runs SET version=version+1 WHERE id=?", (run_id,))

    def event(self, run_id: str, kind: str, payload: dict):
        with self.transaction():
            self._event(run_id, kind, payload)

    def put(self, value) -> str:
        raw = canonical(value).encode("utf-8")
        key = hashlib.sha256(raw).hexdigest()
        target = self.artifacts / f"{key}.json"
        if not target.exists():
            temp = self.artifacts / f"{key}.{uuid.uuid4().hex}.tmp"
            with temp.open("xb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, target)
        return key

    def read(self, key: str):
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise MasaError("invalid artifact reference")
        try:
            raw = (self.artifacts / f"{key}.json").read_bytes()
        except OSError as exc:
            raise MasaError(f"evidence_missing: {key}") from exc
        if hashlib.sha256(raw).hexdigest() != key:
            raise MasaError(f"integrity_error: artifact {key}")
        return json.loads(raw)

    def create(self, run_id: str, data: dict):
        with self.transaction():
            self.db.execute("INSERT INTO runs(id,status,data) VALUES(?,?,?)",
                            (run_id, "created", canonical(data)))
            for node in data["graph"]["nodes"]:
                self.db.execute("INSERT INTO steps VALUES(?,?,?,NULL)",
                                (run_id, node["id"], "pending"))
            self._event(run_id, "run_created", {"snapshot_id": data["snapshot_id"],
                                              "graph_version": data["graph"]["version"]})

    def run(self, run_id: str) -> dict:
        row = self.db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise MasaError(f"unknown run: {run_id}")
        value = dict(row)
        value["data"] = json.loads(value["data"])
        return value

    def steps(self, run_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM steps WHERE run_id=? ORDER BY rowid", (run_id,))]

    def events(self, run_id: str) -> list[dict]:
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in
                self.db.execute("SELECT * FROM events WHERE run_id=? ORDER BY seq", (run_id,))]

    def set_status(self, run_id: str, status: str, reason: str = ""):
        with self.transaction():
            self.db.execute("UPDATE runs SET status=?,reason=? WHERE id=?", (status, reason, run_id))
            self._event(run_id, "run_" + status, {"reason": reason})

    def cancel(self, run_id: str):
        with self.transaction():
            current = self.run(run_id)
            if current["status"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
                raise MasaError("run is already terminal")
            self.db.execute("UPDATE runs SET cancel_requested=1 WHERE id=?", (run_id,))
            self._event(run_id, "cancellation_requested", {})

    def request_pause(self, run_id: str):
        with self.transaction():
            if self.run(run_id)["status"] not in {"created", "running"}:
                raise MasaError("only an executing run can be paused")
            self.db.execute("INSERT INTO run_controls VALUES(?,1) ON CONFLICT(run_id) DO UPDATE SET pause_requested=1", (run_id,))
            self._event(run_id, "pause_requested", {"boundary": "after current node"})

    def pause_requested(self, run_id: str) -> bool:
        row = self.db.execute("SELECT pause_requested FROM run_controls WHERE run_id=?", (run_id,)).fetchone()
        return bool(row and row[0])

    def clear_pause(self, run_id: str):
        with self.transaction():
            self.db.execute("UPDATE run_controls SET pause_requested=0 WHERE run_id=?", (run_id,))

    def start(self, run_id: str, step_id: str) -> str:
        attempt_id = uuid.uuid4().hex
        with self.transaction():
            ordinal = self.db.execute("SELECT COUNT(*) FROM attempts WHERE run_id=? AND step_id=?",
                                      (run_id, step_id)).fetchone()[0] + 1
            self.db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?,NULL,NULL)",
                            (attempt_id, run_id, step_id, ordinal, "running", time.time()))
            self.db.execute("UPDATE steps SET status='running' WHERE run_id=? AND id=?", (run_id, step_id))
            self._event(run_id, "attempt_started", {"step_id": step_id, "attempt_id": attempt_id})
        return attempt_id

    def finish(self, run_id: str, step_id: str, attempt_id: str, status: str, result: dict):
        ref = self.put(result)
        with self.transaction():
            self.db.execute("UPDATE attempts SET status=?,finished=?,result_ref=? WHERE id=?",
                            (status, time.time(), ref, attempt_id))
            self.db.execute("UPDATE steps SET status=?,result_ref=? WHERE run_id=? AND id=?",
                            (status, ref, run_id, step_id))
            self._event(run_id, "step_finished", {"step_id": step_id, "status": status, "result_ref": ref})

    def skip(self, run_id: str, step_id: str):
        with self.transaction():
            self.db.execute("UPDATE steps SET status='skipped' WHERE run_id=? AND id=?", (run_id, step_id))
            self._event(run_id, "step_skipped", {"step_id": step_id})

    def recover(self, run_id: str) -> bool:
        unknown = self.db.execute("SELECT id FROM tool_calls WHERE run_id=? AND status='intent'", (run_id,)).fetchall()
        if unknown:
            self.set_status(run_id, "needs_attention", "uncertain_tool_state: no automatic replay; " + ",".join(r[0] for r in unknown))
            return False
        with self.transaction():
            count = self.db.execute("UPDATE attempts SET status='interrupted',finished=? WHERE run_id=? AND status='running'",
                                    (time.time(), run_id)).rowcount
            self.db.execute("UPDATE steps SET status='pending' WHERE run_id=? AND status='running'", (run_id,))
            if count:
                self._event(run_id, "attempts_recovered", {"count": count})
        return True

    def charge_model(self, run_id: str, step_id: str, limit: int, route: dict, context_ref: str):
        with self.transaction():
            if self.run(run_id)["model_calls"] >= limit:
                raise MasaError("model_call_budget_exhausted")
            self.db.execute("UPDATE runs SET model_calls=model_calls+1 WHERE id=?", (run_id,))
            self._event(run_id, "model_requested", {"step_id": step_id, "route": route, "context_ref": context_ref})

    def tool_intent(self, run_id: str, step_id: str, attempt_id: str, request: dict, limit: int):
        ref = self.put(request)
        with self.transaction():
            if self.run(run_id)["tool_calls"] >= limit:
                raise MasaError("tool_call_budget_exhausted")
            self.db.execute("UPDATE runs SET tool_calls=tool_calls+1 WHERE id=?", (run_id,))
            self.db.execute("INSERT INTO tool_calls VALUES(?,?,?,?,?, ?,NULL)",
                            (request["request_id"], run_id, step_id, attempt_id, "intent", ref))
            self._event(run_id, "tool_intent", {"tool_call_id": request["request_id"], "request_ref": ref})

    def tool_result(self, run_id: str, request_id: str, result: dict):
        ref = self.put(result)
        with self.transaction():
            self.db.execute("UPDATE tool_calls SET status='recorded',result_ref=? WHERE id=? AND run_id=?",
                            (ref, request_id, run_id))
            self._event(run_id, "tool_finished", {"tool_call_id": request_id, "result_ref": ref})

    def tools(self, run_id: str, step_id: str | None = None) -> list[dict]:
        sql = "SELECT * FROM tool_calls WHERE run_id=?"
        args = [run_id]
        if step_id is not None:
            sql += " AND step_id=?"
            args.append(step_id)
        return [dict(row) for row in self.db.execute(sql + " ORDER BY rowid", args)]
