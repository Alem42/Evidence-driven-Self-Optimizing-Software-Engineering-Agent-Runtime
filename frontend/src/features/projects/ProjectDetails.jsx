import React,{useState} from 'react';
import {api} from '../../api/client';
import {ProjectPlanReview} from './PlanReview';
import {ProjectCodeReview} from './CodeReview';
import {ResultsPanel} from './ResultsPanel';
import {RepairPanel} from './RepairPanel';
import {WorkflowGraph} from './CheckGraph';
import {CodeSnapshot} from './CodeSnapshot';

// 审核与下一步操作只在当前记录的语义范围内显示。
// Show review and next actions according to the selected record's actual lifecycle.
export function ProjectDetails({detail,view,profiles,select,onProgress,working}){
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[log,setLog]=useState(null),[copied,setCopied]=useState(false);
  const data=detail.run.data,plan=data.project_plan;
  async function action(fn){setBusy(true);setError('');try{await fn();}catch(e){setError(e.message);}finally{setBusy(false);}}
  return <>
    {error&&<p className="error" role="alert">{error}</p>}
    {plan?(plan.kind==='code'?<ProjectCodeReview detail={detail} select={select} working={working}/>:<ProjectPlanReview detail={detail} profiles={profiles} select={select} onProgress={onProgress} working={working}/>):<>
      <div className="panel action-bar"><button disabled={busy||working} onClick={()=>action(()=>api('/runs/'+detail.run.id+'/open-workspace',{}))}>打开代码文件夹 ↗</button>
        <button disabled={busy||working||!['succeeded','failed','needs_attention'].includes(detail.run.status)} onClick={()=>action(async()=>select((await api('/runs/'+detail.run.id+'/rerun',{})).id))}>重新验证</button>
        {!detail.active&&['created','paused','running'].includes(detail.run.status)&&<button className="primary" disabled={busy||working} onClick={()=>action(()=>api('/runs/'+detail.run.id+'/resume',{}))}>继续验证</button>}
        {detail.active&&<button onClick={()=>action(()=>api('/runs/'+detail.run.id+'/cancel',{}))}>停止验证</button>}
        <span className="muted">当前支持测试验证；应用程序启动尚未接入。</span></div>
      {detail.run.status==='failed'&&data.project_bundle&&<RepairPanel detail={detail} profiles={profiles} select={select} onProgress={onProgress}/>}
      <ResultsPanel detail={detail}/>
      {data.project_bundle&&<CodeSnapshot detail={detail}/>}
    </>}
    <details className="panel diagnostics"><summary>版本记录与执行日志</summary><div className="action-bar"><button onClick={()=>action(async()=>{setLog((await api('/runs/'+detail.run.id+'/logs')).text);setCopied(false);})}>读取整体日志</button>{log&&<button onClick={()=>action(async()=>{await navigator.clipboard.writeText(log);setCopied(true);})}>{copied?'已复制':'复制日志'}</button>}</div>
      {log&&<pre className="log-output">{log}</pre>}
      <div className="version-list">{[...view.versions].reverse().map(v=><button key={v.run_id} className={v.run_id===detail.run.id?'current':''} onClick={()=>select(v.run_id)}><span>{v.kind==='plan'?'项目方案':v.kind==='code'?'代码版本':'验证运行'}</span><code>{v.run_id.slice(0,8)}</code><span>{v.status}</span></button>)}</div>
      <h4>当前版本的检查依赖图</h4><WorkflowGraph detail={detail} selected="" onSelect={()=>{}}/>
      <p className="muted">运行 ID：{detail.run.id} · 证据事件：{detail.event_count}</p>
    </details>
  </>;
}
