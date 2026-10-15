"""用官方 MCP 客户端（stdio）真的启动服务器子进程、握手、列出工具并调用：这是协议层的端到端验证（不是 Claude Desktop/Code，但走的是同一个协议）。
Start the server as a real subprocess with the official MCP client over stdio, handshake, list tools and call them: a protocol-level end-to-end check (not Claude Desktop/Code, but the same protocol).

用法 / usage:  python scripts/smoke/mcp_client_check.py
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOOD = {'go.mod': 'module demo\n\ngo 1.27.0\n', 'calc/calc.go': 'package calc\n\nfunc Add(a, b int) int { return a + b }\n',
        'calc/calc_test.go': 'package calc\n\nimport "testing"\n\nfunc TestAdd(t *testing.T) {\n\tif Add(1, 2) != 3 {\n\t\tt.Fatal("bad")\n\t}\n}\n'}
BAD = dict(GOOD, **{'calc/calc.go': 'package calc\n\nfunc Add(a, b int) int { return a - b }\n'})
CANDIDATES = [{'id': 'small', 'level': 1, 'priority': 0, 'model_type': 'local', 'model': 'qwen', 'context_limit': 16384, 'max_output_tokens': 4096},
              {'id': 'big', 'level': 2, 'priority': 0, 'model_type': 'cloud', 'model': 'pro', 'context_limit': 262144, 'max_output_tokens': 8192}]


async def main():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / 'scripts/ops/mcp_server.py')])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [t.name for t in (await session.list_tools()).tools]
            print('tools:', tools)
            good = await session.call_tool('verify_project', {'files': GOOD})
            bad = await session.call_tool('verify_project', {'files': BAD, 'checks': ['go_test']})
            refused = await session.call_tool('verify_project', {'files': {'go.mod': 'x', '../evil.go': 'x'}})
            route = await session.call_tool('route_preview', {'role': 'project_planner', 'candidates': CANDIDATES})
            paid = await session.call_tool('run_benchmark', {})
            parse = lambda r: json.loads(r.content[0].text) if not r.isError else {'error': r.content[0].text[:140]}  # noqa: E731
            result = {'good_passed': parse(good).get('passed'), 'bad_passed': parse(bad).get('passed'), 'traversal_refused': refused.isError,
                      'route_model': parse(route).get('model'), 'benchmark_refused_by_default': paid.isError}
            print(json.dumps(result, ensure_ascii=False))
            ok = result == {'good_passed': True, 'bad_passed': False, 'traversal_refused': True, 'route_model': 'pro', 'benchmark_refused_by_default': True}
            print('OK' if ok else 'UNEXPECTED')
            return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
