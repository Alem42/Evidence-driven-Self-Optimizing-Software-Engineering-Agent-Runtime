"""Offline provider makes orchestration reproducible, not intelligent."""

from typing import Protocol


class ModelProvider(Protocol):
    def respond(self, context: dict) -> dict: ...


class ScriptedProvider:
    def respond(self, context: dict) -> dict:
        if not context["tool_results"]:
            return {"type": "tool_call", "operation": context["operation"], "arguments": {}}
        return {"type": "final", "summary": "Verification observed; runtime Gate decides the outcome."}
