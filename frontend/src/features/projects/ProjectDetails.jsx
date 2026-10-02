import React,{useState} from 'react';
import {api} from '../../api/client';
import {ProjectPlanReview} from './PlanReview';
import {ProjectCodeReview} from './CodeReview';
import {ResultsPanel} from './ResultsPanel';
import {RepairPanel} from './RepairPanel';
import {WorkflowGraph} from './CheckGraph';
import {CodeSnapshot} from './CodeSnapshot';
import {projectJob} from './progress';
import {ApplicationPanel} from './ApplicationPanel';
import {ClarificationPanel} from './ClarificationPanel';

// 审核与下一步操作只在当前记录的语义范围内显示。
// Show review and next actions according to the selected record's actual lifecycle.
export function ProjectDetails({detail,view,profiles,select,onProgress,working}){
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[log,setLog]=useState(null),[copied,setCopied]=useState(false);
  const data=detail.run.data,plan=data.project_plan;
  if(plan?.status==='waiting_for_input')return <ClarificationPanel key={plan.clarification_id} detail={detail} profiles={profiles} select={select} onProgress={onProgress}/>;
  async function action(fn){setBusy(true);setError('');try{await fn();}catch(e){setError(e.message);}finally{setBusy(false);}}
  return <>
    {error&&<p className="error" role="alert">{error}</p>}
    {plan&&['planning','generating'].includes(plan.status)&&<section className="panel"><p>若服务已重启，可恢复已保存的角色结果。未收到结果的模型请求不会自动重放。</p><button disabled={busy} onClick={()=>action(async()=>{const r=await projectJob('/runs/'+detail.run.id+'/resume-project',{api_profile_id:profiles?.active_id},onProgress);select(r.id);})}>恢复中断的角色流程</button></section>}
    {plan?(plan.kind==='code'?<ProjectCodeReview detail={detail} select={select} working={working}/>:<ProjectPlanReview detail={detail} profiles={profiles} select={select} onProgress={onProgress} working={working}/>):<>
      <div className="panel action-bar"><button disabled={busy||working} onClick={()=>action(()=>api('/runs/'+detail.run.id+'/open-workspace',{}))}>打开代码文件夹 ↗</button>
        <button disabled={busy||working||!['succeeded','failed','needs_attention'].includes(detail.run.status)} onClick={()=>action(async()=>select((await api('/runs/'+detail.run.id+'/rerun',{})).id))}>重新验证</button>
        {!detail.active&&['created','paused','running'].includes(detail.run.status)&&<button className="primary" disabled={busy||working} onClick={()=>action(()=>api('/runs/'+detail.run.id+'/resume',{}))}>继续验证</button>}
        {detail.active&&<button onClick={()=>action(()=>api('/runs/'+detail.run.id+'/cancel',{}))}>停止验证</button>}
        </div>
      {data.project_bundle&&detail.run.status==='succeeded'&&<ApplicationPanel detail={detail}/>}
      {detail.run.status==='failed'&&data.project_bundle&&<RepairPanel detail={detail} profiles={profiles} select={select} onProgress={onProgress}/>}
      <ResultsPanel detail={detail}/>
      {data.project_bundle&&<CodeSnapshot detail={detail}/>}
    </>}
    <details className="panel diagnostics"><summary>版本记录与执行日志</summary><div className="action-bar"><button onClick={()=>action(async()=>{setLog((await api('/runs/'+detail.run.id+'/logs')).text);setCopied(false);})}>读取整体日志</button>{log&&<button onClick={()=>action(async()=>{await navigator.clipboard.writeText(log);setCopied(true);})}>{copied?'已复制':'复制日志'}</button>}</div>
      {log&&<pre className="log-output">{log}</pre>}
      <div className="version-list">{[...view.versions].reverse().map(v=><button key={v.run_id} className={v.run_id===detail.run.id?'current':''} onClick={()=>select(v.run_id)}><span>{v.kind==='plan'?'项目方案':v.kind==='code'?'代码版本':'验证运行'}</span><code>{v.run_id.slice(0,8)}</code><span>{v.status}</span></button>)}</div>
      {!plan&&<details><summary>技术详情：验证检查之间的依赖</summary><p className="muted">此图展示本版本选定的工具检查如何汇总到 Gate。它不是 Planner、Tester、Developer 的项目流程；重新运行同一验证配置时结构保持一致。</p><WorkflowGraph detail={detail} selected="" onSelect={()=>{}}/></details>}
      <p className="muted">运行 ID：{detail.run.id} · 证据事件：{detail.event_count}</p>
      {detail.role_calls?.length>0&&<div><h4>持久角色调用</h4>{detail.role_calls.map(r=><p key={r.purpose+':'+r.invocation_id}>{r.purpose} · 第 {r.attempt_no||1} 次 · {r.status} · {r.output_ref?'结果已保存':'结果未保存'}</p>)}</div>}
    </details>
  </>;
}
