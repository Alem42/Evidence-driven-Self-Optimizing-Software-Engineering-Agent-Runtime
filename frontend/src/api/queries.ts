// 服务端状态：缓存、去重、结构共享。轮询频率随真实活动自适应，切换任务先显示缓存，不再白屏。
// Server state with adaptive polling; revisiting a task shows cached data instantly.
import { useQuery, type Query } from '@tanstack/react-query';
import { api } from './client';
import type { BenchCatalog } from './bench-types';
import type { Bootstrap, CheckResult, TaskReport, Detail, Job, ProjectSummary, ProjectView, Profiles, RepairAdvice } from './types';

export const qk = {
  bootstrap: ['bootstrap'] as const,
  projects: ['projects'] as const,
  profiles: ['profiles'] as const,
  detail: (id: string) => ['detail', id] as const,
  view: (id: string) => ['view', id] as const,
  job: (id: string) => ['job', id] as const,
  artifact: (run: string, ref: string) => ['artifact', run, ref] as const,
  results: (id: string, rev: string) => ['results', id, rev] as const,
  logs: (id: string) => ['logs', id] as const,
  ollama: ['ollama'] as const,
  report: (id: string) => ['report', id] as const,
};

const isLive = (d?: Detail): boolean => Boolean(d && (d.active || d.role_active));

export const useBenchTasks = () =>
  useQuery({ queryKey: ['bench', 'tasks'], queryFn: () => api<BenchCatalog>('/bench/tasks'), staleTime: Infinity });

export const useBootstrap = () =>
  useQuery({ queryKey: qk.bootstrap, queryFn: () => api<Bootstrap>('/bootstrap'), refetchInterval: 2500 });

export const useProjects = (hot: boolean) =>
  useQuery({ queryKey: qk.projects, queryFn: async () => (await api<{ projects: ProjectSummary[] }>('/projects')).projects, refetchInterval: hot ? 2000 : 6000 });

import { applyRoles, type RoleInfo } from '../entities/roles';

/** 角色清单（RoleSpec 注册表）；在 select 里把名字写进标签表，所以消费者渲染时标签已经就绪。 Role list; labels are applied in select so they are ready when consumers render. */
export const useRoles = () =>
  useQuery({ queryKey: ['roles'] as const, queryFn: () => api<{ roles: RoleInfo[] }>('/roles'), staleTime: Infinity, select: (d) => applyRoles(d.roles) });

export const useProfiles = () => useQuery({ queryKey: qk.profiles, queryFn: () => api<Profiles>('/settings'), staleTime: 30_000 });

export const useDetail = (runId?: string) =>
  useQuery({
    queryKey: qk.detail(runId ?? ''),
    queryFn: ({ signal }) => api<Detail>('/runs/' + runId, undefined, signal),
    enabled: Boolean(runId),
    refetchInterval: (q: Query<Detail>) => (isLive(q.state.data) ? 1000 : 5000),
    retry: (n, e) => (e as { status?: number }).status !== 404 && n < 2,
  });

export const useView = (runId?: string, live = false) =>
  useQuery({
    queryKey: qk.view(runId ?? ''),
    queryFn: ({ signal }) => api<ProjectView>('/projects/' + runId, undefined, signal),
    enabled: Boolean(runId),
    refetchInterval: live ? 1500 : 6000,
  });

export const useJob = (id?: string) =>
  useQuery({
    queryKey: qk.job(id ?? ''),
    queryFn: () => api<Job>('/jobs/' + id),
    enabled: Boolean(id),
    refetchInterval: (q: Query<Job>) => (q.state.data?.status === 'running' ? 1000 : false),
    retry: false,
  });

/** 产物是不可变内容，永不过期。 Artifacts are immutable. */
export const useArtifact = <T = any>(runId?: string, ref?: string | null) =>
  useQuery({
    queryKey: qk.artifact(runId ?? '', ref ?? ''),
    queryFn: async () => (await api<{ artifact: T }>('/runs/' + runId + '/artifacts/' + ref)).artifact,
    enabled: Boolean(runId && ref),
    staleTime: Infinity,
  });

/** 仅在工具结果引用变化时重新取（revision 作为 key），避免每秒加载完整输出。 */
export const useResults = (detail?: Detail) => {
  const revision = detail?.tools.map((t) => t.id + ':' + t.result_ref).join('|') ?? '';
  return useQuery({
    queryKey: qk.results(detail?.run.id ?? '', revision),
    queryFn: () => api<{ checks: CheckResult[]; repair_advice?: RepairAdvice; ownership?: { primary: string; lines: string[] } }>('/runs/' + detail!.run.id + '/results'),
    enabled: Boolean(detail),
  });
};

/** 任务报告：token、耗时、工具调用。执行中 2 秒刷新，空闲时 10 秒。 Task report, polled faster while live. */
export const useReport = (runId?: string, live = false) =>
  useQuery({
    queryKey: qk.report(runId ?? ''),
    queryFn: ({ signal }) => api<TaskReport>('/projects/' + runId + '/report', undefined, signal),
    enabled: Boolean(runId),
    refetchInterval: live ? 2000 : 10_000,
  });
