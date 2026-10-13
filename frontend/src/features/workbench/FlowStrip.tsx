import { useEffect, useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { ProjectView } from '../../api/types';
import { useOpenRun } from '../../app/nav';
import { focusNode, stageGraph, versionGraph, type GraphNode } from '../../entities/graph';
import { statusText } from '../../entities/status';
import { useUi } from '../../stores/ui';
import { GraphView } from '../graph/GraphView';

// 流程带：一行。每个阶段一枚小标签，当前阶段高亮；点“展开”才显示完整的流程图与版本分支。
// Flow strip: one row. One small chip per stage with the current one highlighted; the full graph and version branches open only on request.
export function FlowStrip({ view }: { view?: ProjectView }) {
  const expanded = useUi((s) => s.flowExpanded);
  const set = useUi((s) => s.set);
  const [params, setParams] = useSearchParams();
  const open = useOpenRun();
  const flow = useMemo(() => (view ? stageGraph(view) : undefined), [view]);
  const versions = useMemo(() => (view ? versionGraph(view) : undefined), [view]);
  const strip = useRef<HTMLDivElement>(null);
  const focus = flow ? focusNode(flow.nodes)?.id : undefined;
  const node = params.get('node');

  // 当前阶段滚到视野中央。 Keep the current stage in view.
  useEffect(() => {
    strip.current?.querySelector<HTMLElement>('[data-focus="true"]')?.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' });
  }, [focus, flow?.nodes.length]);

  if (!view || !flow?.nodes.length) return null;

  // 点一个阶段：打开底部“追踪”并高亮这个角色的调用；属于其他版本就同时切到那个版本（手动浏览）。
  // Click a stage: open the trace panel on that role's calls; if it belongs to another version, switch to it (manual browsing).
  const select = (n: GraphNode) => {
    set({ bottomOpen: true, bottomTab: 'trace' });
    if (n.runId && n.runId !== view.selected_run_id) open(n.runId, { manual: true, params: { node: n.id } });
    else setParams((q) => { const next = new URLSearchParams(q); next.set('node', n.id); return next; }, { replace: true });
  };

  return (
    <div className="flow-strip">
      <div className="flow-row">
        <span className="eyebrow">流程</span>
        <div className="flow-chips" ref={strip}>
          {flow.nodes.map((n, i) => (
            <span key={n.id} className="flow-item">
              {i > 0 && <i className="flow-sep" />}
              <button data-focus={n.id === focus} className={`flow-chip s-${n.status} ${n.id === focus ? 'focus' : ''} ${node === n.id ? 'selected' : ''}`} onClick={() => select(n)} title={`${n.label} · ${statusText[n.status] ?? n.status}`}>
                <i className="flow-dot" />
                {n.label}
              </button>
            </span>
          ))}
        </div>
        <button className="link" onClick={() => set({ flowExpanded: !expanded })}>{expanded ? '收起 ▴' : '展开 ▾'}</button>
      </div>
      {expanded && (
        <div className="flow-full">
          <GraphView graph={flow} selected={node} onSelect={select} />
          {versions && versions.nodes.length > 1 && (
            <>
              <div className="flow-sub"><span className="eyebrow">版本分支</span><span className="muted">修复与测试修订以分叉显示</span></div>
              <GraphView graph={versions} compact selected={'v:' + view.selected_run_id} onSelect={(n) => n.runId && open(n.runId, { manual: true })} />
            </>
          )}
        </div>
      )}
    </div>
  );
}
