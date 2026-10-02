import {profileReady,profileLabel} from '../features/settings/profiles';
import React,{useState} from 'react';
import {useWorkbench} from './useWorkbench';
import {SettingsDialog} from '../features/settings/SettingsDialog';
import {statusLabel,taskSummary} from '../features/projects/workspaceState';
import {RuntimeRail} from '../features/projects/RuntimeRail';
import {ProjectDetails} from '../features/projects/ProjectDetails';
import {ProjectProgress} from '../features/projects/progress';
import {api} from '../api/client';

// 项目导航、任务内容和运行状态各占独立区域，不重复呈现同一产物。
// Separate navigation, task content and runtime facts without repeating artifacts.
export default function App(){
  const w=useWorkbench();
  const [goal,setGoal]=useState(''),[model,setModel]=useState(''),[settings,setSettings]=useState(false),[starting,setStarting]=useState(false),[automatic,setAutomatic]=useState(false),[search,setSearch]=useState('');
  const ready=w.profiles?.profiles?.filter(profileReady)||[];
  const profile=ready.find(p=>p.id===model)?.id||ready.find(p=>p.id===w.profiles?.active_id)?.id||ready[0]?.id||'';
  const profiles=w.profiles?{...w.profiles,active_id:profile}:null;
  const newProject=!w.selected&&!w.working;
  const projects=w.projects.filter(p=>p.title.toLowerCase().includes(search.toLowerCase()));
  async function start(e){e.preventDefault();if(!newProject||starting)return;setStarting(true);try{await w.start(goal,profile,automatic);}catch(e){w.setError(e.message);}finally{setStarting(false);}}
  async function resume(id){try{const r=await api('/jobs/'+id+'/resume',{});w.track({job_id:r.job_id,status:'running'});}catch(e){w.setError(e.message);}}
  return <div className="workbench"><aside className="sidebar"><div className="brand">MASA<span>软件工程工作台</span></div><button className="primary wide" disabled={w.working||starting} onClick={()=>w.select('')}>＋ 新建项目</button>
    <label className="project-search">项目历史<input aria-label="搜索项目" value={search} onChange={e=>setSearch(e.target.value)} placeholder="搜索项目…"/></label>
    <nav aria-label="项目列表">{projects.map(p=><button key={p.id} className={'project-item '+(w.view?.id===p.id?'current':'')} onClick={()=>w.select(p.current_run_id)}><strong>{p.title}</strong><small>{statusLabel[p.status]||p.status} · {p.revision_count} 次验证</small></button>)}{!projects.length&&<p className="muted">{search?'没有匹配项目':'项目会保存在这里'}</p>}</nav>
    <div className="sidebar-bottom"><button onClick={()=>setSettings(true)}>API 与模型</button><small>{w.online?'● 服务已连接':'○ 正在连接'}</small></div></aside>
    <main className="workspace"><header className="workspace-header"><div><span className="eyebrow">PROJECT WORKBENCH</span><h1 title={w.view?.title}>{w.view?.title||'新建项目'}</h1></div><button onClick={()=>setSettings(true)}>模型设置</button></header>
      {w.error&&<div className="error global-error" role="alert">{w.error}<button onClick={()=>w.setError('')}>关闭</button></div>}
      {w.job?.status==='running'&&!w.following&&<div className="background-job">后台任务仍在继续。<button onClick={()=>w.track(w.job)}>返回执行中的项目</button></div>}
      {w.boot?.active_run&&w.boot.active_run!==w.selected&&<div className="background-job">另一个版本正在运行工具检查。<button onClick={()=>w.select(w.boot.active_run)}>查看当前验证</button></div>}
      {!w.working&&w.boot?.interrupted_jobs?.length>0&&<details className="recovery-list"><summary>{w.boot.interrupted_jobs.length} 个任务可检查恢复</summary>{w.boot.interrupted_jobs.map(j=><div className="action-bar" key={j.job_id}><code>{j.job_id.slice(0,8)}</code><span>{j.phase}</span><button onClick={()=>resume(j.job_id)}>恢复</button>{j.run_id&&<button onClick={()=>w.select(j.run_id)}>查看</button>}</div>)}</details>}
      {newProject?<div className="new-project"><span className="eyebrow">START A PROJECT</span><h2>描述需求，得到可验证的代码。</h2><p>目前支持 Go 标准库命令行项目。写清输入、输出和错误处理，系统会规划、生成并执行真实检查。</p>
        <form onSubmit={start}><label>项目需求<textarea rows={9} required maxLength={16000} value={goal} onChange={e=>setGoal(e.target.value)} placeholder="例如：生成一个随机整数 CLI，支持范围、数量和可选种子；无效参数返回错误，不输出结果。"/></label>
          <div className="field-pair"><label>使用模型<select required value={profile} onChange={e=>setModel(e.target.value)}><option value="">请选择已配置的模型</option>{ready.map(p=><option key={p.id} value={p.id}>{profileLabel(p)}</option>)}</select></label><label>执行方式<select value={automatic?'automatic':'review'} onChange={e=>setAutomatic(e.target.value==='automatic')}><option value="review">逐步确认方案与代码</option><option value="automatic">自动生成、验证与有界修复</option></select></label></div>
          <div className="start-footer"><span className="hint">{automatic?'自动采用有效草稿，真实检查失败最多修复四轮。':'在发布和执行前，分别确认方案与代码。'} 会调用配置的 API。</span><button className="primary" disabled={starting||!ready.length||!w.boot?.runner_ready||!goal.trim()}>{starting?'正在启动…':'开始项目 →'}</button></div>
          {!ready.length&&<p className="notice">先添加模型配置。<button type="button" onClick={()=>setSettings(true)}>配置 API</button></p>}{w.boot&&!w.boot.runner_ready&&<p className="error">Go runner 尚未就绪，请先按环境指南构建。</p>}
        </form></div>:<div className="project-layout"><div className="project-content">{w.detail&&w.view?<ProjectDetails key={w.selected} detail={w.detail} view={w.view} profiles={profiles} select={w.select} onProgress={w.track} working={w.working}/>:w.working?<ProjectProgress job={w.job}/>:<div className="empty-document" role="status">正在读取保存的项目…</div>}</div>{w.detail&&w.view&&<RuntimeRail detail={w.detail} view={w.view} select={w.select}/>}</div>}
      <footer className="statusbar"><span>{w.working?'● 正在执行':w.detail?taskSummary(w.detail,false).title:'准备开始'}</span><span>{w.selected?'追加需求对话将在后续支持；当前操作在「当前任务」。':'Go 标准库 · 本地工具验证 · 证据可追溯'}</span></footer>
    </main>{settings&&profiles&&<SettingsDialog initial={w.profiles} close={()=>setSettings(false)} onUpdate={w.setProfiles}/>}</div>;
}
