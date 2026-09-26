"""Browser application service: one worker, separate SQLite connection per thread."""

from dataclasses import asdict
from pathlib import Path
import threading

from masa.adapters.runner import Runner
from masa.adapters.sqlite import Store
from masa.domain import Budget, MasaError, OPERATIONS
from masa.report import render
from masa.runtime import Runtime
from masa.web.settings import Settings


class Console:
    def __init__(self, state_dir, runner, go, project):
        self.root = Path(state_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.runner_path, self.go_path, self.project = Path(runner), Path(go), Path(project)
        self.settings = Settings(self.root)
        self.lock = threading.RLock()
        self.active = None
        self.worker = None
        self.errors = {}
        self.closing = False
        Store(self.root).close()

    def bootstrap(self):
        return {"default_repo": str(self.project / "tests/fixtures/go-pass"),
                "runner_ready": self.runner_path.is_file() and self.go_path.is_file(),
                "active_run": self.active, "provider": "scripted-v1", "capabilities": {
                    "execute": True, "graph": True, "node_pause": True, "revise_as_new_run": True,
                    "code_edit": False, "live_llm": False, "multi_agent": False}}

    def list_runs(self):
        store = Store(self.root)
        try:
            ids = [r[0] for r in store.db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 100")]
            result = []
            for rid in ids:
                run = store.run(rid)
                result.append({"id": rid, "status": run["status"], "goal": run["data"]["goal"],
                               "created_at": run["data"]["created_at"], "reason": run["reason"],
                               "active": rid == self.active})
            return result
        finally:
            store.close()

    def detail(self, rid):
        store = Store(self.root)
        try:
            # A read transaction yields one coherent run/step/event view.
            store.db.execute("BEGIN")
            run = store.run(rid)
            events = store.events(rid)
            return {"run": run, "steps": store.steps(rid), "events": events[-500:],
                    "event_count": len(events), "tools": store.tools(rid),
                    "attempts": [dict(r) for r in store.db.execute("SELECT * FROM attempts WHERE run_id=? ORDER BY started", (rid,))],
                    "active": rid == self.active, "worker_error": self.errors.get(rid),
                    "pause_requested": store.pause_requested(rid)}
        finally:
            store.close()

    def artifact(self, rid, ref):
        store = Store(self.root)
        try:
            run = store.run(rid)
            refs = {run["data"]["manifest_ref"]}
            for row in store.steps(rid) + store.tools(rid):
                refs.update(v for k, v in row.items() if k.endswith("_ref") and v)
            for event in store.events(rid):
                refs.update(v for k, v in event["payload"].items() if k.endswith("_ref") and isinstance(v, str))
            if ref not in refs:
                raise MasaError("artifact is not referenced by this run")
            return store.read(ref)
        finally:
            store.close()

    def report(self, rid):
        store = Store(self.root)
        try:
            return render(store, rid)
        finally:
            store.close()

    @staticmethod
    def _budget(body):
        defaults = asdict(Budget(deadline_seconds=1800))
        supplied = body.get("budget", {})
        if not isinstance(supplied, dict) or set(supplied) - set(defaults):
            raise MasaError("unknown budget fields")
        budget = Budget(**{**defaults, **supplied})
        budget.validate()
        if budget.deadline_seconds > 86400 or budget.model_calls > 100 or budget.tool_calls > 100:
            raise MasaError("console budget exceeds local limits")
        return budget

    def create(self, body, parent=None):
        with self.lock:
            self._available()
            store = Store(self.root)
            try:
                source = body.get("repo", "")
                goal = body.get("goal", "")
                operation = body.get("operation", "go_test")
                if not isinstance(source, str) or not source.strip() or not isinstance(goal, str):
                    raise MasaError("repository and goal are required")
                if operation not in OPERATIONS:
                    raise MasaError("unsupported operation")
                budget = self._budget(body)
                runtime = Runtime(store, Runner(self.runner_path, self.go_path))
                rid = runtime.create(Path(source), goal, budget, operation, parent_run_id=parent)
            finally:
                store.close()
            self._launch(rid, 1 if body.get("pause_after") is True else 0)
            return {"id": rid}

    def _available(self):
        if self.closing:
            raise MasaError("console is shutting down")
        if self.active:
            raise MasaError("another run is executing; pause or cancel it first")

    def resume(self, rid):
        with self.lock:
            self._available()
            store = Store(self.root)
            try:
                if store.run(rid)["status"] not in {"created", "paused", "running"}:
                    raise MasaError("this run cannot be resumed; create a new run instead")
            finally:
                store.close()
            self._launch(rid, 0)
            return {"id": rid}

    def _launch(self, rid, pause_after):
        self.active = rid
        self.errors.pop(rid, None)
        def work():
            store = None
            try:
                store = Store(self.root)
                Runtime(store, Runner(self.runner_path, self.go_path)).execute(rid, pause_after)
            except Exception as exc:
                # No settings/credentials are ever passed into this worker.
                self.errors[rid] = str(exc)
            finally:
                if store is not None:
                    store.close()
                with self.lock:
                    self.active = None
        self.worker = threading.Thread(target=work, name=f"masa-{rid[:8]}", daemon=True)
        self.worker.start()

    def control(self, rid, action):
        with self.lock:
            store = Store(self.root)
            try:
                if action == "pause":
                    store.request_pause(rid)
                else:
                    store.cancel(rid)
                    # A paused or detached run has no worker to observe cancellation.
                    if self.active is None:
                        self._launch(rid, 0)
            finally:
                store.close()
        return {"id": rid, "requested": action}

    def close(self):
        with self.lock:
            self.closing = True
            rid, worker = self.active, self.worker
        if rid:
            store = Store(self.root)
            try:
                try:
                    store.cancel(rid)
                except MasaError:
                    pass
            finally:
                store.close()
        if worker:
            worker.join(timeout=15)
