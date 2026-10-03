"""输入 token 估算。系数来自 57 次真实本地调用（Ollama 返回的 prompt_eval_count）对输入字节的拟合：
ASCII 约 3.6 字符/token（0.276 token/字符），非 ASCII（中文等）约 0.54 token/字符；估算/真实的比值范围 0.88–1.71，中位数 1.21。
Input token estimates. Coefficients were fitted on 57 real local calls (Ollama prompt_eval_count): ~3.6 ASCII chars per token and
~0.54 tokens per non-ASCII char; estimate/real ranged 0.88–1.71 with median 1.21.

两个估算 / Two estimates:
  estimate_tokens        保守（偏高）：用于路由的“放不放得下”准入，宁可多预留。 Pessimistic, for admission and reservations.
  estimate_tokens_lower  下界（偏低）：用于硬拦截，只有“几乎一定溢出”才拒绝，避免误杀本来能跑的请求。
                         Lower bound for the hard guard: reject only when overflow is all but certain.
这些只是估算，不是服务端的真实分词。 Estimates only, never the server tokenizer.
"""
import json

HIGH = (0.30, 0.60)  # (ASCII, 非 ASCII) token/字符 / tokens per char
LOW = (0.20, 0.40)


def _count(value, rates) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    return int(ascii_chars * rates[0] + (len(text) - ascii_chars) * rates[1]) + 1


def estimate_tokens(value) -> int:
    return _count(value, HIGH)  # HIGH 在调用时读取，configure() 可替换 / read at call time


def estimate_tokens_lower(value) -> int:
    return _count(value, LOW)


def fit(rows):
    """对 (ASCII 字符数, 非 ASCII 字符数, 真实 token 数) 做两参数最小二乘；退化时只拟合 ASCII。
    Two-parameter least squares over (ascii chars, non-ascii chars, real tokens); falls back to ASCII only when degenerate."""
    saa = sum(a * a for a, n, t in rows)
    san = sum(a * n for a, n, t in rows)
    snn = sum(n * n for a, n, t in rows)
    sat = sum(a * t for a, n, t in rows)
    snt = sum(n * t for a, n, t in rows)
    det = saa * snn - san * san
    if det > 1e-9 * max(saa * snn, 1):
        a, c = (sat * snn - snt * san) / det, (snt * saa - sat * san) / det
        if a > 0 and c > 0:
            return a, c
    return (sat / saa if saa else HIGH[0] / 1.1), 0.54


def configure(path) -> bool:
    """加载 <state>/token-calibration.json（如果存在且合法）；否则保留内置系数。 Load calibration when present and valid."""
    global HIGH, LOW
    try:
        with open(path, encoding='utf-8') as handle:
            data = json.load(handle)
        high, low = tuple(float(x) for x in data['high']), tuple(float(x) for x in data['low'])
        if len(high) == len(low) == 2 and all(0 < x < 5 for x in high + low) and all(l <= h for l, h in zip(low, high)):
            HIGH, LOW = high, low
            return True
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return False
