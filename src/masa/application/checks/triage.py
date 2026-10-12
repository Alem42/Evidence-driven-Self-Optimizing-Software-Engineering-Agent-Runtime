"""可行性预检（triage）：在花任何模型调用之前，判断一个需求是否“确定会失败”或“风险很高”。
Feasibility triage: before spending any model call, judge whether a requirement is CERTAIN to fail or very risky.

两层 / Two layers:
  1. 确定性规则（本文件）：只依据 harness 的硬约束——标准库 Go 命令行项目、入口 cmd/app/main.go、隔离副本、无网络、
     无第三方依赖、用 `go test` 验证。命中“不可行”规则的需求无论模型多强都过不了，所以直接拦下并给出可操作的改写建议。
     Deterministic rules from the harness's HARD constraints. A requirement that hits an infeasible rule cannot pass with any
     model, so it is stopped early with an actionable rewrite hint.
  2. 本地模型复核（application/triage_model.py，可选）：只许把判断升级为“有风险”，不许拦截——弱模型不能误杀好需求，
     而且只用本地模型，不花 API 费用。
     Optional local-model review: it may only raise a warning, never block, and never uses a paid API.

规则很保守：宁可放行也不误杀（命中且未被否定词抵消才算）。Rules are conservative: when in doubt they let the task through.
"""
import re

OK, RISKY, INFEASIBLE = 'ok', 'risky', 'infeasible'

# 否定词：出现在关键词前面的短窗口内，则视为“明确不要它”。 Negators within a short window before a keyword cancel the hit.
_NEGATORS = ('无', '不', '不要', '不使用', '不需要', '没有', '禁止', '避免', '除了', 'without', 'no ', 'not ', "don't", 'never', 'avoid', 'except')

def _negated(text: str, start: int) -> bool:
    """关键词前面的短窗口里有否定词吗？ Is the keyword negated in the short window before it?"""
    window = text[max(0, start - 12):start].lower()
    return any(n in window for n in _NEGATORS)


def _hits(text: str, pattern: str) -> list[str]:
    found = []
    for m in re.finditer(pattern, text, re.I):
        if not _negated(text, m.start()):
            found.append(m.group(0).strip()[:40])
    return found


def assess(goal: str, pack=None) -> dict:
    """纯函数：返回 {verdict, findings:[{rule, level, matched, reason, suggestion}], summary}。无模型、无 I/O。
    Pure function: no model, no I/O."""
    if pack is None:
        from masa.languages.registry import DEFAULT_LANGUAGE, registry
        pack = registry.get(DEFAULT_LANGUAGE)
    RULES, RISKS = pack.triage_rules()  # 规则本身由语言包提供 / the rules come from the language pack
    text = goal or ''
    findings = []
    for rule, level, pattern, reason, suggestion in RULES:
        hit = _hits(text, pattern)
        if hit:
            findings.append({'rule': rule, 'level': level, 'matched': hit[:3], 'reason': reason, 'suggestion': suggestion})
    for rule, pattern, reason, suggestion in RISKS:
        hit = _hits(text, pattern)
        if hit:
            findings.append({'rule': rule, 'level': RISKY, 'matched': hit[:3], 'reason': reason, 'suggestion': suggestion})
    if len(text) > 4000:
        findings.append({'rule': 'long_goal', 'level': RISKY, 'matched': [], 'reason': f'需求很长（{len(text)} 字符），模型容易漏掉细节。',
                         'suggestion': '拆成更小的任务，先做核心行为。'})
    if len(text.strip()) < 6:
        findings.append({'rule': 'too_short', 'level': RISKY, 'matched': [], 'reason': '需求太短，Planner 需要猜测大量细节。',
                         'suggestion': '写清输入、输出和错误处理。'})
    verdict = INFEASIBLE if any(f['level'] == INFEASIBLE for f in findings) else RISKY if findings else OK
    names = {INFEASIBLE: '确定会失败', RISKY: '有风险', OK: '未发现问题'}
    return {'verdict': verdict, 'findings': findings, 'summary': names[verdict]}
