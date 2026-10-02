import React,{useEffect,useState} from 'react';
import {api} from '../../api/client';
import {ProjectPlanReview} from './PlanReview';
import {ProjectCodeReview} from './CodeReview';
import {ResultsPanel} from './ResultsPanel';
import {RepairPanel} from './RepairPanel';
import {CodeSnapshot} from './CodeSnapshot';
import {projectJob} from './progress';
import {ApplicationPanel} from './ApplicationPanel';
import {ClarificationPanel} from './ClarificationPanel';
import {defaultPanel,statusLabel,taskSummary} from './workspaceState';

// 同一版本分区阅读；手动选择后，轮询不再抢走用户当前标签。
// Read one revision through distinct panels; polling never overrides manual tab selection.
export function ProjectDetails({detail,view,profiles,select,onProgress,working}){
  const [panel,setPanel]=useState(()=>defaultPanel(detail)),[manual,setManual]=useState(false);
  const [error,setError]=useState(''),[busy,setBusy]=useState(false),[log,setLog]=useState(null),[copied,setCopied]=useState(false);
  const data=detail.run.data,plan=data.project_plan,summary=taskSummary(detail,Boolean(detail.active||detail.role_active));
  useEffect(()=>{if(!manual)setPanel(defaultPanel(detail));},[detail.run.status,manual]);
  async function action(fn){setBusy(true);setError('');try{await fn();}catch(e){setError(e.message);}finally{setBusy(false);}}
  const tabs=[['task','当前任务'],['code','代码'],['checks','验证'],['history','日志与版本']];
  return <section className="project-document"><nav className="document-tabs" aria-label="工作区内容">{tabs.map(([id,label])=><button key={id} aria-current={panel===id?'page':undefined} className={panel===id?'current':''} onClick={()=>{setManual(true);setPanel(id);}}>{label}{id==='task'&&plan?.status==='waiting_for_input'&&<i className="attention-dot"/>}</button>)}</nav>
    <div className="document-body">{error&&<p className="error" role="alert">{error}</p>}
    {panel==='task'&&<><div className="task-heading"><h2>{summary.title}</h2><p>{summary.text}</p></div>
      {plan?.status==='waiting_for_input'?<ClarificationPanel key={plan.clarification_id} detail={detail} profiles={profiles} select={select} onProgress={onProgress}/>:plan?<>
        {!working&&['planning','generating'].includes(plan.status)&&<div className="notice">原调用记录已保存，不会自动重发未知请求。<button disabled={busy} onClick={()=>action(async()=>{const r=await projectJob('/runs/'+detail.run.id+'/resume-project',{api_profile_id:profiles?.active_id},onProgress);select(r.id);})}>检查并恢复流程</button></div>}
        {plan.kind==='code'?<ProjectCodeReview detail={detail} select={select} working={working}/>:<ProjectPlanReview detail={detail} profiles={profiles} select={select} onProgress={onProgress} working={working}/>}</>:<>
          <div className="action-bar"><button disabled={busy||working} onClick={()=>action(()=>api('/runs/'+detail.run.id+'/open-workspace',{}))}>打开代码文件夹 ↗</button><button disabled={busy||working||!['succeeded','failed','needs_attention'].includes(detail.run.status)} onClick={()=>action(async()=>select((await api('/runs/'+detail.run.id+'/rerun',{})).id))}>重新验证</button>
            {!detail.active&&['created','paused','running'].includes(detail.run.status)&&<button className="primary" disabled={busy||working} onClick={()=>action(()=>api('/runs/'+detail.run.id+'/resume',{}))}>继续验证</button>}{detail.active&&<button disabled={busy} onClick={()=>action(()=>api('/runs/'+detail.run.id+'/cancel',{}))}>停止验证</button>}</div>
          {data.project_bundle&&detail.run.status==='succeeded'&&<ApplicationPanel detail={detail}/>}
          {detail.run.status==='failed'&&data.project_bundle&&<><button onClick={()=>{setManual(true);setPanel('checks');}}>查看失败检查 →</button><RepairPanel detail={detail} profiles={profiles} select={select} onProgress={onProgress}/></>}
        </>}</>}
    {panel==='code'&&<CodeSnapshot detail={detail}/>}
    {panel==='checks'&&(plan?<div className="empty-document"><h2>尚未执行工具检查</h2><p>规划阶段的 Tester 设计用例。完整代码发布后，Executor 才运行 Go test、vet 和格式检查。</p><button onClick={()=>{setManual(true);setPanel('task');}}>回到当前任务</button></div>:<ResultsPanel detail={detail}/>)}
    {panel==='history'&&<><div className="section-heading"><h2>日志与版本</h2><div className="action-bar"><button disabled={busy} onClick={()=>action(async()=>{setLog((await api('/runs/'+detail.run.id+'/logs')).text);setCopied(false);})}>读取日志</button>{log!==null&&<button onClick={()=>action(async()=>{await navigator.clipboard.writeText(log);setCopied(true);})}>{copied?'已复制':'复制日志'}</button>}</div></div>{log!==null&&<pre className="log-output">{log||'暂无日志'}</pre>}
      <h3>版本记录</h3><div className="version-list">{[...view.versions].reverse().map(v=><button key={v.run_id} className={v.run_id===detail.run.id?'current':''} onClick={()=>select(v.run_id)}><span>{v.kind==='plan'?'方案':v.kind==='code'?'代码草稿':'工具验证'}</span><code>{v.run_id.slice(0,8)}</code><span>{statusLabel[v.status]||v.status}</span></button>)}</div>
      <details><summary>角色调用与技术信息</summary><p className="path">Run：{detail.run.id} · {detail.event_count} 个事件</p>{detail.role_calls?.map(r=><p key={r.purpose+':'+r.invocation_id}>{r.purpose} · 第 {r.attempt_no||1} 次 · {r.status} · {r.output_ref?'结果已保存':'结果未保存'}</p>)}</details></>}
    </div></section>;
}
