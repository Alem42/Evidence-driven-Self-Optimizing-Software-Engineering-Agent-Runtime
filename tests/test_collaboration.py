"""角色权限与交接恢复回归。 Role permissions and durable handoff regressions."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from masa.agents.scripted import ScriptedProvider
from masa.infrastructure.store import Store
from masa.agents.handoffs import receive_handoffs, validate_role_result
from masa.domain.models import Budget, MasaError, canonical
from masa.runtime.engine import Runtime
from masa.runtime.graph import collaboration_policy
from test_runtime import FakeExecutor


class CollaborationTests(unittest.TestCase):
    def setUp(self):
        """隔离真实 SQLite 与测试工具。 Isolate real SQLite and fake tool execution."""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        (self.repo / "go.mod").write_text("module demo\ngo 1.27.0\n")
        self.store = Store(self.root / "state")
        self.executor = FakeExecutor()
        self.runtime = Runtime(self.store, self.executor)
        self.graph = collaboration_policy()
        self.rid = self.runtime.create(
            self.repo, "role protocol", Budget(model_calls=6), graph=self.graph
        )

    def tearDown(self):
        """释放连接与临时目录。 Close storage and clean temporary files."""
        self.store.close()
        self.temp.cleanup()

    def test_roles_permissions_and_receipts(self):
        """只有 Tester 执行工具，Reviewer 不继承作者自评。 Only Tester executes tools; Reviewer excludes author claims."""
        run = self.runtime.execute(self.rid)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual((run["model_calls"], self.executor.calls), (5, 1))
        events = self.store.events(self.rid)
        self.assertEqual(sum(e["type"] == "handoff_received" for e in events), 3)
        contexts = [
            self.store.read(e["payload"]["context_ref"])
            for e in events
            if e["type"] == "model_requested"
        ]
        for c in contexts:
            self.assertEqual(
                c["allowed_tools"], ["go_test"] if c["role"] == "tester" else []
            )
        reviewer = next(c for c in contexts if c["role"] == "reviewer")
        self.assertNotIn("summary", reviewer["handoffs"][0])
        self.assertEqual(reviewer["tool_results"], [])

    def test_receipt_recovery_deduplicates(self):
        """收件后中断并重连，不能重复事件或 Planner 调用。 Reconnect after receipt without duplicate events or Planner work."""
        self.runtime.execute(self.rid, pause_after=1)
        developer = self.graph.nodes[1]
        self.store.start(self.rid, developer.id)
        original = receive_handoffs(self.store, self.rid, developer)
        self.store.close()
        self.store = Store(self.root / "state")
        self.assertEqual(receive_handoffs(self.store, self.rid, developer), original)
        run = Runtime(self.store, self.executor).execute(self.rid)
        self.assertEqual(run["status"], "succeeded")
        self.assertEqual(run["model_calls"], 5)
        self.assertEqual(
            sum(e["type"] == "handoff_received" for e in self.store.events(self.rid)), 3
        )

    def test_invalid_identity_reference_receiver_and_snapshot(self):
        """拒绝错误角色、越域证据、假收件人及过期版本。 Reject wrong roles, references, receivers and stale versions."""
        self.runtime.execute(self.rid, pause_after=1)
        value = self.store.read(self.store.steps(self.rid)[0]["result_ref"])
        for delta in (
            {"run_id": "another-run"},
            {"role": "developer"},
            {"snapshot_id": "old"},
            {"evidence_refs": ["0" * 64]},
            {"extra": True},
        ):
            with self.subTest(delta=delta), self.assertRaises(MasaError):
                validate_role_result(
                    self.store, self.rid, self.graph.nodes[0], {**value, **delta}
                )
        with self.assertRaisesRegex(MasaError, "receiver"):
            receive_handoffs(
                self.store, self.rid, replace(self.graph.nodes[1], dependencies=())
            )
        data = self.store.run(self.rid)["data"]
        data["snapshot_id"] = "new"
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE runs SET data=? WHERE id=?", (canonical(data), self.rid)
            )
        with self.assertRaisesRegex(MasaError, "stale"):
            receive_handoffs(self.store, self.rid, self.graph.nodes[1])
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM handoff_receipts").fetchone()[
                0
            ],
            0,
        )

    def test_readonly_role_cannot_call_tools(self):
        """副作用前拒绝越权角色。 Reject unauthorized roles before side effects."""

        class Rogue:
            def respond(self, context):
                """模拟越权 Planner。 Simulate an unauthorized Planner."""
                return {"type": "tool_call", "operation": "go_test", "arguments": {}}

        run = Runtime(self.store, self.executor, Rogue()).execute(self.rid)
        self.assertEqual(run["status"], "needs_attention")
        self.assertEqual(self.executor.calls, 0)

    def test_gate_requires_receipts(self):
        """Gate 不能只相信成功状态。 Gate must not trust successful statuses alone."""
        self.runtime.execute(self.rid, pause_after=4)
        with self.store.transaction():
            self.store.db.execute(
                "DELETE FROM handoff_receipts WHERE receiver=?", ("reviewer",)
            )
        run = self.runtime.execute(self.rid)
        self.assertEqual(run["status"], "needs_attention")
        self.assertIn("missing handoff", run["reason"])

    def test_blocked_role_stops_descendants(self):
        """规划阻塞时不强行运行下游工具。 A blocked plan prevents downstream tools."""

        class Blocked(ScriptedProvider):
            def respond(self, context):
                """模拟缺少规划证据。 Simulate insufficient planning evidence."""
                result = super().respond(context)
                result["decision"] = "blocked"
                return result

        run = Runtime(self.store, self.executor, Blocked()).execute(self.rid)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(self.executor.calls, 0)

    def test_receipt_and_event_roll_back_together(self):
        """事件写入失败时收件记录必须一起回滚。 Receipt and event must roll back together on write failure."""
        self.runtime.execute(self.rid, pause_after=1)
        with patch.object(
            self.store, "_event", side_effect=RuntimeError("injected storage failure")
        ):
            with self.assertRaises(RuntimeError):
                receive_handoffs(self.store, self.rid, self.graph.nodes[1])
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM handoff_receipts").fetchone()[
                0
            ],
            0,
        )
        receive_handoffs(self.store, self.rid, self.graph.nodes[1])
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM handoff_receipts").fetchone()[
                0
            ],
            1,
        )

    def test_receipt_conflict_rejects_changed_sender_result(self):
        """重复投递必须字节一致，不能偷换发送结果。 A duplicate receipt cannot replace the original sender result."""
        self.runtime.execute(self.rid, pause_after=1)
        receive_handoffs(self.store, self.rid, self.graph.nodes[1])
        value = self.store.read(self.store.steps(self.rid)[0]["result_ref"])
        value["summary"] = "changed claim"
        ref = self.store.put(value)
        with self.store.transaction():
            self.store.db.execute(
                "UPDATE steps SET result_ref=? WHERE run_id=? AND id=?",
                (ref, self.rid, "planner"),
            )
        with self.assertRaisesRegex(MasaError, "conflict"):
            receive_handoffs(self.store, self.rid, self.graph.nodes[1])

    def test_shared_budget_cannot_reset_between_roles(self):
        """角色切换不重置模型总预算。 Role changes never reset the shared model budget."""
        rid = self.runtime.create(
            self.repo, "limited", Budget(model_calls=4), graph=self.graph
        )
        run = self.runtime.execute(rid)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["model_calls"], 4)
        self.assertIn("budget_exhausted", run["reason"])

    def test_handoffs_are_mandatory_budgeted_context(self):
        """超限交接不能在裁剪后追加或丢弃。 Mandatory handoffs cannot bypass trimming or be silently dropped."""
        from masa.intelligence.context import ContextBuilder
        from types import SimpleNamespace
        engine = SimpleNamespace(search=lambda *args: {'unknowns': [], 'partial': False, 'candidates': [], 'policy': 'test'})
        builder = ContextBuilder(self.store, engine)
        with self.assertRaisesRegex(MasaError, 'context_budget_exhausted'):
            builder.build(self.rid, {'generation': 'test'}, role='developer', budget_bytes=4096,
                          handoffs=[{'summary': 'x'*5000}])
        context, manifest = builder.build(self.rid, {'generation': 'test'}, role='developer',
                                          handoffs=[{'summary': 'bounded claim'}])
        self.assertEqual(manifest['input_bytes'], len(canonical(context).encode()))
        self.assertEqual(self.store.read(manifest['context_ref']), context)
