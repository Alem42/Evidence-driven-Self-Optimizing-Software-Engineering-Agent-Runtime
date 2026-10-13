"""提示前缀回放：在真实账本上量化“前缀缓存理论上能命中多少”，比较旧的字母序与新的稳定优先排布。只读数据，零 token。
Prompt-prefix replay: over real ledgers, estimate how much of each prompt a prefix cache could serve, comparing the old alphabetical layout with the stable-first one. Read-only, zero tokens.

模型 / model: 服务端缓存“请求开头的最长公共前缀”（DeepSeek 以 64 token 为块）。对每次调用，取它与同一份账本里更早的调用（同角色，最近 40 次）的最长公共前缀，向下取整到 64 token 的整数倍，当作可命中部分。
The server caches the longest common prefix (DeepSeek in blocks of 64 tokens). For each call take its longest common prefix with an earlier call of the same role in the same ledger (latest 40), rounded down to 64-token blocks, as the cacheable part.
这是理论上界的估计，真实命中率以服务端报告的 prompt_cache_hit_tokens 为准（见 docs/reference/PROMPT_LAYOUT.md）。
An estimate, not a measurement: the real hit rate is what the server reports as prompt_cache_hit_tokens.

用法 / usage:  python scripts/eval/prefix_replay.py [--glob '.masa/**/runtime.sqlite3'] [--json out.json]
"""
import argparse
import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from masa.agents.protocol import instruction_for  # noqa: E402
from masa.domain.prompt_layout import render  # noqa: E402
from masa.domain.tokens import estimate_tokens  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402

BLOCK = 64
WINDOW = 40
SKIP = ('old', 'legacy-backup')


def common_prefix(a, b):
    """最长公共前缀的长度：二分 + 切片比较（C 速度）。 Binary search over slice comparisons."""
    low, high = 0, min(len(a), len(b))
    while low < high:
        mid = (low + high + 1) // 2
        if a[:mid] == b[:mid]:
            low = mid
        else:
            high = mid - 1
    return low


def cacheable_chars(prompt, best_prefix_chars):
    """公共前缀按 token 估算后向下取整到整块，再折回字符数。 Round the shared prefix down to whole 64-token blocks."""
    if best_prefix_chars <= 0:
        return 0
    tokens = estimate_tokens(prompt[:best_prefix_chars])
    kept = (tokens // BLOCK) * BLOCK
    return int(best_prefix_chars * kept / tokens) if tokens else 0


def replay(paths, layouts=('sorted', 'stable_first')):
    totals = {name: collections.defaultdict(lambda: [0, 0]) for name in layouts}  # layout -> purpose -> [cacheable chars, chars]
    ledgers = calls = 0
    for path in paths:
        try:
            store = Store(path)
        except Exception:
            continue
        try:
            rows = store.db.execute('select purpose,input_ref from role_invocations order by started').fetchall()
        except Exception:
            store.close()
            continue
        ledgers += 1
        history = {name: collections.defaultdict(list) for name in layouts}
        for row in rows:
            try:
                context = store.read(row['input_ref'])
                head = instruction_for(context)
            except Exception:
                continue
            calls += 1
            for name in layouts:
                prompt = head + '\u0000' + render(context, name)
                earlier = history[name][row['purpose']]
                best = max((common_prefix(prompt, other) for other in earlier[-WINDOW:]), default=0)
                cell = totals[name][row['purpose']]
                cell[0] += cacheable_chars(prompt, best)
                cell[1] += len(prompt)
                earlier.append(prompt)
        store.close()
    return ledgers, calls, totals


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--glob', default='.masa/**/runtime.sqlite3')
    parser.add_argument('--json')
    args = parser.parse_args()
    paths = [p.parent for p in sorted(ROOT.glob(args.glob)) if not any(part in SKIP for part in p.parts)]
    ledgers, calls, totals = replay(paths)
    print(f'{ledgers} 份账本，{calls} 次角色调用')
    roles = sorted({p for t in totals.values() for p in t}, key=lambda p: -totals['sorted'][p][1])
    print(f"{'角色':<24}{'调用字符':>12}{'字母序可命中':>14}{'稳定优先可命中':>16}")
    summary = {'ledgers': ledgers, 'calls': calls, 'roles': {}}
    for role in roles:
        old, new = totals['sorted'][role], totals['stable_first'][role]
        print(f'{role:<24}{old[1]:>12}{old[0] / old[1]:>14.1%}{new[0] / new[1]:>16.1%}')
        summary['roles'][role] = {'chars': old[1], 'sorted': round(old[0] / old[1], 4), 'stable_first': round(new[0] / new[1], 4)}
    for name in ('sorted', 'stable_first'):
        hit = sum(c[0] for c in totals[name].values())
        size = sum(c[1] for c in totals[name].values())
        summary[name] = round(hit / size, 4) if size else None
    print(f"{'合计':<24}{'':>12}{summary['sorted']:>14.1%}{summary['stable_first']:>16.1%}")
    if args.json:
        Path(args.json).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
