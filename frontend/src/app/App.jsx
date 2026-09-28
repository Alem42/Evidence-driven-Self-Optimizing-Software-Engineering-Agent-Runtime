import React,{useEffect,useRef,useState} from 'react';
import {useWorkbench} from './useWorkbench';
import {SettingsDialog} from '../features/settings/SettingsDialog';
import {RoleGraph,statusLabel} from '../features/projects/RoleGraph';
import {ProjectDetails} from '../features/projects/ProjectDetails';
import {ProjectProgress} from '../features/projects/progress';

// 项目优先的单入口工作台；图在上，阶段产物和人工审批在下。
// Project-first workbench: one start action, lifecycle graph above outputs and review.
export default function App(){
  const w=useWorkbench();
  const [goal,setGoal]=useState(''),[model,setModel]=useState(''),[settings,setSettings]=useState(false),[starting,setStarting]=useState(false),[focus,setFocus]=useState(null),[autoVerify,setAutoVerify]=useState(false);
  const detailRef=useRef(null),graphRef=useRef(null),lastStageRef=useRef('');
  const profiles=w.profiles?{...w.profiles,active_id:model||w.profiles.active_id}:null;
  useEffect(()=>{setFocus(null);},[w.selected]);
  const stages=w.view?.stages||[];
  const currentStage=[...stages].reverse().find(s=>['running','blocked','failed'].includes(s.status))||[...stages].reverse().find(s=>s.status==='succeeded')||stages[0];
  const selectedStage=stages.find(s=>s.id===focus?.id)||currentStage;
  // 阶段真正变化时定位对应节点与下方操作区，避免长图和长表单造成手动滚动。
  // Follow real stage transitions into view without scrolling on every polling tick.
  useEffect(()=>{if(!currentStage||lastStageRef.current===currentStage.id)return;lastStageRef.current=currentStage.id;setFocus(null);
    const node=graphRef.current?.querySelector(`[data-stage-id="${currentStage.id}"]`);
    node?.scrollIntoView({behavior:'smooth',block:'nearest',inline:'center'});
    detailRef.current?.scrollIntoView({behavior:'smooth',block:'start'});
  },[currentStage?.id]);
  const ready=profiles?.profiles?.filter(p=>p.key_configured)||[];
  async function start(e){e.preventDefault();setStarting(true);try{await w.start(goal,model||ready.find(p=>p.id===profiles?.active_id)?.id||ready[0]?.id,autoVerify);}catch(e){w.setError(e.message);}finally{setStarting(false);}}
  return <div className="workbench"><aside className="sidebar"><a className="brand" href="/">MASA<span>软件工程工作台</span></a><button className="primary wide" disabled={w.working} onClick={()=>w.select('')}>＋ 新建项目</button><div className="side-title">项目 <span>{w.projects.length}</span></div>
    <nav aria-label="项目列表">{w.projects.map(p=><button key={p.id} className={'project-item '+(w.view?.id===p.id?'current':'')} onClick={()=>w.select(p.current_run_id)}><strong>{p.title}</strong><small>{statusLabel[p.status]||p.status} · {p.revision_count} 次验证</small></button>)}</nav>
    <div className="sidebar-bottom"><button onClick={()=>setSettings(true)}>API 与模型设置</button><small>{w.online?'● 本地服务已连接':'○ 正在连接服务'}</small></div></aside>
    <main className="workspace"><header className="workspace-header"><span className="eyebrow">EVIDENCE-DRIVEN SOFTWARE ENGINEERING</span><button onClick={()=>setSettings(true)}>{ready.length?'模型已就绪':'配置 API'}</button></header>
      {w.error&&<div className="error" role="alert">{w.error}<button onClick={()=>w.setError('')}>关闭</button></div>}
      {!w.selected&&!w.working?<section className="start-panel"><span className="eyebrow">从需求开始</span><h1>描述项目，观察每一步。</h1><p>Planner 规划结构，Tester 设计验证。你确认方案和代码后，Runtime 执行检查，Gate 独立验收。</p>
        <form onSubmit={start}><label>使用模型<select required value={model||ready.find(p=>p.id===profiles?.active_id)?.id||ready[0]?.id||''} onChange={e=>setModel(e.target.value)}><option value="">选择已配置的 API</option>{ready.map(p=><option key={p.id} value={p.id}>{p.name||p.model} · {p.model}</option>)}</select></label>
        <label>你想构建什么？<textarea autoFocus rows={7} required maxLength={16000} value={goal} onChange={e=>setGoal(e.target.value)} placeholder="例如：做一个 CSV 费用汇总工具，按类别汇总金额，支持退款；输入有错误时提示行号，不输出半份报表。"/></label>
        <label className="check"><input type="checkbox" checked={autoVerify} onChange={e=>setAutoVerify(e.target.checked)}/><span>自动生成、检查并最多修复四轮</span></label>
        {autoVerify&&<p className="hint">自动模式会直接采用符合契约的模型草稿并运行 Go 检查；不会逐步等你批准。遇到测试文件问题会生成新测试版本。最多 7 次模型调用，失败会停下供你查看。</p>}
        <div className="start-footer"><span>当前支持 Go 标准库 CLI 项目</span><button className="primary" disabled={starting||!ready.length||!w.boot?.runner_ready}>{starting?'正在启动…':'开始规划 →'}</button></div></form>
        {!ready.length&&<p className="hint">先在 API 与模型设置中添加密钥。</p>}</section>:<>
        {!w.detail&&<ProjectProgress job={w.job}/>}
        {w.detail&&w.view&&<><div className="project-heading"><div><span className="eyebrow">PROJECT</span><h1>{w.view.title}</h1></div><span className={'badge '+w.detail.run.status}>{statusLabel[w.detail.run.status]||w.detail.run.status}</span></div>
          <div ref={graphRef}><RoleGraph stages={stages} selected={selectedStage?.id} onSelect={stage=>{setFocus(stage);detailRef.current?.scrollIntoView({behavior:'smooth',block:'start'});}}/></div>
          <div ref={detailRef} className="current-stage"><ProjectDetails key={w.selected} detail={w.detail} view={w.view} profiles={profiles} select={w.select} onProgress={w.track} working={w.working}/></div>
        </>}
      </>}
      <footer>MASA · 每个结果都有来源，每次执行都有证据。</footer>
    </main>{settings&&profiles&&<SettingsDialog initial={w.profiles} close={()=>setSettings(false)} onUpdate={w.setProfiles}/>}</div>;
}
