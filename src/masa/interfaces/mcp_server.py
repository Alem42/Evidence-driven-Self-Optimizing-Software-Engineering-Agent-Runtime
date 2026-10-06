"""MCP 服务：把“验证”和“评测”暴露给任何 MCP 客户端（Claude Desktop / Claude Code / 其它 Agent）。这是暴露层，不是新逻辑：全部复用现有的 Runtime、Runner、Gate、路由与评测。
MCP server: expose verification and evaluation to any MCP client. It is an exposure layer, not new logic: everything reuses the existing Runtime, Runner, Gate, routing and benchmark.

工具 / tools
  verify_project(files, checks)   在隔离工作区里运行白名单检查，返回 Gate 的裁决与证据（真实的 Runtime + Runner + Gate，账本在临时目录）
  route_preview(role, history)    纯函数 route() 的只读预览：下一次尝试会用哪个模型、为什么
  get_run_report(run_id)          读取某次运行的报告（只读）
  run_benchmark(suite, ...)       会调用真实模型并花钱：默认拒绝，必须设置 MASA_MCP_ALLOW_BENCH=1，并受 token/时间上限约束

安全 / safety: 不接受命令字符串；检查只能是白名单；文件数、总大小、路径都有上限；输出截断；工作区在临时目录且用完即删。
依赖 / dependency: 官方 `mcp` SDK 是**可选依赖**（pip install "masa-runtime[mcp]"）；缺失时只有这个入口报错，核心不受影响。
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from masa.domain.models import Budget, Graph, MasaError, Node, OPERATIONS

ROOT = Path(__file__).resolve().parents[3]
MAX_FILES = 64
MAX_FILE_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 512 * 1024
MAX_OUTPUT_CHARS = 4000
MAX_TIMEOUT_SECONDS = 120
ALLOWED_NAMES = ('go.mod', 'go.sum')
RUN_ID = re.compile(r'^[0-9a-f]{32}$')


def state_dir() -> Path:
    return Path(os.environ.get('MASA_STATE') or ROOT / '.masa')


def _exe(name):
    suffix = '.exe' if os.name == 'nt' else ''
    return ROOT / '.tools' / ('bin' if name == 'masa-runner' else 'go/bin') / (name + suffix)


def runner_paths():
    return (Path(os.environ.get('MASA_RUNNER') or _exe('masa-runner')), Path(os.environ.get('MASA_GO') or _exe('go')))


def _clip(text):
    text = str(text or '')
    return text if len(text) <= MAX_OUTPUT_CHARS else text[:MAX_OUTPUT_CHARS] + f'\n… [{len(text) - MAX_OUTPUT_CHARS} more characters truncated]'


def check_files(files) -> dict:
    """校验并规范化输入文件：相对路径、只允许 .go 与 go.mod/go.sum、数量与大小有上限。不合法抛 MasaError。
    Validate input files: relative paths, only .go and go.mod/go.sum, bounded count and size."""
    if not isinstance(files, dict) or not files:
        raise MasaError('files must be a non-empty object {path: content}')
    if len(files) > MAX_FILES:
        raise MasaError(f'too many files (max {MAX_FILES})')
    total = 0
    out = {}
    for path, content in files.items():
        if not isinstance(path, str) or not isinstance(content, str):
            raise MasaError('paths and contents must be strings')
        parts = path.replace('\\', '/').split('/')
        if (path.startswith(('/', '\\')) or re.match(r'^[A-Za-z]:', path) or '..' in parts or '' in parts or '.' in parts or '\x00' in path
                or not (path.endswith('.go') or path in ALLOWED_NAMES)):
            raise MasaError(f'illegal path {path!r}: relative .go files, go.mod or go.sum only')
        size = len(content.encode('utf-8'))
        if size > MAX_FILE_BYTES or '\x00' in content:
            raise MasaError(f'file {path!r} is too large or binary (max {MAX_FILE_BYTES} bytes)')
        total += size
        out['/'.join(parts)] = content
    if total > MAX_TOTAL_BYTES:
        raise MasaError(f'input too large (max {MAX_TOTAL_BYTES} bytes in total)')
    if 'go.mod' not in out:
        raise MasaError('go.mod is required')
    return out


def check_operations(checks) -> list:
    if checks is None:
        return ['go_test', 'go_vet', 'go_fmt_check']
    if not isinstance(checks, list) or not checks or any(c not in OPERATIONS for c in checks):
        raise MasaError(f'checks must be a non-empty list drawn from {sorted(OPERATIONS)}')
    return list(dict.fromkeys(checks))


def verify_project(files: dict, checks: list | None = None, timeout_seconds: int = 60) -> dict:
    """在隔离工作区里运行白名单检查并返回 Gate 的裁决与证据。 Run whitelisted checks in an isolated workspace and return the Gate verdict with evidence."""
    return _verify(files, checks, timeout_seconds)


def _verify(files, checks, timeout_seconds, executor=None) -> dict:
    """executor 仅供测试注入，不会暴露为工具参数。 `executor` is injected by tests only and is never a tool parameter."""
    from masa.infrastructure.runner import Runner
    from masa.infrastructure.store import Store
    from masa.runtime.engine import Runtime
    files, operations = check_files(files), check_operations(checks)
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise MasaError(f'timeout_seconds must be an integer in 1..{MAX_TIMEOUT_SECONDS}')
    if executor is None:
        runner, go = runner_paths()
        if not runner.is_file() or not go.is_file():
            raise MasaError('runner or Go toolchain missing; set MASA_RUNNER / MASA_GO or run scripts/build.ps1')
        executor = Runner(runner, go)
    nodes = tuple(Node(f'check_{i}', 'tool', operation=op) for i, op in enumerate(operations))
    graph = Graph(nodes + (Node('gate', 'gate', tuple(n.id for n in nodes), 'all_terminal'),), policy_version='mcp-verify-v1')
    with tempfile.TemporaryDirectory(prefix='masa-mcp-') as temp:
        source = Path(temp) / 'source'
        for path, content in files.items():
            target = source / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content.encode('utf-8'))  # 不经过文本模式：Windows 会把 LF 换成 CRLF，gofmt 会因此判为未格式化 / no text mode: Windows would turn LF into CRLF and gofmt would then flag the file
        store = Store(Path(temp) / 'state')
        try:
            runtime = Runtime(store, executor)
            budget = Budget(model_calls=1, tool_calls=len(operations), deadline_seconds=timeout_seconds + 30, tool_timeout_ms=timeout_seconds * 1000)
            run_id = runtime.create(source, 'MCP verify_project', budget, graph=graph)
            run = runtime.execute(run_id)
            results = []
            for tool in store.tools(run_id):
                if tool['result_ref']:
                    request, result = store.read(tool['request_ref']), store.read(tool['result_ref'])
                    results.append({'operation': request['operation'], 'status': result.get('status'), 'exit_code': result.get('exit_code'),
                                    'stdout': _clip(result.get('stdout')), 'stderr': _clip(result.get('stderr')), 'duration_ms': result.get('duration_ms')})
            return {'passed': run['status'] == 'succeeded', 'verdict': run['status'], 'gate_reason': run['reason'], 'checks': results,
                    'note': 'The verdict is derived from the ledger of this isolated run (tool evidence), not from any claim.'}
        finally:
            store.close()


def route_preview(role: str, history: list | None = None, need_tokens: int = 0, candidates: list | None = None) -> dict:
    """纯函数 route() 的只读预览。candidates 缺省时取已保存的模型配置。 A read-only preview of the pure route(); candidates default to the saved model profiles."""
    from masa.application.orchestration.router import _candidate_from
    from masa.application.orchestration.routing import DEFAULT_BUDGET, route, validate_policy
    from masa.roles import registry
    if role not in registry.current().routable_ids():
        raise MasaError(f'unknown routable role {role!r}; known: {list(registry.current().routable_ids())}')
    history = history or []
    if not isinstance(history, list) or any(not isinstance(h, dict) or not {'candidate', 'level'} <= set(h) for h in history):
        raise MasaError("history must be a list of {'candidate': id, 'level': int}")
    if candidates is None:
        from masa.infrastructure.settings import Settings
        settings = Settings(state_dir())
        saved = settings.routing()
        entries = [{'id': i, 'level': p['level'], 'priority': p['priority'], 'model_type': p['model_type'], 'model': p['model'], 'roles': list(p.get('roles') or []),
                    'context_limit': p['context_limit'], 'max_output_tokens': p['max_output_tokens'], 'price_in': p.get('input_price_per_million'),
                    'price_out': p.get('output_price_per_million')} for i, p in settings.ready_profiles()]
        policy, budget = saved['policy'], saved['budget']
    else:
        entries, policy, budget = list(candidates), validate_policy(None), dict(DEFAULT_BUDGET)
    if not entries:
        raise MasaError('no usable model candidates')
    spend = {'calls': 0, 'cloud_tokens': 0, 'active_seconds': 0, 'cost': 0.0}
    decision = route(role, 'fix', [_candidate_from(e) for e in entries], history, spend, budget, policy, int(need_tokens))
    by_id = {e['id']: e for e in entries}
    chosen = by_id.get(decision.candidate)
    return {'action': decision.action, 'reason': decision.reason, 'detail': decision.detail, 'escalated': decision.escalated, 'level': decision.level,
            'model': chosen['model'] if chosen else None, 'model_type': chosen['model_type'] if chosen else None}


def get_run_report(run_id: str) -> str:
    """某次运行的报告（只读）。 The report of one run (read-only)."""
    from masa.application.reports import render
    from masa.infrastructure.store import Store
    if not isinstance(run_id, str) or not RUN_ID.match(run_id):
        raise MasaError('run_id must be 32 hex characters')
    root = state_dir()
    if not root.exists():
        raise MasaError('state directory not found')
    store = Store(root)
    try:
        return _clip(render(store, run_id))
    finally:
        store.close()


def run_benchmark(suite: str = 'canary', max_cloud_tokens: int = 100_000, max_minutes: int = 10) -> dict:
    """真实评测：调用模型、花钱。默认拒绝；MASA_MCP_ALLOW_BENCH=1 才允许，并且 token 与分钟都有硬上限。
    A real benchmark: calls models and spends money. Refused unless MASA_MCP_ALLOW_BENCH=1, and capped in tokens and minutes."""
    from masa.bench.report import format_table
    from masa.bench.runner import BenchRunner, resolve_config
    if os.environ.get('MASA_MCP_ALLOW_BENCH') != '1':
        raise MasaError('run_benchmark spends real money and is disabled: set MASA_MCP_ALLOW_BENCH=1 in the server environment to allow it')
    if type(max_cloud_tokens) is not int or not 1_000 <= max_cloud_tokens <= 500_000 or type(max_minutes) is not int or not 1 <= max_minutes <= 30:
        raise MasaError('max_cloud_tokens must be 1000..500000 and max_minutes 1..30')
    config = resolve_config({'suite': suite, 'total_cloud_tokens': max_cloud_tokens, 'total_minutes': max_minutes})
    runner, go = runner_paths()
    bench = BenchRunner(state_dir(), runner, go, ROOT, config)
    bench.run()
    snapshot = bench.snapshot()
    return {'id': bench.id, 'state': snapshot['state'], 'overall': snapshot['aggregate']['overall'], 'table': _clip(format_table({**snapshot, 'id': bench.id}))}


TOOLS = {
    'verify_project': verify_project,
    'route_preview': route_preview,
    'get_run_report': get_run_report,
    'run_benchmark': run_benchmark,
}


def build_server():
    """用官方 SDK 注册工具。缺 SDK 时给出清晰的报错。 Register the tools with the official SDK; a clear error when it is missing."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise SystemExit('The MCP server needs the optional dependency: pip install "mcp>=1.2"  (' + str(exc) + ')') from None
    server = FastMCP('masa')

    import functools

    def wrap(fn):
        @functools.wraps(fn)  # 保留签名，SDK 据此生成参数 schema / keep the signature: the SDK builds the parameter schema from it
        def tool(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except MasaError as exc:
                raise ValueError(str(exc)) from None  # 给客户端一个干净的错误 / a clean error for the client
        return tool

    for name, fn in TOOLS.items():
        server.tool(name=name, description=(fn.__doc__ or name).strip().split('\n')[0])(wrap(fn))
    return server


def main():
    build_server().run()  # stdio


if __name__ == '__main__':
    main()
