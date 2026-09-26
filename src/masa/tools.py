"""Tool policy and durable side-effect ledger shared by agent/tool nodes."""

import time
import uuid
from pathlib import Path
from typing import Protocol

from masa.domain import MasaError, Node
from masa.workspace import verify_snapshot


class ToolExecutor(Protocol):
    def execute(self, request: dict, workspace: Path, cancelled) -> dict: ...


class Tools:
    def __init__(self, store, executor: ToolExecutor):
        """绑定工具执行与账本适配器。 Bind tool execution and ledger adapters."""
        self.store, self.executor = store, executor

    def existing(self, run_id: str, node: Node) -> list[dict]:
        """只复用当前快照上已落盘的结果。 Reuse only durable results for the current snapshot."""
        results = []
        for call in self.store.tools(run_id, node.id):
            if call["status"] != "recorded":
                raise MasaError("uncertain_tool_state: automatic replay is forbidden")
            request = self.store.read(call["request_ref"])
            result = self.store.read(call["result_ref"])
            snapshot = self.store.run(run_id)["data"]["snapshot_id"]
            if (request["operation"] != node.operation or result["snapshot_id"] != snapshot
                    or request["request_id"] != call["id"] or result["request_id"] != call["id"]):
                raise MasaError("stale_evidence: tool ledger mismatch")
            results.append(result)
        return results

    def execute(self, run_id: str, node: Node, attempt_id: str, action: dict) -> dict:
        """校验权限、记录意图、执行并保存证据。 Validate policy, journal intent, execute, and save evidence."""
        if action.get("operation") != node.operation or action.get("arguments") != {}:
            raise MasaError("tool policy rejected model arguments/operation")
        if self.existing(run_id, node):
            raise MasaError("one tool call per P0 verification node; refusing duplicate")
        run = self.store.run(run_id)
        data = run["data"]
        workspace = Path(data["workspace"])
        verify_snapshot(workspace, data["snapshot_id"])
        remaining = data["deadline_at"] - time.time()
        if remaining <= 0:
            raise MasaError("run_deadline_exhausted")
        request = {"protocol_version": 1, "request_id": uuid.uuid4().hex,
                   "snapshot_id": data["snapshot_id"], "operation": node.operation,
                   "timeout_ms": max(1, min(data["budget"]["tool_timeout_ms"], int(remaining * 1000))),
                   "max_output_bytes": data["budget"]["max_output_bytes"]}
        # 先提交意图再运行外部进程；缺少结果时不能假定工具未执行。
        # Commit intent before launching the process; a missing result does not prove non-execution.
        self.store.tool_intent(run_id, node.id, attempt_id, request, data["budget"]["tool_calls"])
        result = self.executor.execute(request, workspace,
                                       lambda: bool(self.store.run(run_id)["cancel_requested"])
                                       or time.time() >= data["deadline_at"])
        self.store.tool_result(run_id, request["request_id"], result)
        verify_snapshot(workspace, data["snapshot_id"])
        if time.time() >= data["deadline_at"]:
            raise MasaError("run_deadline_exhausted")
        if result["status"] == "cancelled":
            raise MasaError("cancellation_requested")
        return result
