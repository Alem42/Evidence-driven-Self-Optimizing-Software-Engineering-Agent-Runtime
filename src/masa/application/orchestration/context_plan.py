"""上下文预算与分层（影子模式优先）：简单任务原样全给，复杂任务才按优先级裁剪，并把裁剪记录下来。
Context budget and tiers (shadow mode first): simple tasks get everything as before, only complex ones are trimmed by priority, and every drop is recorded.

三层 / three tiers
  must       必需：去掉就无法完成这次调用（规格/验收项、正在失败的文件、失败证据）。永不丢；放不下就如实报告 over_budget。
  important  重要：能显著帮助但可压缩（前几轮摘要、测试清单、其它失败文件的片段）。需要时按比例截断。
  droppable  可丢：没有也能做（与失败无关的文件、历史事件）。最先丢，大的先丢。

规模判定 / scale rule (deterministic)
  需要的全部内容 ≤ 窗口 × small_share（默认 35%）→ "small"：**原样全给，什么都不改**（与现在的行为逐字节相同）；
  否则 → "large"：按预算裁剪。预算 = 窗口 × large_share（默认 50%，给输出和提示留余量）。
  Everything needed fits in window x small_share -> "small": send it all, change nothing (identical to today). Otherwise "large": trim to the budget.

这是纯函数：不读账本、不调用模型，所以可以在影子模式里对真实账本重放，量化“如果开启会丢多少、省多少”，而不影响任何现有行为。
Pure functions: they read no ledger and call no model, so they can be replayed in shadow mode over real ledgers to quantify what would be dropped and saved, without touching any behaviour.
"""
from masa.domain.tokens import estimate_tokens

TIER_ORDER = ('droppable', 'important', 'must')  # 丢弃的顺序 / the order in which things are given up


def item(name, tier, text):
    return {'name': name, 'tier': tier, 'text': text if isinstance(text, str) else str(text)}


def scale_of(total_tokens, window, small_share=0.35):
    return 'small' if total_tokens <= window * small_share else 'large'


def plan(items, window, small_share=0.35, large_share=0.5, estimate=estimate_tokens):
    """返回 {'scale', 'budget', 'total', 'kept_tokens', 'keep': {name: 保留的字符数或 None=全部}, 'dropped': [...], 'trimmed': {...}, 'over_budget': bool}。
    keep[name] 为 None 表示整项保留；为整数表示只保留前 N 个字符（important 的截断）；被丢的项不在 keep 里。
    Returns the decision; keep[name] is None for a whole item, an integer for a truncated one; dropped items are absent from keep."""
    sizes = {i['name']: estimate(i['text']) for i in items}
    total = sum(sizes.values())
    scale = scale_of(total, window, small_share)
    budget = int(window * large_share)
    keep = {i['name']: None for i in items}
    dropped, trimmed = [], {}
    if scale == 'small' or total <= budget:
        return {'scale': scale, 'budget': budget, 'total': total, 'kept_tokens': total, 'keep': keep, 'dropped': [], 'trimmed': {}, 'over_budget': False}
    current = total
    for tier in TIER_ORDER[:2]:
        group = sorted((i for i in items if i['tier'] == tier and i['name'] in keep), key=lambda i: -sizes[i['name']])
        for entry in group:
            if current <= budget:
                break
            name = entry['name']
            if tier == 'important':
                over = current - budget
                target = max(0, sizes[name] - over)
                if target <= sizes[name] * 0.25:  # 压得太狠就不如丢掉 / trimmed too hard: drop it instead
                    current -= sizes[name]
                    dropped.append(name)
                    del keep[name]
                else:
                    keep[name] = int(len(entry['text']) * target / max(1, sizes[name]))
                    trimmed[name] = target
                    current -= sizes[name] - target
            else:
                current -= sizes[name]
                dropped.append(name)
                del keep[name]
    return {'scale': scale, 'budget': budget, 'total': total, 'kept_tokens': current, 'keep': keep, 'dropped': dropped, 'trimmed': trimmed, 'over_budget': current > budget}
