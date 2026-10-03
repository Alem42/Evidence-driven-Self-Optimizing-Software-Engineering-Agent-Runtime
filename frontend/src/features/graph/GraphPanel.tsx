import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { ProjectView } from '../../api/types';
import { useOpenRun } from '../../app/nav';
import { stageGraph, versionGraph, type GraphNode } from '../../entities/graph';
import { useUi } from '../../stores/ui';
import { GraphView } from './GraphView';

// 图条：默认折叠为一行；展开后显示当前流程与版本分支（修复分叉）。
// Graph strip: one compact row, expandable to flow + version branches.
export function GraphPanel({ view }: { view: ProjectView }) {
  const expanded = useUi((s) => s.graphExpanded);
  const set = useUi((s) => s.set);
  const [params, setParams] = useSearchParams();
  const open = useOpenRun();
  const flow = useMemo(() => stageGraph(view), [view]);
  const versions = useMemo(() => versionGraph(view), [view]);
  const node = params.get('node');

  const select = (n: GraphNode) => {
    // 点击节点：在 Inspector 查看；节点属于其他版本时同时切到那个版本（手动浏览）。
    if (n.runId && n.runId !== view.selected_run_id) open(n.runId, { manual: true, params: { node: n.id } });
    else setParams((q) => { const x = new URLSearchParams(q); x.set('node', n.id); return x; }, { replace: true });
    if (!useUi.getState().inspectorOpen) set({ inspectorOpen: true });
  };

  return (
    <div className="graph-panel">
      <div className="graph-head">
        <span className="eyebrow">执行流程</span>
        <button className="link" onClick={() => set({ graphExpanded: !expanded })}>{expanded ? '收起 ▴' : '展开图 ▾'}</button>
      </div>
      <GraphView graph={flow} compact={!expanded} selected={node} onSelect={select} />
      {expanded && versions.nodes.length > 1 && (
        <>
          <div className="graph-head sub"><span className="eyebrow">版本分支</span><span className="muted">修复与测试修订以分叉显示</span></div>
          <GraphView graph={versions} compact selected={'v:' + view.selected_run_id} onSelect={select} />
        </>
      )}
    </div>
  );
}
