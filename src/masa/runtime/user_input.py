"""任务运行中，用户追加的提示（人在回路的第 1 级）。
User notes added while a task runs (level 1 of human-in-the-loop).

设计要点 / Design:
- **先写账本**：提示是 `user_input` 事件，崩溃重启后仍在，回放时看得见“用户在什么时候说了什么”。
  Ledger first: a note is a `user_input` event, so it survives a crash and a replay shows what the user said and when.
- **只在节点边界生效**：提示不会打断正在进行的模型调用（那会浪费已付的费用）；它出现在**之后新开始**的调用的上下文里（`user_notes`）。
  Applied at node boundaries only: a note never interrupts a call in flight (that would waste what was already paid for); it appears in the context of calls that START later.
- **沿版本链收集**：修复/修订会产生新的 run（parent_run_id 链），提示要跟着任务走，而不是只属于某一个版本。
  Collected along the version chain: repairs create new runs, and a note belongs to the task, not to one version.
- **同一次调用的上下文不能变**：已经存在的调用（恢复、重放）要沿用它当时的 `user_notes`，否则输入身份对不上，会被当成“输入变了”拒绝（见 RoleRuntime）。
  A call's context must not change: an existing invocation (resume, replay) keeps the notes it was created with, otherwise its input identity no longer matches.
"""
MAX_NOTE_CHARS = 600
MAX_NOTES = 10


def clean_note(text):
    """校验并整理一条提示：去首尾空白，1–600 字符，不含空字符。 Validate a note: trimmed, 1-600 characters, no NUL."""
    from masa.domain.models import MasaError
    if not isinstance(text, str):
        raise MasaError('a note must be text')
    text = text.strip()
    if not text or len(text) > MAX_NOTE_CHARS or chr(0) in text:
        raise MasaError(f'a note must be 1..{MAX_NOTE_CHARS} characters')
    return text


def record(store, rid, kind, **fields):
    """把一次用户输入写进账本（事件 user_input）。 Write one user input into the ledger as a user_input event."""
    store.event(rid, 'user_input', {'kind': kind, **fields})


def notes_for(store, rid, limit=MAX_NOTES):
    """从 rid 沿 parent_run_id 一直向上，收集所有提示（旧的在前），最多保留最近 limit 条。
    Collect every note from rid up its parent chain, oldest first, keeping the latest `limit`."""
    chain, seen, current = [], set(), rid
    while current and current not in seen:
        seen.add(current)
        chain.append(current)
        try:
            current = store.run(current)['data'].get('parent_run_id')
        except Exception:
            break
    found = []
    for run_id in reversed(chain):  # 祖先在前 / ancestors first
        for event in store.events(run_id):
            if event['type'] == 'user_input' and event['payload'].get('kind') == 'note' and event['payload'].get('text'):
                found.append((event['created'], event['seq'], event['payload']['text']))
    found.sort(key=lambda item: (item[0], item[1]))
    texts = []
    for _, _, text in found:
        if text not in texts:
            texts.append(text)
    return texts[-limit:]


NOTES_INSTRUCTION = (' The user added notes while the task runs (user_notes, oldest first). They are binding requirements from the user: follow them unless they contradict '
                     'the approved acceptance criteria. Never copy the notes into source code, comments or string literals.')
