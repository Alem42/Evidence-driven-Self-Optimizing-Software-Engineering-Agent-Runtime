import {profileReady,profileLabel} from '../settings/profiles';
import React, {useEffect,useState} from 'react';
import {api} from '../../api/client';
import {projectJob,ProjectProgress} from './progress';

// 从持久化引用恢复审核内容，确认提交的是当前可见版本。 Restore persisted artifacts and approve the visible revision.
export function ProjectPlanReview({detail,profiles,select,onProgress,working}) {
  const plan=detail.run.data.project_plan;
  const [spec,setSpec]=useState(null),[checks,setChecks]=useState([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[saved,setSaved]=useState(false);
  const [profile,setProfile]=useState('');
  const [job,setJob]=useState(null);
  async function generate(){setBusy(true);setError('');try{const r=await projectJob('/runs/'+detail.run.id+'/generate-project',{api_profile_id:profile||profiles?.active_id},j=>{setJob(j);onProgress?.(j);});select(r.id);}catch(e){setError(e.message);}finally{setBusy(false);}}
  async function retry(){setBusy(true);setError('');try{const r=await projectJob('/projects/plan',{retry_run_id:detail.run.id,api_profile_id:profiles?.active_id},j=>{setJob(j);onProgress?.(j);});select(r.id);}catch(e){setError(e.message);}finally{setBusy(false);}}
  useEffect(()=>{let stop=false;setSpec(null);setSaved(false);setError('');
    async function load(){if(!plan.spec_ref)return;try{
      if(plan.approval_ref){const r=await api('/runs/'+detail.run.id+'/artifacts/'+plan.approval_ref);if(!stop){setSpec(r.artifact.spec);setChecks(r.artifact.checks);}}
      else{const [s,c]=await Promise.all([api('/runs/'+detail.run.id+'/artifacts/'+plan.spec_ref),plan.checks_ref?api('/runs/'+detail.run.id+'/artifacts/'+plan.checks_ref):Promise.resolve({artifact:[]})]);if(!stop){setSpec(s.artifact);setChecks(c.artifact);}}
    }catch(e){if(!stop)setError(e.message);}}load();return()=>{stop=true;};
  },[detail.run.id,plan.spec_ref,plan.checks_ref,plan.approval_ref]);
  const editable=plan.status==='awaiting_review'&&detail.run.status==='paused'&&!detail.run.cancel_requested&&!saved;
  async function approve(andGenerate=false){setBusy(true);setError('');try{await api('/runs/'+detail.run.id+'/approve-project',{spec,checks,spec_ref:plan.spec_ref,checks_ref:plan.checks_ref});setSaved(true);if(andGenerate){const r=await projectJob('/runs/'+detail.run.id+'/generate-project',{api_profile_id:profile||profiles?.active_id},j=>{setJob(j);onProgress?.(j);});select(r.id);}}catch(e){setError(e.message);}finally{setBusy(false);}}
  return <section className="panel code-review">
    {(busy||(detail.role_active&&plan.status==='planning'))&&<ProjectProgress job={job||{stage:detail.events.filter(e=>e.type==='model_requested').at(-1)?.payload.step_id,started:detail.run.data.created_at}}/>}
    {(plan.status==='approved'||saved)&&plan.review_mode!=='automatic'&&<div><label>生成代码使用的模型<select value={profile||profiles?.active_id||''} onChange={e=>setProfile(e.target.value)}><option value="">选择配置</option>{profiles?.profiles?.filter(profileReady).map(p=><option key={p.id} value={p.id}>{profileLabel(p)}</option>)}</select></label><button className="primary" disabled={busy||working||!profiles?.profiles?.some(profileReady)} onClick={generate}>{busy?'Developer 正在生成文件…':'根据已确认方案生成代码'}</button><p className="hint">一次真实模型调用；生成后审核代码，再自动验证。</p></div>}
    {plan.coverage_warning&&<p className="notice">{plan.coverage_warning}</p>}
    {plan.test_review?.findings?.length>0&&<div className="notice"><strong>独立测试计划评审</strong><ul>{plan.test_review.findings.map((f,i)=><li key={i}>{f.severity==='blocking'?'需修正':'建议检查'}：{f.message}</li>)}</ul><p>规则评审发现潜在问题；它不能证明测试正确或代码通过。</p></div>}
    {plan.status==='failed'&&plan.spec_ref&&<button disabled={busy} onClick={retry}>保留架构，仅重试验证方案</button>}
    {(error||plan.error)&&<p className="error">{error||plan.error}</p>}{spec&&<><p>{spec.summary}</p><p className="muted">{spec.files.length} 个计划文件 · {spec.acceptance.length} 条验收标准 · {checks.length} 项拟执行检查。Tester 此时只设计测试，尚未运行 Go 测试。</p>
    {editable&&<button className="primary" disabled={busy||working} onClick={()=>approve(true)}>{busy?'正在确认…':'确认方案并生成代码与测试'}</button>}
    <details className="plan-edit"><summary>查看或修改项目结构、验收标准和测试方案</summary><fieldset disabled={!editable||busy}><legend>Planner · 架构与职责</legend>
    <label>设计说明<textarea rows={4} value={spec.summary} onChange={e=>setSpec({...spec,summary:e.target.value})}/></label>
    <label>Go 模块名<input value={spec.module} onChange={e=>setSpec({...spec,module:e.target.value})}/></label><p>固定入口：{spec.entrypoint}</p>
    <h4>目录与文件职责</h4>{spec.files.map((f,i)=><div className="field-pair" key={i}><label>文件路径<input value={f.path} onChange={e=>setSpec({...spec,files:spec.files.map((v,n)=>n===i?{...v,path:e.target.value}:v)})}/></label><label>负责什么<input value={f.purpose} onChange={e=>setSpec({...spec,files:spec.files.map((v,n)=>n===i?{...v,purpose:e.target.value}:v)})}/></label></div>)}
    <h4>验收标准</h4>{spec.acceptance.map((v,i)=><label key={i}>#{i+1}<textarea rows={2} value={v} onChange={e=>setSpec({...spec,acceptance:spec.acceptance.map((a,n)=>n===i?e.target.value:a)})}/></label>)}
    <h4>Tester · 验证方案</h4>{checks.map((c,i)=><div key={c.operation}><label>{c.operation} · 覆盖验收项 {c.acceptance_indices.map(n=>n+1).join(', ')||'通用检查'}<textarea rows={3} value={c.purpose} onChange={e=>setChecks(checks.map((v,n)=>n===i?{...v,purpose:e.target.value}:v))}/></label>{c.cases?.map((t,n)=><details key={n}><summary>{t.level} · {t.name}</summary><p>输入：{t.input}</p><p>预期：{t.expected}</p></details>)}</div>)}
    </fieldset></details></>}
  </section>;
}
