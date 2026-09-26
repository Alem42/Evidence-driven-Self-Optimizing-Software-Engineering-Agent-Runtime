"""Single shared loop; scripted provider exercises the real tool boundary."""

import time

from masa.domain import MasaError, Node
from masa.policies import route_model


def execute_agent(store, tools, provider, run_id: str, node: Node, attempt_id: str) -> dict:
    results = tools.existing(run_id, node)
    while True:
        run = store.run(run_id)
        data = run["data"]
        if run["cancel_requested"]:
            raise MasaError("cancellation_requested")
        if time.time() >= data["deadline_at"]:
            raise MasaError("run_deadline_exhausted")
        context = {"goal": data["goal"], "snapshot_id": data["snapshot_id"],
                   "operation": node.operation, "tool_results": results,
                   "allowed_tools": [node.operation], "phase": "P0"}
        context_ref = store.put(context)
        store.charge_model(run_id, node.id, data["budget"]["model_calls"], route_model(), context_ref)
        response = provider.respond(context)    # get llm model response
        ref = store.put(response)
        store.event(run_id, "model_completed", {"step_id": node.id, "response_ref": ref,
                                                "provider": "scripted", "tokens": 0})
        if response.get("type") == "tool_call":
            result = tools.execute(run_id, node, attempt_id, response)
            results.append(result)
            if result["status"] == "cancelled":
                raise MasaError("cancellation_requested")
        elif response.get("type") == "final" and results:
            return {"summary": str(response.get("summary", "")), "tool_results": results,
                    "snapshot_id": data["snapshot_id"]}
        else:
            raise MasaError("invalid model output: final requires real tool evidence")
