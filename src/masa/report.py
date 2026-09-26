"""Human-readable run report; claims limited to the selected P0 check."""


def render(store, run_id: str) -> str:
    """展示真实运行证据及已提交补丁。 Render actual run evidence and committed patches."""
    run = store.run(run_id)
    data = run["data"]
    profile = data.get("model_profile")
    provider = (
        f"Provider: {profile['provider']} / {profile['model']}; cost unknown (see provider billing)."
        if profile
        else "Provider: scripted-v1 (offline, no LLM inference; billed tokens/cost = 0)."
    )
    lines = [
        f"# MASA P0 run {run_id}",
        "",
        f"Status: **{run['status']}**",
        "",
        f"Reason: {run['reason'] or '-'}",
        "",
        f"Snapshot: `{data['snapshot_id']}`",
        "",
        f"Graph version: {data['graph']['version']}; policy: `{data['graph']['policy_version']}`",
        "",
        f"Model calls: {run['model_calls']}; tool calls: {run['tool_calls']}",
        "",
        provider,
        "",
        "| Step | Status | Evidence artifact |",
        "|---|---|---|",
    ]
    for step in store.steps(run_id):
        lines.append(
            f"| {step['id']} | {step['status']} | {step['result_ref'] or '-'} |"
        )
    if profile:
        # 汇总返回的真实用量；无 usage 的请求不能记为零费用。
        # Aggregate reported usage without treating missing billing data as zero cost.
        records = [
            e["payload"].get("usage")
            for e in store.events(run_id)
            if e["type"]
            in {"model_completed", "model_failed", "model_response_discarded"}
        ]
        totals = {
            k: sum(u.get(k) or 0 for u in records if u)
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
        }
        lines += [
            "",
            f"Reported token usage: {totals}; requests with usage: {sum(bool(u) for u in records)}/{run['model_calls']}.",
        ]
    lines += ["", "## Tool evidence", ""]
    for call in store.tools(run_id):
        request = store.read(call["request_ref"])
        if call["result_ref"]:
            result = store.read(call["result_ref"])
            lines.append(
                f"- `{request['operation']}`: {result['status']}, exit={result['exit_code']}, "
                f"duration={result['duration_ms']}ms, truncated={result['truncated']}; "
                f"artifact `{call['result_ref']}`"
            )
        else:
            lines.append(
                f"- `{request['operation']}`: UNCERTAIN; no durable result; do not replay automatically."
            )
    patch = store.db.execute(
        "SELECT * FROM patches WHERE run_id=?", (run_id,)
    ).fetchone()
    if patch:
        lines += [
            "",
            "## Controlled patch",
            "",
            f"Status: {patch['status']}; request artifact: `{patch['request_ref']}`",
        ]
        if patch["result_ref"]:
            change = store.read(patch["result_ref"])
            lines += [
                f"Snapshot: `{change['before_snapshot']}` → `{change['snapshot_id']}`",
                f"Result artifact: `{patch['result_ref']}`",
                "Files: " + ", ".join(f"`{f['path']}`" for f in change["files"]),
            ]
    # 上下文报告引用实际输入清单，不从模型摘要推断覆盖率。
    # Report actual input manifests instead of inferring coverage from model summaries.
    context_events = [e for e in store.events(run_id) if e["type"] == "context_built"]
    if context_events:
        lines += ["", "## Evidence context", ""]
        for event in context_events:
            ref = event["payload"]["manifest_ref"]
            context = store.read(ref)
            lines.append(
                f"- {context['role']}: {context['input_bytes']}/{context['budget_bytes']} UTF-8 bytes; "
                f"included={len(context['included'])}, omitted={len(context['omitted'])}; manifest `{ref}`"
            )
        lines += [
            "Syntax candidates only; byte estimates are not measured model tokens."
        ]
    lines += [
        "",
        "## Scope",
        "",
        "This run verifies only its selected Go operation in a copied workspace. "
        "It does not generate patches, run multiple agents, or prove hidden acceptance tests passed.",
        "",
    ]
    return "\n".join(lines)
