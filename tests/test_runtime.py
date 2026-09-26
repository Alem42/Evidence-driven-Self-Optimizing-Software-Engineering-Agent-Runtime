from pathlib import Path
import tempfile
import unittest

from masa.adapters.sqlite import Store
from masa.domain import Budget, Graph, MasaError, Node
from masa.locking import owner_lock
from masa.runtime import Runtime
from masa.workspace import copy_snapshot, manifest


class FakeExecutor:
    def __init__(self, exit_code=0):
        self.calls = 0
        self.exit_code = exit_code

    def execute(self, request, workspace, cancelled):
        self.calls += 1
        return {"protocol_version": 1, "request_id": request["request_id"],
                "snapshot_id": request["snapshot_id"], "status": "completed",
                "exit_code": self.exit_code, "stdout": "test evidence", "stderr": "",
                "truncated": False, "duration_ms": 1}


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "go.mod").write_text("module demo\ngo 1.27.0\n")
        (self.source / "main.go").write_text("package demo\n")
        self.store = Store(self.root / "state")
        self.executor = FakeExecutor()
        self.runtime = Runtime(self.store, self.executor)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def create(self, **kwargs):
        return self.runtime.create(self.source, "verify", kwargs.pop("budget", Budget()), **kwargs)

    def test_pause_resume_does_not_reexecute_completed_tool(self):
        original = manifest(self.source)
        rid = self.create()
        self.assertEqual(self.runtime.execute(rid, pause_after=1)["status"], "paused")
        self.store.close()
        self.store = Store(self.root / "state")
        self.runtime = Runtime(self.store, self.executor)
        self.assertEqual(self.runtime.execute(rid)["status"], "succeeded")
        self.assertEqual(self.executor.calls, 1)
        self.assertEqual(self.store.run(rid)["model_calls"], 2)
        self.assertEqual(manifest(self.source), original)

    def test_failed_check_reaches_gate_and_cannot_be_claimed_success(self):
        self.executor.exit_code = 1
        rid = self.create()
        self.assertEqual(self.runtime.execute(rid)["status"], "failed")
        self.assertEqual([s["status"] for s in self.store.steps(rid)], ["failed", "failed"])

    def test_gate_committed_before_run_finalization_recovers(self):
        rid = self.create()
        self.runtime.execute(rid)
        # Simulate the only unfinished write being the Run terminal update.
        self.store.set_status(rid, "running", "injected crash after Gate commit")
        self.assertEqual(self.runtime.execute(rid)["status"], "succeeded")
        self.assertEqual(self.executor.calls, 1)

    def test_failed_ancestor_skips_dependent_but_gate_finishes(self):
        self.executor.exit_code = 1
        graph = Graph((Node("one", "tool"), Node("two", "tool", ("one",)),
                       Node("gate", "gate", ("two",), "all_terminal")))
        rid = self.create(graph=graph)
        self.assertEqual(self.runtime.execute(rid)["status"], "failed")
        self.assertEqual([s["status"] for s in self.store.steps(rid)], ["failed", "skipped", "failed"])
        self.assertEqual(self.executor.calls, 1)

    def test_tool_budget_limits_multi_node_graph(self):
        graph = Graph((Node("one", "tool"), Node("two", "tool", ("one",)),
                       Node("gate", "gate", ("two",), "all_terminal")))
        rid = self.create(graph=graph, budget=Budget(tool_calls=1))
        result = self.runtime.execute(rid)
        self.assertEqual(result["status"], "failed")
        self.assertIn("tool_call_budget_exhausted", result["reason"])
        self.assertEqual(self.executor.calls, 1)

    def test_cancelled_direct_tool_is_cancelled_run(self):
        class CancelExecutor(FakeExecutor):
            def execute(self, request, workspace, cancelled):
                result = super().execute(request, workspace, cancelled)
                result.update(status="cancelled", exit_code=None)
                return result
        graph = Graph((Node("one", "tool"), Node("gate", "gate", ("one",), "all_terminal")))
        rid = self.create(graph=graph)
        self.assertEqual(Runtime(self.store, CancelExecutor()).execute(rid)["status"], "cancelled")

    def test_unknown_tool_intent_is_never_replayed(self):
        rid = self.create()
        attempt = self.store.start(rid, "verify")
        self.store.tool_intent(rid, "verify", attempt, {"request_id": "unknown", "operation": "go_test"}, 3)
        result = self.runtime.execute(rid)
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("uncertain_tool_state", result["reason"])
        self.assertEqual(self.executor.calls, 0)

    def test_result_recorded_before_step_crash_is_reused(self):
        rid = self.create()
        attempt = self.store.start(rid, "verify")
        self.runtime.tools.execute(rid, Node("verify", "agent"), attempt,
                                   {"operation": "go_test", "arguments": {}})
        self.assertEqual(self.runtime.execute(rid)["status"], "succeeded")
        self.assertEqual(self.executor.calls, 1)
        attempts = self.store.db.execute("SELECT status FROM attempts WHERE step_id='verify' ORDER BY ordinal").fetchall()
        self.assertEqual([r[0] for r in attempts], ["interrupted", "succeeded"])

    def test_mutated_workspace_blocks_resume(self):
        rid = self.create()
        self.runtime.execute(rid, pause_after=1)
        workspace = Path(self.store.run(rid)["data"]["workspace"])
        (workspace / "main.go").write_text("package changed\n")
        result = self.runtime.execute(rid)
        self.assertEqual(result["status"], "needs_attention")
        self.assertIn("snapshot_mismatch", result["reason"])

    def test_budget_does_not_reset_on_resume(self):
        rid = self.create(budget=Budget(model_calls=1))
        self.assertEqual(self.runtime.execute(rid)["status"], "failed")
        self.assertEqual(self.store.run(rid)["model_calls"], 1)
        self.assertEqual(self.executor.calls, 1)

    def test_pending_cancel_prevents_tool(self):
        rid = self.create()
        self.store.cancel(rid)
        self.assertEqual(self.runtime.execute(rid)["status"], "cancelled")
        self.assertEqual(self.executor.calls, 0)

    def test_deadline_prevents_new_work(self):
        rid = self.create()
        import json
        data = self.store.run(rid)["data"]
        data["deadline_at"] = 0
        with self.store.transaction():
            self.store.db.execute("UPDATE runs SET data=? WHERE id=?", (json.dumps(data), rid))
        self.assertEqual(self.runtime.execute(rid)["status"], "failed")
        self.assertEqual(self.executor.calls, 0)

    def test_multiple_data_driven_nodes(self):
        graph = Graph((Node("one", "tool"), Node("two", "tool", ("one",), operation="go_vet"),
                       Node("gate", "gate", ("two",), "all_terminal")))
        rid = self.create(graph=graph)
        self.assertEqual(self.runtime.execute(rid)["status"], "succeeded")
        self.assertEqual(self.executor.calls, 2)

    def test_owner_lock_is_exclusive(self):
        with owner_lock(self.root / "lock"), self.assertRaises(MasaError):
            with owner_lock(self.root / "lock"):
                self.fail("two owners")

    def test_artifact_integrity_and_traversal(self):
        ref = self.store.put({"a": 1})
        (self.store.artifacts / f"{ref}.json").write_text('{"a":2}')
        with self.assertRaises(MasaError):
            self.store.read(ref)
        with self.assertRaises(MasaError):
            self.store.read("../../secret")

    def test_source_copy_ignores_credentials_and_rejects_nested_destination(self):
        (self.source / ".env").write_text("FAKE=not-a-real-key")
        rid = self.create()
        self.assertFalse((Path(self.store.run(rid)["data"]["workspace"]) / ".env").exists())
        with self.assertRaises(MasaError):
            copy_snapshot(self.source, self.source / "nested")

    def test_state_and_event_rollback_together(self):
        rid = self.create()
        before = self.store.run(rid)
        with self.assertRaises(RuntimeError):
            with self.store.transaction():
                self.store.db.execute("UPDATE runs SET status='succeeded' WHERE id=?", (rid,))
                self.store._event(rid, "wrong", {})
                raise RuntimeError("crash before commit")
        self.assertEqual(self.store.run(rid), before)
        self.assertNotIn("wrong", [e["type"] for e in self.store.events(rid)])

    def test_model_cannot_inject_operation(self):
        class BadModel:
            def respond(self, context):
                return {"type": "tool_call", "operation": "shell", "arguments": {}}
        rid = self.create()
        result = Runtime(self.store, self.executor, BadModel()).execute(rid)
        self.assertEqual(result["status"], "needs_attention")
        self.assertEqual(self.executor.calls, 0)
