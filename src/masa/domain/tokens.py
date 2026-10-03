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
    return _count(value, HIGH)


def estimate_tokens_lower(value) -> int:
    return _count(value, LOW)
