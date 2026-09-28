"""真实生成边界的离线机制测试。 Offline mechanism tests of the live-generation boundary."""

from pathlib import Path
import tempfile
import unittest
from masa.application.single_file import CodeGeneration
from masa.infrastructure.store import Store
from masa.domain.models import MasaError
from masa.runtime.engine import Runtime
from test_runtime import FakeExecutor


class ProposalProvider:
    profile = {
        "provider": "openai-compatible",
        "model": "test",
        "base_url": "https://example.com",
    }
    usage = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}

    def respond(self, context):
        """返回固定草稿供审核流程测试。 Return a deterministic draft for review-flow testing."""
        return {
            "type": "code_proposal",
            "summary": "Generated Add function",
            "content": "package solution\n\nfunc Add(a, b int) int { return a + b }\n",
        }


class CodeGenerationTests(unittest.TestCase):
    def setUp(self):
        """隔离状态与工具副作用。 Isolate storage and tool side effects."""
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.executor = FakeExecutor()
        self.service = CodeGeneration(self.store, self.executor)

    def tearDown(self):
        """关闭隔离状态。 Close isolated storage."""
        self.store.close()
        self.temp.cleanup()

    def generate(self):
        """生成尚未批准的草稿。 Generate an unapproved draft."""
        return self.service.generate(ProposalProvider(), "Implement Add")

    def test_unapproved_cannot_execute_and_edited_code_is_exactly_applied(self):
        """批准前无写入；确认后使用人工编辑版本，源文件不变。 No write before review; apply exact human edits, preserving source."""
        rid = self.generate()
        run = self.store.run(rid)
        source = Path(run["data"]["source"]) / "solution.go"
        work = Path(run["data"]["workspace"]) / "solution.go"
        self.assertEqual(work.read_text(), "package solution\n")
        with self.assertRaisesRegex(MasaError, "human_review_required"):
            Runtime(self.store, self.executor).execute(rid)
        ref = run["data"]["codegen"]["proposal_ref"]
        edited = "package solution\n\n// Human edited.\nfunc Add(a, b int) int { return a + b }\n"
        self.service.approve(rid, ref, edited)
        self.assertEqual(work.read_text(), edited)
        self.assertEqual(source.read_text(), "package solution\n")
        result = Runtime(self.store, self.executor).execute(rid)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(self.executor.calls, 3)
        self.service.approve(rid, ref, edited)
        Runtime(self.store, self.executor).execute(rid)
        self.assertEqual(self.executor.calls, 3)

    def test_stale_proposal_and_rejected_draft_cannot_write(self):
        """过期批准与已拒绝草稿均不能写入。 Stale approvals and rejected drafts cannot write."""
        rid = self.generate()
        with self.assertRaises(MasaError):
            self.service.approve(rid, "wrong-ref", "package solution\n")
        self.service.reject(rid)
        run = self.store.run(rid)
        with self.assertRaises(MasaError):
            self.service.approve(
                rid, run["data"]["codegen"]["proposal_ref"], "package solution\n"
            )
        self.assertEqual(run["status"], "cancelled")
        self.assertEqual(self.executor.calls, 0)

    def test_cancelled_draft_cannot_be_resurrected(self):
        """取消后不能用旧页面重新批准。 Cancellation cannot be undone by stale approval UI."""
        rid = self.generate()
        self.store.cancel(rid)
        ref = self.store.run(rid)["data"]["codegen"]["proposal_ref"]
        with self.assertRaisesRegex(MasaError, "cancelled"):
            self.service.approve(rid, ref, "package solution\n")

    def test_feedback_creates_new_run_with_previous_proposal_context(self):
        """反馈另建运行，保留旧提案和来源。 Feedback creates a new run and retains the old proposal/provenance."""
        first = self.generate()
        source = self.store.run(first)["data"]["source"]
        second = self.service.generate(
            ProposalProvider(), "Add overflow validation", repo=source, parent=first
        )
        data = self.store.run(second)["data"]
        self.assertEqual(data["parent_run_id"], first)
        event = next(
            e for e in self.store.events(second) if e["type"] == "model_requested"
        )
        self.assertIn(
            "previous_proposal", self.store.read(event["payload"]["context_ref"])
        )
        self.assertEqual(
            self.store.run(first)["data"]["codegen"]["status"], "awaiting_review"
        )

    def test_generated_scaffold_preserves_lf_on_windows(self):
        """Windows 模板写入保留 LF，否则 gofmt 门禁会失败。 Preserve LF in Windows scaffolds to satisfy gofmt checks."""
        rid = self.service.generate(
            ProposalProvider(), "Add", tests='package solution\n\nimport "testing"\n'
        )
        root = Path(self.store.run(rid)["data"]["source"])
        for name in ("go.mod", "solution.go", "solution_test.go"):
            self.assertNotIn(b"\r\n", (root / name).read_bytes())
