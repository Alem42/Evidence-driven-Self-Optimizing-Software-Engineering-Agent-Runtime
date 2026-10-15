"""用真实调用校准 token 估算器：拟合 (ASCII, 非 ASCII) 每字符 token 数，写入 <state>/token-calibration.json，Console 启动时自动加载。
Calibrate the token estimators from real calls: fit tokens-per-char for ASCII and non-ASCII text, write
<state>/token-calibration.json, which the Console loads at startup.

只用本地模型（Ollama 返回的 prompt_eval_count 是服务端真实分词）的调用。云端不用：它们不被硬拦截。
Uses local-model calls only: Ollama's prompt_eval_count is the server's real tokenizer count.
用法 / usage: python scripts/eval/calibrate_tokens.py [--state .masa] [--write]
"""
import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))

from masa.agents.protocol import instruction_for  # noqa: E402
from masa.domain.models import canonical  # noqa: E402
from masa.domain.tokens import fit  # noqa: E402
from masa.infrastructure.store import Store  # noqa: E402

MIN_SAMPLES = 20


def collect(store):
    requested, rows = {}, []
    for e in store.db.execute("SELECT run_id,type,payload FROM events WHERE type IN ('model_requested','model_completed') ORDER BY seq"):
        p = json.loads(e['payload'])
        key = (e['run_id'], p.get('step_id'), p.get('invocation_id'), p.get('attempt_no'))
        if e['type'] == 'model_requested':
            requested[key] = p
            continue
        r, usage = requested.get(key), p.get('usage') or {}
        if not r or not usage.get('prompt_tokens') or r.get('route', {}).get('provider') != 'ollama-native':
            continue
        try:
            context = store.read(r['context_ref'])
        except Exception:
            continue
        if isinstance(context, dict) and 'purpose' in context:
            text = instruction_for(context) + canonical(context)
            ascii_chars = sum(1 for ch in text if ord(ch) < 128)
            rows.append((ascii_chars, len(text) - ascii_chars, usage['prompt_tokens']))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--state', type=Path, default=ROOT / '.masa')
    ap.add_argument('--write', action='store_true', help='write token-calibration.json (otherwise just print)')
    args = ap.parse_args()
    store = Store(args.state)
    try:
        rows = collect(store)
    finally:
        store.close()
    print(f'{len(rows)} real local calls found')
    if len(rows) < MIN_SAMPLES:
        print(f'need at least {MIN_SAMPLES} samples; keeping the built-in coefficients')
        return 1
    ascii_rate, other_rate = fit(rows)
    ratios = sorted((ascii_rate * a + other_rate * n) / t for a, n, t in rows)
    print(f'ASCII {ascii_rate:.3f} token/char (~{1 / ascii_rate:.2f} chars/token), non-ASCII {other_rate:.3f} token/char')
    print('estimate/real: min %.2f median %.2f max %.2f' % (ratios[0], statistics.median(ratios), ratios[-1]))
    # 偏高 = 拟合值 ×1.1（准入/预留宁可多留）；下界 = 拟合值 ×0.72（硬拦截只在几乎一定溢出时拒绝）。
    # HIGH = fit x1.1 (admission errs high); LOW = fit x0.72 (the hard guard rejects only near-certain overflow).
    payload = {'high': [round(ascii_rate * 1.1, 4), round(other_rate * 1.1, 4)], 'low': [round(ascii_rate * 0.72, 4), round(other_rate * 0.72, 4)],
               'samples': len(rows)}
    print('proposed', payload)
    if args.write:
        (args.state / 'token-calibration.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
        print('written')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
