// 本地 UI 偏好与草稿；服务端事实全部在 TanStack Query 中，这里不放业务状态。
// UI preferences and drafts only. Server facts live in TanStack Query, never here.
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type Theme = 'system' | 'light' | 'dark';

interface UiState {
  theme: Theme;
  sidebarWidth: number;
  inspectorWidth: number;
  inspectorOpen: boolean;
  graphExpanded: boolean;
  /** 跟随执行：开启时自动切到正在执行的版本；手动选择会关闭。 Follow the executing version; manual picks turn it off. */
  follow: boolean;
  /** 正在展示任务报告的版本；不持久化。 Run whose report dialog is open (transient). */
  reportFor: string | null;
  set: (patch: Partial<Omit<UiState, 'set'>>) => void;
}

export const useUi = create<UiState>()(
  persist(
    (set) => ({
      theme: 'system',
      sidebarWidth: 272,
      inspectorWidth: 340,
      inspectorOpen: true,
      graphExpanded: false,
      follow: true,
      reportFor: null,
      set: (patch) => set(patch),
    }),
    { name: 'masa.ui.v1', partialize: ({ reportFor: _r, ...rest }) => rest },
  ),
);

/** 审核中的代码草稿（按版本 id 保存），切换标签或任务不会丢失编辑。 Edited drafts survive tab/task switches. */
interface DraftState {
  files: Record<string, Record<string, string>>;
  edit: (runId: string, path: string, value: string, original: Record<string, string>) => void;
  reset: (runId: string) => void;
}

export const useDrafts = create<DraftState>((set) => ({
  files: {},
  edit: (runId, path, value, original) =>
    set((s) => ({ files: { ...s.files, [runId]: { ...(s.files[runId] ?? original), [path]: value } } })),
  reset: (runId) =>
    set((s) => {
      const next = { ...s.files };
      delete next[runId];
      return { files: next };
    }),
}));

export interface Toast {
  id: number;
  tone: 'info' | 'ok' | 'bad';
  text: string;
  to?: string;
  action?: string;
}

interface ToastState {
  toasts: Toast[];
  push: (t: Omit<Toast, 'id'>) => void;
  dismiss: (id: number) => void;
}

let seq = 0;
export const useToasts = create<ToastState>((set) => ({
  toasts: [],
  push: (t) => {
    const id = ++seq;
    set((s) => ({ toasts: [...s.toasts.slice(-3), { ...t, id }] }));
    setTimeout(() => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })), t.tone === 'bad' ? 9000 : 5000);
  },
  dismiss: (id) => set((s) => ({ toasts: s.toasts.filter((x) => x.id !== id) })),
}));
