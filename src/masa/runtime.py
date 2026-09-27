"""P0 serial scheduler with immutable evidence and conservative recovery."""

from dataclasses import asdict
import hashlib
from pathlib import Path
import time
import uuid

from masa.adapters.model import ScriptedProvider
from masa.agent import execute_agent
from masa.domain import Budget, Graph, MasaError, TERMINAL
from masa.locking import owner_lock
from masa.patching import Patches
from masa.tools import Tools
from masa.workflow import default_policy, ready_nodes, validate
from masa.workspace import copy_snapshot, verify_snapshot
from masa.collaboration import ANALYSIS_ROLES, validate_role_result, receive_handoffs


class Runtime:
    def __init__(self, store, executor, provider=None):
        """装配运行时依赖。 Assemble runtime dependencies."""
        self.store = store
        self.executor = executor
        self.provider = provider or ScriptedProvider()
        self.tools = Tools(store, executor)

    def _profile(self) -> dict:
        """绑定工具链与策略身份。 Bind evidence to toolchain and policy identity."""
        # Bind evidence to the exact tool binaries and our fixed execution policy.
        import os
        profile = {"platform": os.name, "runner_protocol": 1,
                   "execution_policy": "offline-readonly-mod-v1"}
        for name in ("executable", "go_executable"):
            path = getattr(self.executor, name, None)
            if path:
                profile[name] = {"path": str(path), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
        go_path = getattr(self.executor, "go_executable", None)
        if go_path:
            fmt_path = Path(go_path).with_name("gofmt" + Path(go_path).suffix)
            if not fmt_path.is_file():
                raise MasaError("gofmt executable missing")
            profile["gofmt"] = hashlib.sha256(fmt_path.read_bytes()).hexdigest()
        return profile

    def create(self, source: Path, goal: str, budget: Budget, operation="go_test", graph=None, parent_run_id=None, intelligence=False, codegen=None, project_plan=None, project_bundle=None) -> str:
        """隔离源仓库并持久化初始图。 Isolate source and persist the initial graph."""
        budget.validate()
        if not goal.strip() or len(goal) > 16000:
            raise MasaError("goal must be nonempty and at most 16000 characters")
        graph = graph or default_policy(operation)
        validate(graph)
        if any(n.role != 'verifier' for n in graph.nodes) and hasattr(self.provider, 'profile'):
            raise MasaError('role protocol currently supports scripted validation only')
        with owner_lock(self.store.root / "runtime.lock"):
            if parent_run_id:
                parent = self.store.run(parent_run_id)
                if parent["status"] not in {"paused", "succeeded", "failed", "cancelled", "needs_attention"}:
                    raise MasaError("pause the original run before revising its request")
            run_id = uuid.uuid4().hex
            workspace = self.store.root / "workspaces" / run_id
            snapshot_id, files = copy_snapshot(source, workspace)
            now = time.time()
            data = {"goal": goal, "source": str(source.resolve()), "workspace": str(workspace),
                    "snapshot_id": snapshot_id, "manifest_ref": self.store.put(files),
                    "graph": graph.to_dict(), "budget": asdict(budget), "created_at": now,
                    "deadline_at": now + budget.deadline_seconds, "profile": self._profile()}
            if intelligence:
                data['intelligence'] = True
            if codegen is not None:
                data['codegen'] = codegen
            if project_plan is not None:
                data['project_plan'] = project_plan
            if project_bundle is not None:
                data['project_bundle'] = project_bundle
                data['source'] = str(workspace)
            if hasattr(self.provider, 'profile'):
                data['model_profile'] = self.provider.profile
            if parent_run_id:
                data["parent_run_id"] = parent_run_id
            self.store.create(run_id, data)
            if parent_run_id:
                self.store.event(run_id, "human_request_revised", {"parent_run_id": parent_run_id,
                                                                   "policy": "new run; old evidence not reused"})
        return run_id

    @staticmethod
    def compile_project_checks(spec, checks):
        """校验角色计划并生成受限动作图；提案不能提供任意执行图。 Validate role plans and compile an allowlisted action graph."""
        from masa.project_plan import validate_spec, validate_checks
        from masa.workflow import validate
        from masa.domain import Node
        validate_spec(spec)
        validate_checks(checks, spec)
        # 采用已批准的 Agent 检查选择，最终 Gate 始终由 Runtime 添加。
        # Compile approved agent choices; Runtime always adds the independent Gate.
        names={'go_test':'test','go_vet':'vet','go_fmt_check':'format'}
        nodes=tuple(Node(names[c['operation']],'tool',operation=c['operation']) for c in checks)
        graph=Graph(nodes+(Node('gate','gate',tuple(n.id for n in nodes),'all_terminal'),),policy_version='agent-check-plan-v1')
        validate(graph)
        return graph

    def execute(self, run_id: str, pause_after: int = 0) -> dict:
        """持锁恢复并调度图，依据证据结束。 Recover and schedule under lock, then finalize from evidence."""
        with owner_lock(self.store.root / "runtime.lock"):
            run = self.store.run(run_id)
            if run["status"] in {"succeeded", "failed", "cancelled", "needs_attention"}:
                return run
            generation = run['data'].get('codegen')
            if run['data'].get('project_bundle'):
                # 多文件快照必须与用户批准的完整内容一致。
                # The complete snapshot must match the human-approved file bundle.
                approved = self.store.read(run['data']['project_bundle']['approval_ref'])
                expected = {name: hashlib.sha256(content.encode('utf-8')).hexdigest()
                            for name, content in approved['files'].items()}
                if self.store.read(run['data']['manifest_ref']) != expected:
                    raise MasaError('project snapshot does not match approved files')
            if run['data'].get('project_plan'):
                # 规划通过不等于代码存在，CLI 也不能执行占位项目。
                # Approved planning is not generated code; block placeholder execution through CLI too.
                raise MasaError('project plan only: code generation is not implemented yet')
            if generation:
                # 人工批准与对应补丁缺一不可，CLI 恢复也不能绕过审核。
                # Both human approval and its exact patch are mandatory, including CLI resumes.
                patch = self.store.db.execute('SELECT request_ref FROM patches WHERE run_id=?', (run_id,)).fetchone()
                if generation.get('status') != 'approved' or not patch:
                    raise MasaError('human_review_required: approve the code proposal before execution')
                if self.store.read(patch[0])['patch'] != self.store.read(generation['approval_ref']):
                    raise MasaError('approved patch does not match the applied patch')
            try:
                # 必须先核对未完成补丁，再按已发布快照校验工具证据。
                # Reconcile pending writes before validating evidence against the published snapshot.
                Patches(self.store).recover(run_id)
                run = self.store.run(run_id)
                data = run["data"]
                if data.get('model_profile') != getattr(self.provider,'profile',None):
                    raise MasaError('model_profile_mismatch: resume requires the original provider configuration')
                graph = Graph.from_dict(data["graph"])
                validate(graph)
                if self._profile() != data["profile"]:
                    raise MasaError("tool_profile_mismatch: resume requires the original tool binaries")
                self.store.read(data["manifest_ref"])
                verify_snapshot(Path(data["workspace"]), data["snapshot_id"])
                if not self.store.recover(run_id):
                    return self.store.run(run_id)
                builder, index = None, None
                if data.get('intelligence'):
                    from masa.intelligence import Intelligence
                    from masa.context import ContextBuilder
                    engine = Intelligence(self.store, self.executor)
                    index = engine.ensure(run_id)
                    builder = ContextBuilder(self.store, engine)
                if run["status"] == "paused":
                    self.store.clear_pause(run_id)
                self.store.set_status(run_id, "running")
                completed = 0
                while True:
                    current = self.store.run(run_id)
                    if current["cancel_requested"]:
                        self.store.set_status(run_id, "cancelled", "cancellation_requested")
                        break
                    if time.time() >= data["deadline_at"]:
                        self.store.set_status(run_id, "failed", "run_deadline_exhausted")
                        break
                    if self.store.pause_requested(run_id):
                        self.store.set_status(run_id, "paused", "human requested node-boundary pause")
                        break
                    states = {s["id"]: s["status"] for s in self.store.steps(run_id)}
                    gate = next(n for n in graph.nodes if n.type == "gate")
                    if states[gate.id] in {"succeeded", "failed"}:
                        # Crash after Gate commit but before Run finalization.
                        passed, result = self._gate(run_id, graph, data["snapshot_id"])
                        self.store.set_status(run_id, "succeeded" if passed else "failed", result["reason"])
                        break
                    for node in graph.nodes:
                        if states[node.id] == "pending" and node.trigger == "all_succeeded" and any(
                                states[d] in {"failed", "cancelled", "skipped"} for d in node.dependencies):
                            self.store.skip(run_id, node.id)
                            states[node.id] = "skipped"
                    ready = ready_nodes(graph, states)
                    if not ready:
                        raise MasaError("graph_stalled: no ready node and no final Gate")
                    node = ready[0]
                    # 先写 attempt 再执行，确保中断后可以识别未完成节点。
                    # Persist the attempt before execution so recovery can identify unfinished nodes.
                    attempt_id = self.store.start(run_id, node.id)
                    try:
                        verify_snapshot(Path(data["workspace"]), data["snapshot_id"])
                        if node.type == "agent":
                            result = execute_agent(self.store, self.tools, self.provider, run_id, node, attempt_id, builder, index)
                            passed = (result['decision'] == 'ready' if node.role in ANALYSIS_ROLES else
                                      all(r["status"] == "completed" and r["exit_code"] == 0 for r in result["tool_results"]))
                        elif node.type == "tool":
                            known = self.tools.existing(run_id, node)
                            tool_result = known[-1] if known else self.tools.execute(
                                run_id, node, attempt_id, {"operation": node.operation, "arguments": {}})
                            result = {"tool_results": [tool_result], "snapshot_id": data["snapshot_id"]}
                            passed = tool_result["status"] == "completed" and tool_result["exit_code"] == 0
                        else:
                            passed, result = self._gate(run_id, graph, data["snapshot_id"])
                        verify_snapshot(Path(data["workspace"]), data["snapshot_id"])
                        self.store.finish(run_id, node.id, attempt_id, "succeeded" if passed else "failed", result)
                    except (MasaError, KeyboardInterrupt) as exc:
                        reason = "cancellation_requested" if isinstance(exc, KeyboardInterrupt) else str(exc)
                        cancelled = reason == "cancellation_requested"
                        self.store.finish(run_id, node.id, attempt_id, "cancelled" if cancelled else "failed", {"error": reason})
                        raise MasaError(reason) from exc
                    if node.type == "gate":
                        self.store.set_status(run_id, "succeeded" if passed else "failed", result["reason"])
                        break
                    completed += 1
                    if self.store.pause_requested(run_id) or (pause_after and completed >= pause_after):
                        self.store.set_status(run_id, "paused", "explicit node-boundary pause")
                        break
            except MasaError as exc:
                reason = str(exc)
                status = ("cancelled" if reason == "cancellation_requested" else
                          "failed" if "budget_exhausted" in reason or "deadline_exhausted" in reason else "needs_attention")
                self.store.set_status(run_id, status, reason)
            except KeyboardInterrupt:
                self.store.set_status(run_id, "cancelled", "interrupted at node boundary")
            return self.store.run(run_id)

    def _gate(self, run_id: str, graph: Graph, snapshot: str) -> tuple[bool, dict]:
        """独立核对当前快照和工具账本。 Independently verify the current snapshot and tool ledger."""
        steps = {s["id"]: s for s in self.store.steps(run_id)}
        checks = [n for n in graph.nodes if n.type != "gate"]
        passed = True
        for n in checks:
            step = steps[n.id]
            if step["status"] not in TERMINAL:
                raise MasaError("gate encountered nonterminal evidence")
            if step["status"] != "succeeded" or not step["result_ref"]:
                passed = False
                continue
            evidence = self.store.read(step["result_ref"])
            if n.role != 'verifier':
                receive_handoffs(self.store, run_id, n, require_existing=True)
            if n.role in ANALYSIS_ROLES:
                passed &= validate_role_result(self.store, run_id, n, evidence)['decision'] == 'ready'
                continue
            results = evidence.get("tool_results", [])
            if evidence.get("snapshot_id") != snapshot or not results:
                raise MasaError("stale_evidence: gate has no current tool result")
            # Re-read the tool ledger, not just the model/node summary.
            recorded = self.tools.existing(run_id, n)
            if results != recorded:
                raise MasaError("integrity_error: node result differs from tool ledger")
            passed &= all(r["status"] == "completed" and r["exit_code"] == 0 for r in recorded)
        return passed, {"snapshot_id": snapshot, "passed": passed,
                        "reason": ("read-only role protocol and selected checks passed" if any(n.role != 'verifier' for n in graph.nodes)
                                   else "P0 selected checks passed") if passed else "one or more selected checks failed"}
