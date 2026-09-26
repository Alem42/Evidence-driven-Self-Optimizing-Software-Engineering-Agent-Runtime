"""Human-readable run report; claims limited to the selected P0 check."""


def render(store, run_id: str) -> str:
    """展示真实运行证据及已提交补丁。 Render actual run evidence and committed patches."""
    run = store.run(run_id)
    data = run["data"]
    lines = [f"# MASA P0 run {run_id}", "", f"Status: **{run['status']}**", "",
             f"Reason: {run['reason'] or '-'}", "", f"Snapshot: `{data['snapshot_id']}`", "",
             f"Graph version: {data['graph']['version']}; policy: `{data['graph']['policy_version']}`", "",
             f"Scripted model calls: {run['model_calls']}; tool calls: {run['tool_calls']}", "",
             "Provider: scripted-v1 (offline, no LLM inference; billed tokens/cost = 0).", "",
             "| Step | Status | Evidence artifact |", "|---|---|---|"]
    for step in store.steps(run_id):
        lines.append(f"| {step['id']} | {step['status']} | {step['result_ref'] or '-'} |")
    lines += ["", "## Tool evidence", ""]
    for call in store.tools(run_id):
        request = store.read(call["request_ref"])
        if call["result_ref"]:
            result = store.read(call["result_ref"])
            lines.append(f"- `{request['operation']}`: {result['status']}, exit={result['exit_code']}, "
                         f"duration={result['duration_ms']}ms, truncated={result['truncated']}; "
                         f"artifact `{call['result_ref']}`")
        else:
            lines.append(f"- `{request['operation']}`: UNCERTAIN; no durable result; do not replay automatically.")
    patch = store.db.execute("SELECT * FROM patches WHERE run_id=?", (run_id,)).fetchone()
    if patch:
        lines += ["", "## Controlled patch", "", f"Status: {patch['status']}; request artifact: `{patch['request_ref']}`"]
        if patch['result_ref']:
            change = store.read(patch['result_ref'])
            lines += [f"Snapshot: `{change['before_snapshot']}` → `{change['snapshot_id']}`",
                      f"Result artifact: `{patch['result_ref']}`",
                      "Files: " + ", ".join(f"`{f['path']}`" for f in change['files'])]
    lines += ["", "## Scope", "", "This run verifies only its selected Go operation in a copied workspace. "
              "It does not generate patches, run multiple agents, or prove hidden acceptance tests passed.", ""]
    return "\n".join(lines)
