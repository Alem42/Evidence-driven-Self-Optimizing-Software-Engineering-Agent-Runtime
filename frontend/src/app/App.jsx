import React,{useState} from 'react';
import {useWorkbench} from './useWorkbench';
import {SettingsDialog} from '../features/settings/SettingsDialog';
import {RoleGraph,statusLabel} from '../features/projects/RoleGraph';
import {ProjectDetails} from '../features/projects/ProjectDetails';
import {ProjectProgress} from '../features/projects/progress';
import {api} from '../api/client';

// 单项目工作台：历史、中央产物、紧凑阶段、固定需求输入。
// One project workbench: history, central artifacts, compact stages and a persistent composer.
export default function App(){
  const w=useWorkbench();
  const [goal,setGoal]=useState(''),[model,setModel]=useState(''),[settings,setSettings]=useState(false),[starting,setStarting]=useState(false),[autoVerify,setAutoVerify]=useState(false);
  const profiles=w.profiles?{...w.profiles,active_id:model||w.profiles.active_id}:null;
  const ready=profiles?.profiles?.filter(p=>p.key_configured)||[];
  const stages=w.view?.stages||[];
  const current=[...stages].reverse().find(s=>['running','blocked','failed'].includes(s.status))||[...stages].reverse().find(s=>s.status==='succeeded');
  const canCompose=!w.selected&&!w.working&&!starting;
  async function start(e){e.preventDefault();if(!canCompose)return;setStarting(true);try{await w.start(goal,model||ready.find(p=>p.id===profiles?.active_id)?.id||ready[0]?.id,autoVerify);}catch(e){w.setError(e.message);}finally{setStarting(false);}}
  async function resume(id){try{const r=await api('/jobs/'+id+'/resume',{});w.track({job_id:r.job_id,status:'running'});}catch(e){w.setError(e.message);}}
  return <div className="workbench"><aside className="sidebar"><a className="brand" href="/">MASA<span>软件工程工作台</span></a><button className="primary wide" disabled={w.working} onClick={()=>w.select('')}>＋ 新建项目</button><div className="side-title">项目历史 <span>{w.projects.length}</span></div>
    <nav aria-label="项目列表">{w.projects.map(p=><button key={p.id} className={'project-item '+(w.view?.id===p.id?'current':'')} onClick={()=>w.select(p.current_run_id)}><strong>{p.title}</strong><small>{statusLabel[p.status]||p.status} · {p.revision_count} 次验证</small></button>)}</nav>
    <div className="sidebar-bottom"><button onClick={()=>setSettings(true)}>API 与模型设置</button><small>{w.online?'● 本地服务已连接':'○ 正在连接服务'}</small></div></aside>
    <main className="workspace ide-workspace"><header className="workspace-header"><strong>MASA / {w.view?'项目工作区':'新建项目'}</strong><button onClick={()=>setSettings(true)}>{ready.length?'模型设置':'配置 API'}</button></header>
      {w.view&&<section className="stage-strip" aria-label="当前项目进度"><span>{current?`${current.label} · ${statusLabel[current.status]||current.status}`:'准备中'}</span><details><summary>查看阶段记录</summary><RoleGraph stages={stages} selected={current?.id} onSelect={stage=>w.select(stage.run_id)}/></details></section>}
      <div className="artifact-area">
        {w.error&&<div className="error" role="alert">{w.error}<button onClick={()=>w.setError('')}>关闭</button></div>}
        {!w.working&&w.boot?.interrupted_jobs?.map(j=><div className="notice" key={j.job_id}>中断任务 · {j.phase}<button onClick={()=>resume(j.job_id)}>恢复任务</button>{j.run_id&&<button onClick={()=>w.select(j.run_id)}>查看记录</button>}</div>)}
        {!w.detail?(w.selected||w.working?<ProjectProgress job={w.job}/>:<section className="empty-workspace"><h1>从需求到可验证的代码</h1><p>在下方选择模型、描述用途和边界，然后开始。Planner 规划、Tester 设计测试、Developer 生成代码，Runtime 执行检查，Gate 核对真实结果。</p><p>代码生成期间显示实际阶段进度，完整响应校验后显示文件。左侧保存项目与版本历史。</p></section>):w.view&&<ProjectDetails key={w.selected} detail={w.detail} view={w.view} profiles={profiles} select={w.select} onProgress={w.track} working={w.working}/>}
      </div>
      <form className="composer" onSubmit={start}><div className="composer-toolbar"><label>模型<select disabled={!canCompose} required value={model||ready.find(p=>p.id===profiles?.active_id)?.id||ready[0]?.id||''} onChange={e=>setModel(e.target.value)}><option value="">选择已配置 API</option>{ready.map(p=><option key={p.id} value={p.id}>{p.name||p.model}</option>)}</select></label><label className="check"><input type="checkbox" disabled={!canCompose} checked={autoVerify} onChange={e=>setAutoVerify(e.target.checked)}/><span>自动验证与有界修复</span></label><button className="primary" disabled={!canCompose||!ready.length||!w.boot?.runner_ready||!goal.trim()}>{starting?'正在启动…':'开始'}</button></div>
        <label className="composer-input">项目需求<textarea rows={3} required maxLength={16000} disabled={!canCompose} value={goal} onChange={e=>setGoal(e.target.value)} placeholder="描述用途、输入输出和边界条件…"/></label>
        <small>{w.working?'执行期间暂停新需求输入；需要澄清时会显示问题。':w.selected?'当前项目的动作在上方。选择「新建项目」输入新需求；追加需求对话将在后续支持。':autoVerify?'自动采用有效方案和代码，真实检查失败最多修复四轮；可能调用付费 API。':'逐步确认方案和代码后，再运行真实检查。'}</small>
      </form>
    </main>{settings&&profiles&&<SettingsDialog initial={w.profiles} close={()=>setSettings(false)} onUpdate={w.setProfiles}/>}</div>;
}
