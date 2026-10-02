import React from 'react';
import {currentStage,statusLabel,taskSummary,displayStatus} from './workspaceState';

// 独立状态侧栏，主区域不再被流程卡片挤占。 Keep runtime facts beside the work rather than stacking workflow cards.
export function RuntimeRail({detail,view,select}){
  const stages=view.stages||[],active=currentStage(stages),summary=taskSummary(detail,Boolean(detail.active||detail.role_active));
  return <aside className="runtime-rail" aria-label="执行状态"><div className="rail-heading"><span className="eyebrow">RUNTIME</span><h2>执行状态</h2></div>
    <div className="rail-summary"><span className={'badge '+detail.run.status}>{displayStatus(detail)}</span><h3>{summary.title}</h3><p>{summary.text}</p></div>
    <ol className="stage-timeline">{stages.map(s=><li key={s.id}><button className={s.status+(s.id===active?.id?' selected':'')} onClick={()=>select(s.run_id)} aria-current={s.id===active?.id?'step':undefined}><i aria-hidden="true"/><span><strong>{s.label}</strong><small>{statusLabel[s.status]||s.status}</small></span></button></li>)}</ol>
    <details className="requirement"><summary>原始需求</summary><p>{detail.run.data.goal}</p></details>
    <p className="muted">角色负责决策；Executor 运行检查；Gate 独立核对证据。</p>
  </aside>;
}
