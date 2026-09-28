"""受控多文件补丁与可核对恢复。 Controlled multi-file patches and reconciliation."""

import hashlib
import os
from pathlib import Path
import re
import time

from masa.domain.models import MasaError, canonical, digest
from masa.infrastructure.locking import owner_lock
from masa.infrastructure.workspaces import MAX_BYTES, manifest


def content_hash(raw: bytes) -> str:
    """计算文件内容身份。 Compute the identity of exact file bytes."""
    return hashlib.sha256(raw).hexdigest()


class Patches:
    """以独占锁和持久化 intent 管理写入。 Own writes through a lock and durable intent."""

    def __init__(self, store):
        """绑定状态存储，不持有长事务。 Bind storage without a long-lived transaction."""
        self.store = store

    def _row(self, run_id):
        """读取每个 run 的准备阶段补丁。 Read the run's single preparation patch."""
        row = self.store.db.execute("SELECT * FROM patches WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def _validate(self, run, body):
        """完整校验后才允许产生副作用。 Validate the entire request before any side effect."""
        if not isinstance(body, dict) or set(body) != {"base_snapshot", "files"}:
            raise MasaError("patch requires base_snapshot and files")
        data = run["data"]
        if body["base_snapshot"] != data["snapshot_id"]:
            raise MasaError("stale_patch: base snapshot differs")
        files = body["files"]
        if not isinstance(files, list) or not 1 <= len(files) <= 20:
            raise MasaError("patch must contain 1..20 files")
        before = self.store.read(data["manifest_ref"])
        workspace = Path(data["workspace"])
        if digest(before) != data["snapshot_id"] or manifest(workspace) != before:
            raise MasaError("snapshot_mismatch: cannot prepare patch")
        after, paths, total = dict(before), set(), 0
        for change in files:
            if not isinstance(change, dict) or set(change) != {"path", "before_sha256", "content"}:
                raise MasaError("invalid patch file fields")
            path, content = change["path"], change["content"]
            # 只写已跟踪的 Go 实现文件，拒绝路径别名、测试及模块配置。
            # Restrict writes to tracked Go implementation files; reject aliases and policy files.
            if (not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", path)
                    or any(p in {"", ".", ".."} or p.endswith(".") for p in path.split("/"))
                    or path not in before or not path.endswith(".go") or path.endswith("_test.go")
                    or path in paths):
                raise MasaError("patch path must be a unique tracked Go implementation file")
            if not isinstance(content, str) or "\x00" in content:
                raise MasaError("patch content must be UTF-8 text without NUL")
            raw = content.encode("utf-8")
            total += len(raw)
            if total > 1024 * 1024:
                raise MasaError("patch exceeds 1 MiB")
            if change["before_sha256"] != before[path]:
                raise MasaError("stale_patch: file preimage differs")
            after[path] = content_hash(raw)
            paths.add(path)
        if before == after:
            raise MasaError("patch makes no changes")
        projected_size = sum((workspace / path).stat().st_size for path in before)
        projected_size += sum(len(c["content"].encode("utf-8")) - (workspace / c["path"]).stat().st_size for c in files)
        if projected_size > MAX_BYTES:
            raise MasaError("patched workspace exceeds snapshot size limit")
        return {"patch": body, "before": before, "after": after}

    def apply(self, run_id, body):
        """记录写意图、应用补丁并发布新快照。 Journal, apply, and publish a new snapshot."""
        with owner_lock(self.store.root / "runtime.lock"):
            old = self._row(run_id)
            if old:
                request = self.store.read(old["request_ref"])
                if request["patch"] != body:
                    raise MasaError("one preparation patch per run; create another run for another patch")
                if old["status"] == "recorded":
                    return self.store.read(old["result_ref"])
                raise MasaError("patch intent already exists; resume the run to reconcile")
            run = self.store.run(run_id)
            # 首版仅允许验证前写入，绝不重置旧步骤以伪造新版本成功。
            # This first slice only writes before verification; never reset evidence to claim new success.
            if run["status"] != "created" or self.store.db.execute(
                    "SELECT 1 FROM attempts WHERE run_id=? LIMIT 1", (run_id,)).fetchone():
                raise MasaError("patch requires a created run without attempts")
            if run["cancel_requested"] or time.time() >= run["data"]["deadline_at"]:
                raise MasaError("patch rejected: run cancelled or expired")
            request = self._validate(run, body)
            ref = self.store.put(request)
            with self.store.transaction():
                if run["tool_calls"] >= run["data"]["budget"]["tool_calls"]:
                    raise MasaError("tool_call_budget_exhausted")
                self.store.db.execute("INSERT INTO patches VALUES(?,?,?,NULL)", (run_id, "intent", ref))
                self.store.db.execute("UPDATE runs SET tool_calls=tool_calls+1 WHERE id=?", (run_id,))
                self.store._event(run_id, "patch_intent", {"request_ref": ref, "snapshot_id": body["base_snapshot"]})
            return self.recover(run_id)

    def _replace(self, workspace, change, index, run_id):
        """原子替换单文件；暂存位于工作区外。 Atomically replace a file using external staging."""
        staging = self.store.root / "patch-staging"
        staging.mkdir(exist_ok=True)
        temp = staging / f"{run_id}-{index}.tmp"
        with temp.open("wb") as handle:
            handle.write(change["content"].encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, workspace / change["path"])

    def recover(self, run_id):
        """持锁后按前后哈希恢复；未知内容停机。 Reconcile under owner lock; stop on unknown bytes."""
        row = self._row(run_id)
        if row is None or row["status"] == "recorded":
            return None
        try:
            request = self.store.read(row["request_ref"])
            run = self.store.run(run_id)
            workspace = Path(run["data"]["workspace"])
            before, after = request["before"], request["after"]
            current = manifest(workspace)
            # 先核对整个副本，再完成任何剩余写入；第三种内容绝不能被覆盖。
            # Inspect the entire copy first; a third content version must never be overwritten.
            if set(current) != set(before) or any(current[p] not in {before[p], after[p]} for p in before):
                raise MasaError("patch_conflict: workspace is neither preimage nor postimage")
            if run["data"]["snapshot_id"] != digest(before):
                raise MasaError("patch_conflict: committed base snapshot changed")
            for index, change in enumerate(request["patch"]["files"]):
                path = change["path"]
                if current[path] != after[path]:
                    self._replace(workspace, change, index, run_id)
            if manifest(workspace) != after:
                raise MasaError("patch_conflict: final snapshot differs")
            manifest_ref = self.store.put(after)
            result = {"before_snapshot": digest(before), "snapshot_id": digest(after),
                      "manifest_ref": manifest_ref, "files": [
                          {"path": c["path"], "before_sha256": before[c["path"]], "after_sha256": after[c["path"]]}
                          for c in request["patch"]["files"]]}
            result_ref = self.store.put(result)
            # 新快照指针、账本与事件一起提交；崩溃后不会出现“新指针、旧账本”。
            # Commit snapshot pointer, ledger, and event together to avoid split state after a crash.
            data = {**run["data"], "snapshot_id": result["snapshot_id"], "manifest_ref": manifest_ref,
                    "initial_snapshot_id": result["before_snapshot"]}
            with self.store.transaction():
                self.store.db.execute("UPDATE runs SET data=? WHERE id=?", (canonical(data), run_id))
                self.store.db.execute("UPDATE patches SET status='recorded',result_ref=? WHERE run_id=?", (result_ref, run_id))
                self.store._event(run_id, "patch_committed", {"result_ref": result_ref, "snapshot_id": result["snapshot_id"]})
            return result
        except (MasaError, OSError) as exc:
            self.store.set_status(run_id, "needs_attention", str(exc))
            raise
