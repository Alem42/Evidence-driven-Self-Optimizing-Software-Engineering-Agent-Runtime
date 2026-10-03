// 全局后台任务跟踪：组件只“启动并登记”任务，轮询与完成处理集中在 JobTracker，
// 组件卸载（切换历史）不会丢任务，也不会各自开轮询循环。
// Global job tracking: components start & register jobs; polling lives in one JobTracker.
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { api } from './client';
import type { Job } from './types';

export interface JobHooks {
  label?: string;
  /** 任务完成（含等待回答）后调用，即使发起它的页面已卸载。 Runs on completion even if the page unmounted. */
  onDone?: (job: Job) => void;
  /** 发起时所在版本；完成后只在仍跟随时才跳转。 */
  originRunId?: string;
}

const hooks = new Map<string, JobHooks>();
export const hooksFor = (id: string): JobHooks | undefined => hooks.get(id);
export const dropHooks = (id: string): void => void hooks.delete(id);

interface JobsState {
  ids: string[];
  track: (id: string, h?: JobHooks) => void;
  untrack: (id: string) => void;
}

export const useJobs = create<JobsState>()(
  persist(
    (set) => ({
      ids: [],
      track: (id, h) => {
        if (h) hooks.set(id, h);
        set((s) => (s.ids.includes(id) ? s : { ids: [...s.ids, id] }));
      },
      untrack: (id) => set((s) => ({ ids: s.ids.filter((x) => x !== id) })),
    }),
    { name: 'masa.jobs.v1', partialize: (s) => ({ ids: s.ids }) },
  ),
);

/** 发起后台任务并登记；返回 job_id。 Start a background job and register it. */
export async function startJob(path: string, body: Record<string, unknown>, h?: JobHooks): Promise<string> {
  const { job_id } = await api<{ job_id: string }>(path, { ...body, background: true });
  useJobs.getState().track(job_id, h);
  return job_id;
}

/** 恢复已中断的任务。 Resume an interrupted job. */
export async function resumeJob(jobId: string): Promise<void> {
  const r = await api<{ job_id: string }>('/jobs/' + jobId + '/resume', {});
  useJobs.getState().track(r.job_id, { label: '恢复任务' });
}
