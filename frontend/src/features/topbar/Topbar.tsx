import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { Detail, Job, ProjectView } from '../../api/types';
import type { DerivedStatus } from '../../entities/status';
import { fmtSeconds } from '../../entities/text';
import { fmtTokens } from '../../entities/report';
import { useReport } from '../../api/queries';
import { useAction } from '../thread/useAction';
import { useUi } from '../../stores/ui';
import { Badge, Button } from '../../shared/ui';

// 顶栏：任务名、真实状态、模型、计时、文件进度、最近一次已完成推理速度；跟随开关与停止。
// Top bar: real status, model, elapsed time, file progress and last measured speed.
export function Topbar({ detail, view, status, job }: { detail: Detail; view?: ProjectView; status: DerivedStatus; job?: Job }) {
  const follow = useUi((s) => s.follow);
  const inspectorOpen = useUi((s) => s.inspectorOpen);
  const set = useUi((s) => s.set);
  const [now, setNow] = useState(Date.now());
  const report = useReport(detail.run.id, status.live);
  const totals = report.data?.totals;
  const cancel = useAction(() => api('/runs/' + detail.run.id + '/cancel', {}));

  useEffect(() => {
    setNow(Date.now());
    if (!status.live) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [status.live]);

  const gen = job?.generation_progress ?? detail.run.data.project_plan?.gen_progress;
  const rate = job?.last_model_metrics?.generation_tokens_per_second;
  const started = job?.started;

  return (
    <header className="topbar">
      <div className="topbar-main">
        <h1 title={view?.title ?? detail.run.data.goal}>{view?.title ?? detail.run.data.goal}</h1>
        <div className="topbar-meta">
          <Badge tone={status.tone} dot>{status.label}</Badge>
          <span className="meta">{status.headline}</span>
          {job?.model && <span className="chip-static">{job.model}</span>}
          {status.live && started && <span className="meta">{fmtSeconds(Math.max(0, now / 1000 - started))}</span>}
          {gen && status.live && <span className="meta" title={gen.current}>文件 {gen.completed}/{gen.total}</span>}
          {rate != null && <span className="meta">最近调用 {rate} tok/s</span>}
          {totals && totals.calls > 0 && (
            <button className="token-chip" onClick={() => set({ reportFor: detail.run.id })} title="点击查看任务报告：按模型、按步骤、逐次调用">
              Σ {fmtTokens(totals.total_tokens)} tok
              {totals.local_tokens > 0 && <span className="tc-local">本地 {fmtTokens(totals.local_tokens)}</span>}
              {totals.cloud_tokens > 0 && <span className="tc-cloud">API {fmtTokens(totals.cloud_tokens)}</span>}
            </button>
          )}
        </div>
      </div>
      <div className="row">
        <label className="switch" title="开启后自动跟随正在执行的版本；手动选择会关闭">
          <input type="checkbox" checked={follow} onChange={(e) => set({ follow: e.target.checked })} />
          <span>跟随执行</span>
        </label>
        <Button size="sm" variant="ghost" onClick={() => set({ reportFor: detail.run.id })}>任务报告</Button>
        {status.live && <Button size="sm" variant="danger" disabled={cancel.busy} onClick={cancel.run}>停止</Button>}
        <Button size="sm" variant="ghost" onClick={() => set({ inspectorOpen: !inspectorOpen })} title="节点详情">{inspectorOpen ? '隐藏详情 ▸' : '◂ 详情'}</Button>
      </div>
      {status.live && <progress className="topbar-progress" aria-label="任务正在运行" />}
    </header>
  );
}
