import { lazy, Suspense, useEffect, useRef, type CSSProperties } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useActivity } from '../../app/activity';
import { TABS, type TaskTab } from '../../app/nav';
import { Resizer } from '../../app/Shell';
import { useDetail, useView } from '../../api/queries';
import { deriveStatus } from '../../entities/status';
import { useUi } from '../../stores/ui';
import { Button, Empty, Spinner } from '../../shared/ui';
import { GraphPanel } from '../graph/GraphPanel';
import { HistoryView } from '../history/HistoryView';
import { Inspector } from '../inspector/Inspector';
import { Topbar } from '../topbar/Topbar';
import { VerificationView } from '../verification/VerificationView';
import { Thread } from './Thread';

// CodeMirror 体积较大，进入代码标签时才加载。 CodeMirror is loaded on demand.
const CodeView = lazy(() => import('../code/CodeView').then((m) => ({ default: m.CodeView })));

export function TaskPage() {
  const { runId } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const { jobs, running } = useActivity();
  const detail = useDetail(runId);
  const live = Boolean(detail.data?.active || detail.data?.role_active || running);
  const view = useView(runId, live);
  const inspectorOpen = useUi((s) => s.inspectorOpen);
  const inspectorWidth = useUi((s) => s.inspectorWidth);

  // 手动流程里的验证没有后台任务：由“执行中 → 空闲”的转变触发报告。必须在任何提前 return 之前调用 hook。
  // Manual verification has no job: trigger on live→idle. Hooks must run before any early return.
  const wasLive = useRef(false);
  const dd = detail.data;
  const nowLive = Boolean(dd?.active || dd?.role_active);
  useEffect(() => {
    if (wasLive.current && !nowLive && dd?.run.data.project_bundle && ['succeeded', 'failed'].includes(dd.run.status) && !useUi.getState().reportFor)
      useUi.getState().set({ reportFor: dd.run.id });
    wasLive.current = nowLive;
  }, [nowLive, dd?.run.id, dd?.run.status, dd?.run.data.project_bundle]);

  if (detail.isError && (detail.error as { status?: number }).status === 404)
    return <Empty title="找不到这个任务" action={<Button onClick={() => navigate('/')}>返回新任务</Button>}>它可能已被清理，或后端状态目录不同。</Empty>;
  if (!detail.data)
    return (
      <div className="center fill">
        {detail.isError ? <div className="bad-text">{(detail.error as Error).message}</div> : <><Spinner /> 正在读取任务…</>}
      </div>
    );

  const d = detail.data;
  // 与当前版本关联的后台任务（含已结束但尚未清理的）。 The job attached to this version, if any.
  const job = jobs.find((j) => j.run_id === d.run.id) ?? (running && (!running.run_id || running.run_id === d.run.id) ? running : undefined);
  const status = deriveStatus(d, job);
  const tab = (TABS.some(([t]) => t === params.get('tab')) ? params.get('tab') : 'thread') as TaskTab;
  const setTab = (t: TaskTab) => setParams((q) => { const n = new URLSearchParams(q); n.set('tab', t); return n; }, { replace: true });

  return (
    <div className="task" style={{ '--iw': (inspectorOpen ? inspectorWidth : 0) + 'px' } as CSSProperties}>
      <section className="task-main">
        <Topbar detail={d} view={view.data} status={status} job={job} />
        {view.data && <GraphPanel view={view.data} />}
        <nav className="tabs" aria-label="任务内容">
          {TABS.map(([id, label]) => (
            <button key={id} className={tab === id ? 'on' : ''} aria-current={tab === id ? 'page' : undefined} onClick={() => setTab(id)}>
              {label}
              {id === 'thread' && status.needsUser && <i className="attn" />}
              {id === 'checks' && d.run.status === 'failed' && !!d.run.data.project_bundle && <i className="attn bad" />}
            </button>
          ))}
        </nav>
        <div className={`tab-body ${tab === 'code' ? 'flush' : ''}`}>
          {tab === 'thread' && <Thread detail={d} status={status} />}
          {tab === 'code' && (
            <Suspense fallback={<div className="center pad"><Spinner /> 加载编辑器…</div>}>
              <CodeView detail={d} />
            </Suspense>
          )}
          {tab === 'checks' && <VerificationView detail={d} />}
          {tab === 'history' && <HistoryView detail={d} view={view.data} />}
        </div>
        <Composer status={status.headline} live={status.live} />
      </section>
      {inspectorOpen && (
        <>
          <Resizer side="inspectorWidth" min={260} max={560} />
          <Inspector detail={d} view={view.data} />
        </>
      )}
    </div>
  );
}

// 预留：追加需求/持续对话的入口。后端支持需求版本化后在此接入；目前只显示真实状态，不伪造聊天。
// Reserved slot for follow-up requirements; shows real status only until the backend supports it.
function Composer({ status, live }: { status: string; live: boolean }) {
  return (
    <footer className="composer">
      <input disabled aria-label="追加需求" placeholder="追加需求 / 与 Agent 对话（后端需求版本化后开放）" />
      <span className="muted">{live ? '● ' : '○ '}{status}</span>
    </footer>
  );
}
