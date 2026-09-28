"""Offline provider makes orchestration reproducible, not intelligent."""

from typing import Protocol


class ModelProvider(Protocol):
    def respond(self, context: dict) -> dict: ...


class ScriptedProvider:
    def respond(self, context: dict) -> dict:
        """生成可重复的协议提案；不假装进行了智能审查。 Produce reproducible protocol proposals, not intelligent review."""
        if context.get('role') in {'planner', 'developer', 'reviewer'}:
            return {'type': 'role_result', 'role': context['role'], 'snapshot_id': context['snapshot_id'], 'run_id': context['run_id'],
                    'summary': 'Read-only scripted protocol check; no code changes or independent semantic review.',
                    'evidence_refs': context['role_evidence_refs'], 'decision': 'ready'}
        if not context["tool_results"]:
            return {"type": "tool_call", "operation": context["operation"], "arguments": {}}
        return {"type": "final", "summary": "Verification observed; runtime Gate decides the outcome."}
