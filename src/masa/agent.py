"""Single shared loop; scripted provider exercises the real tool boundary."""

import time

from masa.domain import MasaError, Node
from masa.policies import route_model


def execute_agent(
    store,
    tools,
    provider,
    run_id: str,
    node: Node,
    attempt_id: str,
    context_builder=None,
    index=None,
) -> dict:
    """循环处理模型提案，成功必须附带工具证据。 Process model proposals; completion requires tool evidence."""
    results = tools.existing(run_id, node)
    while True:
        run = store.run(run_id)
        data = run["data"]
        if run["cancel_requested"]:
            raise MasaError("cancellation_requested")
        if time.time() >= data["deadline_at"]:
            raise MasaError("run_deadline_exhausted")
        context = {
            "goal": data["goal"],
            "snapshot_id": data["snapshot_id"],
            "operation": node.operation,
            "tool_results": results,
            "allowed_tools": [node.operation],
            "phase": "P0",
        }
        if context_builder is not None:
            # 每次调用重新过滤记忆和预算，模型不会隐式继承旧上下文。
            # Refilter memory and budget on each call; models never implicitly inherit old context.
            context, _ = context_builder.build(
                run_id,
                index,
                role="verifier",
                tool_results=results,
                operation=node.operation,
            )
        context_ref = store.put(context)
        # 模型调用前保存实际上下文并扣预算；恢复不会重置额度。
        # Persist actual context and charge budget before inference; recovery never resets the allowance.
        profile = getattr(provider, "profile", route_model())
        store.charge_model(
            run_id, node.id, data["budget"]["model_calls"], profile, context_ref
        )
        started = time.monotonic()
        try:
            if profile.get("provider") == "openai-compatible":
                response = provider.respond(
                    context, timeout=max(0.1, data["deadline_at"] - time.time())
                )
            else:
                response = provider.respond(context)
        except MasaError:
            store.event(
                run_id,
                "model_failed",
                {
                    "step_id": node.id,
                    "provider": profile["provider"],
                    "usage": getattr(provider, "usage", None),
                    "cost": None,
                    "duration_ms": round((time.monotonic() - started) * 1000),
                },
            )
            raise
        if store.run(run_id)["cancel_requested"] or time.time() >= data["deadline_at"]:
            store.event(
                run_id,
                "model_response_discarded",
                {"step_id": node.id, "usage": getattr(provider, "usage", None)},
            )
            raise MasaError(
                "cancellation_requested"
                if store.run(run_id)["cancel_requested"]
                else "run_deadline_exhausted"
            )
        ref = store.put(response)
        store.event(
            run_id,
            "model_completed",
            {
                "step_id": node.id,
                "response_ref": ref,
                "provider": profile["provider"],
                "model": profile.get("model"),
                "usage": getattr(provider, "usage", None),
                "duration_ms": round((time.monotonic() - started) * 1000),
                "cost": 0 if profile["provider"] == "scripted" else None,
            },
        )
        if response.get("type") == "tool_call":
            result = tools.execute(run_id, node, attempt_id, response)
            results.append(result)
            if result["status"] == "cancelled":
                raise MasaError("cancellation_requested")
        elif response.get("type") == "final" and results:
            return {
                "summary": str(response.get("summary", "")),
                "tool_results": results,
                "snapshot_id": data["snapshot_id"],
            }
        else:
            raise MasaError("invalid model output: final requires real tool evidence")
