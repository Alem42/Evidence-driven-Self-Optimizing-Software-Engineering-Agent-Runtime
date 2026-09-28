import React,{useEffect,useState} from 'react';
import {useWorkbench} from './useWorkbench';
import {SettingsDialog} from '../features/settings/SettingsDialog';
import {RoleGraph,statusLabel} from '../features/projects/RoleGraph';
import {RoleResult} from '../features/projects/RoleResult';
import {ProjectDetails} from '../features/projects/ProjectDetails';
import {ProjectProgress} from '../features/projects/progress';

// 项目优先的单入口工作台；图在上，阶段产物和人工审批在下。
// Project-first workbench: one start action, lifecycle graph above outputs and review.
export default function App(){
  const w=useWorkbench();
  const [goal,setGoal]=useState(''),[model,setModel]=useState(''),[settings,setSettings]=useState(false),[starting,setStarting]=useState(false),[focus,setFocus]=useState(null);
  const profiles=w.profiles?{...w.profiles,active_id:model||w.profiles.active_id}:null;
  useEffect(()=>{setFocus(null);},[w.selected]);
  const stages=w.view?.stages||[];
  const latestOutput=[...stages].reverse().find(s=>s.response_ref);
  const selectedStage=stages.find(s=>s.id===focus?.id)||latestOutput;
  const ready=profiles?.profiles?.filter(p=>p.key_configured)||[];
  async function start(e){e.preventDefault();setStarting(true);try{await w.start(goal,model||ready.find(p=>p.id===profiles?.active_id)?.id||ready[0]?.id);}catch(e){w.setError(e.message);}finally{setStarting(false);}}
  return <div className="workbench"><aside className="sidebar"><a className="brand" href="/">MASA<span>软件工程工作台</span></a><button className="primary wide" disabled={w.working} onClick={()=>w.select('')}>＋ 新建项目</button><div className="side-title">项目 <span>{w.projects.length}</span></div>
    <nav aria-label="项目列表">{w.projects.map(p=><button key={p.id} className={'project-item '+(w.view?.id===p.id?'current':'')} onClick={()=>w.select(p.current_run_id)}><strong>{p.title}</strong><small>{statusLabel[p.status]||p.status} · {p.revision_count} 次验证</small></button>)}</nav>
    <div className="sidebar-bottom"><button onClick={()=>setSettings(true)}>API 与模型设置</button><small>{w.online?'● 本地服务已连接':'○ 正在连接服务'}</small></div></aside>
    <main className="workspace"><header className="workspace-header"><span className="eyebrow">EVIDENCE-DRIVEN SOFTWARE ENGINEERING</span><button onClick={()=>setSettings(true)}>{ready.length?'模型已就绪':'配置 API'}</button></header>
      {w.error&&<div className="error" role="alert">{w.error}<button onClick={()=>w.setError('')}>关闭</button></div>}
      {!w.selected&&!w.working?<section className="start-panel"><span className="eyebrow">从需求开始</span><h1>描述项目，观察每一步。</h1><p>Planner 规划结构，Tester 设计验证。你确认方案和代码后，Runtime 执行检查，Gate 独立验收。</p>
        <form onSubmit={start}><label>使用模型<select required value={model||ready.find(p=>p.id===profiles?.active_id)?.id||ready[0]?.id||''} onChange={e=>setModel(e.target.value)}><option value="">选择已配置的 API</option>{ready.map(p=><option key={p.id} value={p.id}>{p.name||p.model} · {p.model}</option>)}</select></label>
        <label>你想构建什么？<textarea autoFocus rows={7} required maxLength={16000} value={goal} onChange={e=>setGoal(e.target.value)} placeholder="例如：做一个 CSV 费用汇总工具，按类别汇总金额，支持退款；输入有错误时提示行号，不输出半份报表。"/></label>
        <div className="start-footer"><span>当前支持 Go 标准库 CLI 项目</span><button className="primary" disabled={starting||!ready.length||!w.boot?.runner_ready}>{starting?'正在启动…':'开始规划 →'}</button></div></form>
        {!ready.length&&<p className="hint">先在 API 与模型设置中添加密钥。</p>}</section>:<>
        {!w.detail&&<ProjectProgress job={w.job}/>}
        {w.detail&&w.view&&<><div className="project-heading"><div><span className="eyebrow">PROJECT</span><h1>{w.view.title}</h1></div><span className={'badge '+w.detail.run.status}>{statusLabel[w.detail.run.status]||w.detail.run.status}</span></div>
          <RoleGraph stages={stages} selected={selectedStage?.id} onSelect={setFocus}/>
          <RoleResult stage={selectedStage}/>
          <ProjectDetails key={w.selected} detail={w.detail} view={w.view} profiles={profiles} select={w.select} onProgress={w.track} working={w.working}/>
        </>}
      </>}
      <footer>MASA · 每个结果都有来源，每次执行都有证据。</footer>
    </main>{settings&&profiles&&<SettingsDialog initial={w.profiles} close={()=>setSettings(false)} onUpdate={w.setProfiles}/>}</div>;
}
