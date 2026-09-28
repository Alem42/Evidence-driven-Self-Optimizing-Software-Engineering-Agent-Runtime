import React from 'react';

export const statusLabel={pending:'等待前序步骤',running:'执行中',succeeded:'已完成',failed:'失败',blocked:'等待你确认',cancelled:'已取消',paused:'已暂停',created:'准备中',needs_attention:'需检查'};

// 每个节点来自后端证据投影，点击仅切换下方详情。 Evidence-derived nodes select details without changing execution.
export function RoleGraph({stages,selected,onSelect}){
  return <section className="workflow-panel"><div className="section-heading"><div><span className="eyebrow">WORKFLOW</span><h2>项目执行流程</h2></div><span className="muted">角色决策 → 人工确认 → 真实执行</span></div>
    <div className="role-graph" role="list" aria-label="项目角色流程">{stages.map((stage,index)=><React.Fragment key={stage.id}>
      {index>0&&<span className="flow-arrow" aria-hidden="true">→</span>}
      <button role="listitem" className={`role-card ${stage.status} ${selected===stage.id?'selected':''}`} onClick={()=>onSelect(stage)} aria-label={`${stage.label}：${statusLabel[stage.status]||stage.status}`}>
        <span className="role-kind">{stage.kind==='human'?'人工确认':stage.kind==='gate'?'独立验收':'执行角色'}</span>
        <strong>{stage.label}</strong><span className="role-status"><i/>{statusLabel[stage.status]||stage.status}</span>
      </button></React.Fragment>)}</div>
  </section>;
}
