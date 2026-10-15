import { lazy, Suspense, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useActivity } from '../../app/activity';
import { useDetail, useLive, useView } from '../../api/queries';
import type { LiveCall } from '../../api/types';
import { editingFile, fileState, writingFile } from '../../entities/live';
import { isCodePath, isEntryPath, isTestPath } from '../../entities/languages';
import { isPaused } from '../../entities/now';
import { deriveStatus } from '../../entities/status';
import { Resizer } from '../../shared/Resizer';
import { Button, Empty, Spinner } from '../../shared/ui';
import { useUi } from '../../stores/ui';
import { BottomPanel } from './BottomPanel';
import { FileTree } from './FileTree';
import { FlowStrip } from './FlowStrip';
import { InputBar } from './InputBar';
import { NeedsYou } from './NeedsYou';
import { TopBar } from './TopBar';
import { useWorkbenchFiles } from './useWorkbenchFiles';

// CodeMirror 体积较大：进入有代码的任务时才加载。 CodeMirror is large: loaded only when a task with code opens.
const EditorPane = lazy(() => import('./EditorPane').then((m) => ({ default: m.EditorPane })));

// 工作台：左边文件树，中间编辑器（代码逐字出现），上面一行“现在在做什么”和一行流程，下面折叠的面板和一个输入框。
// 默认只露出三样东西：当前动作、代码、进度；其余都收起来，点开才加载。
// The workbench: file tree on the left, the editor (code appears character by character) in the middle, one line of "what is happening now" and one line of flow above,
// a collapsed panel and an input box below. By default only three things show: the current action, the code, the progress. The rest is collapsed and loaded on demand.
export function Workbench() {
  const { runId } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const { jobs, running, bootstrap } = useActivity();
  const detail = useDetail(runId);
  const busy = Boolean(detail.data?.active || detail.data?.role_active || running);
  const view = useView(runId, busy);
  const liveCall = useLive(runId, busy).data?.live ?? null;
  const treeWidth = useUi((s) => s.treeWidth);
  const set = useUi((s) => s.set);
  const [following, setFollowing] = useState(true);
  useEffect(() => setFollowing(true), [runId]);

  // 旧入口用 ?tab=checks / history 表示“去看验证结果 / 版本”：现在翻译成打开底部面板，然后把参数去掉。
  // Old links used ?tab=checks / history for "show the results / versions": translate that into opening the bottom panel, then drop the parameter.
  useEffect(() => {
    const tab = params.get('tab');
    if (!tab) return;
    if (tab === 'checks') set({ bottomOpen: true, bottomTab: 'output' });
    else if (tab === 'history') set({ bottomOpen: true, bottomTab: 'versions' });
    setParams((q) => { const next = new URLSearchParams(q); next.delete('tab'); return next; }, { replace: true });
  }, [params, set, setParams]);

  // 手动流程里的验证没有后台任务：由“执行中 → 空闲”的转变触发报告。必须在任何提前 return 之前调用 hook。
  // Manual verification has no job: trigger on live→idle. Hooks must run before any early return.
  const wasLive = useRef(false);
  const dd = detail.data;
  const nowLive = Boolean(dd?.active || dd?.role_active);
  useEffect(() => {
    const ui = useUi.getState();
    if (wasLive.current && !nowLive && dd?.run.data.project_bundle && ['succeeded', 'failed'].includes(dd.run.status) && !ui.reportFor && !ui.reportedRuns.includes(dd.run.id))
      ui.set({ reportFor: dd.run.id, reportedRuns: [...ui.reportedRuns, dd.run.id] });
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

  return (
    <Loaded
      key={detail.data.run.id}
      detail={detail.data}
      view={view.data}
      liveCall={liveCall}
      jobs={jobs}
      running={running}
      demo={Boolean(bootstrap?.demo)}
      treeWidth={treeWidth}
      following={following}
      setFollowing={setFollowing}
      file={params.get('file')}
      pickFile={(p) => { setFollowing(false); setParams((q) => { const next = new URLSearchParams(q); next.set('file', p); next.delete('line'); return next; }, { replace: true }); }}
    />
  );
}

type LoadedProps = {
  detail: NonNullable<ReturnType<typeof useDetail>['data']>;
  view: ReturnType<typeof useView>['data'];
  liveCall: LiveCall | null;
  jobs: ReturnType<typeof useActivity>['jobs'];
  running: ReturnType<typeof useActivity>['running'];
  demo: boolean;
  treeWidth: number;
  following: boolean;
  setFollowing: (on: boolean) => void;
  file: string | null;
  pickFile: (path: string) => void;
};

function Loaded({ detail, view, liveCall, jobs, running, demo, treeWidth, following, setFollowing, file, pickFile }: LoadedProps) {
  const d = detail;
  const job = jobs.find((j) => j.run_id === d.run.id) ?? (running && (!running.run_id || running.run_id === d.run.id) ? running : undefined);
  const status = deriveStatus(d, job);
  const paused = isPaused(job, d.events);
  const { code, files, paths } = useWorkbenchFiles(d, liveCall);
  const writing = writingFile(liveCall);
  const editing = editingFile(liveCall);
  const plan = d.run.data.project_plan;

  // 显示哪个文件：跟随写入时是正在写的那个；否则是 URL 里选的；再否则是入口文件。
  // Which file to show: the one being written while following; else the one in the URL; else the entry file.
  const entry = useMemo(() => paths.find(isEntryPath) ?? paths.find((p) => isCodePath(p) && !isTestPath(p)) ?? paths[0], [paths]);
  const path = following && writing && !writing.complete ? writing.path : file && Object.hasOwn(files, file) ? file : writing?.path ?? entry;
  const state = path ? fileState(path, liveCall, code.changed) : 'sealed';

  const tags = (p: string): string[] => [
    ...(code.isDraft && code.locked(p) ? ['冻结'] : []),
    ...(code.dirty && code.original?.[p] !== files[p] ? ['已编辑'] : []),
  ];
  const treeLabel = code.hasRef ? (code.isDraft ? (code.editable ? '代码草稿 · 可编辑' : '代码草稿') : '已批准快照') : paths.length ? '生成中' : '文件';

  return (
    <div className="wb" style={{ '--tw': treeWidth + 'px' } as CSSProperties}>
      <TopBar detail={d} view={view} status={status} job={job} live={liveCall} paused={paused} />
      <FlowStrip view={view} />
      <div className="wb-main">
        <aside className="wb-tree" aria-label="文件">
          <div className="tree-head">{treeLabel}{paths.length ? ` · ${paths.length}` : ''}</div>
          <FileTree paths={paths} selected={path} state={(p) => fileState(p, liveCall, code.changed)} tags={tags} onSelect={pickFile} />
        </aside>
        <Resizer target="treeWidth" axis="x" min={160} max={420} />
        <section className="wb-editor">
          <NeedsYou detail={d} status={status} />
          {path ? (
            <Suspense fallback={<div className="center pad"><Spinner /> 加载编辑器…</div>}>
              <EditorPane detail={d} code={code} path={path} content={files[path] ?? ''} state={state} fragment={editing?.path === path ? editing.content.length : undefined} following={following} onFollow={setFollowing} />
            </Suspense>
          ) : (
            <div className="editor-empty">
              <p className="eyebrow">需求</p>
              <p className="goal">{d.run.data.goal}</p>
              <p className="muted">{status.live ? '代码会在这里逐字出现；文件树里会实时标出正在写的文件。' : '这个版本还没有代码文件。'}</p>
            </div>
          )}
        </section>
      </div>
      <BottomPanel detail={d} view={view} live={liveCall} hasPlanSpec={Boolean(plan && plan.kind !== 'code' && (plan.spec_ref || plan.approval_ref))} />
      <InputBar detail={d} live={status.live} paused={paused} disabled={demo} />
    </div>
  );
}
