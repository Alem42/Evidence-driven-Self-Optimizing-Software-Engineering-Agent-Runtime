# MASA — An AI Coding Runtime for Unattended Work
**Local small models iterate; cloud models supervise on demand.**

> MASA｜面向无人值守场景的 AI 编码运行时：本地小模型迭代，云端大模型按需监督。

![Workbench: code appears as it is written](assets/workbench-streaming.jpg)

## What it is / 这是什么

You describe a small coding task (or bring a spec). MASA **plans → writes and freezes the tests first → generates code file by file → runs real checks in an isolated sandbox → diagnoses failures and reopens only the failing package → reports pass / fail with evidence.**

Three problems it targets:

| Problem with typical AI coding tools | What MASA does |
|---|---|
| The model says "done", nobody proves it | An independent **Gate** decides, from real tool results recorded in an append-only ledger. Tests are written first and frozen; changing them is refused. |
| Cloud cost has no hard ceiling | Per-task budget for tokens, time and money. Cheap models try first, stronger ones only after a failure. Budget is **reserved before** a call and **settled after**. |
| A crash repeats paid calls | An event-sourced ledger records the *intent* before any network request, so a paid call is never replayed after a restart. |

> 输入一句需求，系统自动规划、先写并冻结测试、逐文件生成、在隔离环境里真实运行检查，失败后只重开出问题的包，最后给出“通过 / 没通过”和证据。“是否完成”由运行时按真实工具结果裁决，不信模型自报。

**Intended scenario / 目标场景：** hand over a well-specified small module before you leave; read an evidence-backed report in the morning (what passed, what it cost, where it got stuck and why). Think "test-driven overnight delegation", not an interactive pair programmer.

## Quick start / 快速开始 (Windows)

