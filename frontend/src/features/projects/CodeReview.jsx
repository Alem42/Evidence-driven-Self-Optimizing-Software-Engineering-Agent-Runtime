import React,{useEffect,useState} from 'react';
import {api} from '../../api/client';
import {ProjectProgress} from './progress';

// 文件内容来自不可变草稿，批准时提交当前完整集合。 Load immutable draft files and submit the complete visible bundle.
export function ProjectCodeReview({detail,select,working}) {
  const plan=detail.run.data.project_plan;
  const [files,setFiles]=useState(null),[path,setPath]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [base,setBase]=useState(null);
  useEffect(()=>{let stopped=false;setBase(null);if(plan.base_approval_ref)api('/runs/'+detail.run.id+'/artifacts/'+plan.base_approval_ref).then(r=>{if(!stopped)setBase(r.artifact.files);}).catch(e=>{if(!stopped)setError(e.message);});return()=>{stopped=true;};},[detail.run.id,plan.base_approval_ref]);
  useEffect(()=>{let stopped=false;setFiles(null);setError('');if(!plan.files_ref)return;
    const ref=plan.approval_ref||plan.files_ref;
    api('/runs/'+detail.run.id+'/artifacts/'+ref).then(r=>{if(!stopped){const value=plan.approval_ref?r.artifact.files:r.artifact;setFiles(value);setPath(Object.keys(value)[0]);}}).catch(e=>{if(!stopped)setError(e.message);});
    return()=>{stopped=true;};
  },[detail.run.id,plan.files_ref,plan.approval_ref]);
  async function approve(){setBusy(true);setError('');try{const r=await api('/runs/'+detail.run.id+'/approve-project-code',{files_ref:plan.files_ref,files});select(r.id);}catch(e){setError(e.message);}finally{setBusy(false);}}
  const pending=plan.status==='awaiting_review'&&detail.run.status==='paused'&&!detail.run.cancel_requested;
  const locked=Boolean(plan.repair_of)&&(plan.revision_scope==='tests'?!path.endsWith('_test.go'):path==='go.mod'||path.endsWith('_test.go'));
  return <section className="panel code-review"><h3>{plan.revision_scope==='tests'?'Tester · 测试修订审核':plan.repair_of?'Developer · 修复版本审核':'Developer · 多文件代码审核'}</h3><p>查看当前代码和测试。批准后创建新项目版本并运行检查；Gate 根据实际退出码判断结果。</p>
    {plan.repair_of&&<div className="notice"><span>本次改动：{plan.changed_files?.join(', ')||'生成中'}。{plan.revision_scope==='tests'?'仅测试文件可修改，务必确认断言没有被削弱。':'测试和模块只读。'}</span><button onClick={()=>select(plan.repair_of)}>查看失败版本与日志</button></div>}
    {plan.status==='generating'&&<ProjectProgress job={{stage:'project_developer',started:detail.run.data.created_at}}/>}
    {(error||plan.error)&&<p className="error">{error||plan.error}</p>}{files&&<><label>项目文件<select value={path} onChange={e=>setPath(e.target.value)}>{Object.keys(files).map(n=><option key={n}>{n}</option>)}</select></label>
    {base&&<details><summary>查看此文件修复前内容</summary><pre>{base[path]}</pre></details>}
    <label>{path}{locked?' · 已冻结':''}<textarea className="code-editor" rows={22} readOnly={!pending||busy||locked} value={files[path]||''} onChange={e=>setFiles({...files,[path]:e.target.value})}/></label>
    {plan.review_mode!=='automatic'&&<button className="primary" disabled={busy||working||detail.run.cancel_requested||!['awaiting_review','approved'].includes(plan.status)} onClick={approve}>{busy?'准备项目与验证…':plan.status==='approved'?'查看或继续已批准项目':'批准整套代码并验证'}</button>}</>}
    <p className="hint">最多 20 个文件；未批准前只保存草稿，不执行代码。文件路径来自已确认架构。</p>
  </section>;
}
