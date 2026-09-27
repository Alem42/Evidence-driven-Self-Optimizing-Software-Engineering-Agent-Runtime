import React, {useEffect,useState} from 'react';
import {api} from './api';

// 两次角色调用只产生方案，界面明确区分规划与代码生成。 Two role calls create a plan, not generated code.
export function ProjectPlanningDialog({profiles,select,close}) {
  const ready=profiles?.profiles?.filter(p=>p.key_configured)||[];
  const [goal,setGoal]=useState(''),[profile,setProfile]=useState(ready[0]?.id||''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  async function submit(e){e.preventDefault();setBusy(true);setError('');try{const r=await api('/projects/plan',{goal,api_profile_id:profile});select(r.id);close();}catch(e){setError(e.message);}finally{setBusy(false);}}
  return <div className="overlay"><section className="modal inspector" role="dialog" aria-modal="true" aria-label="规划 Go 项目"><div className="panel-head"><h2>让 Planner 设计项目结构</h2><button disabled={busy} onClick={close}>关闭</button></div>
    <p>描述用途、输入输出、边界情况。Planner 提出架构与文件职责，Tester 再设计验证方案。标准库 Go CLI；本步骤不生成代码或执行工具。</p>
    <form onSubmit={submit}><label>API 配置<select required value={profile} onChange={e=>setProfile(e.target.value)}><option value="">选择已配置密钥的 API</option>{ready.map(p=><option key={p.id} value={p.id}>{p.name||p.model}</option>)}</select></label>
    <label>项目需求<textarea required rows={8} maxLength={16000} value={goal} onChange={e=>setGoal(e.target.value)} placeholder="做一个读取 CSV 的统计 CLI，支持指定列，输出计数、最小值与平均值；遇到空值跳过，非法数字给出行号。"/></label>
    <p className="hint">最多两次模型调用，不自动重试。请在 API 管理中配置足够的输出 token。</p>{error&&<p className="error">{error}</p>}
    <button className="primary wide" disabled={busy||!ready.length}>{busy?'Planner / Tester 正在设计，请稍候…':'生成项目方案'}</button></form></section></div>;
}

// 从持久化引用恢复审核内容，确认提交的是当前可见版本。 Restore persisted artifacts and approve the visible revision.
export function ProjectPlanReview({detail}) {
  const plan=detail.run.data.project_plan;
  const [spec,setSpec]=useState(null),[checks,setChecks]=useState([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[saved,setSaved]=useState(false);
  useEffect(()=>{let stop=false;setSpec(null);setSaved(false);setError('');
    async function load(){if(!plan.spec_ref)return;try{
      if(plan.approval_ref){const r=await api('/runs/'+detail.run.id+'/artifacts/'+plan.approval_ref);if(!stop){setSpec(r.artifact.spec);setChecks(r.artifact.checks);}}
      else{const [s,c]=await Promise.all([api('/runs/'+detail.run.id+'/artifacts/'+plan.spec_ref),plan.checks_ref?api('/runs/'+detail.run.id+'/artifacts/'+plan.checks_ref):Promise.resolve({artifact:[]})]);if(!stop){setSpec(s.artifact);setChecks(c.artifact);}}
    }catch(e){if(!stop)setError(e.message);}}load();return()=>{stop=true;};
  },[detail.run.id,plan.spec_ref,plan.checks_ref,plan.approval_ref]);
  const editable=plan.status==='awaiting_review'&&detail.run.status==='paused'&&!detail.run.cancel_requested&&!saved;
  async function approve(){setBusy(true);setError('');try{await api('/runs/'+detail.run.id+'/approve-project',{spec,checks,spec_ref:plan.spec_ref,checks_ref:plan.checks_ref});setSaved(true);}catch(e){setError(e.message);}finally{setBusy(false);}}
  return <section className="panel code-review"><h3>项目方案 · {plan.status==='approved'||saved?'已确认，等待代码生成阶段':plan.status==='awaiting_review'?'等待你确认':plan.status==='failed'?'规划失败':'角色规划中'}</h3>
    <p className="notice">这是架构提案，尚未生成项目文件，也没有运行测试。确认后冻结当前规格，供下一阶段生成代码使用。</p>
    {(error||plan.error)&&<p className="error">{error||plan.error}</p>}{spec&&<><fieldset disabled={!editable||busy}><legend>Planner · 架构与职责</legend>
    <label>设计说明<textarea rows={4} value={spec.summary} onChange={e=>setSpec({...spec,summary:e.target.value})}/></label>
    <label>Go 模块名<input value={spec.module} onChange={e=>setSpec({...spec,module:e.target.value})}/></label><p>固定入口：{spec.entrypoint}</p>
    <h4>目录与文件职责</h4>{spec.files.map((f,i)=><div className="field-pair" key={i}><label>文件路径<input value={f.path} onChange={e=>setSpec({...spec,files:spec.files.map((v,n)=>n===i?{...v,path:e.target.value}:v)})}/></label><label>负责什么<input value={f.purpose} onChange={e=>setSpec({...spec,files:spec.files.map((v,n)=>n===i?{...v,purpose:e.target.value}:v)})}/></label></div>)}
    <h4>验收标准</h4>{spec.acceptance.map((v,i)=><label key={i}>#{i+1}<textarea rows={2} value={v} onChange={e=>setSpec({...spec,acceptance:spec.acceptance.map((a,n)=>n===i?e.target.value:a)})}/></label>)}
    <h4>Tester · 验证方案</h4>{checks.map((c,i)=><label key={c.operation}>{c.operation} · 覆盖验收项 {c.acceptance_indices.map(n=>n+1).join(', ')||'通用检查'}<textarea rows={3} value={c.purpose} onChange={e=>setChecks(checks.map((v,n)=>n===i?{...v,purpose:e.target.value}:v))}/></label>)}
    </fieldset>{editable&&<button className="primary" disabled={busy} onClick={approve}>{busy?'正在确认…':'确认当前项目方案（不执行代码）'}</button>}</>}
  </section>;
}
