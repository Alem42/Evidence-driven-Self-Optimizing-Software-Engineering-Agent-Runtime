import React from 'react';
import {layoutGraph} from './graphLayout';

// 图只展示服务端状态，不推断成功或触发执行。 Render server state without inferring success or starting execution.
export function WorkflowGraph({detail, selected, onSelect}) {
  const nodes = detail.run.data.graph.nodes;
  const {positions, width, height} = layoutGraph(nodes);
  const steps = new Map(detail.steps.map(step => [step.id, step]));
  return <div className="graph-scroll"><div className="graph" style={{width, height}}>
    <svg width={width} height={height} aria-hidden="true">
      <defs><marker id="workflow-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#8ba59f"/></marker></defs>
      {nodes.flatMap(node => node.dependencies.map(dep => {
        const from = positions[dep], to = positions[node.id];
        const x = from.x + 210, y = from.y + 47, endY = to.y + 47;
        return <path key={dep+'-'+node.id} d={`M ${x} ${y} C ${x+35} ${y}, ${to.x-35} ${endY}, ${to.x} ${endY}`} fill="none" stroke="#8ba59f" strokeWidth="2" markerEnd="url(#workflow-arrow)"/>;
      }))}
    </svg>
    {nodes.map(node => {
      const status = steps.get(node.id)?.status || 'pending';
      const names = {pending:'待执行',running:'执行中',succeeded:'已通过',failed:'失败',skipped:'已跳过',cancelled:'已取消'};
      return <button key={node.id} className={'node '+status+(selected===node.id?' selected':'')}
        style={{left:positions[node.id].x, top:positions[node.id].y}}
        aria-pressed={selected===node.id} aria-label={`${node.id} ${names[status] || status}`}
        onClick={()=>onSelect(selected===node.id?'':node.id)}>
        <span className="node-top">{node.type.toUpperCase()} <span className={'badge '+status}>{names[status] || status}</span></span>
        <strong>{node.id}</strong><small>{node.type==='gate'?'独立证据验收':node.operation}</small>
      </button>;
    })}
  </div></div>;
}
