// 本地 UI 偏好与草稿；服务端事实全部在 TanStack Query 中，这里不放业务状态。
// UI preferences and drafts only. Server facts live in TanStack Query, never here.
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type Theme = 'system' | 'light' | 'dark';

interface UiState {
  theme: Theme;
  sidebarWidth: number;
  /** 任务列表（左侧栏）是否展开；任务页里默认收起，让代码占满。 Whether the task list is open; closed by default on a task so the code gets the room. */
  sidebarOpen: boolean;
  /** 文件树宽度。 File tree width. */
  treeWidth: number;
  /** 底部面板（输出 / 追踪 / 版本 / 报告）：默认折叠，点开才加载。 Bottom panel, collapsed by default and loaded only when opened. */
  bottomOpen: boolean;
  bottomTab: 'output' | 'plan' | 'trace' | 'versions' | 'report' | 'delivery';
  bottomHeight: number;
  /** 流程带展开成完整图。 The flow strip expanded into the full graph. */
  flowExpanded: boolean;
  /** 跟随执行：开启时自动切到正在执行的版本；手动选择会关闭。 Follow the executing version; manual picks turn it off. */
  follow: boolean;
  /** 正在展示任务报告的版本；不持久化。 Run whose report dialog is open (transient). */
  reportFor: string | null;
  /** 这个页面会话里已经自动弹过报告的任务：同一个任务不会因为再次“执行中→空闲”（例如运行程序）而重复弹出。 Tasks whose report already popped up in this page session: the same task never pops it again (e.g. after running the program). */
  reportedRuns: string[];
  /** 本任务参与路由的模型；null = 全部可用。 Models taking part in a ladder task; null = every ready model. */
  selectedModels: string[] | null;
  set: (patch: Partial<Omit<UiState, 'set'>>) => void;
}

export const useUi = create<UiState>()(
  persist(
    (set) => ({
      theme: 'system',
      sidebarWidth: 252,
      sidebarOpen: false,
      treeWidth: 232,
      bottomOpen: false,
      bottomTab: 'output',
      bottomHeight: 300,
      flowExpanded: false,
      follow: true,
      reportFor: null,
      reportedRuns: [],
      selectedModels: null,
      set: (patch) => set(patch),
    }),
    { name: 'masa.ui.v2', partialize: ({ reportFor: _r, reportedRuns: _d, ...rest }) => rest },
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
