import { useMemo, useState } from 'react';
import { NavLink, useMatch, useNavigate } from 'react-router-dom';
import { useActivity } from '../../app/activity';
import { useOpenRun } from '../../app/nav';
import { resumeJob } from '../../api/jobs';
import { useProjects, useView } from '../../api/queries';
import type { ProjectSummary } from '../../api/types';
import { useToasts, useUi } from '../../stores/ui';
import { Button } from '../../shared/ui';

type Group = { title: string; items: ProjectSummary[] };

const toneOf = (status: string, live: boolean): string =>
  live ? 'running' : status === 'succeeded' ? 'ok' : status === 'failed' || status === 'needs_attention' ? 'bad' : status === 'paused' ? 'wait' : 'neutral';

export function Sidebar() {
  const navigate = useNavigate();
  const open = useOpenRun();
  const { bootstrap, working, online, running } = useActivity();
  const projects = useProjects(working);
  const taskMatch = useMatch('/task/:runId');
  const view = useView(taskMatch?.params.runId);
  const [search, setSearch] = useState('');
  const set = useUi((s) => s.set);
  const push = useToasts((s) => s.push);
  const currentProject = view.data?.id;
  const liveRun = running?.run_id ?? bootstrap?.active_run ?? undefined;

  const groups = useMemo<Group[]>(() => {
    const list = (projects.data ?? []).filter((p) => p.title.toLowerCase().includes(search.toLowerCase()));
    const live = list.filter((p) => p.current_run_id === liveRun);
    const waiting = list.filter((p) => !live.includes(p) && p.status === 'paused');
    const rest = list.filter((p) => !live.includes(p) && !waiting.includes(p));
    return [
      { title: '进行中', items: live },
      { title: '待我处理', items: waiting },
      { title: '最近', items: rest },
    ].filter((g) => g.items.length);
  }, [projects.data, search, liveRun]);

  return (
    <aside className="sidebar" aria-label="任务历史">
      <div className="brand">
        <span className="logo">M</span>
        <div>
          <strong>MASA</strong>
          <small>多 Agent 工程工作台</small>
        </div>
      </div>
      <Button variant="primary" className="wide" onClick={() => navigate('/')}>
        ＋ 新任务
      </Button>
      <input className="search" aria-label="搜索任务" placeholder="搜索任务…" value={search} onChange={(e) => setSearch(e.target.value)} />

      {!!bootstrap?.interrupted_jobs?.length && (
        <details className="recover">
          <summary>{bootstrap.interrupted_jobs.length} 个任务被中断，可恢复</summary>
          {bootstrap.interrupted_jobs.map((j) => (
            <div className="row between" key={j.job_id}>
              <code>{j.job_id.slice(0, 8)}</code>
              <span className="muted">{j.phase}</span>
              <span className="row">
                <Button size="sm" onClick={() => resumeJob(j.job_id).catch((e) => push({ tone: 'bad', text: e.message }))}>
                  恢复
                </Button>
                {j.run_id && (
                  <Button size="sm" variant="ghost" onClick={() => open(j.run_id!, { manual: true })}>
                    查看
                  </Button>
                )}
              </span>
            </div>
          ))}
        </details>
      )}

      <nav className="history">
        {groups.map((g) => (
          <div key={g.title} className="group">
            <h4>{g.title}</h4>
            {g.items.map((p) => {
              const live = p.current_run_id === liveRun;
              const current = p.id === currentProject;
              return (
                <button key={p.id} className={`history-item ${current ? 'current' : ''}`} onClick={() => open(p.current_run_id, { manual: true, tab: undefined })}>
                  <i className={`dot tone-${toneOf(p.status, live)} ${live ? 'pulse' : ''}`} />
                  <span className="title">{p.title}</span>
                  <small>{p.revision_count} 次验证</small>
                </button>
              );
            })}
          </div>
        ))}
        {!groups.length && <p className="muted pad">{search ? '没有匹配的任务' : projects.isLoading ? '读取中…' : '任务会保存在这里'}</p>}
      </nav>

      <div className="sidebar-foot">
        <NavLink to="/settings/models" className="nav-link">
          ⚙ 设置
        </NavLink>
        <NavLink to="/settings/local" className="nav-link">
          ◍ 本地模型
        </NavLink>
        <div className="row between">
          <small className={online ? 'ok-text' : 'bad-text'}>{online ? '● 后端已连接' : '○ 后端未连接'}</small>
          <button className="icon-btn" title="切换主题" onClick={() => set({ theme: nextTheme(useUi.getState().theme) })}>
            ◐
          </button>
        </div>
      </div>
    </aside>
  );
}

const nextTheme = (t: 'system' | 'light' | 'dark') => (t === 'system' ? 'light' : t === 'light' ? 'dark' : 'system');
