// 实时文件流的纯函数：哪个文件正在写、把正在写的内容叠加到已有文件上、每个文件此刻的状态。
// Pure helpers for the live file stream: which file is being written, overlaying it on the known files, and each file's state right now.
import type { LiveCall, LiveFile } from '../api/types';

/** 正在写的文件：最后一个还没写完的整份文件；都写完了就取最后一个。 The file being written: the last unfinished whole file, else the last one. */
export function writingFile(live: LiveCall | null | undefined): LiveFile | undefined {
  const writes = (live?.files ?? []).filter((f) => f.kind === 'write');
  return [...writes].reverse().find((f) => !f.complete) ?? writes.at(-1);
}

/** 正在被“编辑”的文件（V2 工人的 edit_file：只有新片段，没有整份内容）。 Files being edited (V2 edit_file: only the new fragment). */
export function editingFile(live: LiveCall | null | undefined): LiveFile | undefined {
  return (live?.files ?? []).filter((f) => f.kind === 'edit' && !f.complete).at(-1);
}

/** 已有文件 + 正在写的文件（整份内容叠加；edit 不叠加）。 Known files plus the files being written (whole contents only). */
export function overlayFiles(base: Record<string, string> | undefined, live: LiveCall | null | undefined): Record<string, string> {
  const out = { ...(base ?? {}) };
  for (const f of live?.files ?? []) if (f.kind === 'write' && f.path) out[f.path] = f.content;
  return out;
}

export type FileState = 'writing' | 'editing' | 'written' | 'changed' | 'sealed';

/** 一个文件此刻的状态：写入中 > 编辑中 > 刚写完 > 本版改动 > 已封包。 */
export function fileState(path: string, live: LiveCall | null | undefined, changed: ReadonlySet<string>): FileState {
  const hit = (live?.files ?? []).find((f) => f.path === path);
  if (hit?.kind === 'write' && !hit.complete) return 'writing';
  if (hit?.kind === 'edit' && !hit.complete) return 'editing';
  if (hit) return 'written';
  return changed.has(path) ? 'changed' : 'sealed';
}

export const FILE_STATE_LABEL: Record<FileState, string> = {
  writing: '写入中',
  editing: '修改中',
  written: '刚写完',
  changed: '本版改动',
  sealed: '已封包',
};
