// 活动上下文：统一跟踪后台任务、决定“跟随执行”的跳转，页面组件只读取结果。
// Activity context: one place tracks jobs and drives follow-mode navigation.
import { createContext, useContext, useEffect, useMemo, useRef, type ReactNode } from 'react';
import { useQueries, useQueryClient } from '@tanstack/react-query';
import { useLocation, useMatch, useNavigate } from 'react-router-dom';
import { api, ApiError } from '../api/client';
import { dropHooks, hooksFor, useJobs } from '../api/jobs';
import { qk, useBootstrap } from '../api/queries';
import type { Bootstrap, Job } from '../api/types';
import { useToasts, useUi } from '../stores/ui';
import { stageLabels } from '../entities/status';

interface Activity {
  bootstrap?: Bootstrap;
  online: boolean;
  jobs: Job[];
  running?: Job;
  /** 有任何后台工作线程在跑：只用于“开始新模型任务”类按钮，浏览与编辑永不被锁。 */
  working: boolean;
}

const Ctx = createContext<Activity>({ online: false, jobs: [], working: false });
export const useActivity = (): Activity => useContext(Ctx);

export function ActivityProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const taskMatch = useMatch('/task/:runId');
  const currentRun = taskMatch?.params.runId;
  const { ids, track, untrack } = useJobs();
  const follow = useUi((s) => s.follow);
  const push = useToasts((s) => s.push);
  const boot = useBootstrap();
  const handled = useRef(new Set<string>());

  // 发现刷新后仍在运行的后台任务。 Re-attach to a live worker after a page refresh.
  const discovered = boot.data?.active_job?.job_id;
  useEffect(() => {
    if (discovered && !ids.includes(discovered)) track(discovered, { label: '后台任务' });
  }, [discovered, ids, track]);

  const results = useQueries({
    queries: ids.map((id) => ({
      queryKey: qk.job(id),
      queryFn: () => api<Job>('/jobs/' + id),
      refetchInterval: (q: { state: { data?: Job } }) => (q.state.data?.status === 'running' ? 1000 : false),
      retry: (n: number, e: Error) => (e as ApiError).status !== 404 && n < 3,
    })),
  });

  const jobs = useMemo(() => results.flatMap((r, i) => (r.data ? [{ ...r.data, job_id: ids[i] }] : [])), [results, ids]);
  const running = jobs.find((j) => j.status === 'running');

  // 完成处理：每个任务只处理一次。 Handle each finished job exactly once.
  useEffect(() => {
    results.forEach((r, i) => {
      const id = ids[i];
      if (r.error && (r.error as ApiError).status === 404) {
        untrack(id);
        dropHooks(id);
        return;
      }
      const job = r.data;
      if (!job || job.status === 'running' || handled.current.has(id)) return;
      handled.current.add(id);
      const hooks = hooksFor(id);
      const label = hooks?.label ?? stageLabels[job.phase ?? ''] ?? '任务';
      qc.invalidateQueries({ queryKey: ['detail'] });
      qc.invalidateQueries({ queryKey: ['view'] });
      qc.invalidateQueries({ queryKey: qk.projects });
      qc.invalidateQueries({ queryKey: qk.bootstrap });
      qc.invalidateQueries({ queryKey: ['results'] });
      hooks?.onDone?.(job);
      const newRun: string | undefined = job.result?.id;
      if (job.status === 'failed') push({ tone: 'bad', text: `${label}失败：${job.error ?? '未知错误'}`, to: job.run_id ? '/task/' + job.run_id : undefined, action: '查看记录' });
      else if (job.status === 'interrupted') push({ tone: 'bad', text: `${label}被中断，可在侧栏恢复。` });
      else if (job.status === 'waiting_for_input') push({ tone: 'info', text: 'Planner 需要你补充需求。', to: newRun ? '/task/' + newRun : undefined, action: '去回答' });
      else if (newRun && newRun !== currentRun) {
        if (follow) navigate('/task/' + newRun + location.search);
        else push({ tone: 'ok', text: `${label}已完成，生成了新版本。`, to: '/task/' + newRun, action: '查看' });
      } else if (job.status === 'completed' && !hooks?.onDone) push({ tone: 'ok', text: `${label}已完成。` });
      untrack(id);
      dropHooks(id);
    });
  }, [results, ids, qc, untrack, push, navigate, follow, currentRun, location.search]);

  // 跟随：执行中的版本与当前页面不同，且用户处于任务/新建页时才跳转。 Follow only on task/new pages.
  const target = running?.run_id;
  const onFollowable = location.pathname === '/' || location.pathname.startsWith('/task/');
  useEffect(() => {
    if (follow && onFollowable && target && target !== currentRun) navigate('/task/' + target + location.search, { replace: true });
  }, [follow, onFollowable, target, currentRun, navigate, location.search]);

  const value = useMemo<Activity>(
    () => ({
      bootstrap: boot.data,
      online: !boot.isError,
      jobs,
      running,
      working: Boolean(running || boot.data?.active_job || boot.data?.active_run),
    }),
    [boot.data, boot.isError, jobs, running],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
