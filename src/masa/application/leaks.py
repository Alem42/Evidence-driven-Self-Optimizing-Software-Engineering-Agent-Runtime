"""提示词泄漏检查：模型生成的源码里不应出现我们自己提示词里的字段名或固定句子。
Prompt-leak check: generated source must never contain our own prompt field names or fixed sentences.

起因（真实任务）：弱模型把反馈字段 `previous_attempt_error` 连同后面的说明抄进了测试里的字符串，导致冻结的测试文件语法错误，
之后每一轮“修复实现”都无法触及它。这类错误是确定性可判的，不应等到完整验证之后再发现。
Origin (a real task): a weak model copied the feedback field `previous_attempt_error` and its text into a string literal of the test file, which
froze a syntax error that no number of implementation repairs could touch. It is machine-decidable, so it should not wait for full verification.
"""

# 我们提示词/上下文里用到的字段名与固定句子；真实程序几乎不可能合理地包含它们。
# Field names and fixed sentences used in our prompts/contexts; a real program has practically no legitimate reason to contain them.
FORBIDDEN = (
    'previous_attempt_error', 'failure_evidence', 'previous_files', 'original_files', 'context_selection',
    'implementation_instructions', 'test_instructions', 'Your previous attempt was rejected',
    'Previous repair rounds in this task', 'generation_mode', 'target_path',
)


def prompt_leaks(source):
    """源码里出现的、不该出现的提示词片段列表。 Prompt fragments that appear in the source."""
    if not isinstance(source, str):
        return []
    return [token for token in FORBIDDEN if token in source]


def leak_messages(files, paths=None):
    """{路径: 说明}，供“就地重写”的反馈使用。 {path: explanation} for in-stage rewrite feedback."""
    out = {}
    for path in (paths if paths is not None else files):
        if not path.endswith('.go'):
            continue
        found = prompt_leaks(files.get(path))
        if found:
            out[path] = ('contains harness prompt text (' + ', '.join(found[:3]) + '). That text is instructions to you, not part of the program: '
                         'remove it and write only valid Go code for the task.')
    return out
