"""CLI composition root. All source changes remain outside P0 scope."""

import argparse
import json
from pathlib import Path
import sys

from masa import __version__
from masa.adapters.runner import Runner
from masa.adapters.sqlite import Store
from masa.domain import Budget, MasaError, OPERATIONS
from masa.report import render
from masa.runtime import Runtime


PROJECT = Path(__file__).resolve().parents[2]


def parser():
    p = argparse.ArgumentParser(prog="masa", description="MASA P0: offline, evidence-backed Go verification")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--state-dir", type=Path, default=PROJECT / ".masa")
    p.add_argument("--runner", type=Path, default=PROJECT / ".tools/bin/masa-runner.exe")
    p.add_argument("--go", type=Path, default=PROJECT / ".tools/go/bin/go.exe")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--repo", required=True, type=Path)
    run.add_argument("--goal", default="Verify the selected Go check; do not modify source.")
    run.add_argument("--operation", choices=sorted(OPERATIONS), default="go_test")
    run.add_argument("--model-calls", type=int, default=4)
    run.add_argument("--tool-calls", type=int, default=3)
    run.add_argument("--deadline-seconds", type=int, default=300)
    run.add_argument("--tool-timeout-ms", type=int, default=120000)
    run.add_argument("--pause-after", type=int, default=0)
    for name in ("resume", "status", "events", "cancel", "report"):
        cmd = sub.add_parser(name)
        cmd.add_argument("run_id")
        if name == "resume":
            cmd.add_argument("--pause-after", type=int, default=0)
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    store = None
    try:
        store = Store(args.state_dir)
        if args.command in {"run", "resume"}:
            if args.pause_after < 0:
                raise MasaError("pause-after must not be negative")
            runtime = Runtime(store, Runner(args.runner, args.go))
            if args.command == "run":
                budget = Budget(model_calls=args.model_calls, tool_calls=args.tool_calls,
                                deadline_seconds=args.deadline_seconds, tool_timeout_ms=args.tool_timeout_ms)
                run_id = runtime.create(args.repo, args.goal, budget, args.operation)
                print(f"run_id={run_id}", file=sys.stderr, flush=True)
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
    except (MasaError, OSError) as exc:
        print(f"masa: {exc}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()
