"""CLI composition root. All source changes remain outside P0 scope."""

import argparse
import json
from pathlib import Path
import sys

from masa import __version__
from masa.infrastructure.runner import Runner
from masa.infrastructure.store import Store
from masa.domain.models import Budget, MasaError, OPERATIONS
from masa.application.reports import render
from masa.runtime.engine import Runtime


PROJECT = Path(__file__).resolve().parents[3]


def parser():
    """定义命令和显式权限参数。 Define commands and explicit operation parameters."""
    p = argparse.ArgumentParser(prog="masa", description="MASA P0: offline, evidence-backed Go verification")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--state-dir", type=Path, default=PROJECT / ".masa")
    p.add_argument("--runner", type=Path, default=PROJECT / ".tools/bin/masa-runner.exe")
    p.add_argument("--go", type=Path, default=PROJECT / ".tools/go/bin/go.exe")
    sub = p.add_subparsers(dest="command", required=True)
    ui = sub.add_parser("serve", aliases=["ui"], help="launch the local API server (frontend runs separately)")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--origin", action="append", default=None, help="allowed frontend origin (repeatable); default: local Vite dev/preview ports")
    run = sub.add_parser("run")
    run.add_argument("--repo", required=True, type=Path)
    run.add_argument("--goal", default="Verify the selected Go check; do not modify source.")
    run.add_argument("--operation", choices=sorted(OPERATIONS), default="go_test")
    run.add_argument("--model-calls", type=int, default=4)
    run.add_argument("--tool-calls", type=int, default=3)
    run.add_argument("--deadline-seconds", type=int, default=300)
    run.add_argument("--tool-timeout-ms", type=int, default=120000)
    run.add_argument("--pause-after", type=int, default=0)
    run.add_argument("--patch", type=Path, help="apply a controlled JSON patch before verification")
    run.add_argument("--intelligence", action="store_true", help="include versioned Go evidence in each model context")
    run.add_argument('--collaboration', action='store_true', help='LEGACY demo: read-only scripted four-role protocol; not project generation')
    inspect = sub.add_parser("inspect", help="inspect Go syntax evidence and role context")
    inspect.add_argument("run_id")
    inspect.add_argument("--query", default="")
    inspect.add_argument("--role", choices=['planner','developer','tester','reviewer','verifier'], default='developer')
    inspect.add_argument("--budget-bytes", type=int, default=32000)
    for name in ("resume", "status", "events", "cancel", "report"):
        cmd = sub.add_parser(name)
        cmd.add_argument("run_id")
        if name == "resume":
            cmd.add_argument("--pause-after", type=int, default=0)
    return p


def main(argv=None) -> int:
    """装配应用并输出可诊断结果。 Compose the application and return diagnostic results."""
    args = parser().parse_args(argv)
    store = None
    try:
        if args.command in ("serve", "ui"):
            from masa.interfaces.http.server import serve, DEFAULT_ORIGINS
            serve(args.state_dir, args.runner, args.go, PROJECT, args.port, tuple(args.origin) if args.origin else DEFAULT_ORIGINS)
            return 0
        store = Store(args.state_dir)
        if args.command == 'inspect':
            from masa.intelligence.context import ContextBuilder
            from masa.intelligence.index import Intelligence
            from masa.infrastructure.locking import owner_lock
            engine = Intelligence(store, Runner(args.runner, args.go))
            with owner_lock(store.root / 'runtime.lock'):
                index = engine.ensure(args.run_id)
                search = engine.search(args.run_id, index, args.query)
                context, manifest = ContextBuilder(store, engine).build(args.run_id, index, args.role, args.query, budget_bytes=args.budget_bytes)
            print(json.dumps({'index':index, 'search':search, 'context':context, 'manifest':manifest}, ensure_ascii=False, indent=2))
            return 0
        if args.command in {"run", "resume"}:
            if args.pause_after < 0:
                raise MasaError("pause-after must not be negative")
            runtime = Runtime(store, Runner(args.runner, args.go))
            if args.command == "run":
                budget = Budget(model_calls=args.model_calls, tool_calls=args.tool_calls,
                                deadline_seconds=args.deadline_seconds, tool_timeout_ms=args.tool_timeout_ms)
                from masa.runtime.graph import collaboration_policy
                if args.collaboration and args.patch:
                    raise MasaError('read-only role demo cannot apply a patch')
                run_id = runtime.create(args.repo, args.goal, budget, args.operation, intelligence=args.intelligence,
                                        graph=collaboration_policy(args.operation) if args.collaboration else None)
                print(f"run_id={run_id}", file=sys.stderr, flush=True)
                if args.patch:
                    # 补丁先发布新快照，再让验证图执行；用户源仓库不参与写入。
                    # Publish the patched snapshot before verification; never write to the source repo.
                    from masa.runtime.patches import Patches
                    Patches(store).apply(run_id, json.loads(args.patch.read_text(encoding="utf-8")))
            else:
                run_id = args.run_id
            result = runtime.execute(run_id, args.pause_after)
            print(json.dumps({k: result[k] for k in ("id", "status", "reason", "model_calls", "tool_calls")}, ensure_ascii=False))
            return 0 if result["status"] in {"succeeded", "paused"} else 1
        if args.command == "cancel":
            store.cancel(args.run_id)
            print("Cancellation requested; active runtime will stop the tool, or resume will observe it.")
        elif args.command == "report":
            print(render(store, args.run_id), end="")
        elif args.command == "events":
            store.run(args.run_id)
            print(json.dumps(store.events(args.run_id), ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"run": store.run(args.run_id), "steps": store.steps(args.run_id)}, ensure_ascii=False, indent=2))
        return 0
    except (MasaError, OSError, ValueError) as exc:
        print(f"masa: {exc}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()