**Requirements:** Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js 20+, and at least one model: a cloud API key (OpenAI-compatible, e.g. DeepSeek) and/or a local [Ollama](https://ollama.com) model. A Go toolchain is installed locally by the setup script; Python projects use a project-local environment.

```powershell
# 1. One-time setup: Go toolchain into .tools, Python deps, build the Go runner
.\scripts\setup-env.ps1
.\scripts\build.ps1

# 2. Terminal A — backend API (loopback only)
.\.venv\Scripts\python.exe -m masa serve --port 8765

# 3. Terminal B — frontend
cd frontend; npm install; npm run dev        # http://127.0.0.1:5173
```

1. Open **Settings → Models** and add a cloud model (and/or pick an Ollama model under **Local runtime**).
2. On the home page, choose a language (Go or Python), describe the task, and start.
3. Watch the flow strip, the streaming editor and the file tree. You can **add a hint, pause, resume or stop** from the input bar; input takes effect at node boundaries and never interrupts a paid call.
4. Open the report for the verdict, evidence, cost per role and every model call (the inspector shows exactly what each call saw).

> 先在“设置 → 模型配置”添加云端模型（或选择本地 Ollama 模型），再在首页选择语言并描述需求。底部输入框可追加提示、暂停、继续、停止，只在节点边界生效。

### Command line / 命令行

```powershell
python -m masa lang list                       # available language packs
python -m masa lang check python               # conformance check of a pack
python -m masa run --repo <dir> --language go  # run verification on a project
python -m masa status <run_id>                 # also: events, report, resume, cancel
```

### MCP server (independent verification for other agents)

Expose the Gate as a tool so another agent (Claude Code, Cursor, …) can ask "is this really correct?" before declaring success.

```powershell
pip install "mcp>=1.2"
python scripts/ops/mcp_server.py               # stdio
```

| Tool | Purpose |
|---|---|
| `verify_project(files, checks, language)` | Run allow-listed checks in an isolated workspace; return the Gate verdict with evidence |
| `route_preview(role, history, …)` | Read-only preview of which model the router would pick, and why |
| `get_run_report(run_id)` | Read a finished run's report |
| `run_benchmark(...)` | Real evaluation; spends money, **refused unless** `MASA_MCP_ALLOW_BENCH=1` |

Only relative source-file paths (size-limited) are accepted; no command strings are ever executed.

### Docker (read-only showcase) / Docker 展示部署

```bash
docker compose up --build -d      # http://localhost:8765, read-only demo mode by default
```

No local model and no API keys are inside the image. Set `MASA_DEMO=0` and mount a state volume for full mode.

### Tests

```powershell
$env:PYTHONPATH="src"; python -m pytest -q     # backend (a few ERRORs are test_* helper functions collected by mistake)
cd frontend; npx vitest run; npm run build     # frontend
cd runner; go test ./...                       # Go runner
```

## How a task flows / 任务流程

```
requirement
   │  pre-check (rules; optional local review)
   ▼
Architect ─► spec + acceptance criteria + check plan + cases + assumptions   (strong model, ONE call; deterministic gate checks every part;
   │          optional blind-derivation audit of expected values; for Python the cases can be structured black-box cases that the harness
   │          turns into tests/test_acceptance.py deterministically)
   │  approved & FROZEN
   ▼
Generate files ──► per-file syntax check, import repair, in-place rewrite
   ▼
Verify in sandbox ──► Gate: real tests / static analysis / format, snapshot matches, tool ledger matches
   ├─ pass ──► report with evidence
   └─ fail ──► ownership analysis ─► Diagnoser = the Architect in adjudication mode (the only way a test gets revised)
                 ├─ code wrong ─► reopen ONLY the failing package (worker loop, writes limited to that package, tests frozen)
                 ├─ test wrong ─► revise tests in a new version (old versions are never overwritten)
                 └─ "cannot fix the code" is NOT evidence that the test is wrong: ask the Diagnoser once, then hand over to a human
```

## Architecture / 架构

```
 ┌──────────────── Frontend (React + TypeScript) ────────────────┐
 │ workbench · streaming editor + diff · flow strip · inspector  │
 └──────────────────────────────┬────────────────────────────────┘
                                │ HTTP / SSE (loopback by default)
 ┌──────────────────────────────▼────────────────────────────────┐
 │ interfaces/   http server · CLI · MCP server                   │
 ├────────────────────────────────────────────────────────────────┤
 │ application/  coordinator (workflow graph) · routing · budget  │
 │               planning · generation · v2 package workers       │
 │               checks/ (deterministic) · review/ · call inspector│
 ├────────────────────────────────────────────────────────────────┤
 │ roles/        RoleSpec registry: roles as validated data       │
 │ languages/    language packs: go · python (pluggable)          │
 │ runtime/      engine + Gate · role runtime (exactly-once)      │
 │               streaming · live output · user input             │
 ├────────────────────────────────────────────────────────────────┤
 │ infrastructure/  SQLite event ledger + content-addressed store │
 │                  model providers · settings · process control  │
 └──────────────────────────────┬────────────────────────────────┘
                                │ allow-listed operations only
                      ┌─────────▼─────────┐
                      │ runner/ (Go)      │  isolated execution, size & time limits
                      └───────────────────┘
```

### Key design decisions / 关键设计

| Decision | How it works | Where |
|---|---|---|
| **The Gate, not the model, says "done"** | Verdict needs: required checks executed in the sandbox, code snapshot hash equal to the delivered artifact, tool-call ledger consistent, exit code 0. Model claims are never evidence. | `runtime/engine.py` |
| **Exactly-once paid calls** | Intent, budget reservation and the "started" event are committed in one transaction *before* the request; idempotency key is (run, role, invocation id). After a crash a paid call is not replayed; a free local call may be retried. | `runtime/roles.py` |
| **Pure-function routing** | `route(role, failure history, spend, budget, policy)` returns a model or a stop reason. Candidates are ordered by (level, priority); escalation is bounded; blocked candidates are recorded so every decision is explainable. | `application/orchestration/routing.py` |
| **Two-phase budget** | Context, call count, time, cloud tokens and cost are checked at admission; cloud calls reserve "input + max output" first and settle after. | `application/orchestration/routing.py` |
| **Tests first, then frozen** | Tests are approved before implementation and frozen; implementation workers cannot write test files. | `application/generation.py`, `application/v2/workers.py` |
| **Local repair of one package** | After a failure only the packages named by the evidence are reopened; the worker keeps the best-checked state and returns "unchanged" if it cannot improve. | `application/v2/workers.py` |
| **Roles as data** | Each role is a validated JSON (+ optional prompt file): permissions, output schema, validator. Adding a role needs no routing/UI code. | `roles/`, `src/masa/roles/specs/` |
| **Language packs** | Everything language-specific (commands, failure parsing, layout, import repair, prompts) sits behind one interface; the core only talks to the pack. | `languages/` |
| **Cache-friendly prompts** | Stable fields first, per-step fields last, to raise server-side prefix-cache hits. Only the serialization order changes; ledger identity hashes do not. | `domain/prompt_layout.py` |
| **Streaming + human in the loop** | Partial JSON is parsed incrementally to show the file being written; user input is appended to the ledger and applied at node boundaries. | `runtime/streamfiles.py`, `runtime/user_input.py` |

### Code map / 代码地图

```
src/masa/
  interfaces/     cli.py (serve · watch · ops · delivery …) · mcp_server.py · watchdog.py · http/server.py
  application/    console.py + console_*.py (orchestration entry, views, jobs) · planning.py · generation.py
                  orchestration/  coordinator.py · fixflow.py · prepare.py · routing.py · flow.py · workflows.py · attempts.py
                  v2/             workers.py (package worker loop) · workspace.py (tools) · memory.py (window-adaptive memory strategies)
                  checks/         deterministic checks: leaks, ownership, referee, case audit …
                  review/         source and test review
                  ops.py          pending queue · event feed · webhook · stall rule · stop-all
                  delivery.py     delivery report generated from the ledger
                  call_inspector.py   "what did this call see?"
  runtime/        engine.py (Gate) · roles.py (exactly-once) · tools.py · streamfiles.py · live.py · user_input.py
  roles/          registry.py · jsonschema.py · specs/*.json + *.prompt.md
  languages/      base.py · registry.py · conformance.py · go/ · python/
  agents/         protocol.py (prompts, validators) · schemas.py · handoffs.py
  infrastructure/ store.py (ledger + artifacts) · llm.py · providers.py · settings.py · windows.py (effective context window) · workspaces.py
  bench/          23-task benchmark, independent oracle, reports
  intelligence/   code index search, memory, repair context
runner/           Go program: allow-listed operations, sandboxed execution
frontend/         React + TypeScript workbench
scripts/          setup · eval/ (A/B and analysis tools) · ops/ · smoke/
tests/            backend tests
```

## Evidence and honest status / 实测结果与诚实说明

All numbers are small samples and indicate direction, not statistical significance.

- **Benchmark:** 23 tasks in 10 levels with an independent oracle (hand-written reference implementations). The first run showed the system declared "pass" while the oracle disagreed on **17%** of tasks; after fixing 5 classes of defects, the cloud configuration passed **79–92%** of tasks with no false pass.
- **Blind-derivation test audit:** asking a model "is this expected value right?" catches only **26–33%** of planted errors (it anchors on the claimed value), while deriving the answer blind and comparing catches **91%** (8% false alarms). In a small paired A/B the audit was better in 5 pairs, worse in 0, and cut cost per run by about **36%**.
- **Prompt-cache layout:** server-reported cache hit rate rose from about **39% to 68%** in a 3-pair Go comparison.
- **Experiments that did *not* pay off** (removed in the 2026-10 pruning; tag `archive/pre-prune-2026-10` keeps them): LLM "conductor" scheduling (3/7 vs 6/7 passes, 24–96% dearer), best-of-N repair sampling, an experience library, planner examples, a package contract, an offline workflow tuner.
- **Worker memory:** the latest failing check had already left the verbatim 3-step window on **21%** of late worker steps (replay over 264 ledgers); digest lines + pinned items + recall keep it at about +11% input; the strategy is chosen from the model's effective context window. A real A/B on two 10-file tasks passed with both strategies (n=1).
- **Unattended operation (minimal):** crash recovery has three outcomes - reuse a stored result, pause an uncertain paid call as *pending* (never resent), or retry only when explicitly allowed; `masa watch` restarts the service after a crash (2 hard kills, 2 restarts) and an ops API/webhook exposes status, pending items and events.
- **Planner + Tester merged into one Architect call (2026-10-23):** about 25 real Flash-only runs per arm on the nine-task VagueBench ruler plus larger tasks, judged by the independent oracle: pass rate 89% (25/28, Architect) / 91% (20/22, Architect + executable acceptance cases) vs 76% (19/25, old two-role flow); cost per run CNY 0.0485 / 0.0465 vs 0.0650. Directional only - the differences are not statistically significant, the point is *no regression* and fewer expensive tails. The first run of the ruler also exposed a real bug (a Markdown-to-HTML task was ruled infeasible by the pre-check because it mentioned HTML).
- **How weak are the model-written tests against shallow bugs?** In mutation testing (419 single-point mutants of the final code of 43 runs, 305 of them judged really wrong by the independent oracle) the model-written unit tests caught 94.8%; the generated acceptance tests alone caught 90% and added only 1 of 157 on top of the unit tests. Shallow weakness is NOT where false passes come from - they come from scenarios nobody wrote down (a leap-day search horizon, prerelease ordering across versions), which mutation cannot reveal.
- **Honest status of the last end-to-end check:** bank, grades and 3 LCS runs passed with Flash only (about 1.4 CNY in total); one inventory run was a **false pass** (Gate passed, the independent oracle failed) - model-written tests from one family can be wrong together, which is what the next phase (property/differential checks) targets.
- **Local models:** a 17.9 GB local model could not reliably act as a tool-using repair worker on a 16 GB GPU; today the local model is used for pre-checks and first drafts, and cloud Flash handles repair. The "local small model grinds, cloud model supervises" goal is the design direction; a local pass@k study is still to be done.

## Limitations / 做不到或没做的

- Languages: Go and Python only; tasks are small, well-specified modules (about 1–10 files), not large repositories.
- A Gate pass means the selected checks passed; it does not prove business correctness.
- If the tests themselves are wrong, repeated repairs are wasted; the audit, the stable-actual-value signal and the Diagnoser's per-case check reduce this but do not eliminate it. Tests, audit, referee and Diagnoser are all model-written, and errors of one model family are correlated.
- Bring-your-own-tests entry (human-written tests, frozen as-is) and requirement versioning are designed but not implemented.
- Hybrid (local-first) mode is not yet proven cheaper or better than a cloud-only run.

## License

MIT — see [LICENSE](LICENSE).
