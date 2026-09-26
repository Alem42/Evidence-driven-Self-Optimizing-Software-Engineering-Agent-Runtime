"""P0 policies are plain functions, replaceable without changing the scheduler."""


def route_model() -> dict:
    return {"alias": "default", "provider": "scripted", "model": "scripted-v1",
            "policy_version": "single-model-v1", "reason": "offline P0 verification",
            "billed_tokens": 0, "cost": 0}
