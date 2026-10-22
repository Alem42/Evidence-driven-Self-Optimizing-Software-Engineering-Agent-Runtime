"""提示的排布：不变的内容放前面，每次都变的放后面，让服务端的前缀缓存能命中。
Prompt layout: stable content first, volatile content last, so the server's prefix cache can hit.

为什么 / Why
  DeepSeek 等服务按“请求开头的最长公共前缀”做上下文缓存（命中部分按更低的价格计费，也更快）；本地 Ollama/llama.cpp 同样复用相同前缀的 KV。
  以前上下文按键名字母序序列化——`last_results`、`step`（每一步都变）夹在 `acceptance`、`goal` 之间，前缀在第一个变化的字段处就断了。
  现在按“稳定程度”排序：整个任务不变的在最前，只在写文件/换包时变的居中，每一步都变的在最后。
  Prefix caches reuse the longest common prefix of the request. Alphabetical order put per-step fields in the middle of constant ones and cut the prefix at the first changed field.
  Now: constant for the whole task first, changing on writes in the middle, changing on every call last.

排序依据是真实账本（256 份运行、约 4 700 次角色调用）里每个字段在同一角色连续两次调用之间变化的比例，见 docs/reference/PROMPT_LAYOUT.md。
The ranking comes from how often each field changed between consecutive calls of the same role in 256 real ledgers (about 4,700 role calls).

不变量 / Invariants
  - 只改序列化顺序：内容、键、值一个不少；上下文的身份哈希（canonical，字母序）完全不变，账本里的输入因此不受影响。
    Serialization order only: same keys and values; the context identity hash (alphabetical canonical form) is untouched, so ledger inputs are unaffected.
  - 顺序是确定的：先按下面的表，表里没有的字段排在“*”处，同一档内按字母序。 Deterministic: the table first, unknown keys at "*", alphabetical within a tier.
  - 嵌套的值仍用字母序（canonical）。 Nested values keep the alphabetical canonical form.
  - 环境变量 MASA_PROMPT_LAYOUT=sorted 回到旧的字母序（评测对照用）。 MASA_PROMPT_LAYOUT=sorted restores the old alphabetical layout (for A/B measurements).
"""
import json
import os

from masa.domain.models import canonical

# 从最稳定到最易变。"*" 是表里没有的字段的位置。 Most stable to most volatile; "*" marks where unknown keys go.
ORDER = (
    # 整个任务不变：身份、语言、需求、验收、冻结的用例 / constant for the task: identity, language, requirement, acceptance, frozen cases
    'purpose', 'language', 'language_notes', 'protocol_version', 'generation_mode', 'module_path', 'import_paths', 'max_steps', 'examples_required', 'clarification_allowed',
    'graph', 'goal', 'spec', 'acceptance', 'checks', 'contract', 'cases', 'examples', 'spec_examples', 'sources',
    '*',
    # 换包/写文件/换版本时才变 / changes when a package is switched, a file is written or a version is made
    'package', 'interfaces', 'own_files', 'notes', 'memory', 'memory_notes', 'repair', 'original_files', 'files', 'all_files', 'ownership', 'previous_files', 'target_path',
    # 每次调用都可能变 / may change on every call
    'allowed_tools', 'context_selection', 'history', 'noop_streak', 'clarification', 'answers', 'user_notes', 'previous_attempt_error',
    'feedback', 'failure_evidence', 'briefing', 'pairs', 'progress_pins', 'progress_digest', 'step', 'last_results',
)
_RANK = {name: index for index, name in enumerate(ORDER)}
_UNKNOWN = _RANK['*']


def layout_name():
    """当前排布：'stable_first'（默认）或 'sorted'（旧的字母序）。 The active layout."""
    return 'sorted' if os.environ.get('MASA_PROMPT_LAYOUT', '').strip().lower() == 'sorted' else 'stable_first'


def ordered_keys(context, layout=None):
    """顶层字段在提示里出现的顺序。 The order in which the top-level fields appear in the prompt."""
    if (layout or layout_name()) == 'sorted':
        return sorted(context)
    return sorted(context, key=lambda key: (_RANK.get(key, _UNKNOWN), key))


def render(context, layout=None):
    """发给模型的用户消息正文（合法 JSON，字段顺序按排布，嵌套值仍是字母序）。 The user message body: valid JSON, top-level order by layout, nested values canonical."""
    layout = layout or layout_name()
    if layout == 'sorted':
        return canonical(context)
    return '{' + ','.join(json.dumps(key, ensure_ascii=False) + ':' + canonical(context[key]) for key in ordered_keys(context, layout)) + '}'
