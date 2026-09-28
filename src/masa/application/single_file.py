"""需求到单文件代码：先提案、人工确认、再复用补丁和验证。 Single-file generation with review before existing patch/verification flow."""

from pathlib import Path
import subprocess
import time
import uuid

from masa.domain.models import Budget, MasaError, canonical
from masa.runtime.patches import Patches
from masa.runtime.engine import Runtime
from masa.runtime.graph import harness_policy


class CodeGeneration:
    def __init__(self, store, executor):
        """复用现有状态、补丁和 Go 工具，不创建第二套运行时。 Reuse state, patches and tools without a second runtime."""
        self.store, self.executor = store, executor

    def _update(self, rid, generation, status, event, payload):
        """原子发布审核状态和事件。 Atomically publish review state and its event."""
        with self.store.transaction():
            data = self.store.run(rid)["data"]
            data["codegen"] = generation
            self.store.db.execute(
                "UPDATE runs SET data=?,status=?,reason=? WHERE id=?",
                (
                    canonical(data),
                    status,
                    payload.get(
                        "reason",
                        "awaiting human code review" if status == "paused" else "",
                    ),
                    rid,
                ),
            )
            self.store._event(rid, event, payload)

    def _format(self, content):
        """只格式化内存文本，不修改用户仓库或已批准内容。 Format in-memory text, never user repositories or approved content."""
        go = getattr(self.executor, "go_executable", None)
        if not go:
            return content
        fmt = Path(go).with_name("gofmt" + Path(go).suffix)
        result = subprocess.run(
            [str(fmt)],
            input=content.encode(),
            capture_output=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.stdout.decode("utf-8") if result.returncode == 0 else content

    def generate(
        self, provider, goal, repo="", target="solution.go", tests="", parent=None
    ):
        """一次有界真实生成，只保存草稿，不写工作文件。 Make one bounded model request and save a draft without changing work files."""
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 16000:
            raise MasaError("describe the code you want (1..16000 characters)")
        if (
            not isinstance(repo, str)
            or not isinstance(target, str)
            or not isinstance(tests, str)
            or len(tests.encode()) > 16000
        ):
            raise MasaError("invalid repository, target or test text")
        if not repo.strip():
            source = self.store.root / "codegen-seeds" / uuid.uuid4().hex
            source.mkdir(parents=True)
            (source / "go.mod").write_text(
                "module example.com/generated\n\ngo 1.27.0\n",
                encoding="utf-8",
                newline="\n",
            )
            (source / "solution.go").write_text(
                "package solution\n", encoding="utf-8", newline="\n"
            )
            if tests.strip():
                (source / "solution_test.go").write_text(
                    self._format(tests), encoding="utf-8", newline="\n"
                )
            target = "solution.go"
        else:
            source = Path(repo)
            if tests.strip():
                raise MasaError(
                    "inline acceptance tests are supported only for a new project"
                )
        generation = {
            "status": "generating",
            "target": target,
            "provider": provider.profile,
            "has_tests": bool(tests.strip()),
            "protocol": "codegen-hitl-v1",
        }
        runtime = Runtime(self.store, self.executor)
        rid = runtime.create(
            source,
            goal,
            Budget(model_calls=8, tool_calls=6, deadline_seconds=86400),
            graph=harness_policy(),
            parent_run_id=parent,
            codegen=generation,
        )
        data = self.store.run(rid)["data"]
        self._update(
            rid, generation, "created", "generation_started", {"target": target}
        )
        charged = completed = False
        try:
            files = self.store.read(data["manifest_ref"])
            generation["has_tests"] = any(n.endswith("_test.go") for n in files)
            if (
                target not in files
                or not target.endswith(".go")
                or target.endswith("_test.go")
            ):
                raise MasaError(
                    "choose an existing Go implementation file in the repository"
                )
            workspace = Path(data["workspace"])
            selected = {}
            total = 0
            for name in [target] + [
                n
                for n in sorted(files)
                if n != target and (n.endswith(".go") or n == "go.mod")
            ]:
                raw = (workspace / name).read_bytes()
                if len(raw) + total > 100000 or len(selected) >= 30:
                    if name == target:
                        raise MasaError("target file is too large for this demo")
                    continue
                selected[name] = raw.decode("utf-8")
                total += len(raw)
            context = {
                "purpose": "code_generation",
                "goal": goal,
                "target": target,
                "files": selected,
                "omitted_files": [n for n in files if n not in selected],
            }
            if parent:
                previous = self.store.run(parent)["data"].get("codegen", {})
                if previous.get("proposal_ref"):
                    context["previous_proposal"] = self.store.read(
                        previous["proposal_ref"]
                    )["content"]
            ref = self.store.put(context)
            route = {**provider.profile, "policy_version": "codegen-hitl-v1"}
            self.store.charge_model(rid, "codegen", 8, route, ref)
            charged = True
            started = time.monotonic()
            proposal = provider.respond(context)
            result_ref = self.store.put(proposal)
            self.store.event(
                rid,
                "model_completed",
                {
                    "step_id": "codegen",
                    "response_ref": result_ref,
                    "provider": route["provider"],
                    "model": route["model"],
                    "usage": provider.usage,
                    "cost": None,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )
            # gofmt 仅通过 stdin/stdout 整理预览，不使用 -w；人工看到的就是将写入的代码。
            # Format the preview via stdin/stdout without -w; the human sees the exact proposed content.
            completed = True
            content = self._format(proposal["content"])
            draft = {
                "target": target,
                "content": content,
                "before_content": selected[target],
                "summary": proposal["summary"],
                "base_snapshot": data["snapshot_id"],
                "before_sha256": files[target],
            }
            draft_ref = self.store.put(draft)
            generation.update(status="awaiting_review", proposal_ref=draft_ref)
            self._update(
                rid,
                generation,
                "paused",
                "human_review_requested",
                {"proposal_ref": draft_ref},
            )
            return rid
        except Exception as exc:
            if charged and not completed:
                self.store.event(rid, 'model_failed', {'step_id':'codegen', 'provider':provider.profile['provider'],
                                 'usage':getattr(provider,'usage',None), 'cost':None})
            generation["status"] = "failed"
            self._update(
                rid,
                generation,
                "failed",
                "generation_failed",
                {
                    "reason": str(exc)
                    if isinstance(exc, MasaError)
                    else "generation or local file processing failed",
                    "usage": getattr(provider, "usage", None),
                },
            )
            raise MasaError(
                f"generation failed; inspect run {rid}: "
                + (str(exc) if isinstance(exc, MasaError) else "local processing error")
            ) from None

    def approve(self, rid, proposal_ref, content):
        """批准特定草稿及人工编辑内容，再执行原有受控补丁。 Approve an exact draft/edit and apply the existing controlled patch."""
        run = self.store.run(rid)
        generation = run["data"].get("codegen", {})
        if (
            run["cancel_requested"]
            or run["status"] == "cancelled"
            or time.time() >= run["data"]["deadline_at"]
        ):
            raise MasaError("cancelled or expired proposal cannot be approved")
        if generation.get("status") not in {
            "awaiting_review",
            "approved",
        } or proposal_ref != generation.get("proposal_ref"):
            raise MasaError("stale or unavailable proposal; reload before approving")
        if (
            not isinstance(content, str)
            or not content.strip()
            or len(content.encode()) > 60000
        ):
            raise MasaError("approved code must be 1..60000 UTF-8 bytes")
        draft = self.store.read(proposal_ref)
        patch = {
            "base_snapshot": draft["base_snapshot"],
            "files": [
                {
                    "path": draft["target"],
                    "before_sha256": draft["before_sha256"],
                    "content": content,
                }
            ],
        }
        ref = self.store.put(patch)
        if (
            generation.get("status") == "approved"
            and generation.get("approval_ref") != ref
        ):
            raise MasaError(
                "this run already approved different code; create a new proposal"
            )
        if generation["status"] == "awaiting_review":
            Patches(self.store)._validate(run, patch)
            generation.update(status="approved", approval_ref=ref)
            self._update(
                rid,
                generation,
                "created",
                "human_code_approved",
                {"approval_ref": ref, "proposal_ref": proposal_ref},
            )
        row = self.store.db.execute(
            "SELECT status FROM patches WHERE run_id=?", (rid,)
        ).fetchone()
        if not row:
            Patches(self.store).apply(rid, patch)
        return rid

    def reject(self, rid):
        """拒绝草稿，不写入任何代码。 Reject a draft without writing code."""
        run = self.store.run(rid)
        generation = run["data"].get("codegen", {})
        if generation.get("status") != "awaiting_review":
            raise MasaError("only pending proposals can be rejected")
        generation["status"] = "rejected"
        self._update(rid, generation, "cancelled", "human_code_rejected", {})
