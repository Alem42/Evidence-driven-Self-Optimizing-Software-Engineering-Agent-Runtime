"""Run 内的证据记忆与保守失效。 Evidence memory with run isolation and conservative invalidation."""

import uuid
from pathlib import Path

from masa.domain import MasaError, digest
from masa.workspace import verify_snapshot


class Memory:
    def __init__(self, store):
        """绑定可追溯存储。 Bind traceable persistent storage."""
        self.store = store

    def add(
        self,
        run_id,
        statement,
        evidence_ref=None,
        dependencies=(),
        conflict_group=None,
        required=False,
    ):
        """记录有来源观察或未验证假设，不自动晋升模型结论。 Record sourced observations or unverified hypotheses without promotion."""
        if (
            not isinstance(statement, str)
            or not statement.strip()
            or len(statement) > 16000
        ):
            raise MasaError("invalid memory statement")
        data = self.store.run(run_id)["data"]
        for parent in dependencies:
            row = self.store.db.execute(
                "SELECT run_id,status FROM memories WHERE id=?", (parent,)
            ).fetchone()
            if not row or row["run_id"] != run_id or row["status"] != "active":
                raise MasaError("memory dependency must be active in this run")
        status, epistemic = "candidate", "inferred"
        if evidence_ref:
            evidence = self.store.read(evidence_ref)
            verify_snapshot(Path(data["workspace"]), data["snapshot_id"])
            files = self.store.read(data["manifest_ref"])
            if (
                evidence.get("kind") != "source_range"
                or evidence.get("snapshot_id") != data["snapshot_id"]
                or evidence.get("profile_id") != digest(data["profile"])
                or files.get(evidence.get("path")) != evidence.get("content_hash")
            ):
                raise MasaError("memory evidence is stale or unsupported")
            raw = (Path(data["workspace"]) / evidence["path"]).read_bytes()
            if (
                raw[evidence["start"] : evidence["end"]].decode("utf-8")
                != evidence["text"]
                or statement != evidence["text"]
            ):
                raise MasaError(
                    "observations must be exact source excerpts; interpretations remain candidates"
                )
            status, epistemic = "active", "observed"
        mid = uuid.uuid4().hex
        value = {
            "id": mid,
            "run_id": run_id,
            "statement": statement,
            "epistemic_status": epistemic,
            "evidence_ref": evidence_ref,
            "dependencies": list(dependencies),
            "conflict_group": conflict_group,
            "scope": "run",
            "snapshot_id": data["snapshot_id"],
            "profile_id": digest(data["profile"]),
            "required": bool(required),
        }
        ref = self.store.put(value)
        with self.store.transaction():
            self.store.db.execute(
                "INSERT INTO memories VALUES(?,?,?,?,?,?)",
                (
                    mid,
                    run_id,
                    data["snapshot_id"],
                    digest(data["profile"]),
                    status,
                    ref,
                ),
            )
            self.store._event(
                run_id,
                "memory_recorded",
                {"memory_ref": ref, "memory_id": mid, "status": status},
            )
        return mid

    def observe_once(self, run_id, evidence):
        """同版本同证据仅保存一次源码观察。 Persist a source observation once per version and evidence."""
        for row in self.store.db.execute(
            "SELECT artifact_ref FROM memories WHERE run_id=? AND status='active'",
            (run_id,),
        ):
            value = self.store.read(row[0])
            if value["evidence_ref"] == evidence["artifact_ref"]:
                return value["id"]
        return self.add(run_id, evidence["text"], evidence["artifact_ref"])

    def current(self, run_id):
        """失效沿依赖传播并保留历史，当前视图只返回有效观察。 Propagate staleness while retaining history; return valid observations only."""
        data = self.store.run(run_id)["data"]
        rows = [
            dict(r)
            for r in self.store.db.execute(
                "SELECT * FROM memories WHERE run_id=? ORDER BY rowid", (run_id,)
            )
        ]
        values = {r["id"]: self.store.read(r["artifact_ref"]) for r in rows}
        stale = {
            r["id"]
            for r in rows
            if r["snapshot_id"] != data["snapshot_id"]
            or r["profile_id"] != digest(data["profile"])
            or r["status"] == "stale"
        }
        while True:
            expanded = stale | {
                mid
                for mid, v in values.items()
                if any(
                    parent in stale or parent not in values
                    for parent in v["dependencies"]
                )
            }
            if expanded == stale:
                break
            stale = expanded
        changed = [r["id"] for r in rows if r["id"] in stale and r["status"] != "stale"]
        if changed:
            with self.store.transaction():
                self.store.db.executemany(
                    "UPDATE memories SET status='stale' WHERE id=?",
                    [(mid,) for mid in changed],
                )
                self.store._event(
                    run_id,
                    "memory_invalidated",
                    {
                        "memory_ids": changed,
                        "reason": "snapshot/profile/dependency changed",
                    },
                )
        groups = {
            v["conflict_group"]
            for v in values.values()
            if v["conflict_group"]
            and sum(w["conflict_group"] == v["conflict_group"] for w in values.values())
            > 1
        }
        admitted = []
        excluded = []
        for row in rows:
            value = values[row["id"]]
            reason = (
                "stale"
                if row["id"] in stale
                else "unverified"
                if row["status"] != "active" and not value.get("required")
                else "conflict"
                if value["conflict_group"] in groups
                else None
            )
            if reason:
                excluded.append(
                    {
                        "id": row["id"],
                        "reason": reason,
                        **({"required": True} if value.get("required") else {}),
                    }
                )
            else:
                admitted.append({**value, "artifact_ref": row["artifact_ref"]})
        return admitted, excluded
