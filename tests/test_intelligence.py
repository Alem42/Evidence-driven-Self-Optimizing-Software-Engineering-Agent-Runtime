"""真实索引、检索与上下文有效性验收。 Real indexing, retrieval, and context-validity acceptance."""

import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from masa.adapters.runner import Runner
from masa.adapters.sqlite import Store
from masa.context import ContextBuilder
from masa.domain import Budget, MasaError, digest
from masa.intelligence import Intelligence
from masa.memory import Memory
from masa.patching import Patches
from masa.runtime import Runtime
from masa.workspace import manifest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.name == "nt" and (ROOT / ".tools/bin/masa-runner.exe").exists(),
    "built Windows runner required",
)
class IntelligenceTests(unittest.TestCase):
    def setUp(self):
        """使用真实 Go 索引器创建隔离副本。 Create an isolated copy with the real Go indexer."""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "repo"
        shutil.copytree(ROOT / "examples/go-todo", self.source)
        self.store = Store(self.root / "state")
        self.runner = Runner(
            ROOT / ".tools/bin/masa-runner.exe", ROOT / ".tools/go/bin/go.exe"
        )
        self.runtime = Runtime(self.store, self.runner)
        self.rid = self.runtime.create(
            self.source, "NormalizeTitle Create", Budget(tool_calls=10)
        )
        self.engine = Intelligence(self.store, self.runner)
        self.index = self.engine.build(self.rid)
        self.builder = ContextBuilder(self.store, self.engine)

    def tearDown(self):
        """清理独立测试状态。 Clean isolated test state."""
        self.store.close()
        self.temp.cleanup()

    def test_exact_receiver_tests_and_durable_cache(self):
        """检索保留方法身份、测试候选和缓存。 Search retains method identity, test candidates, and durable cache."""
        result = self.engine.search(self.rid, self.index, "Create")
        self.assertTrue(
            any(
                c["name"] == "Create" and c["receiver"] == "*Service"
                for c in result["candidates"]
            )
        )
        self.assertTrue(any(c["test"] for c in result["candidates"]))
        before = self.store.run(self.rid)["tool_calls"]
        with patch.object(
            self.runner, "execute", side_effect=AssertionError("cache replay")
        ):
            self.assertEqual(self.engine.build(self.rid), self.index)
        self.assertEqual(self.store.run(self.rid)["tool_calls"], before)
        self.assertTrue(
            all(
                c["resolution"] == "unresolved_syntax"
                for f in self.index["files"]
                for c in f["calls"]
            )
        )

    def test_context_is_consumed_by_actual_agent_loop(self):
        """确认每个模型请求实际使用证据上下文。 Verify that each real loop request consumes evidence context."""
        rid = self.runtime.create(
            self.source, "NormalizeTitle", Budget(tool_calls=4), intelligence=True
        )
        result = self.runtime.execute(rid)
        self.assertEqual(
            result["status"], "failed"
        )  # 原样例故意有缺陷 / The original fixture deliberately fails.
        requests = [e for e in self.store.events(rid) if e["type"] == "model_requested"]
        self.assertEqual(len(requests), 2)
        for event in requests:
            context = self.store.read(event["payload"]["context_ref"])
            self.assertEqual(context["phase"], "P1-evidence")
            self.assertTrue(context["code"])
            self.assertEqual(context["snapshot_id"], result["data"]["snapshot_id"])

    def test_memory_stale_after_patch_and_old_index_rejected(self):
        """修改后旧索引及依赖记忆失效，历史仍保留。 A patch invalidates old index and dependent memory while retaining history."""
        candidate = self.engine.search(self.rid, self.index, "NormalizeTitle")[
            "candidates"
        ][0]
        evidence = self.engine.evidence(self.rid, candidate)
        memory = Memory(self.store)
        first = memory.add(self.rid, evidence["text"], evidence["artifact_ref"])
        second = memory.add(
            self.rid, evidence["text"], evidence["artifact_ref"], dependencies=[first]
        )
        run = self.store.run(self.rid)
        path = "todo/item.go"
        files = self.store.read(run["data"]["manifest_ref"])
        content = (Path(run["data"]["workspace"]) / path).read_text(
            encoding="utf-8"
        ) + "\n// changed\n"
        Patches(self.store).apply(
            self.rid,
            {
                "base_snapshot": run["data"]["snapshot_id"],
                "files": [
                    {"path": path, "before_sha256": files[path], "content": content}
                ],
            },
        )
        active, excluded = memory.current(self.rid)
        self.assertEqual(active, [])
        self.assertEqual({e["id"] for e in excluded}, {first, second})
        with self.assertRaisesRegex(MasaError, "stale_index"):
            self.engine.search(self.rid, self.index, "NormalizeTitle")
        fresh = self.engine.build(self.rid)
        self.assertNotEqual(fresh["generation"], self.index["generation"])
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], 2
        )

    def test_unverified_claims_scope_and_conflicts(self):
        """候选不自动成为事实，跨 run 隔离，冲突不靠覆盖解决。 Candidates stay unverified; scopes and conflicts are explicit."""
        memory = Memory(self.store)
        mid = memory.add(self.rid, "all tests passed")
        candidate = self.engine.search(self.rid, self.index, "NormalizeTitle")[
            "candidates"
        ][0]
        evidence = self.engine.evidence(self.rid, candidate)
        with self.assertRaises(MasaError):
            memory.add(self.rid, "tests passed", evidence["artifact_ref"])
        memory.add(
            self.rid, evidence["text"], evidence["artifact_ref"], conflict_group="g"
        )
        memory.add(
            self.rid, evidence["text"], evidence["artifact_ref"], conflict_group="g"
        )
        active, excluded = memory.current(self.rid)
        self.assertFalse(active)
        self.assertIn({"id": mid, "reason": "unverified"}, excluded)
        other = self.runtime.create(self.source, "other", Budget())
        self.assertEqual(memory.current(other), ([], []))
        with self.assertRaises(MasaError):
            memory.add(other, "hypothesis", dependencies=[mid])

    def test_context_budget_role_and_manifest(self):
        """预算包含契约，必需材料超限拒绝，角色视图可追溯。 Budgets include contracts; required overflow fails; roles are traceable."""
        context, manifest_data = self.builder.build(
            self.rid, self.index, "planner", "NormalizeTitle", budget_bytes=2200
        )
        self.assertLessEqual(
            manifest_data["input_bytes"] + manifest_data["reserved_bytes"], 2200
        )
        self.assertEqual(self.store.read(manifest_data["context_ref"]), context)
        self.assertTrue(manifest_data["omitted"])
        with self.assertRaisesRegex(MasaError, "required"):
            self.builder.build(self.rid, self.index, budget_bytes=512)
        full, _ = self.builder.build(
            self.rid, self.index, "developer", "NormalizeTitle"
        )
        planner, _ = self.builder.build(
            self.rid, self.index, "planner", "NormalizeTitle"
        )
        self.assertGreater(
            len(full["code"][0]["text"]), len(planner["code"][0]["text"])
        )

    def test_partial_parse_and_failed_generation_not_published(self):
        """语法损坏可降级；截断结果不发布索引。 Parse failures degrade explicitly; truncated results never publish."""
        (self.source / "broken.go").write_text("package todo\nfunc Broken( {")
        rid = self.runtime.create(self.source, "broken", Budget(tool_calls=4))
        index = self.engine.build(rid)
        self.assertTrue(index["partial"])
        self.assertIn(
            "broken.go", self.engine.search(rid, index, "Broken")["diagnostics"]
        )
        small = self.runtime.create(self.source, "small", Budget(max_output_bytes=32))
        with self.assertRaisesRegex(MasaError, "index_unavailable"):
            self.engine.build(small)
        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM code_indexes WHERE run_id=?", (small,)
            ).fetchone()[0],
            0,
        )

    def test_external_mutation_rejects_even_cached_index(self):
        """缓存命中不能绕过快照校验。 A cache hit cannot bypass snapshot validation."""
        data = self.store.run(self.rid)["data"]
        (Path(data["workspace"]) / "todo/item.go").write_text("changed")
        with self.assertRaisesRegex(MasaError, "snapshot_mismatch"):
            self.engine.build(self.rid)

    def test_required_hypothesis_retained_without_promotion(self):
        """未解决项保留为假设，超限拒绝而非静默丢弃。 Keep unresolved hypotheses labelled; reject overflow instead of dropping them."""
        mid = Memory(self.store).add(
            self.rid, "Need to check whitespace behavior", required=True
        )
        context, _ = self.builder.build(self.rid, self.index)
        self.assertEqual(context["unresolved"][0]["id"], mid)
        self.assertEqual(context["unresolved"][0]["epistemic_status"], "inferred")
        Memory(self.store).add(self.rid, "x" * 8000, required=True)
        with self.assertRaisesRegex(MasaError, "required"):
            self.builder.build(self.rid, self.index, budget_bytes=4000)

    def test_context_records_observations_without_duplicate_memory(self):
        """实际调用积累源码记忆且重复构建不重复记录。 Actual builds persist observations without duplicate records."""
        self.builder.build(self.rid, self.index, query="NormalizeTitle")
        count = self.store.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        self.assertGreater(count, 0)
        self.builder.build(self.rid, self.index, query="NormalizeTitle")
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM memories").fetchone()[0], count
        )

    def test_required_conflict_blocks_context(self):
        """必需内容冲突时阻止构建，不能省略后继续。 Required conflicts block construction instead of silently disappearing."""
        memory = Memory(self.store)
        memory.add(
            self.rid, "assumption one", conflict_group="unresolved", required=True
        )
        memory.add(
            self.rid, "assumption two", conflict_group="unresolved", required=True
        )
        with self.assertRaisesRegex(MasaError, "required_memory_unavailable"):
            self.builder.build(self.rid, self.index)
