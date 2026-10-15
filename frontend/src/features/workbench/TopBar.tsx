import { useEffect, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useReport } from '../../api/queries';
import type { Detail, Job, LiveCall, ProjectView } from '../../api/types';
import { describeNow } from '../../entities/now';
import type { DerivedStatus } from '../../entities/status';
import { fmtSeconds } from '../../entities/text';
import { useUi } from '../../stores/ui';
import { Badge, Button } from '../../shared/ui';
import { BudgetChips, TokenChip } from './Chips';

// 顶栏（一行）：任务名 · 状态 · “现在在做什么” · 用量 · 设置。别的细节都收在下面的面板里。
// Top bar (one row): task title, status, what is happening now, usage, settings. Everything else lives in the panels below.
export function TopBar({ detail, view, status, job, live, paused }: { detail: Detail; view?: ProjectView; status: DerivedStatus; job?: Job; live: LiveCall | null; paused: boolean }) {
  const set = useUi((s) => s.set);
  const sidebarOpen = useUi((s) => s.sidebarOpen);
  const report = useReport(detail.run.id, status.live);
  const totals = report.data?.totals;
  const routing = report.data?.routing;
  const now = describeNow({ status, job, live, paused });
  const [tick, setTick] = useState(Date.now());
  useEffect(() => {
    setTick(Date.now());
    if (!status.live || !job?.started) return;
    const t = setInterval(() => setTick(Date.now()), 1000);
    return () => clearInterval(t);
  }, [status.live, job?.started]);

  return (
    <header className="wb-bar">
      <button className="icon-btn" onClick={() => set({ sidebarOpen: !sidebarOpen })} title={sidebarOpen ? '收起任务列表' : '展开任务列表'} aria-label="任务列表">☰</button>
      <h1 title={view?.title ?? detail.run.data.goal}>{view?.title ?? detail.run.data.goal}</h1>
      <Badge tone={paused ? 'wait' : status.tone} dot>{paused ? '已暂停' : status.label}</Badge>
      <div className={`wb-now tone-${now.tone}`} aria-live="polite">
        <i className={`dot ${now.busy ? 'pulse' : ''}`} />
        <strong>{now.text}</strong>
        {now.sub && <span className="muted">{now.sub}</span>}
        {status.live && job?.started && <span className="muted">· {fmtSeconds(Math.max(0, tick / 1000 - job.started))}</span>}
      </div>
      <span className="spacer" />
      {routing?.mode === 'ladder' && <BudgetChips routing={routing} />}
      {totals && totals.calls > 0 && <TokenChip totals={totals} onClick={() => set({ reportFor: detail.run.id })} />}
      <Button size="sm" variant="ghost" onClick={() => set({ reportFor: detail.run.id })}>报告</Button>
      <NavLink to="/settings/models" className="icon-btn" title="设置" aria-label="设置">⚙</NavLink>
      {status.live && <progress className="wb-progress" aria-label="任务正在运行" />}
    </header>
  );
}
